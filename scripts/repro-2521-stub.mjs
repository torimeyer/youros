#!/usr/bin/env node
// Stub HTTPS backend for →2521 reproduction.
// Accepts connections on PORT (argv[2], default 8999) and responds after
// DELAY ms (argv[3], default 100).  Write "READY port=PORT\n" on stdout
// when listening so the test script can detect readiness.
import https from 'node:https';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const port = parseInt(process.argv[2] ?? '8999', 10);
const delay = parseInt(process.argv[3] ?? '100', 10);

const myosDir = path.join(os.homedir(), '.youros');

const server = https.createServer({
  key: fs.readFileSync(path.join(myosDir, 'localhost.key')),
  cert: fs.readFileSync(path.join(myosDir, 'localhost.crt')),
  rejectUnauthorized: false,
}, (req, res) => {
  // Health check responds immediately so the test harness can detect readiness
  if (req.url === '/health' || req.url === '/ping') {
    res.writeHead(200, { 'Content-Type': 'text/plain' });
    res.end('ok');
    return;
  }
  // All other paths use the configured delay (keeps requests in-flight at kill time)
  setTimeout(() => {
    if (res.destroyed) return;
    res.writeHead(200, {
      'Content-Type': 'application/json',
      'Connection': 'keep-alive',
    });
    res.end(JSON.stringify({ ok: true, url: req.url, port }));
  }, delay);
});

server.on('error', (err) => {
  process.stderr.write(`stub error: ${err.message}\n`);
  process.exit(1);
});

server.listen(port, '127.0.0.1', () => {
  process.stdout.write(`READY port=${port}\n`);
});
