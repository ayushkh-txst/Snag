import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // In prod, VITE_API_BASE points at the deployed API origin (client.ts
    // reads it directly); in dev the SPA and API run as separate processes
    // (`npm run dev` + `uv run snag serve`), so requests to /api are
    // proxied to the local backend instead.
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
