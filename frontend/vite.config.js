import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// The legacy prototype occupies 5173. ClaimIQ Enterprise uses 5273 so the two
// can run side by side during the migration.
const DEV_PORT = 5273
const API_TARGET = process.env.CLAIMIQ_API_TARGET || 'http://localhost:8100'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: DEV_PORT,
    strictPort: true,
    proxy: {
      // Same-origin in dev so session cookies and the CSRF cookie behave
      // exactly as they do behind the production reverse proxy.
      '/api': {
        target: API_TARGET,
        changeOrigin: false,
      },
    },
  },
  preview: {
    port: DEV_PORT,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    chunkSizeWarningLimit: 900,
  },
})
