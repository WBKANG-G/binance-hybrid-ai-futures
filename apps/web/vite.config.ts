import { defineConfig } from 'vitest/config';

export default defineConfig({
  server: { host: '127.0.0.1', port: 5173, strictPort: true,
    proxy: { '/api': process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000' } },
  preview: { host: '127.0.0.1', port: 4173, strictPort: true,
    proxy: { '/api': process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000' } },
  test: { environment: 'jsdom', include: ['src/**/*.test.tsx'] },
});
