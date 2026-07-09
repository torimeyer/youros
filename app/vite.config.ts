import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import fs from 'node:fs'
import https from 'node:https'
import os from 'node:os'
import path from 'node:path'

// Needle 287 (round 3): when the backend restarts, vite's default
// proxy agent keeps dead keep-alive sockets in its pool and routes
// new requests to them, causing every /api/* fetch to hang for 30
// seconds before timing out. Tori saw this as red status dots and
// hung Tasks/Agents/Briefing pages every time I restarted uvicorn.
//
// Previous fix (now superseded by →1431): forced Connection:close on
// every proxied request so sockets were never pooled. That worked but
// caused a new problem in Vite 8 — see backendAgent comment below.
// Current fix: pooled https.Agent with keepAlive + error handler that
// converts ECONNRESET (dead socket) to a fast 502.

// Chrome caches HSTS for localhost whenever ANY localhost origin has
// served HTTPS (and the backend on :8000 does). After that, typing
// http://localhost:3010 silently upgrades to https and fails with
// ERR_SSL_PROTOCOL_ERROR if vite is plain HTTP. Serve HTTPS here too
// using the same self-signed cert the backend uses, so the browser
// never has to pick between http/https and there is only one scheme.
const myosDir = path.join(os.homedir(), '.youros')
const keyPath = path.join(myosDir, 'localhost.key')
const certPath = path.join(myosDir, 'localhost.crt')
const httpsConfig = fs.existsSync(keyPath) && fs.existsSync(certPath)
  ? { key: fs.readFileSync(keyPath), cert: fs.readFileSync(certPath) }
  : undefined

// →1431 Vite 8 uses http2.createSecureServer which enables HTTP/2 multiplexing.
// The browser can open 50+ concurrent streams over one connection; each becomes
// a separate proxied HTTP/1.1 request. http-proxy-3 defaults to agent:false (no
// connection pooling), so every proxied request performs a fresh TLS handshake
// to https://127.0.0.1:8000. Under dashboard-mount bursts, simultaneous backend
// TLS handshakes saturate Node's event loop and incoming browser TLS ClientHellos
// get no ServerHello — the wedge.
//
// Fix: share a persistent pooled https.Agent across all proxied requests.
// keepAlive:true     — reuse backend TLS connections; amortise the handshake cost
// keepAliveMsecs     — TCP keep-alive probe interval; dead sockets are detected fast
// maxSockets:10      — cap concurrent backend TLS connections; protects the event loop
// rejectUnauthorized — accept the self-signed localhost cert (same as secure:false)
//
// Needle 287 (dead-socket hangs after backend restart) is still covered: when the
// backend restarts the OS closes the socket, ECONNRESET fires immediately, and the
// existing proxy error handler returns a fast 502. Connection:close is no longer needed.
const backendAgent = new https.Agent({
  keepAlive: true,
  keepAliveMsecs: 5_000,
  maxSockets: 10,
  rejectUnauthorized: false,
})

