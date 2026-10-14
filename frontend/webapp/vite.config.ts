import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发模式下将 /api 与 /healthz 代理到 qaapp 服务（abcqa webapp run）。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8787",
        changeOrigin: true,
      },
      "/healthz": {
        target: "http://127.0.0.1:8787",
        changeOrigin: true,
      },
    },
  },
});
