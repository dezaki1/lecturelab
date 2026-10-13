import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev: `npm run dev` proxies /api to the backend (uv run lecturelab serve --no-browser).
// `npm run dev:preview` proxies to the dev instance on 8766 instead.
export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: {
    // "preview" mode (npm run dev:preview): talk to the dev instance on 8766 (data/dev), so
    // the UI can be tried without touching the real app or its built frontend.
    port: mode === 'preview' ? 5174 : 5173,
    proxy: {
      '/api': { target: `http://127.0.0.1:${mode === 'preview' ? 8766 : 8765}`, changeOrigin: false },
    },
  },
}))
