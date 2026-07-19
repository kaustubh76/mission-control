import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Relative root/outDir ONLY — never hand the bundler the absolute project path,
// which contains a space and a '*' ("BNB Hack * CMC") that breaks some tooling.
// In dev, /api is proxied to the FastAPI server so the SPA uses same-origin
// relative URLs identically in dev and prod (where FastAPI serves web/dist).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        // 127.0.0.1, NOT localhost: Node 17+ resolves localhost to ::1 first,
        // but uvicorn on 0.0.0.0 binds IPv4 only on macOS — the proxy would 500.
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
