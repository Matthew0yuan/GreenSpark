import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Development: `npm run dev` on 5173 with /api proxied to the backend on 8000 (`python -m app.backend`).
// Production: `npm run build` writes dist/, which the backend serves on 8000 itself.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
  test: { include: ["src/**/*.test.ts"] },
});
