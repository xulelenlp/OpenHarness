# 临时账户登录（TEMP-AUTH）

> **状态：临时验证用。** 正式部署接入 OSS / SSO 后，应删除本模块，
> 只保留「默认拒绝 + 角色 + CSRF」的中间件骨架，把身份解析换成 OSS 校验。
> 删除清单见文末「接入 OSS / SSO」。

## 1. 默认账户

首次启动（`users.json` 不存在）时自动生成两个账户：

| 用户名 | 默认密码 | 角色 | 权限 |
| --- | --- | --- | --- |
| `admin` | `Admin@Qa2026` | `admin` | 读写：对话、上传、审批、文件下载 |
| `viewer` | `Viewer@Qa2026` | `viewer` | 只读：仅 GET（历史会话、文件、预览） |

- 用户文件默认在 `<workspace>/users.json`（`~/.ohmo/users.json`），权限 `0600`；
  只保存 `scrypt$n$r$p$salt$hash` 摘要，不存明文。
- 想换初始密码，**在第一次启动前**设置环境变量，之后以文件里的摘要为准：

  ```bash
  export QAAPP_DEFAULT_ADMIN_PASSWORD='YourAdminPass2026'
  export QAAPP_DEFAULT_VIEWER_PASSWORD='YourViewerPass2026'
  ```

- 修改密码：登录后右上角账户菜单 →「修改密码」。改密会吊销该用户全部会话，
  需要重新登录。
- 忘记密码：停止服务，删除 `users.json` 后重启会重新生成默认账户（原有密码作废）；
  生产环境建议直接改 `users.json` 里的 `password_hash`（或接 OSS）。

## 2. 权限模型

- `admin`：全部接口。
- `viewer`：`GET/HEAD`；写接口返回 `403 forbidden`，前端输入框置灰为只读。
- 若配置了 `webapp.json:api_tokens`，`Authorization: Bearer <token>` 继续可用，
  等价于 `admin`，供脚本 / 自动化调用（浏览器登录不依赖它）。

## 3. 已实现的安全措施

| 面向 | 做法 |
| --- | --- |
| 密码存储 | `scrypt`（N=16384, r=8, p=1）+ 每用户 16 字节随机盐；`hmac.compare_digest`；未知账号也跑一次哈希，避免时序探测 |
| 会话 | 服务端 256 位随机 token；`HttpOnly` + `SameSite=Lax` + HTTPS 下 `Secure`；绝对有效期 12h、空闲 3h；退出 / 改密立即失效 |
| 暴力破解 | 同一用户名 5 次失败 / 5 分钟锁定；同一 IP 20 次（阈值的 4 倍）；返回 `429` + `Retry-After` |
| CSRF | 会话 Cookie 的写请求与登录共用一套来源校验：`cors_origins` 显式放行（`"*"` 不算）优先；`Sec-Fetch-Site: same-origin` 可信（代理改写 `Host` 仍成立）；否则要求 `Origin` 与 `Host` 主机名一致（允许端口不同，覆盖「页面 5174 / API 8787」）；`Sec-Fetch-Site: cross-site` 仅显式放行的来源可通过；`SameSite=Lax` 兜底 |
| 响应头 | `X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`、`Permissions-Policy` |
| 账号文件 | 原子写（临时文件 + `os.replace`）、`0600`；解析失败直接拒绝启动（不静默降级） |
| 缓存 | 登录 / 当前用户 / 登出响应 `Cache-Control: no-store` |
| 越权兜底 | `/api/*` 默认拒绝（中间件），路由层再查一次 `request.state.auth_user` |

## 4. 外网部署清单

1. **必须走 HTTPS**：uvicorn 只监听 `127.0.0.1`，由 nginx / SLB 终止 TLS。
2. 反代后设置 `QAAPP_AUTH_TRUST_PROXY=true`，否则 Cookie 不会带 `Secure`，
   且限流拿不到真实客户端 IP。
   - 仅在「只有反代能访问后端」时开启；否则 `X-Forwarded-For` 可被伪造。
3. **改掉默认密码**（或首启前用环境变量覆盖）。
4. **前端与 API 必须同源**：会话 Cookie 是 `SameSite=Lax`，跨站请求不会携带，登录也会被
   CSRF 校验拒绝；用同一端口的反向代理 / 端口映射把 `/api` 转发到后端，并把前端地址
   `VITE_API_BASE_URL` 留空（多入口见 DEPLOYMENT §8.1「内外网双入口」）。
5. `cors_origins` 写成明确来源（不要 `*`）：它同时是登录 / 写请求的可信来源白名单；
   内网、外网每个入口都要列出，入口地址变化时同步更新。
