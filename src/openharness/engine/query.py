"""Core tool-aware query loop."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable
from uuid import uuid4

from openharness.api.client import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiReasoningDeltaEvent,
    ApiRetryEvent,
    ApiTextDeltaEvent,
    SupportsStreamingMessages,
)
from openharness.api.provider import is_model_multimodal
from openharness.api.usage import UsageSnapshot
from openharness.config.paths import get_data_dir
from openharness.engine.focus import (  # noqa: F401 - re-exported for callers/tests
    _record_tool_carryover,
    _remember_active_artifact,
    _remember_async_agent_activity,
    _remember_async_agent_task,
    _remember_read_file,
    _remember_skill_invocation,
    _remember_verified_work,
    _remember_work_log,
    _update_plan_mode,
    remember_user_goal,
)
from openharness.engine.messages import (
    ConversationMessage,
    ImageBlock,
    TextBlock,
    ToolResultBlock,
)
from openharness.engine.stream_events import (
    AssistantReasoningDelta,
    AssistantTextDelta,
    AssistantTurnComplete,
    CompactProgressEvent,
    ErrorEvent,
    StatusEvent,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionProgress,
    ToolExecutionStarted,
)
from openharness.engine.tool_exec import _execute_tool_call
from openharness.hooks import HookEvent, HookExecutor
from openharness.permissions.checker import PermissionChecker
from openharness.services.errors import is_prompt_too_long_error
from openharness.services.tool_outputs import tool_output_inline_chars, tool_output_preview_chars
from openharness.tools.ask_user_question_tool import AskUserPrompt
from openharness.tools.base import ToolExecutionContext, ToolRegistry

AUTO_COMPACT_STATUS_MESSAGE = "Auto-compacting conversation memory to keep things fast and focused."
REACTIVE_COMPACT_STATUS_MESSAGE = "Prompt too long; compacting conversation memory and retrying."
MAX_SAFE_COMPLETION_TOKENS = 128_000

log = logging.getLogger(__name__)


PermissionPrompt = Callable[[str, str], Awaitable[bool]]

MAX_TRACKED_READ_FILES = 6
MAX_TRACKED_SKILLS = 8
MAX_TRACKED_ASYNC_AGENT_EVENTS = 8
MAX_TRACKED_ASYNC_AGENT_TASKS = 12
MAX_TRACKED_WORK_LOG = 10
MAX_TRACKED_USER_GOALS = 5
MAX_TRACKED_ACTIVE_ARTIFACTS = 8
MAX_TRACKED_VERIFIED_WORK = 10


# Backwards-compatible private alias for tests and callers.
_is_prompt_too_long_error = is_prompt_too_long_error


def _bounded_completion_tokens(max_tokens: int, context_window_tokens: int | None = None) -> int:
    """Return a conservative per-request output token cap.

    Some OpenAI-compatible providers reject very large ``max_tokens`` before
    the request reaches model-side context management.  Keep oversized user
    config from making every turn fail while preserving normal defaults.
    """
    limit = MAX_SAFE_COMPLETION_TOKENS
    if context_window_tokens is not None and context_window_tokens > 0:
        limit = min(limit, int(context_window_tokens))
    return max(1, min(int(max_tokens), limit))


def _extract_completion_token_limit(exc: Exception) -> int | None:
    """Parse provider errors such as "supports at most 128000 completion tokens"."""
    text = str(exc).lower().replace(",", "")
    patterns = (
        r"supports at most\s+(\d+)\s+completion tokens",
        r"at most\s+(\d+)\s+completion tokens",
        r"max(?:imum)?(?:_completion)?[_\s-]tokens.*?(?:<=|less than or equal to|at most)\s+(\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return max(1, int(match.group(1)))
            except ValueError:
                return None
    return None


def _is_completion_token_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        ("max_tokens" in text or "max_completion_tokens" in text)
        and ("too large" in text or "at most" in text or "completion tokens" in text)
    )


class MaxTurnsExceeded(RuntimeError):
    """Raised when the agent exceeds the configured max_turns for one user prompt."""

    def __init__(self, max_turns: int) -> None:
        super().__init__(f"Exceeded maximum turn limit ({max_turns})")
        self.max_turns = max_turns


@dataclass
class QueryContext:
    """Context shared across a query run."""

    api_client: SupportsStreamingMessages
    tool_registry: ToolRegistry
    permission_checker: PermissionChecker
    cwd: Path
    model: str
    system_prompt: str
    max_tokens: int
    effort: str | None = None
    context_window_tokens: int | None = None
    auto_compact_threshold_tokens: int | None = None
    permission_prompt: PermissionPrompt | None = None
    ask_user_prompt: AskUserPrompt | None = None
    max_turns: int | None = 200
    hook_executor: HookExecutor | None = None
    tool_metadata: dict[str, object] | None = None

def _tool_artifact_dir() -> Path:
    artifact_dir = get_data_dir() / "tool_artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    return artifact_dir


def _safe_tool_artifact_name(tool_name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", tool_name.strip())
    return (normalized or "tool")[:80]


def _offload_tool_output_if_needed(
    *,
    tool_name: str,
    tool_use_id: str,
    output: str,
) -> tuple[str, Path | None]:
    inline_limit = tool_output_inline_chars()
    if len(output) <= inline_limit:
        return output, None

    artifact_path = (
        _tool_artifact_dir()
        / f"{time.strftime('%Y%m%d-%H%M%S')}-{_safe_tool_artifact_name(tool_name)}-{uuid4().hex[:12]}.txt"
    )
    artifact_path.write_text(output, encoding="utf-8", errors="replace")
    preview = output[:tool_output_preview_chars()]
    omitted = max(0, len(output) - len(preview))
    inline = (
        "[Tool output truncated]\n"
        f"Tool: {tool_name}\n"
        f"Tool use id: {tool_use_id}\n"
        f"Original size: {len(output)} chars\n"
        f"Full output saved to: {artifact_path}\n"
        f"Inline preview: first {len(preview)} chars"
    )
    if omitted:
        inline += f" ({omitted} chars omitted)"
    if preview:
        inline += f"\n\nPreview:\n{preview}"
    return inline, artifact_path


# ---------------------------------------------------------------------------
# Image preprocessing — convert ImageBlocks to text for non-multimodal models
# ---------------------------------------------------------------------------

_IMAGE_PREPROCESS_STATUS = "Converting image to text description via vision model…"


async def _preprocess_images_in_messages(
    messages: list[ConversationMessage],
    context: QueryContext,
) -> AsyncIterator[StreamEvent]:
    """Scan messages for ImageBlocks and convert them to text if the active
    model does not support multimodal input.

    Yields status events during conversion so the UI stays responsive.
    """
    if is_model_multimodal(context.model):
        return

    vision_config = context.tool_metadata.get("vision_model_config")
    if not vision_config:
        # No vision model configured — skip preprocessing.
        return

    # Collect all ImageBlocks with their parent message index and block index
    pending: list[tuple[int, int, ImageBlock]] = []
    for msg_idx, msg in enumerate(messages):
        if msg.role != "user":
            continue
        for blk_idx, block in enumerate(msg.content):
            if isinstance(block, ImageBlock):
                pending.append((msg_idx, blk_idx, block))

    if not pending:
        return

    yield StatusEvent(message=_IMAGE_PREPROCESS_STATUS)

    # Process images in parallel
    async def _describe(msg_idx: int, blk_idx: int, block: ImageBlock) -> tuple[int, int, str]:
        tool = context.tool_registry.get("image_to_text")
        if tool is None:
            return msg_idx, blk_idx, "[Image: could not describe — image_to_text tool not available]"

        # Build tool input
        tool_input_data: dict[str, object] = {
            "image_data": block.data,
            "media_type": block.media_type,
            "prompt": "Describe this image in detail, including any text, "
                      "UI elements, code, diagrams, or visual information present.",
        }

        try:
            parsed = tool.input_model.model_validate(tool_input_data)
        except Exception:
            return msg_idx, blk_idx, "[Image: could not parse image data]"

        exec_context = ToolExecutionContext(
            cwd=context.cwd,
            metadata={
                "vision_model_config": vision_config,
                **(context.tool_metadata or {}),
            },
        )
        result = await tool.execute(parsed, exec_context)
        if result.is_error:
            return msg_idx, blk_idx, f"[Image description failed: {result.output}]"
        return msg_idx, blk_idx, result.output

    results = await asyncio.gather(*[_describe(mi, bi, blk) for mi, bi, blk in pending])

    # Replace ImageBlocks with TextBlocks in-place
    for msg_idx, blk_idx, description in results:
        msg = messages[msg_idx]
        msg.content[blk_idx] = TextBlock(text=description)


async def run_query(
    context: QueryContext,
    messages: list[ConversationMessage],
) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
    """Run the conversation loop until the model stops requesting tools.

    Auto-compaction is checked at the start of each turn.  When the
    estimated token count exceeds the model's auto-compact threshold,
    the engine first tries a cheap microcompact (clearing old tool result
    content) and, if that is not enough, performs a full LLM-based
    summarization of older messages.
    """
    from openharness.services.compact import (
        AutoCompactState,
        auto_compact_if_needed,
    )

    compact_state = AutoCompactState()
    reactive_compact_attempted = False
    last_compaction_result: tuple[list[ConversationMessage], bool] = (messages, False)
    effective_max_tokens = _bounded_completion_tokens(
        context.max_tokens,
        context.context_window_tokens,
    )
    reported_token_clamp = False

    async def _stream_compaction(
        *,
        trigger: str,
        force: bool = False,
    ) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
        nonlocal last_compaction_result
        progress_queue: asyncio.Queue[CompactProgressEvent] = asyncio.Queue()

        async def _progress(event: CompactProgressEvent) -> None:
            await progress_queue.put(event)

        task = asyncio.create_task(
            auto_compact_if_needed(
                messages,
                api_client=context.api_client,
                model=context.model,
                system_prompt=context.system_prompt,
                state=compact_state,
                progress_callback=_progress,
                force=force,
                trigger=trigger,
                hook_executor=context.hook_executor,
                carryover_metadata=context.tool_metadata,
                context_window_tokens=context.context_window_tokens,
                auto_compact_threshold_tokens=context.auto_compact_threshold_tokens,
            )
        )
        while True:
            try:
                event = await asyncio.wait_for(progress_queue.get(), timeout=0.05)
                yield event, None
            except asyncio.TimeoutError:
                if task.done():
                    break
                continue
        while not progress_queue.empty():
            yield progress_queue.get_nowait(), None
        last_compaction_result = await task
        return

    turn_count = 0
    while context.max_turns is None or turn_count < context.max_turns:
        turn_count += 1
        if effective_max_tokens != context.max_tokens and not reported_token_clamp:
            reported_token_clamp = True
            yield StatusEvent(
                message=(
                    "Requested max_tokens="
                    f"{context.max_tokens} exceeds the safe per-request output cap; "
                    f"using {effective_max_tokens}."
                )
            ), None
        # --- auto-compact check before calling the model ---------------
        async for event, usage in _stream_compaction(trigger="auto"):
            yield event, usage
        compacted_messages, was_compacted = last_compaction_result
        if compacted_messages is not messages:
            messages[:] = compacted_messages
        # ---------------------------------------------------------------

        # --- image preprocessing: convert ImageBlocks to text for non-vision models ---
        async for event in _preprocess_images_in_messages(messages, context):
            yield event, None
        # -----------------------------------------------------------------------------

        final_message: ConversationMessage | None = None
        usage = UsageSnapshot()

        try:
            async for event in context.api_client.stream_message(
                ApiMessageRequest(
                    model=context.model,
                    messages=messages,
                    system_prompt=context.system_prompt,
                    max_tokens=effective_max_tokens,
                    tools=context.tool_registry.to_api_schema(),
                    effort=context.effort,
                )
            ):
                if isinstance(event, ApiTextDeltaEvent):
                    yield AssistantTextDelta(text=event.text), None
                    continue
                if isinstance(event, ApiReasoningDeltaEvent):
                    yield AssistantReasoningDelta(text=event.text), None
                    continue
                if isinstance(event, ApiRetryEvent):
                    yield StatusEvent(
                        message=(
                            f"Request failed; retrying in {event.delay_seconds:.1f}s "
                            f"(attempt {event.attempt + 1} of {event.max_attempts}): {event.message}"
                        )
                    ), None
                    continue

                if isinstance(event, ApiMessageCompleteEvent):
                    final_message = event.message
                    usage = event.usage
        except Exception as exc:
            error_msg = str(exc)
            if _is_completion_token_limit_error(exc):
                supported_limit = _extract_completion_token_limit(exc)
                if supported_limit is not None and effective_max_tokens > supported_limit:
                    previous_max_tokens = effective_max_tokens
                    effective_max_tokens = supported_limit
                    yield StatusEvent(
                        message=(
                            f"Model rejected max_tokens={previous_max_tokens}; "
                            f"retrying with provider limit {effective_max_tokens}."
                        )
                    ), None
                    turn_count = max(0, turn_count - 1)
                    continue
            if not reactive_compact_attempted and _is_prompt_too_long_error(exc):
                reactive_compact_attempted = True
                yield StatusEvent(message=REACTIVE_COMPACT_STATUS_MESSAGE), None
                async for event, usage in _stream_compaction(trigger="reactive", force=True):
                    yield event, usage
                compacted_messages, was_compacted = last_compaction_result
                if compacted_messages is not messages:
                    messages[:] = compacted_messages
                if was_compacted:
                    continue
            if "connect" in error_msg.lower() or "timeout" in error_msg.lower() or "network" in error_msg.lower():
                yield ErrorEvent(message=f"Network error: {error_msg}. Check your internet connection and try again."), None
            else:
                yield ErrorEvent(message=f"API error: {error_msg}"), None
            return

        if final_message is None:
            raise RuntimeError("Model stream finished without a final message")

        coordinator_context_message: ConversationMessage | None = None
        if context.system_prompt.startswith("You are a **coordinator**."):
            if messages and messages[-1].role == "user" and messages[-1].text.startswith("# Coordinator User Context"):
                coordinator_context_message = messages.pop()

        if final_message.role == "assistant" and final_message.is_effectively_empty():
            log.warning("dropping empty assistant message from provider response")
            yield ErrorEvent(
                message=(
                    "Model returned an empty assistant message. "
                    "The turn was ignored to keep the session healthy."
                )
            ), usage
            return

        messages.append(final_message)
        yield AssistantTurnComplete(message=final_message, usage=usage), usage

        if coordinator_context_message is not None:
            messages.append(coordinator_context_message)

        if not final_message.tool_uses:
            if context.hook_executor is not None:
                await context.hook_executor.execute(
                    HookEvent.STOP,
                    {
                        "event": HookEvent.STOP.value,
                        "stop_reason": "tool_uses_empty",
                    },
                )
            return

        tool_calls = final_message.tool_uses

        if len(tool_calls) == 1:
            # Single tool: sequential (stream events immediately)
            tc = tool_calls[0]
            yield ToolExecutionStarted(tool_name=tc.name, tool_input=tc.input), None

            progress_queue: asyncio.Queue[ToolExecutionProgress] = asyncio.Queue()

            async def _emit_progress(text: str) -> None:
                await progress_queue.put(ToolExecutionProgress(tool_name=tc.name, text=text))

            try:
                result_task = asyncio.create_task(
                    _execute_tool_call(context, tc.name, tc.id, tc.input, _emit_progress)
                )
                while True:
                    try:
                        yield await asyncio.wait_for(progress_queue.get(), timeout=0.05), None
                    except asyncio.TimeoutError:
                        if result_task.done():
                            break
                while not progress_queue.empty():
                    yield progress_queue.get_nowait(), None
                result = await result_task
            except Exception as exc:
                log.exception("tool execution raised: name=%s id=%s", tc.name, tc.id)
                result = ToolResultBlock(
                    tool_use_id=tc.id,
                    content=f"Tool {tc.name} failed: {type(exc).__name__}: {exc}",
                    is_error=True,
                )
            yield ToolExecutionCompleted(
                tool_name=tc.name,
                output=result.content,
                is_error=result.is_error,
                metadata=result.result_metadata,
            ), None
            tool_results = [result]
        else:
            # Multiple tools: execute concurrently, stream progress as it arrives
            for tc in tool_calls:
                yield ToolExecutionStarted(tool_name=tc.name, tool_input=tc.input), None

            progress_queue: asyncio.Queue[ToolExecutionProgress] = asyncio.Queue()

            def _make_emit(name: str) -> Callable[[str], Awaitable[None]]:
                async def _emit(text: str) -> None:
                    await progress_queue.put(ToolExecutionProgress(tool_name=name, text=text))

                return _emit

            async def _run(tc):
                return await _execute_tool_call(
                    context, tc.name, tc.id, tc.input, _make_emit(tc.name)
                )

            # Use return_exceptions=True so a single failing tool does not abandon
            # its siblings as cancelled coroutines and leave the conversation with
            # un-replied tool_use blocks (Anthropic's API rejects the next request
            # on the session if any tool_use is missing a matching tool_result).
            gather_future = asyncio.gather(*[_run(tc) for tc in tool_calls], return_exceptions=True)
            while True:
                try:
                    yield await asyncio.wait_for(progress_queue.get(), timeout=0.05), None
                except asyncio.TimeoutError:
                    if gather_future.done():
                        break
            while not progress_queue.empty():
                yield progress_queue.get_nowait(), None
            raw_results = await gather_future
            tool_results = []
            for tc, result in zip(tool_calls, raw_results):
                if isinstance(result, BaseException):
                    log.exception(
                        "tool execution raised: name=%s id=%s",
                        tc.name,
                        tc.id,
                        exc_info=result,
                    )
                    result = ToolResultBlock(
                        tool_use_id=tc.id,
                        content=f"Tool {tc.name} failed: {type(result).__name__}: {result}",
                        is_error=True,
                    )
                tool_results.append(result)

            for tc, result in zip(tool_calls, tool_results):
                yield ToolExecutionCompleted(
                    tool_name=tc.name,
                    output=result.content,
                    is_error=result.is_error,
                    metadata=result.result_metadata,
                ), None

        messages.append(ConversationMessage(role="user", content=tool_results))

    if context.max_turns is not None:
        raise MaxTurnsExceeded(context.max_turns)
    raise RuntimeError("Query loop exited without a max_turns limit or final response")


