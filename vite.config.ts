import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    proxy: {
      "/gpt-api": {
        target: "https://lynote.xyz",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist-web",
    emptyOutDir: true,
  },
});
