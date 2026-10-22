/** 会话文件抽屉：列表刷新、下载、预览与定时静默同步。 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MutableRefObject } from "react";

import { fetchConversationFile, listConversationFiles } from "../api";
import { copyText } from "../clipboard";
import { PREVIEW_CHARS } from "../chat/constants";
import type { ConversationWorkspace, WorkspaceFile } from "../types";

interface Options {
  activeId: string | null;
  activeIdRef: MutableRefObject<string | null>;
  onError: (message: string) => void;
}

export function useWorkspaceFiles({ activeId, activeIdRef, onError }: Options) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [workspace, setWorkspace] = useState<ConversationWorkspace | null>(null);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [preview, setPreview] = useState<{
    name: string;
    text: string;
    truncated: boolean;
  } | null>(null);
  const [copiedDir, setCopiedDir] = useState(false);
  const [downloadingPath, setDownloadingPath] = useState<string | null>(null);
  const drawerOpenRef = useRef(false);
  const workspaceRefreshTimerRef = useRef<number | null>(null);

  useEffect(() => {
    drawerOpenRef.current = drawerOpen;
  }, [drawerOpen]);

  const refreshWorkspace = useCallback(
    async (conversationId?: string, silent = false) => {
      const id = conversationId ?? activeId;
      if (!id) return;
      if (!silent && activeIdRef.current === id) setWorkspaceLoading(true);
      try {
        const data = await listConversationFiles(id);
        if (activeIdRef.current === id) setWorkspace(data);
      } catch (error) {
        if (activeIdRef.current === id) onError((error as Error).message);
      } finally {
        if (!silent && activeIdRef.current === id) setWorkspaceLoading(false);
      }
    },
    [activeId, activeIdRef, onError],
  );

  const scheduleWorkspaceRefresh = useCallback(
    (conversationId: string) => {
      if (!drawerOpenRef.current) return;
      if (workspaceRefreshTimerRef.current !== null) {
        window.clearTimeout(workspaceRefreshTimerRef.current);
      }
      workspaceRefreshTimerRef.current = window.setTimeout(() => {
        workspaceRefreshTimerRef.current = null;
        void refreshWorkspace(conversationId, true);
      }, 600);
    },
    [refreshWorkspace],
  );

  const cancelScheduledRefresh = useCallback(() => {
    if (workspaceRefreshTimerRef.current !== null) {
      window.clearTimeout(workspaceRefreshTimerRef.current);
      workspaceRefreshTimerRef.current = null;
    }
  }, []);

  const flushWorkspaceRefresh = useCallback(
    (conversationId: string) => {
      cancelScheduledRefresh();
      void refreshWorkspace(conversationId);
    },
    [cancelScheduledRefresh, refreshWorkspace],
  );

  useEffect(() => {
    if (drawerOpen && activeId) void refreshWorkspace();
  }, [drawerOpen, activeId, refreshWorkspace]);

  useEffect(() => () => cancelScheduledRefresh(), [cancelScheduledRefresh]);

  useEffect(() => {
    if (!preview) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPreview(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [preview]);

  const handleCopyDir = useCallback(async () => {
    const dir = workspace?.dir;
    if (!dir) return;
    if (await copyText(dir)) {
      setCopiedDir(true);
      window.setTimeout(() => setCopiedDir(false), 1500);
    }
  }, [workspace?.dir]);

  const downloadPath = useCallback(
    async (relativePath: string) => {
      if (!activeId || downloadingPath) return;
      setDownloadingPath(relativePath);
      try {
        const { blob, filename } = await fetchConversationFile(activeId, relativePath);
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = filename;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
      } catch (error) {
        onError((error as Error).message);
      } finally {
        setDownloadingPath(null);
      }
    },
    [activeId, downloadingPath, onError],
  );

  const previewPath = useCallback(
    async (relativePath: string) => {
      if (!activeId) return;
      try {
        const { blob } = await fetchConversationFile(activeId, relativePath);
        const text = await blob.text();
        setPreview({
          name: relativePath,
          text: text.slice(0, PREVIEW_CHARS),
          truncated: text.length > PREVIEW_CHARS,
        });
      } catch (error) {
        onError((error as Error).message);
      }
    },
    [activeId, onError],
  );

  const fileGroups = useMemo(() => {
    const order = ["uploads", "extracted", "scripts", "tmp", "outputs"];
    const map = new Map<string, WorkspaceFile[]>();
    for (const file of workspace?.files ?? []) {
      const dir = file.dir && file.dir !== "." ? file.dir : "根目录";
      const list = map.get(dir);
      if (list) list.push(file);
      else map.set(dir, [file]);
    }
    return [...map.entries()].sort((a, b) => {
      const ia = order.indexOf(a[0]);
      const ib = order.indexOf(b[0]);
      return (ia < 0 ? order.length : ia) - (ib < 0 ? order.length : ib);
    });
  }, [workspace]);

  return {
    drawerOpen,
    setDrawerOpen,
    workspace,
    setWorkspace,
    workspaceLoading,
    preview,
    setPreview,
    copiedDir,
    downloadingPath,
    fileGroups,
    refreshWorkspace,
    scheduleWorkspaceRefresh,
    flushWorkspaceRefresh,
    handleCopyDir,
    downloadPath,
    previewPath,
  };
}
