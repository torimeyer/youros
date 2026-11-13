// @vitest-environment node
import fs from 'node:fs'
import http from 'node:http'
import type { AddressInfo } from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { createServer } from 'vite'
import type { ViteDevServer } from 'vite'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  bindDynamicProxyTarget,
  readActiveBackendPort,
  resetActiveBackendPortCache,
  resolveActiveBackendTarget,
} from './viteProxyTarget'

function atomicWritePort(filePath: string, value: string): void {
  const tmpPath = `${filePath}.tmp.${process.pid}.${Date.now()}`
  fs.writeFileSync(tmpPath, value, 'utf8')
  fs.renameSync(tmpPath, filePath)
}

async function startUpstreamServer(name: 'blue' | 'green'): Promise<{
  server: http.Server
  port: number
  close: () => Promise<void>
}> {
  const server = http.createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ upstream: name, url: req.url, method: req.method }))
  })

  server.on('upgrade', (req, socket) => {
    socket.write(
      'HTTP/1.1 101 Switching Protocols\r\n' +
        'Upgrade: websocket\r\n' +
        'Connection: Upgrade\r\n' +
        `X-Upstream: ${name}\r\n` +
        '\r\n',
    )
    socket.write(`ws:${name}:${req.url}`)
    socket.end()
  })

  await new Promise<void>((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => resolve())
  })

  const port = (server.address() as AddressInfo).port
  return {
    server,
    port,
    close: () =>
      new Promise<void>((resolve) => {
        server.close(() => resolve())
      }),
  }
}

function httpGetJson(
  port: number,
  reqPath: string,
  method: 'GET' | 'POST' = 'GET',
): Promise<{ statusCode: number; body: { upstream: string; url: string; method: string } }> {
  return new Promise((resolve, reject) => {
    const req = http.request(
      {
        hostname: '127.0.0.1',
        port,
        path: reqPath,
        method,
      },
      (res) => {
        let raw = ''
        res.setEncoding('utf8')
        res.on('data', (chunk) => {
          raw += chunk
        })
        res.on('end', () => {
          try {
            resolve({
              statusCode: res.statusCode ?? 0,
              body: JSON.parse(raw),
            })
          } catch (err) {
            reject(new Error(`Invalid JSON from ${reqPath}: ${raw} (${String(err)})`))
          }
        })
      },
    )
    req.on('error', reject)
    req.end()
  })
}

function rawUpgradeProbe(port: number, reqPath: string): Promise<{ statusCode: number; payload: string }> {
  return new Promise((resolve, reject) => {
    const req = http.request({
      hostname: '127.0.0.1',
      port,
      path: reqPath,
      method: 'GET',
      headers: {
        Connection: 'Upgrade',
        Upgrade: 'websocket',
        'Sec-WebSocket-Version': '13',
        'Sec-WebSocket-Key': 'dGhlIHNhbXBsZSBub25jZQ==',
      },
    })
    req.on('upgrade', (res, socket, head) => {
      let data = head.toString('utf8')
      socket.setEncoding('utf8')
      socket.on('data', (chunk) => {
        data += chunk
      })
      socket.on('end', () => {
        resolve({ statusCode: res.statusCode ?? 0, payload: data })
      })
      socket.on('error', reject)
    })
    req.on('response', (res) => {
      reject(new Error(`Expected 101 upgrade, got HTTP ${res.statusCode}`))
    })
    req.on('error', reject)
    req.end()
  })
}

