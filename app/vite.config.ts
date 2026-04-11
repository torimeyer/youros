import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Needle 287 (round 3): when the backend restarts, vite's default
// proxy agent keeps dead keep-alive sockets in its pool and routes
// new requests to them, causing every /api/* fetch to hang for 30
// seconds before timing out. Tori saw this as red status dots and
// hung Tasks/Agents/Briefing pages every time I restarted uvicorn.
//
// I tried setting ``agent: new http.Agent({ keepAlive: false })`` at
// the top level of the proxy config but vite did not actually honor
// it. The reliable fix is to force the proxied REQUEST itself to
// send ``Connection: close``, which tells both sides to drop the
// socket after the response and prevents the proxy from ever
// parking it in a keep-alive pool. Zero-byte overhead per request.

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 3010,
    // Fail hard instead of silently falling back to 3011 when 3010 is
    // already taken. A silent fallback + a browser tab pointed at 3010
    // is exactly the zombie scenario needle 287 documents. If 3010 is
    // occupied we WANT to know immediately.
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        configure: (proxy) => {
          // Force no keep-alive on every proxied HTTP request so a
          // backend restart can never strand dead sockets in the
          // proxy's connection pool. See needle 287.
          proxy.on('proxyReq', (proxyReq) => {
            proxyReq.setHeader('Connection', 'close')
          })
          proxy.on('error', (err, _req, res) => {
            // Make upstream failures a fast 502 instead of a hung
            // socket so the frontend fetch fails quickly and the
            // sidebar health check can flip red in 2s (needle 286)
            // instead of waiting on the default 30 second client
            // timeout.
            // eslint-disable-next-line no-console
            console.error('[vite proxy /api] error:', err?.message || err)
            try {
              if (res && 'writeHead' in res && !res.headersSent) {
                res.writeHead(502, { 'Content-Type': 'text/plain' })
                res.end('Upstream unavailable')
              }
            } catch {
              // nothing to do, response is probably already torn down
            }
          })
        },
      },
      '/ws': { target: 'http://localhost:8000', ws: true },
    },
  },
})