// →2521 console.error writes synchronously when vite's stderr is a TTY.
// When 50+ ECONNRESET proxy errors fire simultaneously (backend restart),
// the 4 KB PTY kernel buffer fills; subsequent writes block until the
// terminal emulator reads. Under heavy background load (pytest + agents
// competing for CPU), reads are slow — each blocked write holds the Node.js
// event loop, new TLS handshakes queue up, and curl times out (exit 28,
// http_code 000) even though the vite process stays alive and listening.
// Fix: one log line per 5-second burst window; the suppressed count prints
// at the start of the next window so nothing is silently discarded.
let _proxyLogTs = 0
let _proxyLogBurst = 0
function proxyErrLog(prefix: string, err: unknown) {
  const now = Date.now()
  if (now - _proxyLogTs > 5_000) {
    if (_proxyLogBurst > 1) {
      // eslint-disable-next-line no-console
      console.error(`${prefix} (${_proxyLogBurst - 1} similar suppressed)`)
    }
    // eslint-disable-next-line no-console
    console.error(prefix, (err as Error)?.message || err)
    _proxyLogTs = now
    _proxyLogBurst = 1
  } else {
    _proxyLogBurst++
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // →1798 Vite 8 blank-page fix: three-part defence against CJS chunk hash
  // divergence.
  //
  // holdUntilCrawlEnd:true — wait for the full import-graph crawl before
  // emitting the pre-bundled chunk so all modules receive the same
  // browserHash in one shot. The Vite 5-era false value (needle 1145) was
  // needed because the 50 ms crawl-end idle timer never expired under
  // request bursts; Vite 8 replaced that timer with an accurate crawl
  // barrier, so true is now safe and correct.
  //
  // ignoreOutdatedRequests:true — when a browser tab already loaded a chunk
  // with an old hash and then the optimizer runs again (e.g. on first lazy-
  // load), Vite 8 would normally throw a 504 "Outdated request" for the
  // stale URL. With this flag set, Vite serves the fresh module instead of
  // erroring, preventing the blank-page reload loop.
  optimizeDeps: {
    holdUntilCrawlEnd: true,
    ignoreOutdatedRequests: true,
    // Every breakroom game is lazy()-loaded. Without React pinned into the
    // initial pre-bundle, the first lazy game chunk can trigger a fresh dep
    // re-optimization that gives `react` a new browserHash while the already
    // loaded `react-dom` keeps the old one. Two React instances in one page
    // means the hook dispatcher is null, so the game crashes on its first
    // hook with "Cannot read properties of null (reading 'useState')".
    // Pinning these (plus resolve.dedupe below) keeps one React per session.
    include: [
      'react',
      'react-dom',
      'react-dom/client',
      'react/jsx-runtime',
      'react/jsx-dev-runtime',
    ],
  },
  // Force a single physical copy of React to resolve regardless of how a
  // module imports it. Belt-and-suspenders with optimizeDeps.include above.
  resolve: {
    dedupe: ['react', 'react-dom'],
  },
  server: {
    port: 3010,
    // Force IPv4 binding. Without this vite defaults to ::1 (IPv6-only)
    // which enters a stuck state after idle periods and refuses all
    // connections — the "wedge". Needle 1138/1142.
    host: '127.0.0.1',
    https: httpsConfig,
    // Fail hard instead of silently falling back to 3011 when 3010 is
    // already taken. A silent fallback + a browser tab pointed at 3010
    // is exactly the zombie scenario needle 287 documents. If 3010 is
    // occupied we WANT to know immediately.
    strictPort: true,
    proxy: {
      // →2626: AI "suggest" endpoints (spec clarity, task clarify, spec
      // wizard) call Claude and legitimately take 5-30s before the FIRST
      // byte arrives — measured 5.3-6.4s for a healthy call on an idle
      // machine. http-proxy's proxyTimeout is a socket-inactivity timer and
      // FastAPI sends nothing until the handler returns, so the blanket 5s
      // timeout on '/api' below killed every one of those requests with a
      // 502 "Upstream unavailable". Give AI routes a 120s window; the
      // frontend's own 30s REQUEST_TIMEOUT_MS stays the effective UI
      // ceiling. Keys starting with "^" are RegExps in vite, and this entry
      // must sit ABOVE '/api' because vite picks the first matching key in
      // insertion order.
      '^/api/.+/suggest(\\?|$)': {
        target: 'https://127.0.0.1:8000',
        secure: false,
        changeOrigin: true,
        proxyTimeout: 120_000,
        agent: backendAgent,
        configure: (proxy) => {
          proxy.on('error', (err, _req, res) => {
            // Dead sockets (backend restart) still fail fast: ECONNRESET
            // fires immediately regardless of proxyTimeout.
            proxyErrLog('[vite proxy /api ai-suggest] error:', err)
            try {
              if (res && 'writeHead' in res && !res.headersSent) {
                res.writeHead(502, { 'Content-Type': 'text/plain' })
                res.end('Upstream unavailable')
              }
            } catch {
              // response already torn down
            }
          })
        },
      },
      '/api': {
        // Use 127.0.0.1 instead of localhost. Node resolves localhost
        // to both ::1 (IPv6) and 127.0.0.1 (IPv4), tries IPv6 first.
        // Uvicorn binds IPv4 only, so every proxy request starts with
        // a refused IPv6 attempt that poisons the connection pool and
        // causes intermittent ETIMEDOUT on the IPv4 fallback. Needle 315.
        target: 'https://127.0.0.1:8000',
        secure: false,  // accept self-signed cert (backendAgent also sets rejectUnauthorized:false)
        changeOrigin: true,
        proxyTimeout: 5000,
        // Also proxy WebSocket upgrade requests under /api (e.g.
        // /api/ws/agents/state). Without ws:true the proxy treats
        // WS handshakes as plain HTTP and the upgrade is rejected.
        ws: true,
        // Pooled TLS agent — see backendAgent comment above (→1431).
        agent: backendAgent,
        configure: (proxy) => {
          proxy.on('error', (err, _req, res) => {
            // Make upstream failures a fast 502 instead of a hung
            // socket so the frontend fetch fails quickly and the
            // sidebar health check can flip red in 2s (needle 286)
            // instead of waiting on the default 30 second client
            // timeout.
            proxyErrLog('[vite proxy /api] error:', err)
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
      // →1631: apply the same backendAgent + changeOrigin + error-handler
      // that was added to /api in →1431. Without agent: backendAgent, each
      // of the three simultaneous WS upgrade handshakes (dashboard/data,
      // notifications, calendar/events) makes a fresh TLS negotiation to the
      // backend. Under HTTP/2 multiplexing that saturates Node's event loop
      // and all three WS connections fail. changeOrigin fixes the Host header
      // sent to uvicorn (was localhost:3010, must be localhost:8000).
      '/ws': {
        target: 'https://127.0.0.1:8000',
        ws: true,
        secure: false,
        changeOrigin: true,
        proxyTimeout: 5000,
        agent: backendAgent,
        configure: (proxy) => {
          proxy.on('error', (err, _req, res) => {
            proxyErrLog('[vite proxy /ws] error:', err)
            try {
              if (res && 'writeHead' in res && !res.headersSent) {
                res.writeHead(502, { 'Content-Type': 'text/plain' })
                res.end('Upstream unavailable')
              }
            } catch {
              // response already torn down
            }
          })
        },
      },
    },
  },
})
