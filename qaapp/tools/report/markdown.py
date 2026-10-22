"""Small Markdown subset parser used by the Word report renderer."""

from __future__ import annotations

import re
from typing import Any


def parse_markdown(text: str) -> list[dict[str, Any]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[dict[str, Any]] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        paragraph = " ".join(item.strip() for item in buffer if item.strip())
        if paragraph:
            blocks.append({"kind": "paragraph", "text": paragraph})
        buffer.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            flush()
            index += 1
            continue
        if stripped in {"<!-- pagebreak -->", "\\pagebreak", "\\newpage"}:
            flush()
            blocks.append({"kind": "pagebreak"})
            index += 1
            continue
        if stripped.startswith("```"):
            flush()
            index += 1
            code: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            index += 1
            blocks.append({"kind": "code", "text": "\n".join(code)})
            continue
        match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if match:
            flush()
            blocks.append({"kind": "heading", "level": len(match.group(1)), "text": match.group(2).strip()})
            index += 1
            continue
        if re.match(r"^[-*_]{3,}$", stripped):
            flush()
            blocks.append({"kind": "hr"})
            index += 1
            continue
        image = re.match(r"^!\[(.*?)\]\((.+?)\)$", stripped)
        if image:
            flush()
            blocks.append({"kind": "image", "caption": image.group(1), "path": image.group(2)})
            index += 1
            continue
        if stripped.startswith(">"):
            flush()
            blocks.append({"kind": "quote", "text": stripped.lstrip("> ").strip()})
            index += 1
            continue
        if stripped.startswith(("- ", "* ", "+ ")):
            flush()
            blocks.append({"kind": "bullet", "text": stripped[2:].strip()})
            index += 1
            continue
        if re.match(r"^\d+\.\s+", stripped):
            flush()
            blocks.append({"kind": "bullet", "text": re.sub(r"^\d+\.\s+", "", stripped)})
            index += 1
            continue
        if "|" in stripped and index + 1 < len(lines) and re.match(r"^\|?[\s:|-]+\|?$", lines[index + 1].strip()):
            flush()
            columns = _split_table_row(stripped)
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(_split_table_row(lines[index].strip()))
                index += 1
            blocks.append({"kind": "table", "columns": columns, "rows": rows})
            continue
        buffer.append(stripped)
        index += 1
    flush()
    return blocks


def _split_table_row(line: str) -> list[str]:
    text = line.strip()
    text = text.removeprefix("|")
    text = text.removesuffix("|")
    # 支持转义竖线
    parts = re.split(r"(?<!\\)\|", text)
    return [part.replace("\\|", "|").strip() for part in parts]
