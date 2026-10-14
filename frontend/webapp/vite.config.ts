import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

/**
 * 后端地址通过环境变量配置，便于「前端 / 后端分机部署」：
 *
 * ```bash
 * # 方式一：命令行临时指定
 * VITE_API_TARGET=http://192.168.139.144:8787 npm run dev
 *
 * # 方式二：写进 .env.local（推荐，长期生效且不进版本库）
 * echo 'VITE_API_TARGET=http://192.168.139.144:8787' >> .env.local
 * ```
 *
 * 说明：`VITE_API_TARGET` 是 **Vite 服务器所在机器**访问后端的地址（服务端代理，
 * 浏览器不产生跨域）。若只想让浏览器直连后端，则改用 `VITE_API_BASE_URL`。
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = (env.VITE_API_TARGET || "http://127.0.0.1:8787").replace(/\/+$/, "");

  const proxy = {
    "/api": { target: apiTarget, changeOrigin: true },
    "/healthz": { target: apiTarget, changeOrigin: true },
  };

  return {
    plugins: [react()],
    server: {
      // 监听所有网卡：局域网内的手机 / 其他设备可直接访问前端页面。
      host: "0.0.0.0",
      port: 5173,
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
