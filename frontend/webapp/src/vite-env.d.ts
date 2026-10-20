/// <reference types="vite/client" />

/**
 * 前端可配置项（Vite 环境变量，配置方式见 frontend/webapp/.env.example）。
 *
 * - 开发 / 预览：改 Vite 代理的后端地址 → `VITE_API_TARGET`
 * - 生产跨域部署：改浏览器直连后端的基址 → `VITE_API_BASE_URL`
 * - 严格 Bearer 部署（关闭 browser_auth）：构建时注入 `VITE_API_TOKEN`
 */
interface ImportMetaEnv {
  /** Vite dev / preview 服务器代理 `/api`、`/healthz` 的目标（后端地址）。 */
  readonly VITE_API_TARGET?: string;
  /** 浏览器直连后端的基址；留空 = 同源（dev 走代理，生产由 qaapp 托管）。 */
  readonly VITE_API_BASE_URL?: string;
  /**
   * 可选的静态 Bearer token。
   *
   * 默认不需要：页面启动时通过 `GET /api/v1/bootstrap` 建立 HttpOnly 会话。
   * 仅当服务端 `browser_auth.enabled=false`（公网 / SSO 场景）时才使用。
   */
  readonly VITE_API_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
