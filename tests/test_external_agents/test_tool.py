"""Tests for the external-agents plugin tool (streaming + parsing)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import external_agent_tool as tool


def test_line_stream_delta_extracts_sse_content():
    response = tool.AgentResponseConfig(json_text_paths=["choices[0].delta.content"])
    assert (
        tool._line_stream_delta('data: {"choices":[{"delta":{"content":"你好，"}}]}', response)
        == "你好，"
    )
    assert tool._line_stream_delta("data: [DONE]", response) == ""
    assert tool._line_stream_delta("", response) == ""


def test_line_stream_delta_handles_plain_and_ndjson_lines():
    response = tool.AgentResponseConfig(json_text_paths=["content"])
    assert tool._line_stream_delta("plain text line", response) == "plain text line\n"
    assert tool._line_stream_delta('{"content": "ndjson line"}', response) == "ndjson line\n"


def test_parse_stream_still_joins_sse_deltas():
    response = tool.AgentResponseConfig(json_text_paths=["choices[0].delta.content"])
    raw = '\n'.join(
        [
            'data: {"choices":[{"delta":{"content":"你好，"}}]}',
            'data: {"choices":[{"delta":{"content":"这是报告"}}]}',
            "data: [DONE]",
        ]
    )
    assert tool.parse_stream(raw, response) == "你好，这是报告"


class _FakeResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStream:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self):
        return _FakeResponse(self._lines)

    async def __aexit__(self, *exc):
        return False


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def stream(self, *args, **kwargs):
        return _FakeStream(
            [
                'data: {"choices":[{"delta":{"content":"你好，"}}]}',
                'data: {"choices":[{"delta":{"content":"这是报告"}}]}',
                "data: [DONE]",
            ]
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_http_transport_streams_progress(monkeypatch):
    monkeypatch.setattr(tool.httpx, "AsyncClient", _FakeAsyncClient)

    agent = tool.AgentConfig(id="power-grid-analysis", type="http")
    deltas: list[str] = []

    async def _collect(delta: str) -> None:
        deltas.append(delta)

    transport = tool.HttpAgentTransport()
    raw = await transport.invoke(
        agent,
        {"endpoint": "http://example.invalid/stream", "request_body": {"message": "{message}"}},
        "hello",
        "conv-1",
        Path("."),
        stream_text=_collect,
    )

    assert deltas == ["你好，", "这是报告"]
    # The raw text is accumulated unchanged so parse_stream still works.
    assert "data: [DONE]" in raw
    assert tool.parse_stream(raw, agent.response) == "你好，这是报告"


# ---------------------------------------------------------------------------
# 最终结果提取（问数类智能体的巨量中间日志）
# ---------------------------------------------------------------------------

#: 真实故障形状：问数智能体把推理过程 + 工具调用 + 工具返回全部塞进
#: ``<record_content>``，只有末尾的 ``<answer>`` 是给用户的最终结果。
_REASONING_LOG = "用户需要查询近5年福建省的售电量……" + "工具返回了2022年至2026年的数据。" * 50
_RECORD_CONTENT = (
    "<record_id>7717</record_id>\n\n<record_content> "
    f"{_REASONING_LOG}\n"
    "<callTool><toolName>query_index_data</toolName>"
    "<toolResult>[{\"val\": \"256.0803\"}]</toolResult></callTool>\n"
    "<answer>近5年福建省售电量：2022-2026 年均为 256.0803 亿千瓦时。</answer>"
    "<extractData>[{\"xData\": [\"2022\"]}]</extractData></record_content>"
)


def test_extract_tag_blocks_takes_last_occurrence():
    text = "<answer>旧</answer>中间<answer>最终答案</answer>"
    assert tool.extract_tag_blocks(text, ["answer"]) == "最终答案"


def test_extract_tag_blocks_handles_unclosed_tag():
    """流被中途截断时，未闭合的标签也要能取到内容。"""
    assert tool.extract_tag_blocks("<answer>只有一半", ["answer"]) == "只有一半"
    assert tool.extract_tag_blocks("没有标签", ["answer"]) is None


def test_finalize_report_keeps_only_answer_and_can_be_disabled():
    response = tool.AgentResponseConfig()
    report = tool.finalize_report(_RECORD_CONTENT, response)
    assert report == "近5年福建省售电量：2022-2026 年均为 256.0803 亿千瓦时。"
    # 没有命中标签时原样返回，避免误伤普通回复。
    assert tool.finalize_report("普通 markdown 报告", response) == "普通 markdown 报告"
    # 显式关闭提取。
    disabled = tool.AgentResponseConfig(extract_tags=[])
    assert tool.finalize_report(_RECORD_CONTENT, disabled) == _RECORD_CONTENT.strip()


def test_finalize_report_supports_multiple_tags():
    response = tool.AgentResponseConfig(extract_tags=["answer", "extractData"])
    report = tool.finalize_report(_RECORD_CONTENT, response)
    assert report.startswith("近5年福建省售电量")
    assert '[{"xData": ["2022"]}]' in report


class _CumulativeStreamClient:
    """每帧回吐「到目前为止的全部文本」的 SSE 客户端（真实平台行为）。"""

    def __init__(self, frames: list[str]) -> None:
        self._frames = frames

    def stream(self, *args, **kwargs):
        frames = self._frames

        class _Resp:
            def raise_for_status(self) -> None:
                return None

            async def aiter_lines(self):
                for frame in frames:
                    yield "data: " + json.dumps({"record_content": frame}, ensure_ascii=False)
                yield "data: [DONE]"

        class _Stream:
            async def __aenter__(self):
                return _Resp()

            async def __aexit__(self, *exc):
                return False

        return _Stream()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _cumulative_frames() -> list[str]:
    """三段累计快照：拼接长度远大于最终内容（模拟平方级膨胀）。"""
    return [_RECORD_CONTENT[: len(_RECORD_CONTENT) // 2], _RECORD_CONTENT, _RECORD_CONTENT]


@pytest.mark.asyncio
async def test_execute_returns_only_final_answer(monkeypatch, tmp_path):
    monkeypatch.setattr(
        tool.httpx, "AsyncClient", lambda *a, **k: _CumulativeStreamClient(_cumulative_frames())
    )
    config_path = tmp_path / "external-agents.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "id": "smart-data-query",
                        "type": "http",
                        "response": {"mode": "auto", "json_text_paths": ["record_content"]},
                        "config": {"endpoint": "http://example.invalid/query"},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    deltas: list[str] = []

    async def _collect(delta: str) -> None:
        deltas.append(delta)

    tool_instance = tool.ExternalAgentTool(config_path=config_path)
    result = await tool_instance.execute(
        tool.ExternalAgentInput(agent="smart-data-query", message="近5年福建售电量 按年"),
        tool.ToolExecutionContext(cwd=tmp_path, stream_text=_collect),
    )

    assert not result.is_error
    # 报告只剩最终答案：不再包含推理过程 / 工具调用回显。
    assert "近5年福建省售电量：2022-2026 年均为 256.0803 亿千瓦时。" in result.output
    assert "工具返回了2022年至2026年的数据" not in result.output
    assert "query_index_data" not in result.output
    assert len(result.output) < 200
    # 进度也不会把巨量日志推给前端：累计快照只转发一次，且总量封顶。
    progress = "".join(deltas)
    assert len(progress) <= tool.DEFAULT_PROGRESS_MAX_CHARS + len(tool.ProgressRelay.OMITTED_NOTICE)
    assert progress.count("<record_content>") == 1


# ---------------------------------------------------------------------------
# 流式进度限流
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_progress_relay_dedupes_cumulative_snapshots():
    sent: list[str] = []

    async def _collect(text: str) -> None:
        sent.append(text)

    relay = tool.ProgressRelay(_collect, max_chars=1000)
    for frame in ["第一段。", "第一段。第二段。", "第一段。第二段。第三段。"]:
        await relay.feed(frame)

    assert "".join(sent) == "第一段。第二段。第三段。"


@pytest.mark.asyncio
async def test_progress_relay_skips_identical_frame():
    """平台重复回吐同一帧（重试/心跳）时不重复转发。"""
    sent: list[str] = []

    async def _collect(text: str) -> None:
        sent.append(text)

    relay = tool.ProgressRelay(_collect, max_chars=1000)
    for _ in range(3):
        await relay.feed("累计快照")

    assert sent == ["累计快照"]


@pytest.mark.asyncio
async def test_progress_relay_caps_total_and_notices_once():
    sent: list[str] = []

    async def _collect(text: str) -> None:
        sent.append(text)

    relay = tool.ProgressRelay(_collect, max_chars=10)
    for index in range(5):
        await relay.feed(f"片段{index}")  # 每次都是新内容（非累计）

    emitted = "".join(sent)
    assert emitted.startswith("片段0片段1片")
    assert emitted.endswith(tool.ProgressRelay.OMITTED_NOTICE)
    assert emitted.count(tool.ProgressRelay.OMITTED_NOTICE) == 1


@pytest.mark.asyncio
async def test_progress_relay_zero_means_unlimited():
    sent: list[str] = []

    async def _collect(text: str) -> None:
        sent.append(text)

    relay = tool.ProgressRelay(_collect, max_chars=0)
    for index in range(50):
        await relay.feed(f"{index:03d}" + "x" * 97)  # 每帧内容都不同

    assert len("".join(sent)) == 5000

