"""qaapp 工作区清理计划与执行测试。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from ohmo.session_storage import get_session_key_latest_path
from qaapp.config import ConversationPoolConfig, ConversationWorkspaceConfig, WebappConfig
from qaapp.conversation_files import ConversationPaths
from qaapp.maintenance import apply_cleanup_plan, build_cleanup_plan, format_cleanup_plan

OLD_ID = "a" * 12
NEW_ID = "b" * 12
ORPHAN_TRACE_ID = "c" * 12


def _write_meta(paths: ConversationPaths, *, last_turn_at: float) -> None:
    paths.ensure()
    paths.write_meta({"last_turn_at": last_turn_at})


def _make_workspace(tmp_path: Path) -> tuple[Path, WebappConfig]:
    ws = tmp_path / "ws"
    webapp = WebappConfig(
        conversation=ConversationPoolConfig(
            max_active_bundles=1,
            idle_timeout_seconds=86400,
        ),
        conversation_workspace=ConversationWorkspaceConfig(),
    )
    ws.mkdir()
    old = ConversationPaths.for_conversation(ws, OLD_ID, webapp.conversation_workspace)
    new = ConversationPaths.for_conversation(ws, NEW_ID, webapp.conversation_workspace)
    _write_meta(old, last_turn_at=time.time() - 30 * 86400)
    _write_meta(new, last_turn_at=time.time())
    (old.outputs / "report.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (new.outputs / "report.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    sessions = ws / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    traces = sessions / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    for cid in (OLD_ID, NEW_ID):
        (sessions / f"session-{cid}.json").write_text(
            json.dumps({"session_id": cid}), encoding="utf-8"
        )
        (traces / f"{cid}.jsonl").write_text('{"type":"x"}\n', encoding="utf-8")
        latest = get_session_key_latest_path(ws, cid)
        latest.write_text(json.dumps({"session_id": cid}), encoding="utf-8")

    # 会话目录已被手工删除，但 trace 还在：属于可清理的孤儿。
    orphan = traces / f"{ORPHAN_TRACE_ID}.jsonl"
    orphan.write_text('{"type":"x"}\n', encoding="utf-8")
    old_time = time.time() - 30 * 86400
    os.utime(orphan, (old_time, old_time))
    return ws, webapp


def test_build_plan_selects_old_conversation_and_related_files(tmp_path: Path):
    ws, webapp = _make_workspace(tmp_path)

    plan = build_cleanup_plan(ws, webapp)
    # 只清理超过 idle 阈值且不在最近 N 个会话里的 OLD_ID。
    assert plan.conversation_ids == [OLD_ID]
    kinds = {(item.kind, item.conversation_id) for item in plan.items}
    assert ("conversation", OLD_ID) in kinds
    assert ("snapshot", OLD_ID) in kinds
    assert ("latest_snapshot", OLD_ID) in kinds
    assert ("trace", OLD_ID) in kinds
    assert ("orphan_trace", ORPHAN_TRACE_ID) in kinds
    # 最近会话不进入计划。
    assert all(item.conversation_id != NEW_ID for item in plan.items)
    # dry-run：计划阶段不删除任何东西。
    assert (ws / "conversations" / OLD_ID).is_dir()

    text = format_cleanup_plan(plan)
    assert "dry-run" in text
    assert OLD_ID in text


def test_apply_plan_removes_only_selected_items(tmp_path: Path):
    ws, webapp = _make_workspace(tmp_path)
    plan = build_cleanup_plan(ws, webapp)
    result = apply_cleanup_plan(plan)

    assert result.failed == []
    assert result.freed_bytes > 0
    assert not (ws / "conversations" / OLD_ID).exists()
    assert (ws / "conversations" / NEW_ID).is_dir()
    assert not (ws / "sessions" / f"session-{OLD_ID}.json").exists()
    assert (ws / "sessions" / f"session-{NEW_ID}.json").exists()
    assert not (ws / "sessions" / "traces" / f"{OLD_ID}.jsonl").exists()
    assert not (ws / "sessions" / "traces" / f"{ORPHAN_TRACE_ID}.jsonl").exists()
    assert (ws / "sessions" / "traces" / f"{NEW_ID}.jsonl").exists()


def test_keep_latest_protects_recent_conversation(tmp_path: Path):
    ws, webapp = _make_workspace(tmp_path)
    # 即使 OLD_ID 非常老，keep_latest=2 也会把它保留在最近两个里。
    plan = build_cleanup_plan(ws, webapp, keep_latest=2)
    assert OLD_ID not in plan.conversation_ids
    # 但孤儿 trace 与 keep_latest 无关，仍可清理；先把阈值设为 0 验证。
    plan_all = build_cleanup_plan(ws, webapp, keep_latest=0, older_than_seconds=0)
    assert set(plan_all.conversation_ids) == {OLD_ID, NEW_ID}


def test_symlink_conversation_is_skipped(tmp_path: Path):
    ws, webapp = _make_workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    link = ws / "conversations" / ("d" * 12)
    link.symlink_to(outside, target_is_directory=True)

    plan = build_cleanup_plan(ws, webapp, keep_latest=0, older_than_seconds=0)
    assert all(item.path.name != link.name for item in plan.items)
    assert any("符号链接" in item for item in plan.skipped)
    apply_cleanup_plan(plan)
    assert (outside / "keep.txt").exists()


def test_refuses_workspace_root_as_conversations_root(tmp_path: Path):
    ws, _webapp = _make_workspace(tmp_path)
    bad = WebappConfig(
        conversation=ConversationPoolConfig(),
        conversation_workspace=ConversationWorkspaceConfig(root=str(ws)),
    )
    with pytest.raises(ValueError, match="工作区根"):
        build_cleanup_plan(ws, bad)
