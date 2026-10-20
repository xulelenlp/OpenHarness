"""执行轨迹存储测试。"""


from qaapp.trace import ConversationTraceStore, get_trace_path


def test_append_and_load(tmp_path):
    store = ConversationTraceStore(tmp_path)
    store.append("c1", {"event": "tool_started", "turn_id": "t1", "tool_name": "file_write"})
    store.append("c1", {"event": "tool_completed", "turn_id": "t1", "tool_name": "file_write"})
    store.append("c1", {"event": "turn_complete", "turn_id": "t1"})
    store.append("c1", {"event": "turn_complete", "turn_id": "t2"})

    events = store.load_trace("c1")
    assert len(events) == 4
    assert all("ts" in e for e in events)
    assert all(e["conversation_id"] == "c1" for e in events)

    assert len(store.load_trace("c1", turn_id="t1")) == 3
    assert store.list_turn_ids("c1") == ["t1", "t2"]


def test_load_missing_returns_empty(tmp_path):
    store = ConversationTraceStore(tmp_path)
    assert store.load_trace("missing") == []


def test_trace_file_isolated_per_conversation(tmp_path):
    store = ConversationTraceStore(tmp_path)
    store.append("c1", {"event": "x"})
    store.append("c2", {"event": "y"})
    assert get_trace_path(tmp_path, "c1").exists()
    assert get_trace_path(tmp_path, "c2").exists()
    assert len(store.load_trace("c1")) == 1
    assert len(store.load_trace("c2")) == 1
