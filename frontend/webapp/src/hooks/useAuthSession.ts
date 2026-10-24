/** 启动鉴权（TEMP-AUTH）：读取 bootstrap 配置并确认当前登录状态。 */

import { useEffect, useState } from "react";
import type { Dispatch, SetStateAction } from "react";

import { bootstrap, getSession, hasStaticToken } from "../api";
import type { AuthState } from "../types";

interface Options {
  setError: (message: string) => void;
}

export interface AuthSessionState {
  auth: AuthState;
  setAuth: Dispatch<SetStateAction<AuthState>>;
  loginRequired: boolean;
  defaultCredentials: boolean;
  setDefaultCredentials: Dispatch<SetStateAction<boolean>>;
}

export function useAuthSession({ setError }: Options): AuthSessionState {
  const [auth, setAuth] = useState<AuthState>({ status: "checking" });
  const [loginRequired, setLoginRequired] = useState(false);
  const [defaultCredentials, setDefaultCredentials] = useState(false);

  useEffect(() => {
    // 启动：取服务端鉴权配置，再确认当前会话。
    // 旧的 bootstrap 快捷会话会在这里签发 Cookie；启用登录后只返回 login_required。
    let cancelled = false;
    void (async () => {
      let info: Awaited<ReturnType<typeof bootstrap>> | null = null;
      try {
        info = await bootstrap();
      } catch (e) {
        if (!cancelled) setError(`初始化会话失败：${(e as Error).message}`);
      }
      if (cancelled) return;
      const requiresLogin = Boolean(info?.login_required ?? info?.auth_enabled);
      setLoginRequired(requiresLogin);
      setDefaultCredentials(Boolean(info?.default_credentials));
      if (info?.auth_required && !info.browser_session && !requiresLogin && !hasStaticToken()) {
        setError(
          "服务端要求 Bearer token，但未启用浏览器会话。请启用 browser_auth，" +
            "或在构建前端时设置 VITE_API_TOKEN，或由反向代理注入 Authorization。",
        );
      }
      try {
        const session = await getSession();
        if (cancelled) return;
        if (session?.user) {
          setAuth({ status: "authenticated", user: session.user });
          setDefaultCredentials(Boolean(session.default_password));
        } else if (requiresLogin) {
          setAuth({ status: "anonymous" });
        } else {
          // 未启用登录（兼容旧部署）：没有用户菜单，直接进入应用。
          setAuth({
            status: "authenticated",
            user: { username: "", role: "admin", display_name: "" },
          });
        }
      } catch (e) {
        if (cancelled) return;
        if (requiresLogin) {
          setAuth({ status: "anonymous" });
        } else {
          setError(`检查登录状态失败：${(e as Error).message}`);
          setAuth({
            status: "authenticated",
            user: { username: "", role: "admin", display_name: "" },
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [setError]);

  return { auth, setAuth, loginRequired, defaultCredentials, setDefaultCredentials };
}
