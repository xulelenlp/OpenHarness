"""Feishu message content extraction helpers."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from openharness.channels.policies import normalize_feishu_group_policy
from openharness.config.schema import FeishuConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _FeishuSenderInfo:
    open_id: str
    display_name: str


def _clean_mention_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_mention_name(value: str) -> str:
    return value.strip().lstrip("@").strip().lower()


def _configured_bot_names(config: FeishuConfig) -> set[str]:
    raw_names = getattr(config, "bot_names", []) or []
    if isinstance(raw_names, str):
        items = [item.strip() for item in raw_names.split(",")]
    else:
        items = [str(item).strip() for item in raw_names]
    names = {_normalize_mention_name(item) for item in items if item.strip()}
    return names or {"ohmo"}


def _mention_value(raw: Any, key: str) -> Any:
    if isinstance(raw, dict):
        return raw.get(key)
    return getattr(raw, key, None)


def _mention_id_value(raw_id: Any, key: str) -> Any:
    if isinstance(raw_id, dict):
        return raw_id.get(key)
    return getattr(raw_id, key, None)


def _extract_feishu_mentions(
    content_json: dict,
    raw_mentions: list[Any] | tuple[Any, ...] | None = None,
) -> list[dict[str, str]]:
    """Return normalized mention metadata from Feishu text/post payloads."""
    mentions: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    def add(raw: Any) -> None:
        mention_id = _mention_value(raw, "id") or {}
        record = {
            "key": _clean_mention_value(_mention_value(raw, "key")),
            "name": _clean_mention_value(_mention_value(raw, "name") or _mention_value(raw, "user_name")),
            "open_id": _clean_mention_value(
                _mention_value(raw, "open_id") or _mention_id_value(mention_id, "open_id")
            ),
            "user_id": _clean_mention_value(
                _mention_value(raw, "user_id") or _mention_id_value(mention_id, "user_id")
            ),
            "union_id": _clean_mention_value(
                _mention_value(raw, "union_id") or _mention_id_value(mention_id, "union_id")
            ),
        }
        key = (record["key"], record["name"], record["open_id"], record["user_id"], record["union_id"])
        if any(record.values()) and key not in seen:
            seen.add(key)
            mentions.append(record)

    if raw_mentions:
        for item in raw_mentions:
            add(item)

    raw_mentions = content_json.get("mentions")
    if isinstance(raw_mentions, list):
        for item in raw_mentions:
            if isinstance(item, dict):
                add(item)

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("tag") == "at":
                add(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(content_json)
    return mentions


def _feishu_mentions_bot(
    content_json: dict,
    text: str,
    config: FeishuConfig,
    *,
    mentions: list[dict[str, str]] | None = None,
) -> bool:
    """Best-effort bot mention detection for Feishu group messages."""
    bot_open_id = str(getattr(config, "bot_open_id", "") or "").strip()
    bot_names = _configured_bot_names(config)
    for mention in mentions if mentions is not None else _extract_feishu_mentions(content_json):
        ids = {mention.get("open_id", ""), mention.get("user_id", ""), mention.get("union_id", "")}
        if bot_open_id and bot_open_id in ids:
            return True
        name = _normalize_mention_name(mention.get("name", ""))
        if name and any(name == candidate or candidate in name for candidate in bot_names):
            return True

    normalized_text = text.strip().lower()
    for name in bot_names:
        if re.search(rf"(^|\s)@{re.escape(name)}(?=$|\s|[:：,，])", normalized_text):
            return True
    return False


def _is_ohmo_managed_feishu_group(chat_id: str) -> bool:
    workspace = os.environ.get("OHMO_WORKSPACE")
    if not workspace:
        return False
    try:
        from ohmo.group_registry import load_managed_group_record

        return load_managed_group_record(
            workspace=workspace,
            channel="feishu",
            chat_id=chat_id,
        ) is not None
    except Exception:
        logger.exception("Failed to load ohmo managed Feishu group metadata chat_id=%s", chat_id)
        return False


def _should_process_feishu_group_message(
    *,
    chat_type: str,
    chat_id: str,
    mentions_bot: bool,
    config: FeishuConfig,
) -> bool:
    if str(chat_type or "").strip().lower() != "group":
        return True
    policy = normalize_feishu_group_policy(getattr(config, "group_policy", "managed_or_mention"))
    if policy == "open":
        return True
    if policy == "mention":
        return mentions_bot
    if policy == "managed":
        return _is_ohmo_managed_feishu_group(chat_id)
    if policy == "managed_or_mention":
        return mentions_bot or _is_ohmo_managed_feishu_group(chat_id)
    return mentions_bot


class FeishuApiError(RuntimeError):
    """Raised when Feishu returns an unsuccessful API response."""


def _extract_share_card_content(content_json: dict, msg_type: str) -> str:
    """Extract text representation from share cards and interactive messages."""
    parts = []

    if msg_type == "share_chat":
        parts.append(f"[shared chat: {content_json.get('chat_id', '')}]")
    elif msg_type == "share_user":
        parts.append(f"[shared user: {content_json.get('user_id', '')}]")
    elif msg_type == "interactive":
        parts.extend(_extract_interactive_content(content_json))
    elif msg_type == "share_calendar_event":
        parts.append(f"[shared calendar event: {content_json.get('event_key', '')}]")
    elif msg_type == "system":
        parts.append("[system message]")
    elif msg_type == "merge_forward":
        parts.append("[merged forward messages]")

    return "\n".join(parts) if parts else f"[{msg_type}]"


def _extract_interactive_content(content: dict) -> list[str]:
    """Recursively extract text and links from interactive card content."""
    parts = []

    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return [content] if content.strip() else []

    if not isinstance(content, dict):
        return parts

    if "title" in content:
        title = content["title"]
        if isinstance(title, dict):
            title_content = title.get("content", "") or title.get("text", "")
            if title_content:
                parts.append(f"title: {title_content}")
        elif isinstance(title, str):
            parts.append(f"title: {title}")

    for elements in content.get("elements", []) if isinstance(content.get("elements"), list) else []:
        for element in elements:
            parts.extend(_extract_element_content(element))

    card = content.get("card", {})
    if card:
        parts.extend(_extract_interactive_content(card))

    header = content.get("header", {})
    if header:
        header_title = header.get("title", {})
        if isinstance(header_title, dict):
            header_text = header_title.get("content", "") or header_title.get("text", "")
            if header_text:
                parts.append(f"title: {header_text}")

    return parts


def _extract_element_content(element: dict) -> list[str]:
    """Extract content from a single card element."""
    parts = []

    if not isinstance(element, dict):
        return parts

    tag = element.get("tag", "")

    if tag in ("markdown", "lark_md"):
        content = element.get("content", "")
        if content:
            parts.append(content)

    elif tag == "div":
        text = element.get("text", {})
        if isinstance(text, dict):
            text_content = text.get("content", "") or text.get("text", "")
            if text_content:
                parts.append(text_content)
        elif isinstance(text, str):
            parts.append(text)
        for field in element.get("fields", []):
            if isinstance(field, dict):
                field_text = field.get("text", {})
                if isinstance(field_text, dict):
                    c = field_text.get("content", "")
                    if c:
                        parts.append(c)

    elif tag == "a":
        href = element.get("href", "")
        text = element.get("text", "")
        if href:
            parts.append(f"link: {href}")
        if text:
            parts.append(text)

    elif tag == "button":
        text = element.get("text", {})
        if isinstance(text, dict):
            c = text.get("content", "")
            if c:
                parts.append(c)
        url = element.get("url", "") or element.get("multi_url", {}).get("url", "")
        if url:
            parts.append(f"link: {url}")

    elif tag == "img":
        alt = element.get("alt", {})
        parts.append(alt.get("content", "[image]") if isinstance(alt, dict) else "[image]")

    elif tag == "note":
        for ne in element.get("elements", []):
            parts.extend(_extract_element_content(ne))

    elif tag == "column_set":
        for col in element.get("columns", []):
            for ce in col.get("elements", []):
                parts.extend(_extract_element_content(ce))

    elif tag == "plain_text":
        content = element.get("content", "")
        if content:
            parts.append(content)

    else:
        for ne in element.get("elements", []):
            parts.extend(_extract_element_content(ne))

    return parts


def _extract_post_content(content_json: dict) -> tuple[str, list[str]]:
    """Extract text and image keys from Feishu post (rich text) message.

    Handles three payload shapes:
    - Direct:    {"title": "...", "content": [[...]]}
    - Localized: {"zh_cn": {"title": "...", "content": [...]}}
    - Wrapped:   {"post": {"zh_cn": {"title": "...", "content": [...]}}}
    """

    def _parse_block(block: dict) -> tuple[str | None, list[str]]:
        if not isinstance(block, dict) or not isinstance(block.get("content"), list):
            return None, []
        texts, images = [], []
        if title := block.get("title"):
            texts.append(title)
        for row in block["content"]:
            if not isinstance(row, list):
                continue
            for el in row:
                if not isinstance(el, dict):
                    continue
                tag = el.get("tag")
                if tag in ("text", "a"):
                    texts.append(el.get("text", ""))
                elif tag == "at":
                    texts.append(f"@{el.get('user_name', 'user')}")
                elif tag == "img" and (key := el.get("image_key")):
                    images.append(key)
        return (" ".join(texts).strip() or None), images

    # Unwrap optional {"post": ...} envelope
    root = content_json
    if isinstance(root, dict) and isinstance(root.get("post"), dict):
        root = root["post"]
    if not isinstance(root, dict):
        return "", []

    # Direct format
    if "content" in root:
        text, imgs = _parse_block(root)
        if text or imgs:
            return text or "", imgs

    # Localized: prefer known locales, then fall back to any dict child
    for key in ("zh_cn", "en_us", "ja_jp"):
        if key in root:
            text, imgs = _parse_block(root[key])
            if text or imgs:
                return text or "", imgs
    for val in root.values():
        if isinstance(val, dict):
            text, imgs = _parse_block(val)
            if text or imgs:
                return text or "", imgs

    return "", []


def _extract_post_text(content_json: dict) -> str:
    """Extract plain text from Feishu post (rich text) message content.

    Legacy wrapper for _extract_post_content, returns only text.
    """
    text, _ = _extract_post_content(content_json)
    return text