describe('viteProxyTarget', () => {
  let tmpDir: string

  beforeEach(() => {
    resetActiveBackendPortCache()
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'youros-vite-proxy-test-'))
  })

  afterEach(() => {
    vi.restoreAllMocks()
    resetActiveBackendPortCache()
    fs.rmSync(tmpDir, { recursive: true, force: true })
  })

  it('defaults to port 8000 when active_backend_port is missing, empty, or invalid', () => {
    const missingFile = path.join(tmpDir, 'active_backend_port')
    expect(readActiveBackendPort(missingFile, { ttlMs: 0 })).toBe(8000)

    for (const bad of ['', '   \n', 'abc', '80', '1023', '65536', '8000.5', '-8000', '8000extra']) {
      atomicWritePort(missingFile, bad)
      expect(readActiveBackendPort(missingFile, { ttlMs: 0 })).toBe(8000)
    }

    atomicWritePort(missingFile, '  8002 \n')
    expect(readActiveBackendPort(missingFile, { ttlMs: 0 })).toBe(8002)
  })

  it('reads YOUROS_HOME/active_backend_port when no explicit file path is passed', () => {
    const prevHome = process.env.YOUROS_HOME
    try {
      process.env.YOUROS_HOME = tmpDir
      atomicWritePort(path.join(tmpDir, 'active_backend_port'), '18902\n')
      expect(readActiveBackendPort()).toBe(18902)
      expect(resolveActiveBackendTarget()).toBe('https://127.0.0.1:18902')
    } finally {
      if (prevHome === undefined) {
        delete process.env.YOUROS_HOME
      } else {
        process.env.YOUROS_HOME = prevHome
      }
    }
  })

  it('caches reads within the 100ms TTL window so burst mounts read disk once', () => {
    const portFile = path.join(tmpDir, 'active_backend_port')
    atomicWritePort(portFile, '8002\n')
    const readSpy = vi.spyOn(fs, 'readFileSync')

    expect(readActiveBackendPort(portFile, { ttlMs: 100, now: 1_000 })).toBe(8002)
    atomicWritePort(portFile, '8000\n')

    for (let offset = 10; offset < 95; offset += 15) {
      expect(readActiveBackendPort(portFile, { ttlMs: 100, now: 1_000 + offset })).toBe(8002)
    }
    expect(readSpy).toHaveBeenCalledTimes(1)

    expect(readActiveBackendPort(portFile, { ttlMs: 100, now: 1_101 })).toBe(8000)
    expect(readSpy).toHaveBeenCalledTimes(2)
  })

  it('dynamically switches real Vite 8 HTTP and WebSocket proxy traffic without restarting Vite', async () => {
    const blue = await startUpstreamServer('blue')
    const green = await startUpstreamServer('green')
    const portFile = path.join(tmpDir, 'active_backend_port')
    atomicWritePort(portFile, `${blue.port}\n`)

    const resolver = () =>
      resolveActiveBackendTarget({
        portFilePath: portFile,
        scheme: 'http',
        ttlMs: 20,
      })

    let viteServer: ViteDevServer | undefined
    try {
      viteServer = await createServer({
        configFile: false,
        root: tmpDir,
        logLevel: 'silent',
        optimizeDeps: { noDiscovery: true },
        server: {
          host: '127.0.0.1',
          port: 0,
          hmr: false,
          ws: false,
          proxy: {
            '^/api/.+/suggest(\\?|$)': {
              target: resolver(),
              changeOrigin: true,
              configure: (proxy, opts) => {
                bindDynamicProxyTarget(proxy, opts, resolver)
              },
            },
            '^/api/ws/': {
              target: resolver(),
              changeOrigin: true,
              ws: true,
              configure: (proxy, opts) => {
                bindDynamicProxyTarget(proxy, opts, resolver)
              },
            },
            '/api': {
              target: resolver(),
              changeOrigin: true,
              ws: true,
              configure: (proxy, opts) => {
                bindDynamicProxyTarget(proxy, opts, resolver)
              },
            },
            '/ws': {
              target: resolver(),
              changeOrigin: true,
              ws: true,
              configure: (proxy, opts) => {
                bindDynamicProxyTarget(proxy, opts, resolver)
              },
            },
          },
        },
      })
      await viteServer.listen()
      const vitePort = (viteServer.httpServer?.address() as AddressInfo).port

      // 1. Initial routing hits blue across all 4 proxy lanes (HTTP + WS)
      const r1 = await httpGetJson(vitePort, '/api/health')
      expect(r1.statusCode).toBe(200)
      expect(r1.body.upstream).toBe('blue')

      const rSuggest1 = await httpGetJson(vitePort, '/api/specs/suggest', 'POST')
      expect(rSuggest1.statusCode).toBe(200)
      expect(rSuggest1.body.upstream).toBe('blue')

      const wsApi1 = await rawUpgradeProbe(vitePort, '/api/ws/agents')
      expect(wsApi1.statusCode).toBe(101)
      expect(wsApi1.payload).toBe('ws:blue:/api/ws/agents')

      const wsRoot1 = await rawUpgradeProbe(vitePort, '/ws/chat')
      expect(wsRoot1.statusCode).toBe(101)
      expect(wsRoot1.payload).toBe('ws:blue:/ws/chat')

      // 2. Cutover active_backend_port to green without restarting Vite
      atomicWritePort(portFile, `${green.port}\n`)
      await new Promise((r) => setTimeout(r, 35))

      const r2 = await httpGetJson(vitePort, '/api/health')
      expect(r2.statusCode).toBe(200)
      expect(r2.body.upstream).toBe('green')

      const rSuggest2 = await httpGetJson(vitePort, '/api/specs/suggest', 'POST')
      expect(rSuggest2.statusCode).toBe(200)
      expect(rSuggest2.body.upstream).toBe('green')

      const wsApi2 = await rawUpgradeProbe(vitePort, '/api/ws/agents')
      expect(wsApi2.statusCode).toBe(101)
      expect(wsApi2.payload).toBe('ws:green:/api/ws/agents')

      const wsRoot2 = await rawUpgradeProbe(vitePort, '/ws/chat')
      expect(wsRoot2.statusCode).toBe(101)
      expect(wsRoot2.payload).toBe('ws:green:/ws/chat')

      // 3. Cutback to blue after primary reload completes
      atomicWritePort(portFile, `${blue.port}\n`)
      await new Promise((r) => setTimeout(r, 35))

      const r3 = await httpGetJson(vitePort, '/api/health')
      expect(r3.statusCode).toBe(200)
      expect(r3.body.upstream).toBe('blue')
    } finally {
      if (viteServer) {
        await viteServer.close()
      }
      await blue.close()
      await green.close()
    }
  })

  it('wires bindDynamicProxyTarget into all four proxy routes in app/vite.config.ts', () => {
    const viteConfigPath = path.resolve(__dirname, '../../vite.config.ts')
    const content = fs.readFileSync(viteConfigPath, 'utf8')
    const matches = content.match(/bindDynamicProxyTarget\s*\(/g) ?? []
    expect(matches.length).toBeGreaterThanOrEqual(4)
  })
})
