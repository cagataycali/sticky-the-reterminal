import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Build into ./dist (served by ../server.py). Dev proxies to :8787.
export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true },
  server: {
    proxy: {
      '/api': 'http://localhost:8787',
      '/auth': 'http://localhost:8787',
    },
  },
})
