import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Tailwind v4 plugs straight into Vite — no tailwind.config.js / postcss needed.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, strictPort: true },
  build: {
    rollupOptions: {
      // pdf.js is ~1 MB on its own; keep it in its own cacheable chunk.
      output: { manualChunks: { pdf: ["react-pdf", "pdfjs-dist"], markdown: ["react-markdown", "remark-gfm"] } },
    },
  },
});
