import { defineConfig, type ProxyOptions } from 'vite';
import react from '@vitejs/plugin-react';
const target = process.env.CAIRN_DEV_API || 'http://127.0.0.1:18765';
const proxy: ProxyOptions = {
  target, changeOrigin: true,
  configure(server) {
    server.on('proxyReq', (forwarded, request) => {
      // Translate only same-origin development requests; keep foreign origins intact
      // so the server's CSRF check still rejects them.
      const origin = request.headers.origin;
      try {
        if (origin && new URL(origin).host === request.headers.host) {
          forwarded.setHeader('origin', new URL(target).origin);
        }
      } catch { /* Forward malformed origins unchanged for server rejection. */ }
    });
  },
};
export default defineConfig({
  plugins: [react()],
  build: { outDir: '../cairn/ui_static', emptyOutDir: true },
  server: { host: '127.0.0.1', proxy: {'/v1': proxy, '/ui-api': proxy} },
});
