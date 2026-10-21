"""文档解析测试（docx / xlsx / xls / 文本）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from qaapp.workspace.documents import (
    DocumentTooLarge,
    UnsupportedDocument,
    parse_document,
    save_extracted_text,
)

LIMITS = {"max_chars": 100_000, "max_excel_rows_per_sheet": 100}


def _write_txt(path: Path, text: str, encoding: str = "utf-8") -> Path:
    path.write_bytes(text.encode(encoding))
    return path


def test_parse_txt_utf8_and_gbk(tmp_path: Path):
    utf8 = parse_document(_write_txt(tmp_path / "a.txt", "售电量 123\n第二行"), **LIMITS)
    assert utf8.kind == "text"
    assert "售电量" in utf8.text
    assert utf8.meta["encoding"] == "utf-8-sig"

    gbk = parse_document(_write_txt(tmp_path / "b.txt", "中文内容", "gb18030"), **LIMITS)
    assert "中文内容" in gbk.text
    assert gbk.meta["encoding"] in {"gb18030", "utf-8-sig"}


def test_parse_markdown_and_json(tmp_path: Path):
    md = parse_document(_write_txt(tmp_path / "readme.md", "# 标题\n\n正文"), **LIMITS)
    assert md.kind == "markdown"
    assert "# 标题" in md.text

    data = parse_document(_write_txt(tmp_path / "d.json", '{"a": 1}'), **LIMITS)
    assert data.meta["json_root"] == "dict"


def test_parse_csv_to_markdown_table(tmp_path: Path):
    csv_path = _write_txt(tmp_path / "数据.csv", "月份,售电量\n1月,10\n2月,20\n")
    parsed = parse_document(csv_path, **LIMITS)
    assert parsed.kind == "csv"
    assert "| 月份 | 售电量 |" in parsed.text
    assert "| 1月 | 10 |" in parsed.text
    assert parsed.meta["rows"] == 3


def test_parse_csv_truncates_rows(tmp_path: Path):
    rows = "\n".join(f"r{i},{i}" for i in range(10))
    csv_path = _write_txt(tmp_path / "big.csv", f"a,b\n{rows}\n")
    parsed = parse_document(csv_path, max_chars=100_000, max_excel_rows_per_sheet=3)
    assert parsed.truncated
    assert "仅解析前 3 行" in parsed.text


def test_parse_docx_paragraphs_tables_headings(tmp_path: Path):
    docx = pytest.importorskip("docx")

    document = docx.Document()
    document.add_heading("季度报告", level=1)
    document.add_paragraph("售电量同比增长 5%。")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "月份"
    table.cell(0, 1).text = "售电量"
    table.cell(1, 0).text = "1月"
    table.cell(1, 1).text = "10"
    docx_path = tmp_path / "报告.docx"
    document.save(str(docx_path))

    parsed = parse_document(docx_path, **LIMITS)
    assert parsed.kind == "word"
    assert "# 季度报告" in parsed.text
    assert "售电量同比增长 5%。" in parsed.text
    assert "| 月份 | 售电量 |" in parsed.text
    assert parsed.meta["tables"] == 1
    assert parsed.meta["paragraphs"] >= 2


def test_parse_xlsx_multi_sheet_and_truncation(tmp_path: Path):
    openpyxl = pytest.importorskip("openpyxl")

    workbook = openpyxl.Workbook()
    first = workbook.active
    first.title = "售电量"
    first.append(["月份", "售电量"])
    for index in range(1, 6):
        first.append([f"{index}月", index * 10])
    second = workbook.create_sheet("线损")
    second.append(["月份", "线损率"])
    second.append(["1月", 0.032])
    xlsx_path = tmp_path / "指标.xlsx"
    workbook.save(str(xlsx_path))

    parsed = parse_document(xlsx_path, max_chars=100_000, max_excel_rows_per_sheet=3)
    assert parsed.kind == "excel"
    assert "## Sheet: 售电量" in parsed.text
    assert "## Sheet: 线损" in parsed.text
    assert parsed.meta["sheets"] == ["售电量", "线损"]
    assert "仅解析前 3 行" in parsed.text
    assert parsed.truncated


def test_parse_xlsx_column_width_rows(tmp_path: Path):
    openpyxl = pytest.importorskip("openpyxl")

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append([1, 2, 3])
    sheet.append([4.5, None, 6])
    xlsx_path = tmp_path / "nums.xlsx"
    workbook.save(str(xlsx_path))

    parsed = parse_document(xlsx_path, **LIMITS)
    assert "1\t2\t3" in parsed.text
    assert "4.5\t\t6" in parsed.text


def test_parse_xls(tmp_path: Path):
    xlrd = pytest.importorskip("xlrd")
    xlwt = pytest.importorskip("xlwt")

    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet("旧表")
    sheet.write(0, 0, "月份")
    sheet.write(0, 1, "售电量")
    sheet.write(1, 0, "1月")
    sheet.write(1, 1, 12.0)
    xls_path = tmp_path / "旧.xls"
    workbook.save(str(xls_path))

    parsed = parse_document(xls_path, **LIMITS)
    assert parsed.kind == "excel"
    assert "## Sheet: 旧表" in parsed.text
    assert "1月\t12" in parsed.text
    assert xlrd  # 使用到依赖，避免 unused 警告


def test_parse_rejects_legacy_and_unknown(tmp_path: Path):
    doc_path = tmp_path / "旧文档.doc"
    doc_path.write_bytes(b"\xd0\xcf\x11\xe0")
    with pytest.raises(UnsupportedDocument) as excinfo:
        parse_document(doc_path, **LIMITS)
    assert "另存为" in str(excinfo.value)

    with pytest.raises(UnsupportedDocument):
        parse_document(_write_txt(tmp_path / "x.exe", "MZ"), **LIMITS)


def test_parse_rejects_corrupt_and_encrypted(tmp_path: Path):
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"not a zip")
    with pytest.raises(Exception) as excinfo:
        parse_document(broken, **LIMITS)
    assert "压缩格式" in str(excinfo.value) or "无法打开" in str(excinfo.value)

    bad_xlsx = tmp_path / "broken.xlsx"
    bad_xlsx.write_bytes(b"PK\x03\x04 garbage")
    with pytest.raises(Exception) as xlsx_info:
        parse_document(bad_xlsx, **LIMITS)
    assert "无法打开" in str(xlsx_info.value) or "压缩格式" in str(xlsx_info.value)


def test_parse_zip_bomb_guard(tmp_path: Path):
    import zipfile

    bomb = tmp_path / "bomb.xlsx"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("big.xml", b"0" * (5 * 1024 * 1024))
    with pytest.raises((DocumentTooLarge, UnsupportedDocument)):
        parse_document(bomb, **LIMITS)


def test_parse_document_truncates_to_max_chars(tmp_path: Path):
    parsed = parse_document(
        _write_txt(tmp_path / "long.txt", "a" * 5000), max_chars=1000, max_excel_rows_per_sheet=10
    )
    assert parsed.truncated
    assert parsed.text.startswith("a" * 100)
    assert "已截断" in parsed.text


def test_save_extracted_text(tmp_path: Path):
    parsed = parse_document(_write_txt(tmp_path / "a.txt", "内容"), **LIMITS)
    target = save_extracted_text(tmp_path / "extracted" / "a.txt.txt", parsed)
    assert target.read_text(encoding="utf-8") == "内容"
