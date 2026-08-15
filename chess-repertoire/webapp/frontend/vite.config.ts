import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";
import { fileURLToPath, URL } from "node:url";

const chessJs = fileURLToPath(
  new URL("./public/vendor/chess.js", import.meta.url),
);

export default defineConfig({
  publicDir: "public",
  base: "/",
  resolve: {
    alias: {
      "/vendor/chess.js": chessJs,
      "@vendor/chess.js": chessJs,
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:5000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "../static",
    emptyOutDir: true,
    sourcemap: true,
  },
  plugins: [
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["opening-explorer-logo.png", "pieces/*.png", "privacy.html", "terms.html"],
      manifest: {
        name: "Opening Explorer",
        short_name: "Opening Explorer",
        description: "Opening analytics and repertoire practice for chess",
        theme_color: "#312e2b",
        background_color: "#312e2b",
        display: "standalone",
        start_url: "/",
        icons: [
          {
            src: "/opening-explorer-logo.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "any",
          },
        ],
      },
      workbox: {
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [
          /\/privacy(?:\.html)?$/,
          /\/terms(?:\.html)?$/,
          /\/vendor\//,
          /\/api\//,
        ],
        runtimeCaching: [
          {
            urlPattern: ({ url }) => url.pathname.startsWith("/api/"),
            handler: "NetworkOnly",
          },
        ],
      },
    }),
  ],
});
