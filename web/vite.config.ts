import { sentryVitePlugin } from "@sentry/vite-plugin";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

declare const process: { env: Record<string, string | undefined> };

// Source maps exist only to be uploaded to Sentry, and are deleted before the
// image is assembled. A build without the token has none.
const sentryAuthToken = process.env.SENTRY_AUTH_TOKEN;
console.log(`Sentry source maps: ${sentryAuthToken ? "uploading" : "off (no SENTRY_AUTH_TOKEN)"}`);

export default defineConfig({
  plugins: [
    react(),
    ...(sentryAuthToken
      ? [
          sentryVitePlugin({
            org: "kvist-nt",
            project: "zentral-web",
            url: "https://de.sentry.io",
            authToken: sentryAuthToken,
            release: { name: process.env.VITE_SENTRY_RELEASE },
            sourcemaps: { filesToDeleteAfterUpload: ["dist/**/*.map"] },
            telemetry: false,
          }),
        ]
      : []),
  ],
  build: {
    sourcemap: sentryAuthToken ? "hidden" : false,
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
