"""Feishu/Lark channel implementation using lark-oapi SDK with WebSocket long connection."""

import asyncio
import importlib.util
import json
import logging
import os
import threading
from collections import OrderedDict
from typing import Any

from openharness.channels.bus.events import OutboundMessage
from openharness.channels.bus.queue import MessageBus
from openharness.channels.impl.base import BaseChannel
from openharness.channels.impl.feishu_cards import FeishuCardsMixin
from openharness.channels.impl.feishu_content import (
    FeishuApiError,
    _extract_feishu_mentions,
    _extract_post_content,
    _extract_share_card_content,
    _feishu_mentions_bot,
    _FeishuSenderInfo,
    _should_process_feishu_group_message,
)
from openharness.channels.impl.feishu_media import FeishuMediaMixin
from openharness.config.schema import FeishuConfig

logger = logging.getLogger(__name__)

FEISHU_AVAILABLE = importlib.util.find_spec("lark_oapi") is not None

MSG_TYPE_MAP = {
    "image": "[image]",
    "audio": "[audio]",
    "file": "[file]",
    "sticker": "[sticker]",
}



class FeishuChannel(FeishuCardsMixin, FeishuMediaMixin, BaseChannel):
    """
    Feishu/Lark channel using WebSocket long connection.

    Uses WebSocket to receive events - no public IP or webhook required.

    Requires:
    - App ID and App Secret from Feishu Open Platform
    - Bot capability enabled
    - Event subscription enabled (im.message.receive_v1)
    """

    name = "feishu"

    def __init__(self, config: FeishuConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: FeishuConfig = config
        self._client: Any = None
        self._ws_client: Any = None
        self._ws_thread: threading.Thread | None = None
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()  # Ordered dedup cache
        self._sender_cache: OrderedDict[str, _FeishuSenderInfo] = OrderedDict()
        self._loop: asyncio.AbstractEventLoop | None = None

    def _ensure_rest_client(self) -> bool:
        """Initialize the Feishu REST client without starting the WebSocket receiver."""
        if self._client is not None:
            return True
        if not FEISHU_AVAILABLE:
            logger.error("Feishu SDK not installed. Run: pip install lark-oapi")
            return False
        if not self.config.app_id or not self.config.app_secret:
            logger.error("Feishu app_id and app_secret not configured")
            return False
        import lark_oapi as lark

        self._client = lark.Client.builder() \
            .app_id(self.config.app_id) \
            .app_secret(self.config.app_secret) \
            .domain(self.config.domain) \
            .log_level(lark.LogLevel.INFO) \
            .build()
        return True

    async def start(self) -> None:
        """Start the Feishu bot with WebSocket long connection."""
        if not FEISHU_AVAILABLE:
            logger.error("Feishu SDK not installed. Run: pip install lark-oapi")
            return

        if not self.config.app_id or not self.config.app_secret:
            logger.error("Feishu app_id and app_secret not configured")
            return

        import lark_oapi as lark
        self._running = True
        self._loop = asyncio.get_running_loop()

        if not self._ensure_rest_client():
            return

        # Create event handler (only register message receive, ignore other events)
        event_handler = lark.EventDispatcherHandler.builder(
            self.config.encrypt_key or "",
            self.config.verification_token or "",
        ).register_p2_im_message_receive_v1(
            self._on_message_sync
        ).build()

        # Create WebSocket client for long connection
        self._ws_client = lark.ws.Client(
            self.config.app_id,
            self.config.app_secret,
            event_handler=event_handler,
            domain=self.config.domain,
            log_level=lark.LogLevel.INFO
        )

        # Start WebSocket client in a separate thread with reconnect loop.
        # A dedicated event loop is created for this thread so that lark_oapi's
        # module-level `loop = asyncio.get_event_loop()` picks up an idle loop
        # instead of the already-running main asyncio loop, which would cause
        # "This event loop is already running" errors.
        def run_ws():
            import time

            import lark_oapi.ws.client as _lark_ws_client
            ws_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(ws_loop)
            # Patch the module-level loop used by lark's ws Client.start()
            _lark_ws_client.loop = ws_loop
            try:
                while self._running:
                    try:
                        self._ws_client.start()
                    except Exception as e:
                        logger.warning("Feishu WebSocket error: %s", e)
                    if self._running:
                        time.sleep(5)
            finally:
                ws_loop.close()

        self._ws_thread = threading.Thread(target=run_ws, daemon=True)
        self._ws_thread.start()

        logger.info("Feishu bot started with WebSocket long connection")
        logger.info("No public IP required - using WebSocket to receive events")

        # Keep running until stopped
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """
        Stop the Feishu bot.

        Notice: lark.ws.Client does not expose stop method， simply exiting the program will close the client.

        Reference: https://github.com/larksuite/oapi-sdk-python/blob/v2_main/lark_oapi/ws/client.py#L86
        """
        self._running = False
        logger.info("Feishu bot stopped")

    def _add_reaction_sync(self, message_id: str, emoji_type: str) -> None:
        """Sync helper for adding reaction (runs in thread pool)."""
        from lark_oapi.api.im.v1 import (
            CreateMessageReactionRequest,
            CreateMessageReactionRequestBody,
            Emoji,
        )
        try:
            request = CreateMessageReactionRequest.builder() \
                .message_id(message_id) \
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                    .build()
                ).build()

            response = self._client.im.v1.message_reaction.create(request)

            if not response.success():
                logger.warning("Failed to add reaction: code=%s, msg=%s", response.code, response.msg)
            else:
                logger.debug("Added %s reaction to message %s", emoji_type, message_id)
        except Exception as e:
            logger.warning("Error adding reaction: %s", e)

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        """
        Add a reaction emoji to a message (non-blocking).

        Common emoji types: THUMBSUP, OK, EYES, DONE, OnIt, HEART
        """
        if not self._client:
            return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._add_reaction_sync, message_id, emoji_type)


    def _send_message_sync(self, receive_id_type: str, receive_id: str, msg_type: str, content: str, reply_in_thread: str | None = None) -> bool:
        """Send a single message (text/image/file/interactive) synchronously.

        When *reply_in_thread* (a message_id) is provided, the message is
        posted as a reply inside the originating thread via
        ``ReplyMessageRequest`` instead of a standalone message.
        """
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
        try:
            if reply_in_thread:
                from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody
                request = ReplyMessageRequest.builder() \
                    .message_id(reply_in_thread) \
                    .request_body(
                        ReplyMessageRequestBody.builder()
                        .msg_type(msg_type)
                        .content(content)
                        .reply_in_thread(True)
                        .build()
                    ).build()
                response = self._client.im.v1.message.reply(request)
            else:
                request = CreateMessageRequest.builder() \
                    .receive_id_type(receive_id_type) \
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(receive_id)
                        .msg_type(msg_type)
                        .content(content)
                        .build()
                    ).build()
                response = self._client.im.v1.message.create(request)
            if not response.success():
                logger.error(
                    "Failed to send Feishu %s message: code=%s, msg=%s, log_id=%s",
                    msg_type, response.code, response.msg, response.get_log_id()
                )
                return False
            logger.debug("Feishu %s message sent to %s", msg_type, receive_id)
            return True
        except Exception as e:
            logger.error("Error sending Feishu %s message: %s", msg_type, e)
            return False

    @staticmethod
    def _format_response_error(action: str, response: Any) -> str:
        log_id = response.get_log_id() if hasattr(response, "get_log_id") else ""
        return (
            f"{action} failed: code={getattr(response, 'code', '')}, "
            f"msg={getattr(response, 'msg', '')}, log_id={log_id}"
        )

    def _create_managed_group_sync(self, user_open_id: str, name: str) -> str:
        """Create a Feishu group containing the user and the app bot."""
        from lark_oapi.api.im.v1 import CreateChatRequest, CreateChatRequestBody

        if not self._client:
            raise RuntimeError("Feishu client not initialized")
        request = (
            CreateChatRequest.builder()
            .user_id_type("open_id")
            .set_bot_manager(True)
            .request_body(
                CreateChatRequestBody.builder()
                .name(name)
                .user_id_list([user_open_id])
                .chat_mode("group")
                .chat_type("private")
                .build()
            )
            .build()
        )
        response = self._client.im.v1.chat.create(request)
        if not response.success():
            raise FeishuApiError(self._format_response_error("create Feishu group", response))
        chat_id = getattr(getattr(response, "data", None), "chat_id", None)
        if not chat_id:
            raise FeishuApiError("create Feishu group failed: response missing chat_id")
        logger.info("Created Feishu managed group name=%r chat_id=%s", name, chat_id)
        return str(chat_id)

    async def create_managed_group(self, *, user_open_id: str, name: str) -> str:
        """Create a Feishu group for a single user and the ohmo bot."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._create_managed_group_sync, user_open_id, name)

    def _rename_group_sync(self, chat_id: str, name: str) -> None:
        """Rename a Feishu group."""
        from lark_oapi.api.im.v1 import UpdateChatRequest, UpdateChatRequestBody

        if not self._client:
            raise RuntimeError("Feishu client not initialized")
        request = (
            UpdateChatRequest.builder()
            .user_id_type("open_id")
            .chat_id(chat_id)
            .request_body(UpdateChatRequestBody.builder().name(name).build())
            .build()
        )
        response = self._client.im.v1.chat.update(request)
        if not response.success():
            raise FeishuApiError(self._format_response_error("rename Feishu group", response))
        logger.info("Renamed Feishu group chat_id=%s name=%r", chat_id, name)

    async def rename_group(self, *, chat_id: str, name: str) -> None:
        """Rename a Feishu group."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._rename_group_sync, chat_id, name)

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Feishu, including media (images/files) if present."""
        if not self._ensure_rest_client():
            logger.warning("Feishu client not initialized")
            return

        try:
            receive_id_type = "chat_id" if msg.chat_id.startswith("oc_") else "open_id"
            loop = asyncio.get_running_loop()
            chat_type = str(msg.metadata.get("chat_type") or "").lower()
            reply_mid = (
                msg.metadata.get("message_id")
                if chat_type == "group" or msg.metadata.get("thread_id") or msg.metadata.get("root_id")
                else None
            )

            failed_media: list[str] = []
            sent_media: list[str] = []
            for file_path in msg.media:
                if not os.path.isfile(file_path):
                    logger.warning("Media file not found: %s", file_path)
                    failed_media.append(file_path)
                    continue
                ext = os.path.splitext(file_path)[1].lower()
                if ext in self._IMAGE_EXTS:
                    key = await loop.run_in_executor(None, self._upload_image_sync, file_path)
                    if key:
                        ok = await loop.run_in_executor(
                            None, self._send_message_sync,
                            receive_id_type, msg.chat_id, "image", json.dumps({"image_key": key}, ensure_ascii=False),
                            reply_mid,
                        )
                        if ok:
                            sent_media.append(file_path)
                        else:
                            failed_media.append(file_path)
                    else:
                        failed_media.append(file_path)
                else:
                    key = await loop.run_in_executor(None, self._upload_file_sync, file_path)
                    if key:
                        # Use msg_type "media" for audio/video so users can play inline;
                        # "file" for everything else (documents, archives, etc.)
                        if ext in self._AUDIO_EXTS or ext in self._VIDEO_EXTS:
                            media_type = "media"
                        else:
                            media_type = "file"
                        ok = await loop.run_in_executor(
                            None, self._send_message_sync,
                            receive_id_type, msg.chat_id, media_type, json.dumps({"file_key": key}, ensure_ascii=False),
                            reply_mid,
                        )
                        if ok:
                            sent_media.append(file_path)
                        else:
                            failed_media.append(file_path)
                    else:
                        failed_media.append(file_path)

            if failed_media:
                names = ", ".join(os.path.basename(path) for path in failed_media)
                failure_body = json.dumps({"text": f"文件发送失败：{names}"}, ensure_ascii=False)
                await loop.run_in_executor(
                    None, self._send_message_sync,
                    receive_id_type, msg.chat_id, "text", failure_body,
                    reply_mid,
                )

            if msg.content and msg.content.strip():
                fmt = self._detect_msg_format(msg.content)

                if fmt == "text":
                    # Short plain text – send as simple text message
                    text_body = json.dumps({"text": msg.content.strip()}, ensure_ascii=False)
                    await loop.run_in_executor(
                        None, self._send_message_sync,
                        receive_id_type, msg.chat_id, "text", text_body,
                        reply_mid,
                    )

                elif fmt == "post":
                    # Medium content with links – send as rich-text post
                    post_body = self._markdown_to_post(msg.content)
                    await loop.run_in_executor(
                        None, self._send_message_sync,
                        receive_id_type, msg.chat_id, "post", post_body,
                        reply_mid,
                    )

                else:
                    # Complex / long content – send as interactive card
                    elements = self._build_card_elements(msg.content)
                    for chunk in self._split_elements_by_table_limit(elements):
                        card = {"config": {"wide_screen_mode": True}, "elements": chunk}
                        await loop.run_in_executor(
                            None, self._send_message_sync,
                            receive_id_type, msg.chat_id, "interactive", json.dumps(card, ensure_ascii=False),
                            reply_mid,
                        )

        except Exception as e:
            logger.error("Error sending Feishu message: %s", e)

    def _resolve_sender_display_name_sync(self, open_id: str) -> str:
        """Resolve a human-friendly sender name from Feishu contact APIs."""
        cached = self._sender_cache.get(open_id)
        if cached is not None:
            self._sender_cache.move_to_end(open_id)
            return cached.display_name
        if not self._client or not open_id:
            return open_id or "unknown"

        try:
            import lark_oapi as lark

            request = (
                lark.api.contact.v3.GetUserRequest.builder()
                .user_id(open_id)
                .user_id_type("open_id")
                .build()
            )
            response = self._client.contact.v3.user.get(request)
            if getattr(response, "success", lambda: False)():
                user = getattr(getattr(response, "data", None), "user", None)
                display_name = (
                    getattr(user, "name", None)
                    or getattr(user, "en_name", None)
                    or getattr(user, "nickname", None)
                    or open_id
                )
            else:
                display_name = open_id
        except Exception:
            logger.exception("Failed to resolve Feishu sender name open_id=%s", open_id)
            display_name = open_id

        self._sender_cache[open_id] = _FeishuSenderInfo(open_id=open_id, display_name=display_name)
        while len(self._sender_cache) > 512:
            self._sender_cache.popitem(last=False)
        return display_name

    def _on_message_sync(self, data: "P2ImMessageReceiveV1") -> None:  # noqa: F821
        """
        Sync handler for incoming messages (called from WebSocket thread).
        Schedules async handling in the main event loop.
        """
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._on_message(data), self._loop)

    async def _on_message(self, data: "P2ImMessageReceiveV1") -> None:  # noqa: F821
        """Handle incoming message from Feishu."""
        try:
            event = data.event
            message = event.message
            sender = event.sender

            # Deduplication check
            message_id = message.message_id
            if message_id in self._processed_message_ids:
                return
            self._processed_message_ids[message_id] = None

            # Trim cache
            while len(self._processed_message_ids) > 1000:
                self._processed_message_ids.popitem(last=False)

            # Skip bot messages
            if sender.sender_type == "bot":
                return

            sender_id = sender.sender_id.open_id if sender.sender_id else "unknown"
            loop = asyncio.get_running_loop()
            sender_display_name = await loop.run_in_executor(
                None,
                self._resolve_sender_display_name_sync,
                sender_id,
            )
            chat_id = message.chat_id
            chat_type = message.chat_type
            msg_type = message.message_type

            # Parse content
            content_parts = []
            media_paths = []

            try:
                content_json = json.loads(message.content) if message.content else {}
            except json.JSONDecodeError:
                content_json = {}

            if msg_type == "text":
                text = content_json.get("text", "")
                if text:
                    content_parts.append(text)

            elif msg_type == "post":
                text, image_keys = _extract_post_content(content_json)
                if text:
                    content_parts.append(text)
                # Download images embedded in post
                for img_key in image_keys:
                    file_path, content_text = await self._download_and_save_media(
                        "image", {"image_key": img_key}, message_id
                    )
                    if file_path:
                        media_paths.append(file_path)
                    content_parts.append(content_text)

            elif msg_type in ("image", "audio", "file", "media"):
                file_path, content_text = await self._download_and_save_media(msg_type, content_json, message_id)
                if file_path:
                    media_paths.append(file_path)
                content_parts.append(content_text)

            elif msg_type in ("share_chat", "share_user", "interactive", "share_calendar_event", "system", "merge_forward"):
                # Handle share cards and interactive messages
                text = _extract_share_card_content(content_json, msg_type)
                if text:
                    content_parts.append(text)

            else:
                content_parts.append(MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]"))

            content = "\n".join(content_parts) if content_parts else ""

            if not content and not media_paths:
                return
            mentions = _extract_feishu_mentions(content_json, getattr(message, "mentions", None))
            mentions_bot = chat_type == "group" and _feishu_mentions_bot(
                content_json,
                content,
                self.config,
                mentions=mentions,
            )
            if not _should_process_feishu_group_message(
                chat_type=chat_type,
                chat_id=chat_id,
                mentions_bot=mentions_bot,
                config=self.config,
            ):
                logger.info(
                    "Feishu group message ignored by policy chat_id=%s policy=%s mentions_bot=%s mention_names=%s mention_open_ids=%s",
                    chat_id,
                    getattr(self.config, "group_policy", "managed_or_mention"),
                    mentions_bot,
                    [item.get("name", "") for item in mentions],
                    [item.get("open_id", "") for item in mentions],
                )
                return

            # Add reaction only after policy says this message will be handled.
            await self._add_reaction(message_id, self.config.react_emoji)

            # Forward to message bus
            reply_to = chat_id if chat_type == "group" else sender_id

            # thread_id enables per-topic session routing in router.py
            thread_id = getattr(message, "thread_id", None) or getattr(message, "root_id", None)

            await self._handle_message(
                sender_id=sender_id,
                chat_id=reply_to,
                content=content,
                media=media_paths,
                metadata={
                    "message_id": message_id,
                    "thread_id": thread_id,
                    "root_id": getattr(message, "root_id", None),
                    "chat_type": chat_type,
                    "msg_type": msg_type,
                    "mentions": mentions,
                    "mentions_bot": mentions_bot,
                    "sender_display_name": sender_display_name,
                    "sender_label": sender_display_name or sender_id,
                }
            )

        except Exception as e:
            logger.error("Error processing Feishu message: %s", e)
