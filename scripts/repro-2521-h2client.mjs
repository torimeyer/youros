#!/usr/bin/env node
// →2521: HTTP/2-accurate reproduction of the vite frontend freeze.
// Uses Node.js http2 client (same protocol as a real browser) to:
//   1. Open an H2 session to vite:3999
//   2. Send N concurrent /api/... streams (all in-flight)
//   3. Kill the stub backend (via signal to child process)
//   4. Poll GET / via a SEPARATE TLS connection — must succeed even though
//      the proxy is in an error cascade on the H2 session.
//
// This isolates the key question: does the H2 proxy error cascade on
// the main session block the event loop so badly that an independent
// new TLS connection can't complete its handshake?
//
// Exit 0 = vite stayed responsive (pass)
// Exit 1 = vite wedged (fail — GET / timed out)
//
// Usage:
//   node scripts/repro-2521-h2client.mjs [VITE_PORT] [CONCURRENT]
//
// VITE_PORT defaults to 3999, CONCURRENT defaults to 50.

import http2 from 'node:http2';
import https from 'node:https';
import os from 'node:os';
import path from 'node:path';
import fs from 'node:fs';

const VITE_PORT = parseInt(process.argv[2] ?? '3999', 10);
const CONCURRENT = parseInt(process.argv[3] ?? '50', 10);
// Optional: PID of the stub backend to kill (simulates backend restart)
const STUB_PID = process.argv[4] ? parseInt(process.argv[4], 10) : null;

const myosDir = path.join(os.homedir(), '.youros');
const tlsOpts = {
  rejectUnauthorized: false,
  ca: fs.existsSync(path.join(myosDir, 'localhost.crt'))
    ? fs.readFileSync(path.join(myosDir, 'localhost.crt'))
    : undefined,
};

async function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

// Make a single GET / request over a fresh HTTPS connection (not the H2 session).
// Returns the response time in ms, or Infinity if it timed out.
function checkStaticFile(timeoutMs = 3000) {
  return new Promise((resolve) => {
    const t0 = Date.now();
    const timer = setTimeout(() => {
      req.destroy();
      resolve({ ok: false, ms: Date.now() - t0, code: 0 });
    }, timeoutMs);

    const req = https.get({
      hostname: '127.0.0.1',
      port: VITE_PORT,
      path: '/',
      ...tlsOpts,
    }, (res) => {
      clearTimeout(timer);
      res.resume();
      resolve({ ok: res.statusCode >= 200 && res.statusCode < 400, ms: Date.now() - t0, code: res.statusCode });
    });
    req.on('error', () => {
      clearTimeout(timer);
      resolve({ ok: false, ms: Date.now() - t0, code: 0 });
    });
  });
}

async function run() {
  // --- Baseline check ---
  const baseline = await checkStaticFile(5000);
  if (!baseline.ok) {
    console.error(`[h2client] SKIP: vite not reachable at :${VITE_PORT} (code=${baseline.code})`);
    process.exit(0);
  }
  console.log(`[h2client] baseline GET /: ${baseline.code} OK (${baseline.ms}ms)`);

  // --- Open HTTP/2 session (like a real browser) ---
  const session = http2.connect(`https://127.0.0.1:${VITE_PORT}`, tlsOpts);
  session.on('error', () => {}); // swallow session errors
  await new Promise((r) => session.on('connect', r));
  console.log(`[h2client] HTTP/2 session connected to :${VITE_PORT}`);

  // --- Fire CONCURRENT proxy requests over H2 session ---
  console.log(`[h2client] Sending ${CONCURRENT} concurrent /api/... streams (will be in-flight when backend dies)...`);
  const streams = [];
  for (let i = 0; i < CONCURRENT; i++) {
    const stream = session.request({ ':path': `/api/test?req=${i}` });
    stream.on('error', () => {}); // individual stream errors are expected
    stream.on('response', () => { stream.resume(); });
    streams.push(stream);
  }

  // Small pause so the connections establish to the stub
  await sleep(400);

  // Kill stub backend (simulating launchctl kickstart of uvicorn)
  if (STUB_PID) {
    console.log(`[h2client] Killing stub backend (pid=${STUB_PID})...`);
    try { process.kill(STUB_PID, 'SIGTERM'); } catch {}
  } else {
    console.log('[h2client] Signaling caller to kill backend (no PID provided)...');
    process.stdout.write('KILL_NOW\n');
  }

  // Give the kill a moment to propagate
  await sleep(200);
  console.log('[h2client] Backend killed. Polling GET / for 20 iterations...');

  // --- Poll GET / via fresh HTTPS connection (not the H2 session) ---
  let pass = 0;
  let fail = 0;
  for (let i = 1; i <= 20; i++) {
    const { ok, ms, code } = await checkStaticFile(3000);
    if (ok) {
      console.log(`[h2client]   poll ${i}: ${code} OK (${ms}ms)`);
      pass++;
    } else {
      console.log(`[h2client]   poll ${i}: ${code} WEDGED (${ms}ms) <-- event loop blocked`);
      fail++;
    }
    await sleep(250);
  }

  // Clean up H2 session
  try { session.close(); } catch {}
  for (const s of streams) { try { s.close(); } catch {} }

  console.log(`\n[h2client] Results: ${pass} OK, ${fail} WEDGED`);
  if (fail > 0) {
    console.log('[h2client] FAIL: vite wedged during H2 proxy error cascade');
    process.exit(1);
  }
  console.log('[h2client] PASS: vite stayed responsive through H2 proxy error cascade');
  process.exit(0);
}

run().catch((err) => {
  console.error('[h2client] Unexpected error:', err);
  process.exit(1);
});
