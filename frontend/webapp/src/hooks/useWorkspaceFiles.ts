/** 会话文件抽屉：列表刷新、下载、预览与定时静默同步。 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MutableRefObject } from "react";

import { fetchConversationFile, listConversationFiles } from "../api";
import { copyText } from "../clipboard";
import { PREVIEW_CHARS } from "../chat/constants";
import type { ConversationWorkspace, WorkspaceFile } from "../types";

/** 任务进行中、抽屉打开时，文件列表的定时同步间隔（毫秒）。 */
const WORKSPACE_POLL_MS = 5000;

interface Options {
  activeId: string | null;
  activeIdRef: MutableRefObject<string | null>;
  /** 是否正在流式执行；只在任务进行中定时轮询，空闲时不打扰服务端。 */
  streaming: boolean;
  onError: (message: string) => void;
}

/** 清单指纹：路径 / 大小 / 修改时间都没变就不重渲染，避免列表无谓闪动。 */
function workspaceSignature(data: ConversationWorkspace): string {
  const files = data.files
    .map((file) => `${file.path}\u0000${file.size}\u0000${file.mtime}`)
    .join("\u0001");
  return `${data.dir}\u0002${data.total_bytes}\u0002${data.truncated}\u0002${files}`;
}

export function useWorkspaceFiles({ activeId, activeIdRef, streaming, onError }: Options) {
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
  const workspaceRef = useRef<ConversationWorkspace | null>(null);
  const signatureRef = useRef("");
  const refreshingRef = useRef(false);
  const onErrorRef = useRef(onError);

  // 回调放进 ref：refreshWorkspace 的引用保持稳定，避免流式渲染时反复重建定时器。
  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  // 供刷新逻辑判断“是否已有数据”，避免每次静默刷新都闪一下“加载中…”。
  useEffect(() => {
    workspaceRef.current = workspace;
    if (!workspace) signatureRef.current = "";
  }, [workspace]);

  // 注意：依赖必须保持稳定（只依赖 ref）。若把 activeId / onError 直接放进依赖，
  // 流式渲染期间每次 render 都会重建本函数，抽屉的 effect 就会不停刷新（曾出现列表闪动）。
  const refreshWorkspace = useCallback(
    async (conversationId?: string, silent = false) => {
      const id = conversationId ?? activeIdRef.current;
      if (!id || refreshingRef.current) return;
      refreshingRef.current = true;
      const showLoading = !silent && activeIdRef.current === id && !workspaceRef.current;
      if (showLoading) setWorkspaceLoading(true);
      try {
        const data = await listConversationFiles(id);
        if (activeIdRef.current !== id) return;
        // 内容没变化就跳过 setState：定时轮询时列表保持稳定，不闪。
        const signature = workspaceSignature(data);
        if (signature !== signatureRef.current) {
          signatureRef.current = signature;
          setWorkspace(data);
        }
      } catch (error) {
        if (activeIdRef.current === id) onErrorRef.current((error as Error).message);
      } finally {
        refreshingRef.current = false;
        if (showLoading && activeIdRef.current === id) setWorkspaceLoading(false);
      }
    },
    [activeIdRef],
  );

  /** 任务结束 / 断流时立即刷新一次；已有列表时静默更新，不闪“加载中…”。 */
  const flushWorkspaceRefresh = useCallback(
    (conversationId: string) => {
      void refreshWorkspace(conversationId);
    },
    [refreshWorkspace],
  );

  useEffect(() => {
    if (drawerOpen && activeId) void refreshWorkspace();
  }, [drawerOpen, activeId, refreshWorkspace]);

  // 任务进行中且抽屉打开时按固定间隔静默同步；页面不可见时跳过，回来再刷新。
  useEffect(() => {
    if (!drawerOpen || !streaming || !activeId) return;
    const timer = window.setInterval(() => {
      if (document.hidden) return;
      void refreshWorkspace(activeId, true);
    }, WORKSPACE_POLL_MS);
    return () => window.clearInterval(timer);
  }, [drawerOpen, streaming, activeId, refreshWorkspace]);

  // 从后台切回页面时补一次同步，避免错过轮询周期的产出。
  useEffect(() => {
    if (!drawerOpen || !activeId) return;
    const onVisible = () => {
      if (!document.hidden) void refreshWorkspace(activeId, true);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [drawerOpen, activeId, refreshWorkspace]);

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
    flushWorkspaceRefresh,
    handleCopyDir,
    downloadPath,
    previewPath,
  };
}
