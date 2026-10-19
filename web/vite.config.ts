import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8090", ws: true },
      "/docs": "http://127.0.0.1:8090",
      "/openapi.json": "http://127.0.0.1:8090",
    },
  },
  build: { outDir: "../src/t1_lab/static", emptyOutDir: true },
});