6. 不用的 `api_tokens` 清空；保留则按密码级密钥管理。
7. `users.json` 与 workspace 目录不要提交 git；备份时按密钥文件处理。
8. 只开放 443；8787 不要直接暴露公网。
9. 建议在网关再加一层 IP 白名单 / WAF 频率限制。

nginx 参考：

```nginx
server {
    listen 443 ssl http2;
    server_name qa.example.com;

    ssl_certificate     /etc/ssl/certs/qa.pem;
    ssl_certificate_key /etc/ssl/private/qa.key;

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_read_timeout 3600s;   # SSE 长连接
        proxy_buffering off;        # SSE 不要缓冲
    }
}
```

启动示例：

```bash
export QAAPP_STATIC_DIR=frontend/webapp/dist
export QAAPP_HOST=127.0.0.1
export QAAPP_AUTH_TRUST_PROXY=true
python -m qaapp webapp run
```

## 5. 环境变量

| 变量 | 说明 |
| --- | --- |
| `QAAPP_AUTH_ENABLED` | `true/false`；覆盖 `webapp.json:auth.enabled` |
| `QAAPP_USERS_FILE` | 账户文件路径；默认 `<workspace>/users.json` |
| `QAAPP_SESSION_TTL_SECONDS` | 会话绝对有效期（默认 43200） |
| `QAAPP_AUTH_TRUST_PROXY` | 是否信任 `X-Forwarded-Proto/For` |
| `QAAPP_DEFAULT_ADMIN_PASSWORD` | 首次生成 `admin` 时使用的密码 |
| `QAAPP_DEFAULT_VIEWER_PASSWORD` | 首次生成 `viewer` 时使用的密码 |

## 6. 接口

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/v1/auth/login` | 否 | `{username,password}` → `{user,expires_in,default_password}` + `Set-Cookie` |
| POST | `/api/v1/auth/logout` | 是 | 吊销当前会话并清 Cookie |
| GET | `/api/v1/auth/me` | 是 | 当前用户；未登录返回 401 |
| POST | `/api/v1/auth/password` | 是 | `{old_password,new_password}`；成功后所有会话失效 |

`GET /api/v1/bootstrap` 增加 `login_required` / `auth_enabled` / `default_credentials`；
启用登录后 **不再** 免密签发 `qaapp_session`。

## 7. 接入 OSS / SSO 时删除什么

后端：

- 删除 `qaapp/server/auth.py`、`qaapp/server/api/auth_middleware.py`、`qaapp/server/api/routes/auth.py`。
- `qaapp/server/api/app.py`：去掉 `AuthMiddleware` 注册（保留 `SecurityHeadersMiddleware`）。
- `qaapp/server/api/context.py`：去掉 `auth.enabled` 分支与旧 Bearer 兜底中不再需要的部分。
- `qaapp/server/api/routes/system.py`：`bootstrap` 的登录字段改为 OSS 状态。
- `qaapp/config/models.py`：删除 `AuthConfig` 与 `WebappConfig.auth`；
  `qaapp/config/loader.py` 删除 `QAAPP_AUTH_*` / `QAAPP_USERS_FILE` 覆盖。
- `config/backend/webapp.json`：删除 `auth` 段。
- `qaapp/server/service.py`：删除 `AuthManager` 初始化与默认密码告警。

前端：

- 删除 `components/LoginPage.tsx`、`components/PasswordDialog.tsx`、`styles/auth.css`。
- `api.ts`：删除 `getSession/login/logout/changePassword/notifyUnauthorized` 与 401 分支。
- `App.tsx`：删除 `AuthState`、`handleLoggedIn/handleLogout/handlePasswordChanged`、
  登录页分支与 `PasswordDialog`；`TopBar` 的账户菜单改为展示 OSS 用户。

OSS / SSO 需要补的只有「身份解析」：

1. 校验 OSS / SSO 下发的凭证（回调 code、JWT 或网关注入的 header）。
2. 映射成 `AuthUser(username, role, display_name)` 写入 `request.state.auth_user`。
3. 保留现有中间件的 **默认拒绝 / viewer 只读 / Origin 校验** 骨架，继续按角色放行。

## 8. 测试

```bash
pytest tests/test_qaapp/test_auth.py -q
```

覆盖：未登录 401、登录成功/失败、Cookie 属性、登出、限流 429、viewer 只读、
跨站写请求拒绝、改密后会话吊销、账户文件为哈希且 0600、环境变量覆盖初始密码、
启用登录后 Bearer 仍可用。
