import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

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

// Chrome caches HSTS for localhost whenever ANY localhost origin has
// served HTTPS (and the backend on :8000 does). After that, typing
// http://localhost:3010 silently upgrades to https and fails with
// ERR_SSL_PROTOCOL_ERROR if vite is plain HTTP. Serve HTTPS here too
// using the same self-signed cert the backend uses, so the browser
// never has to pick between http/https and there is only one scheme.
const myosDir = path.join(os.homedir(), '.myos')
const keyPath = path.join(myosDir, 'localhost.key')
const certPath = path.join(myosDir, 'localhost.crt')
const httpsConfig = fs.existsSync(keyPath) && fs.existsSync(certPath)
  ? { key: fs.readFileSync(keyPath), cert: fs.readFileSync(certPath) }
  : undefined

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 3010,
    https: httpsConfig,
    // Fail hard instead of silently falling back to 3011 when 3010 is
    // already taken. A silent fallback + a browser tab pointed at 3010
    // is exactly the zombie scenario needle 287 documents. If 3010 is
    // occupied we WANT to know immediately.
    strictPort: true,
    proxy: {
      '/api': {
        // Use 127.0.0.1 instead of localhost. Node resolves localhost
        // to both ::1 (IPv6) and 127.0.0.1 (IPv4), tries IPv6 first.
        // Uvicorn binds IPv4 only, so every proxy request starts with
        // a refused IPv6 attempt that poisons the connection pool and
        // causes intermittent ETIMEDOUT on the IPv4 fallback. Needle 315.
        target: 'https://127.0.0.1:8000',
        secure: false,  // accept self-signed cert
        changeOrigin: true,
        // Also proxy WebSocket upgrade requests under /api (e.g.
        // /api/ws/agents/state). Without ws:true the proxy treats
        // WS handshakes as plain HTTP and the upgrade is rejected.
        ws: true,
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
      '/ws': { target: 'https://127.0.0.1:8000', ws: true, secure: false },
    },
  },
})
