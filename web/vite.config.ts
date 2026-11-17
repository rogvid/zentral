import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  build: {
    sourcemap: false,
    assetsInlineLimit: 0,
    // Two pages: the staff app and the requester portal (no staff sign-in code).
    rollupOptions: { input: { main: "index.html", portal: "portal.html" } },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/mcp": "http://127.0.0.1:8000",
      "/dev-idp": "http://127.0.0.1:8000",
    },
  },
  test: { environment: "node" },
});
