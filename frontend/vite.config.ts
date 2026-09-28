import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs on :8000; requests to /api are proxied there.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://localhost:8000", rewrite: (p) => p.replace(/^\/api/, "") } },
  },
});
