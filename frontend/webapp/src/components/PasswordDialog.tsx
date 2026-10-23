/** 修改密码弹窗（TEMP-AUTH，随登录模块一起删除）。 */

import { useState } from "react";

import { changePassword } from "../api";

interface PasswordDialogProps {
  onClose: () => void;
  /** 修改成功后由 App 退出登录并提示。 */
  onChanged: (message: string) => void;
}

export function PasswordDialog({ onClose, onChanged }: PasswordDialogProps) {
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (submitting) return;
    if (newPassword !== confirm) {
      setError("两次输入的新密码不一致");
      return;
    }
    if (newPassword.length < 10) {
      setError("新密码至少 10 位，且需同时包含字母和数字");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await changePassword(oldPassword, newPassword);
      onChanged("密码已修改，请用新密码重新登录");
    } catch (err) {
      setError((err as Error).message || "修改失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form
        className="auth-dialog"
        onClick={(event) => event.stopPropagation()}
        onSubmit={submit}
      >
        <h2>修改密码</h2>
        <p className="muted">修改成功后当前会话会立即失效，需要重新登录。</p>
        <label className="auth-field">
          <span>原密码</span>
          <input
            type="password"
            autoComplete="current-password"
            value={oldPassword}
            onChange={(event) => setOldPassword(event.target.value)}
            disabled={submitting}
            autoFocus
          />
        </label>
        <label className="auth-field">
          <span>新密码</span>
          <input
            type="password"
            autoComplete="new-password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            placeholder="至少 10 位，包含字母和数字"
            disabled={submitting}
          />
        </label>
        <label className="auth-field">
          <span>确认新密码</span>
          <input
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            disabled={submitting}
          />
        </label>
        {error ? (
          <p className="auth-error" role="alert">
            {error}
          </p>
        ) : null}
        <div className="auth-dialog-actions">
          <button type="button" className="ghost-btn" onClick={onClose} disabled={submitting}>
            取消
          </button>
          <button type="submit" className="auth-submit" disabled={submitting}>
            {submitting ? "提交中…" : "确认修改"}
          </button>
        </div>
      </form>
    </div>
  );
}
