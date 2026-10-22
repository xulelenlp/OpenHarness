"""Tool for asking the interactive user a follow-up question.

Besides free-form questions, the tool can offer a small set of concrete
choices. Hosts that support rich dialogs (the qaapp web client) render the
choices as a picker; hosts that do not can ignore ``options`` and fall back to
a plain text prompt.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field, field_validator

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

#: ``options`` is passed to the host as plain JSON-friendly dicts so UI layers
#: do not need to import this module's Pydantic models.
AskUserPrompt = Callable[[str, list[dict[str, Any]]], Awaitable[str]]

MAX_QUESTION_OPTIONS = 6


class AskUserOption(BaseModel):
    """One concrete choice shown to the user."""

    label: str = Field(
        min_length=1,
        max_length=80,
        description="Short, mutually exclusive choice label shown on the button.",
    )
    description: str = Field(
        default="",
        max_length=200,
        description="Optional one-line explanation of when to choose this option.",
    )
    recommended: bool = Field(
        default=False,
        description="Mark the option you would suggest as the default.",
    )


class AskUserQuestionToolInput(BaseModel):
    """Arguments for asking the user a question."""

    question: str = Field(
        min_length=1,
        description="The exact question to ask the user.",
    )
    options: list[AskUserOption] = Field(
        default_factory=list,
        description=(
            "Optional 2-6 concrete choices for an ambiguous decision. "
            "Omit this field for a free-form question."
        ),
    )

    @field_validator("question")
    @classmethod
    def _normalize_question(cls, value: str) -> str:
        text = " ".join(str(value or "").split())
        if not text:
            raise ValueError("question must not be empty")
        return text

    @field_validator("options", mode="before")
    @classmethod
    def _coerce_options(cls, value: Any) -> Any:
        """Tolerate weak models that emit strings or forget ``label``.

        A missing label is recovered from ``description`` (truncated) so a
        transient malformed tool call does not surface as a scary failed step in
        the conversation. The full description is still preserved separately.
        """
        if value is None:
            return []
        if isinstance(value, str):
            return [{"label": value}]
        if isinstance(value, list):
            cleaned: list[Any] = []
            for item in value:
                if isinstance(item, str):
                    cleaned.append({"label": item})
                elif isinstance(item, dict):
                    data = dict(item)
                    if str(data.get("label") or "").strip():
                        data["label"] = " ".join(str(data["label"]).split())[:80]
                    if str(data.get("description") or "").strip():
                        data["description"] = " ".join(
                            str(data["description"]).split()
                        )[:200]
                    if not str(data.get("label") or "").strip() and str(
                        data.get("description") or ""
                    ).strip():
                        data["label"] = str(data["description"])[:60]
                    if str(data.get("label") or "").strip():
                        cleaned.append(data)
                else:
                    cleaned.append(item)
            return cleaned
        return value

    @field_validator("options")
    @classmethod
    def _clean_options(cls, value: list[AskUserOption]) -> list[AskUserOption]:
        cleaned: list[AskUserOption] = []
        seen: set[str] = set()
        for option in value[:MAX_QUESTION_OPTIONS]:
            label = " ".join(str(option.label or "").split())
            if not label:
                continue
            key = label.casefold()
            if key in seen:
                continue
            seen.add(key)
            description = " ".join(str(option.description or "").split())
            cleaned.append(
                option.model_copy(update={"label": label, "description": description})
            )
        if len(cleaned) == 1:
            raise ValueError(
                "options must contain at least two choices; "
                "omit options for a free-form question"
            )
        return cleaned


class AskUserQuestionTool(BaseTool):
    """Ask the interactive user a question and return the answer."""

    name = "ask_user_question"
    description = (
        "Ask the user a clarifying question and wait for their answer. Use this when a "
        "material ambiguity blocks progress and cannot be resolved from the conversation, "
        "attachments, or repository (for example: scope, target, time range, output format, "
        "or which of several valid approaches to take). When you have concrete "
        "alternatives, pass 2-6 `options` with short mutually exclusive labels and optional "
        "descriptions, and mark one `recommended` when a default is clearly better. Omit "
        "`options` only when the answer is inherently free-form. Never ask for information "
        "you can obtain yourself, and never use this tool to avoid doing the work. After the "
        "user answers, continue the task with that answer instead of asking the same point "
        "again."
    )
    input_model = AskUserQuestionToolInput

    def is_read_only(self, arguments: AskUserQuestionToolInput) -> bool:
        del arguments
        return True

    def to_api_schema(self) -> dict[str, Any]:
        """Inline the option object schema.

        Pydantic normally emits a ``$defs``/``$ref`` pair for nested models. Some
        OpenAI-compatible gateways handle that poorly, so the tool schema is
        flattened here while parsing still uses the Pydantic model above.
        """
        schema = super().to_api_schema()
        input_schema = schema.setdefault("input_schema", {})
        properties = input_schema.setdefault("properties", {})
        if isinstance(properties, dict):
            properties["options"] = {
                "type": "array",
                "maxItems": MAX_QUESTION_OPTIONS,
                "description": (
                    "Optional 2-6 concrete choices for an ambiguous decision. "
                    "Omit for a free-form question."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "description": "Short, mutually exclusive choice label.",
                        },
                        "description": {
                            "type": "string",
                            "description": "Optional one-line explanation of the choice.",
                        },
                        "recommended": {
                            "type": "boolean",
                            "description": "Mark the suggested default option.",
                        },
                    },
                    "required": ["label"],
                    "additionalProperties": False,
                },
            }
        input_schema.pop("$defs", None)
        return schema

    async def execute(
        self,
        arguments: AskUserQuestionToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        prompt = context.metadata.get("ask_user_prompt")
        if not callable(prompt):
            return ToolResult(
                output="ask_user_question is unavailable in this session",
                is_error=True,
            )
        options = [option.model_dump() for option in arguments.options]
        answer = await prompt(arguments.question, options)
        text = str(answer or "").strip()
        if not text:
            return ToolResult(output="(no response)")
        return ToolResult(output=text)


__all__ = [
    "AskUserOption",
    "AskUserPrompt",
    "AskUserQuestionTool",
    "AskUserQuestionToolInput",
    "MAX_QUESTION_OPTIONS",
]
