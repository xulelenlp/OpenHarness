/** 附件选择、上传、移除与拖拽处理。 */

import { useCallback, useRef, useState } from "react";
import type { ChangeEvent, DragEvent, MutableRefObject } from "react";

import { createConversation, uploadAttachments } from "../api";
import {
  MAX_FILES,
  SUPPORTED_SUFFIXES,
  pickWithinLimit,
} from "../attachments";
import type { AttachmentInfo } from "../types";

interface Options {
  activeId: string | null;
  activeIdRef: MutableRefObject<string | null>;
  setActiveId: (id: string) => void;
  refreshConversations: () => Promise<void>;
  streaming: boolean;
  onError: (message: string) => void;
}

export function useAttachments({
  activeId,
  activeIdRef,
  setActiveId,
  refreshConversations,
  streaming,
  onError,
}: Options) {
  const [attachments, setAttachments] = useState<AttachmentInfo[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFilesPicked = useCallback(
    async (incoming: File[]) => {
      if (streaming || uploading || incoming.length === 0) return;

      const { accepted, rejected } = pickWithinLimit(attachments.length, incoming);
      if (rejected.length > 0) {
        onError(
          rejected
            .map((item) =>
              item.reason === "limit"
                ? `最多上传 ${MAX_FILES} 个文件，「${item.name}」未添加`
                : `不支持的文件类型：「${item.name}」（支持 ${SUPPORTED_SUFFIXES.join(" ")}）`,
            )
            .join("；"),
        );
      }
      if (accepted.length === 0) return;

      let conversationId = activeId;
      if (!conversationId) {
        try {
          conversationId = (await createConversation()).conversation_id;
          activeIdRef.current = conversationId;
          setActiveId(conversationId);
          await refreshConversations();
        } catch (error) {
          onError((error as Error).message);
          return;
        }
      }

      setUploading(true);
      try {
        const response = await uploadAttachments(conversationId, accepted);
        setAttachments((prev) =>
          [...prev, ...(response.attachments ?? [])].slice(0, MAX_FILES),
        );
      } catch (error) {
        onError((error as Error).message);
      } finally {
        setUploading(false);
      }
    },
    [
      activeId,
      activeIdRef,
      attachments.length,
      onError,
      refreshConversations,
      setActiveId,
      streaming,
      uploading,
    ],
  );

  const removeAttachment = useCallback((attachmentId: string) => {
    setAttachments((prev) => prev.filter((item) => item.attachment_id !== attachmentId));
  }, []);

  const handleFileInputChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(event.target.files ?? []);
      event.target.value = "";
      void handleFilesPicked(files);
    },
    [handleFilesPicked],
  );

  const handleDragOver = useCallback(
    (event: DragEvent<HTMLElement>) => {
      if (streaming || uploading) return;
      if (!Array.from(event.dataTransfer?.types ?? []).includes("Files")) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      setDragging(true);
    },
    [streaming, uploading],
  );

  const handleDragLeave = useCallback((event: DragEvent<HTMLElement>) => {
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
    setDragging(false);
  }, []);

  const handleDrop = useCallback(
    (event: DragEvent<HTMLElement>) => {
      event.preventDefault();
      event.stopPropagation();
      setDragging(false);
      if (streaming || uploading) return;
      const files = Array.from(event.dataTransfer?.files ?? []);
      if (files.length > 0) void handleFilesPicked(files);
    },
    [handleFilesPicked, streaming, uploading],
  );

  return {
    attachments,
    setAttachments,
    uploading,
    dragging,
    fileInputRef,
    handleFilesPicked,
    removeAttachment,
    handleFileInputChange,
    handleDragOver,
    handleDragLeave,
    handleDrop,
  };
}
