/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import basicSsl from '@vitejs/plugin-basic-ssl'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    ...(process.env.VITE_HTTPS === 'true' ? [basicSsl()] : []),
  ],
  server: {
    proxy: {
      '/api': {
        target: process.env.VITE_PROXY_TARGET || 'http://localhost:8000',
        changeOrigin: true,
        secure: false, // allow self-signed certs when backend uses HTTPS
      },
    },
  },
  // Unit tests only; Playwright owns tests/*.spec.ts (npm run test:ui).
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'jsdom',
  },
})
