"""Conversation workspace files and attachment index (compatibility facade)."""

from qaapp.workspace.files.errors import (
    AttachmentError,
    AttachmentNotFound,
    AttachmentTooLarge,
    TooManyAttachments,
    UnsupportedAttachment,
)
from qaapp.workspace.files.inventory import build_context_block, list_files
from qaapp.workspace.files.paths import (
    CONVERSATION_ID_PATTERN,
    INDEX_FILENAME,
    META_FILENAME,
    SUBDIRS,
    ConversationPaths,
    conversations_root,
)
from qaapp.workspace.files.store import (
    AttachmentStore,
    build_record,
    new_attachment_id,
    public_record,
)
from qaapp.workspace.files.uploads import (
    ensure_supported,
    sanitize_filename,
    save_upload,
    unique_path,
)

__all__ = [
    "CONVERSATION_ID_PATTERN",
    "INDEX_FILENAME",
    "META_FILENAME",
    "SUBDIRS",
    "AttachmentError",
    "AttachmentNotFound",
    "AttachmentStore",
    "AttachmentTooLarge",
    "ConversationPaths",
    "TooManyAttachments",
    "UnsupportedAttachment",
    "build_context_block",
    "build_record",
    "conversations_root",
    "ensure_supported",
    "list_files",
    "new_attachment_id",
    "public_record",
    "sanitize_filename",
    "save_upload",
    "unique_path",
]
