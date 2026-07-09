import path from 'path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const BACKEND_URL = 'http://localhost:8000'
const BACKEND_WS = 'ws://localhost:8000'

// Proxy all backend routes so that the Vite dev-server (port 3000/5173)
// forwards them to the FastAPI backend (port 8000).
// Docker overrides ports via docker-compose.yml — these defaults are for
// local `npm run dev` + `uvicorn` development only.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    proxy: {
      // ---- SSE streaming endpoint ----
      // MUST disable buffering + compression, otherwise Vite's http-proxy
      // buffers the entire SSE response and delivers it all at once.
      '/api/v1/chat/stream': {
        target: BACKEND_URL,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            proxyRes.headers['cache-control'] = 'no-cache, no-store, must-revalidate'
            proxyRes.headers['content-encoding'] = 'identity'
            proxyRes.headers['x-accel-buffering'] = 'no'
          })
        },
      },

      // ---- All other /api/* routes ----
      '/api': {
        target: BACKEND_URL,
        changeOrigin: true,
      },

      // ---- WebSocket (monitor, realtime, etc.) ----
      // With this proxy, frontend WS connections to ws://localhost:3000/ws/*
      // are forwarded to the backend. This means api.ts WS_URL using
      // window.location.host works correctly in dev — no separate WS_URL needed.
      '/ws': {
        target: BACKEND_WS,
        ws: true,
        changeOrigin: true,
      },

      // ---- OpenAI-compatible endpoint ----
      '/v1': {
        target: BACKEND_URL,
        changeOrigin: true,
      },

      // ---- Health-check endpoint ----
      '/health': {
        target: BACKEND_URL,
        changeOrigin: true,
      },
    },
  },
})
