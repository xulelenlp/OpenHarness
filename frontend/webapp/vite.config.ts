import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

/**
 * 前端配置集中在仓库根 `config/frontend/`：
 *
 * - `.env`        当前环境（由 `.env_dev` / `.env_prod` 复制，gitignore）
 * - `.env_dev`    开发模板
 * - `.env_prod`   生产模板
 *
 * VITE_API_TARGET 是 Vite dev server 所在机器访问后端的地址（服务端代理，
 * 浏览器不产生跨域）；VITE_API_BASE_URL 是浏览器直连后端的基址，默认留空。
 *
 * 代理必须保留浏览器带来的 Host（changeOrigin: false）：后端对 Cookie 写请求做
 * Origin/Host 同源校验，改写 Host 会让「内网 :5174 / 外网 :15174」两个入口被误判
 * 为跨站。保留 Host 后，同一份前端在任意入口访问时都能自动对齐。
 */
const envDir = resolve(fileURLToPath(new URL(".", import.meta.url)), "../../config/frontend");

export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, envDir, "");
  const apiTarget = (env.VITE_API_TARGET || "http://127.0.0.1:8787").replace(/\/+$/, "");
  const apiBase = (env.VITE_API_BASE_URL || "").trim();
  const devPort = Number(env.VITE_DEV_PORT || 5174);

  // 内外网双入口必须走同源代理；填了绝对地址时提醒一次，否则外网页面会去连内网 IP。
  if (command === "serve" && apiBase) {
    console.warn(
      `[qaapp] VITE_API_BASE_URL=${apiBase}：浏览器将直连该地址，` +
        "只有它对所有访问者都可达时才有效；内外网双入口请留空（走同源代理）。",
    );
  }

  const proxy = {
    "/api": { target: apiTarget, changeOrigin: false },
    "/healthz": { target: apiTarget, changeOrigin: false },
  };

  return {
    plugins: [react()],
    envDir,
    server: {
      // 监听所有网卡（不写死任何本机 IP）：局域网内的手机 / 其他设备可直接访问。
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
