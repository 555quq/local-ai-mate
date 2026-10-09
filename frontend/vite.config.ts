import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    restoreMocks: true,
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    // The browser stays same-origin with Vite; only this local development
    // proxy reaches the loopback FastAPI service. `changeOrigin: false` keeps
    // the browser's exact Origin so the backend's Origin guard remains active.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: false,
      },
    },
  },
})
