/// <reference types="vite/client" />

/**
 * 前端可配置项（Vite 环境变量，配置方式见 frontend/webapp/.env.example）。
 *
 * - 开发 / 预览：改 Vite 代理的后端地址 → `VITE_API_TARGET`
 * - 生产跨域部署：改浏览器直连后端的基址 → `VITE_API_BASE_URL`
 */
interface ImportMetaEnv {
  /** Vite dev / preview 服务器代理 `/api`、`/healthz` 的目标（后端地址）。 */
  readonly VITE_API_TARGET?: string;
  /** 浏览器直连后端的基址；留空 = 同源（dev 走代理，生产由 qaapp 托管）。 */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
