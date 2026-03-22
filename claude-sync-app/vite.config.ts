import { defineConfig } from "vite";

// Vite configuration for Tauri 2 frontend.
// Serves the TypeScript frontend in dev mode and builds for production.
export default defineConfig({
  // Prevent vite from obscuring Rust errors
  clearScreen: false,
  server: {
    // Tauri expects a fixed port
    port: 1420,
    strictPort: true,
    // Allow HMR through Tauri's dev server
    watch: {
      ignored: ["**/src-tauri/**"],
    },
  },
  build: {
    // Output to dist/ for Tauri to serve
    outDir: "dist",
    // Tauri uses Chromium on Windows and WebKit on macOS/Linux
    target: process.env.TAURI_ENV_PLATFORM === "windows" ? "chrome105" : "safari13",
    // Don't minify for debug builds
    minify: !process.env.TAURI_ENV_DEBUG ? "esbuild" : false,
    // Generate sourcemaps for debug builds
    sourcemap: !!process.env.TAURI_ENV_DEBUG,
  },
});
