import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

/**
 * 前端配置集中在仓库根 `config/frontend/`：
 *
 * - `.env`              公共品牌 / 端口
 * - `.env.development`  开发代理后端地址 VITE_API_TARGET
 * - `.env.production`   生产直连地址 VITE_API_BASE_URL
 * - `.env.local`        本机覆盖（gitignore）
 *
 * VITE_API_TARGET 是 Vite dev server 所在机器访问后端的地址（服务端代理，
 * 浏览器不产生跨域）；VITE_API_BASE_URL 用于浏览器直连后端的部署方式。
 */
const envDir = resolve(fileURLToPath(new URL(".", import.meta.url)), "../../config/frontend");

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, envDir, "");
  const apiTarget = (env.VITE_API_TARGET || "http://127.0.0.1:8787").replace(/\/+$/, "");
  const devPort = Number(env.VITE_DEV_PORT || 5173);

  const proxy = {
    "/api": { target: apiTarget, changeOrigin: true },
    "/healthz": { target: apiTarget, changeOrigin: true },
  };

  return {
    plugins: [react()],
    envDir,
    server: {
      // 监听所有网卡：局域网内的手机 / 其他设备可直接访问前端页面。
      host: "0.0.0.0",
      port: devPort,
      proxy,
    },
    // 预览生产构建产物（npm run preview）时同样对外监听并代理 API。
    preview: {
      host: "0.0.0.0",
      port: 4173,
      proxy,
    },
  };
});
