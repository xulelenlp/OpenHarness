/** 临时账户登录页（TEMP-AUTH）；正式接入 OSS / SSO 后删除。 */

import { useState } from "react";

import { login } from "../api";
import { BRAND } from "../branding";
import type { AuthUser } from "../types";

interface LoginPageProps {
  /** 是否仍有账户在用默认密码（服务端 bootstrap 下发）。 */
  defaultCredentials: boolean;
  /** 退出 / 改密后回到登录页时的提示。 */
  notice?: string;
  onSuccess: (user: AuthUser, defaultPassword: boolean) => void;
}

export function LoginPage({ defaultCredentials, notice, onSuccess }: LoginPageProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!username.trim() || !password || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      const result = await login(username.trim(), password);
      onSuccess(result.user, result.default_password);
    } catch (err) {
      setError((err as Error).message || "登录失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="auth-page">
      <form className="auth-card" onSubmit={submit}>
        <div className="auth-brand">
          <span className="auth-mark" aria-hidden="true">
            {BRAND.appName.slice(0, 1)}
          </span>
          <div>
            <h1>{BRAND.appName}</h1>
            <p>{BRAND.subtitle}</p>
          </div>
        </div>

        {notice ? <p className="auth-notice">{notice}</p> : null}
        {defaultCredentials ? (
          <p className="auth-warning" role="status">
            当前仍在使用默认密码。首次登录后请点击右上角账户菜单修改密码。
          </p>
        ) : null}

        <label className="auth-field">
          <span>用户名</span>
          <input
            name="username"
            autoComplete="username"
            autoFocus
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            placeholder="请输入用户名"
            disabled={submitting}
          />
        </label>
        <label className="auth-field">
          <span>密码</span>
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="请输入密码"
            disabled={submitting}
          />
        </label>

        {error ? (
          <p className="auth-error" role="alert">
            {error}
          </p>
        ) : null}

        <button
          type="submit"
          className="auth-submit"
          disabled={submitting || !username.trim() || !password}
        >
          {submitting ? "登录中…" : "登录"}
        </button>

        <p className="auth-foot">
          仅限授权人员使用。登录会话保存在 HttpOnly Cookie 中，公网部署请通过 HTTPS 访问。
        </p>
      </form>
    </div>
  );
}
