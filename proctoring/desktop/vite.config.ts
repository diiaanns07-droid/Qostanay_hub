// Renderer build (owner: A01; renderer code: A07). No CDN/runtime downloads: everything is bundled.
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  root: here("./renderer"),
  base: "./", // loaded from file:// by Electron
  plugins: [react()],
  resolve: { alias: { "@contracts": here("../contracts/ts") } },
  build: {
    outDir: here("./dist/renderer"),
    emptyOutDir: true,
    sourcemap: true,
    target: "es2022",
  },
  server: { host: "127.0.0.1", port: 5173, strictPort: true },
  preview: { host: "127.0.0.1", port: 4173, strictPort: true },
});
