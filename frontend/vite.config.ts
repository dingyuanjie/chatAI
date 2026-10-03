// Vite 开发服务器设置：React 插件负责 JSX 转换；/api 请求转发给本机 FastAPI。
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 部署/本地端口可通过 CHATAI_BACKEND_URL 覆盖，未配置时使用默认后端端口。
      "/api": {
        target: loadEnv(mode, ".", "CHATAI_").CHATAI_BACKEND_URL || "http://127.0.0.1:8000",
        changeOrigin: true
      }
    }
  }
}));
