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
    {
      // The built file has this stable name, so the dev server answers to it too.
      name: "signin-fonts-dev",
      configureServer(server) {
        server.middlewares.use((req, _res, next) => {
          const request = req as { url?: string }; // the project has no Node types
          if (request.url === "/signin-fonts.css") request.url = "/src/signin-fonts.css?direct";
          next();
        });
      },
    },
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
    // The server-rendered dev sign-in page links the fonts by this stable name.
    rollupOptions: {
      input: { main: "index.html", portal: "portal.html", "signin-fonts": "src/signin-fonts.css" },
      output: {
        assetFileNames: (asset) =>
          asset.names.includes("signin-fonts.css") ? "signin-fonts.css" : "assets/[name]-[hash][extname]",
      },
    },
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
