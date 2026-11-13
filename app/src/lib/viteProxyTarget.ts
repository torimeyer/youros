/// <reference types="node" />
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

export const DEFAULT_BACKEND_PORT = 8000
export const DEFAULT_PORT_CACHE_TTL_MS = 100

export interface ReadActivePortOptions {
  ttlMs?: number
  now?: number
  defaultPort?: number
}

export interface ResolveBackendTargetOptions extends ReadActivePortOptions {
  portFilePath?: string
  scheme?: 'http' | 'https'
  host?: string
}

interface CacheEntry {
  port: number
  readAt: number
}

const portCache = new Map<string, CacheEntry>()

export function resetActiveBackendPortCache(): void {
  portCache.clear()
}

export function defaultActiveBackendPortPath(): string {
  const dataRoot =
    process.env.YOUROS_HOME ||
    process.env.MYOS_DIR ||
    path.join(os.homedir(), '.youros')
  return path.join(dataRoot, 'active_backend_port')
}

function parseValidPort(raw: string, fallback: number): number {
  const trimmed = raw.trim()
  if (!/^\d+$/.test(trimmed)) {
    return fallback
  }
  const parsed = Number(trimmed)
  if (!Number.isInteger(parsed) || parsed < 1024 || parsed > 65535) {
    return fallback
  }
  return parsed
}

export function readActiveBackendPort(
  portFilePath?: string,
  opts?: ReadActivePortOptions,
): number {
  const resolvedPath = portFilePath ?? defaultActiveBackendPortPath()
  const ttlMs = opts?.ttlMs ?? DEFAULT_PORT_CACHE_TTL_MS
  const now = opts?.now ?? Date.now()
  const fallback = opts?.defaultPort ?? DEFAULT_BACKEND_PORT

  if (ttlMs > 0) {
    const hit = portCache.get(resolvedPath)
    if (hit !== undefined && now - hit.readAt < ttlMs) {
      return hit.port
    }
  }

  let port = fallback
  try {
    const raw = fs.readFileSync(resolvedPath, 'utf8')
    port = parseValidPort(raw, fallback)
  } catch {
    port = fallback
  }

  if (portCache.size > 64) {
    portCache.clear()
  }
  portCache.set(resolvedPath, { port, readAt: now })
  return port
}

export function resolveActiveBackendTarget(
  opts?: ResolveBackendTargetOptions,
): string {
  const scheme = opts?.scheme ?? 'https'
  const host = opts?.host ?? '127.0.0.1'
  const port = readActiveBackendPort(opts?.portFilePath, opts)
  return `${scheme}://${host}:${port}`
}

export function bindDynamicProxyTarget<
  TOpts extends {
    target?: unknown
    bypass?: (...args: never[]) => unknown
  },
>(
  proxy: object,
  opts: TOpts,
  targetResolver: () => string = resolveActiveBackendTarget,
): void {
  const syncTarget = (runtimeOpts?: { target?: unknown }): string => {
    const nextTarget = targetResolver()
    opts.target = nextTarget
    const proxyInternal = proxy as { options?: { target?: unknown } }
    if (proxyInternal.options && typeof proxyInternal.options === 'object') {
      proxyInternal.options.target = nextTarget
    }
    if (runtimeOpts && typeof runtimeOpts === 'object') {
      runtimeOpts.target = nextTarget
    }
    return nextTarget
  }

  syncTarget()

  const previousBypass = opts.bypass as
    | ((req: unknown, res: unknown, runtimeOpts: unknown) => unknown)
    | undefined

  ;(
    opts as {
      bypass?: (req: unknown, res: unknown, runtimeOpts: unknown) => unknown
    }
  ).bypass = (req, res, runtimeOpts) => {
    syncTarget(runtimeOpts as { target?: unknown } | undefined)
    return previousBypass ? previousBypass(req, res, runtimeOpts) : undefined
  }

  const proxyMethods = proxy as {
    web?: (...args: unknown[]) => unknown
    ws?: (...args: unknown[]) => unknown
  }
  if (typeof proxyMethods.web === 'function') {
    const origWeb = proxyMethods.web.bind(proxy)
    proxyMethods.web = (...args: unknown[]) => {
      syncTarget()
      return origWeb(...args)
    }
  }
  if (typeof proxyMethods.ws === 'function') {
    const origWs = proxyMethods.ws.bind(proxy)
    proxyMethods.ws = (...args: unknown[]) => {
      syncTarget()
      return origWs(...args)
    }
  }
}
