import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Development only: Vite proxies /api to the local API (`npm run dev:api`, port 8788).
// In production the Netlify edge function proxies /api to Railway; the browser
// bundle itself always calls the same-origin relative path /api (checked by
// scripts/check-dist.mjs), so no production request can target localhost.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: process.env.DESK_DEV_API_ORIGIN ?? 'http://localhost:8788', changeOrigin: false } },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    target: 'es2022',
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom'],
          charts: ['lightweight-charts'],
          query: ['@tanstack/react-query'],
        },
      },
    },
  },
});
