"""``qaapp.turn_files``：轮次文件快照、diff 与结果文件筛选。"""

from __future__ import annotations

from pathlib import Path

from qaapp.turn_files import (
    ChangedFile,
    build_payload,
    classify_kind,
    compute_turn_files,
    compute_turn_files_event,
    select_result_files,
    snapshot_workspace,
)


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "abc123abc123"
    for name in ("scripts", "tmp", "outputs", "uploads", "extracted"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_created_text_file_reports_added_lines(tmp_path: Path):
    root = _root(tmp_path)
    before = snapshot_workspace(root)
    _write(root, "scripts/analyze.py", "import pandas\n\nprint('ok')\n")

    changed, truncated = compute_turn_files(root, before)

    assert truncated is False
    assert len(changed) == 1
    item = changed[0]
    assert item.path == "scripts/analyze.py"
    assert item.dir == "scripts"
    assert item.action == "created"
    assert item.added == 3
    assert item.removed == 0
    assert item.kind == "script"


def test_modified_file_reports_diff(tmp_path: Path):
    root = _root(tmp_path)
    _write(root, "outputs/notes.md", "a\nb\n")
    before = snapshot_workspace(root)
    _write(root, "outputs/notes.md", "a\nc\nb\nd\n")

    changed, _ = compute_turn_files(root, before)

    assert len(changed) == 1
    item = changed[0]
    assert item.action == "modified"
    assert item.added == 2
    assert item.removed == 0
    assert item.kind == "markdown"


def test_unchanged_and_deleted_files_are_ignored(tmp_path: Path):
    root = _root(tmp_path)
    _write(root, "scripts/keep.py", "print(1)\n")
    _write(root, "tmp/remove.txt", "gone\n")
    before = snapshot_workspace(root)
    (root / "tmp/remove.txt").unlink()

    changed, _ = compute_turn_files(root, before)

    assert changed == []


def test_binary_file_has_no_line_counts(tmp_path: Path):
    root = _root(tmp_path)
    before = snapshot_workspace(root)
    (root / "outputs" / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00data")

    changed, _ = compute_turn_files(root, before)

    assert len(changed) == 1
    item = changed[0]
    assert item.kind == "image"
    assert item.added is None
    assert item.removed is None


def test_uploads_extracted_and_index_files_are_excluded(tmp_path: Path):
    root = _root(tmp_path)
    _write(root, "uploads/user.csv", "a,b\n")
    _write(root, "extracted/user.csv.txt", "a\nb\n")
    _write(root, "meta.json", "{}")
    _write(root, "attachments.json", "{}")
    _write(root, "scripts/__pycache__/x.pyc", "binary")
    before = snapshot_workspace(root)

    _write(root, "uploads/user.csv", "a,b\n1,2\n")
    _write(root, "extracted/user.csv.txt", "a\nb\nc\n")
    _write(root, "meta.json", '{"last_turn_at": 1}')
    _write(root, "scripts/__pycache__/x.pyc", "binary2")
    _write(root, "outputs/final.docx", "fake-docx")
    (root / "outputs" / "keep.txt").write_text("keep\n", encoding="utf-8")

    changed, _ = compute_turn_files(root, before)

    paths = {item.path for item in changed}
    assert paths == {"outputs/final.docx", "outputs/keep.txt"}


def test_result_files_include_word_and_markdown(tmp_path: Path):
    root = _root(tmp_path)
    before = snapshot_workspace(root)
    (root / "outputs" / "chart1.png").write_bytes(b"\x89PNG\r\n\x1a\x00")
    (root / "outputs" / "report.docx").write_bytes(b"PK\x03\x04docx")
    (root / "outputs" / "report.md").write_text("# 报告\n", encoding="utf-8")

    changed, _ = compute_turn_files(root, before)
    results = select_result_files(changed, conversation_id="abc123abc123")

    # docx 与 md 都是成果性文档；图片不进结果区。
    assert [item.path for item in results] == [
        "outputs/report.docx",
        "outputs/report.md",
    ]
    assert results[0].download_path == (
        "/api/v1/conversations/abc123abc123/files/outputs/report.docx"
    )
    assert results[0].title == "report"
    assert results[0].kind == "word"
    assert results[1].kind == "markdown"


def test_markdown_is_a_result(tmp_path: Path):
    root = _root(tmp_path)
    before = snapshot_workspace(root)
    (root / "outputs" / "plan.md").write_text("# 方案\n", encoding="utf-8")

    changed, _ = compute_turn_files(root, before)
    results = select_result_files(changed)

    assert [item.path for item in results] == ["outputs/plan.md"]
    assert results[0].kind == "markdown"


def test_csv_scripts_and_images_are_not_results(tmp_path: Path):
    root = _root(tmp_path)
    before = snapshot_workspace(root)
    (root / "outputs" / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\x00")
    (root / "outputs" / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (root / "outputs" / "notes.txt").write_text("过程说明\n", encoding="utf-8")
    (root / "scripts" / "analyze.py").write_text("print(1)\n", encoding="utf-8")

    changed, _ = compute_turn_files(root, before)
    results = select_result_files(changed)

    # csv / txt / py / 图片都是过程文件，不能出现在结果区。
    assert results == []


def test_result_files_are_capped_at_eight(tmp_path: Path):
    root = _root(tmp_path)
    before = snapshot_workspace(root)
    for index in range(10):
        (root / "outputs" / f"report-{index}.docx").write_text("x", encoding="utf-8")

    changed, _ = compute_turn_files(root, before)
    results = select_result_files(changed)

    assert len(results) == 8


def test_payload_marks_truncation_and_caps_changed():
    changed = [
        ChangedFile(
            path=f"scripts/s{index}.py",
            dir="scripts",
            action="created",
            size=1,
            mtime=float(index),
            added=1,
            removed=0,
            kind="script",
        )
        for index in range(120)
    ]

    payload = build_payload("abc123abc123", changed, [], truncated=False)

    assert payload["truncated"] is True
    assert len(payload["changed"]) == 100


def test_no_changes_yields_no_event(tmp_path: Path):
    root = _root(tmp_path)
    before = snapshot_workspace(root)

    assert compute_turn_files_event(root, before, "abc123abc123") is None


def test_classify_kind_covers_common_formats():
    assert classify_kind("outputs/report.docx") == "word"
    assert classify_kind("outputs/book.XLSX") == "excel"
    assert classify_kind("outputs/deck.pptx") == "pptx"
    assert classify_kind("outputs/report.pdf") == "pdf"
    assert classify_kind("assets/logo.svg") == "image"
    assert classify_kind("scripts/run.js") == "script"
    assert classify_kind("tmp/blob.bin") == "other"
