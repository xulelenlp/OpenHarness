/// <reference types="vite/client" />

/**
 * 前端可配置项（Vite 环境变量，配置集中在 config/frontend/）。
 *
 * - 开发 / 预览：改 Vite 代理的后端地址 → `VITE_API_TARGET`
 * - 多入口 / 生产：通常保持 `VITE_API_BASE_URL` 留空（同源），
 *   只有前后端是不同站点域名时才填浏览器直连的基址
 * - 严格 Bearer 部署（关闭 browser_auth）：构建时注入 `VITE_API_TOKEN`
 * - 品牌与开发端口：`VITE_APP_*` / `VITE_DEV_PORT`
 */
interface ImportMetaEnv {
  /** Vite dev / preview 服务器代理 `/api`、`/healthz` 的目标（后端地址）。 */
  readonly VITE_API_TARGET?: string;
  /**
   * 浏览器直连后端的基址；留空 = 同源（dev 走代理，生产由 qaapp 托管）。
   *
   * 内外网双入口（内网 IP + 外网端口映射）必须留空：填绝对地址后浏览器会
   * 固定去连该地址，外网入口会打到内网 IP 或变成跨站请求。
   */
  readonly VITE_API_BASE_URL?: string;
  /**
   * 可选的静态 Bearer token。
   *
   * 默认不需要：页面启动时通过 `GET /api/v1/bootstrap` 建立 HttpOnly 会话。
   * 仅当服务端 `browser_auth.enabled=false`（公网 / SSO 场景）时才使用。
   */
  readonly VITE_API_TOKEN?: string;
  readonly VITE_APP_NAME?: string;
  readonly VITE_APP_SUBTITLE?: string;
  readonly VITE_PRODUCT_NAME?: string;
  readonly VITE_APP_DESCRIPTION?: string;
  readonly VITE_DEV_PORT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
