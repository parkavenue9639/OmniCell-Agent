import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const apiTarget = process.env.OMNICELL_API_PROXY_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    watch: {
      // Codex/apply-patch replaces files atomically on macOS. Polling keeps the
      // local research UI from serving a stale transform after those edits.
      usePolling: true,
      interval: 250,
    },
    proxy: {
      "/api/v1": {
        target: apiTarget,
        changeOrigin: true,
        timeout: 0,
        proxyTimeout: 0,
      },
    },
  },
  preview: {
    host: "127.0.0.1",
  },
});
