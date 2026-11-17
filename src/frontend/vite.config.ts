import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

// The FastAPI backend (run-backend-dev.sh). Proxying it makes the page and API one origin in
// development too, just like behind the reverse proxy when deployed, so the frontend only ever uses
// relative URLs. Vite keeps the browser's Host and Origin headers, so the backend's origin check still
// sees the dev server's origin (list it in WARU_ALLOWED_ORIGINS).
const api_target = 'http://127.0.0.1:3002'

// https://vite.dev/config/
export default defineConfig({
  server: {
    host: '0.0.0.0',
    port: 3001,
    allowedHosts: true,
    proxy: {
      '/auth': api_target,
      '/papi': api_target,
      '/ws': { target: api_target, ws: true },
    },
  },
  plugins: [
    vue(),
    vueDevTools(),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
})
