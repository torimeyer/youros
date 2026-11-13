import { bumpAgents, bumpTasks, bumpSpecs, bumpCalendar } from './sidebarBus'
import { recordSettingsWriteStart, recordSettingsWriteSettled } from './settingsWriteBarrier'

const BASE = '/api'

// Hard upper bound on how long any API call can hang before the UI
// treats it as a failure. A stuck fetch (proxy timeout, dropped socket,
// buggy server) must never pin a button in its "sending" state forever.
// Exported so tests can shorten it via Object.defineProperty on a mock
// or override the module entirely.
export const REQUEST_TIMEOUT_MS = 30000

// Default transient retry policy for idempotent GET requests in the browser
// so brief backend restarts (502/503/504/connection reset) self-heal without
// flashing empty states (→3109).
export const DEFAULT_GET_RETRIES = 4
export const DEFAULT_GET_RETRY_DELAY_MS = 350

const TRANSIENT_HTTP_STATUSES = new Set([502, 503, 504])

function isRunningUnderVitest(): boolean {
  const proc = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process
  return Boolean(proc?.env?.VITEST)
}

// Pure helper resolving the default retry count for an HTTP verb.
// Returns DEFAULT_GET_RETRIES (4) for GET outside Vitest, and 0 for mutating
// verbs or inside Vitest so unit tests that mock fetch errors or fake timers
// stay deterministic unless they opt in explicitly.
export function resolveDefaultRetries(
  method: string,
  isVitestEnv: boolean = isRunningUnderVitest(),
): number {
  if (isVitestEnv) return 0
  return method.toUpperCase() === 'GET' ? DEFAULT_GET_RETRIES : 0
}

// Options for api.get (and other verbs). Pass retryOn502 > 0 to silently
// retry on 502 responses and network errors (e.g. during backend warmup).
// retryDelayMs controls the wait between attempts.
// timeoutMs overrides the global REQUEST_TIMEOUT_MS for a single call —
// useful for hydration paths where a fast fallback is preferable to a
// 30-second hang.
export interface RequestOptions {
  retryOn502?: number
  retryDelayMs?: number
  timeoutMs?: number
}

// Custom error that preserves the parsed JSON detail from FastAPI responses
// so UI code can check things like err.response.data.detail.api_not_enabled.
export class ApiError extends Error {
  status: number
  response: { status: number; data: { detail?: unknown } }
  constructor(status: number, text: string) {
    let parsed: { detail?: unknown } = { detail: text }
    try {
      parsed = JSON.parse(text)
    } catch {
      // Not JSON, keep the raw text as the detail.
    }
    const message =
      typeof parsed.detail === 'string'
        ? parsed.detail
        : typeof parsed.detail === 'object' && parsed.detail && 'message' in parsed.detail
          ? String((parsed.detail as { message: unknown }).message)
          : text || `HTTP ${status}`
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.response = { status, data: parsed }
  }
}

// Raised when a request exceeds REQUEST_TIMEOUT_MS. UI code can catch
// this specifically to tell the user the server did not respond in
// time, instead of showing a generic network error.
export class ApiTimeoutError extends Error {
  timeoutMs: number
  constructor(timeoutMs: number) {
    super(`Request timed out after ${timeoutMs} ms`)
    this.name = 'ApiTimeoutError'
    this.timeoutMs = timeoutMs
  }
}

// Notify the sidebar bus after a successful write (POST / PUT / PATCH /
// DELETE) that targeted an agent or task endpoint. This lets the Agents
// and Tasks badges refetch within milliseconds of the change instead of
// waiting for the next poll tick.
function notifySidebarOnWrite(method: string, path: string): void {
  if (method === 'GET') return
  // Normalize: strip a leading slash and take the first path segment.
  const seg = path.replace(/^\/+/, '').split(/[/?#]/)[0]
  if (seg === 'agents') {
    bumpAgents()
    return
  }
  if (seg === 'tasks') {
    bumpTasks()
    // Some task mutations also spawn an agent (e.g. POST /tasks/:id/commit
    // runs an agent). Bumping agents too is cheap and covers those cases.
    bumpAgents()
    return
  }
  if (seg === 'specs') {
    bumpSpecs()
    return
  }
  // Any write against a calendar endpoint (event create, delete, sync)
  // refreshes every mounted Calendar subscriber. Covers the chat-driven
  // create_calendar_event path too, because the backend tool handler
  // calls the same POST /calendar/events route... except it does not.
  // The tool bypasses the HTTP layer and writes directly via
  // services.calendar.create_event, so ChatPanel also bumps manually
  // when it sees a successful create_calendar_event tool_result.
  if (seg === 'calendar') {
    bumpCalendar()
  }
}

// Strip an accidental /api prefix from path — BASE already contains /api.
// Logs a warning in dev so callers can fix their paths at the source.
function normalizePath(path: string): string {
  if (path.startsWith('/api/') || path === '/api') {
    if (typeof import.meta !== 'undefined' && (import.meta as any).env?.DEV) {
      console.warn(`[api] Path "${path}" starts with /api which is already in BASE. Remove the /api prefix.`)
    }
    return path.slice(4)
  }
  return path
}

async function requestOnce<T>(method: string, path: string, body?: unknown, timeoutMs = REQUEST_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs)
  const normalizedPath = normalizePath(path)
  try {
    const res = await fetch(`${BASE}${normalizedPath}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
      credentials: 'include',
    })
    if (!res.ok) {
      const text = await res.text()
      throw new ApiError(res.status, text)
    }
    if (res.status === 204) {
      notifySidebarOnWrite(method, normalizedPath)
      return undefined as T
    }
    const data = await res.json()
    notifySidebarOnWrite(method, normalizedPath)
    return data
  } catch (err) {
    // AbortController.abort() causes fetch to reject with a DOMException
    // whose name is "AbortError". Convert it to our own typed error so
    // the UI can detect timeouts specifically and surface plain language.
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new ApiTimeoutError(timeoutMs)
    }
    throw err
  } finally {
    clearTimeout(timeoutId)
  }
}

async function request<T>(method: string, path: string, body?: unknown, options: RequestOptions = {}): Promise<T> {
  const maxRetries =
    options.retryOn502 !== undefined ? options.retryOn502 : resolveDefaultRetries(method)
  const baseDelay =
    options.retryDelayMs !== undefined ? options.retryDelayMs : DEFAULT_GET_RETRY_DELAY_MS
  const timeoutMs = options.timeoutMs ?? REQUEST_TIMEOUT_MS
  let lastErr: unknown
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    if (attempt > 0 && options.retryDelayMs !== 0) {
      const delay = attempt === 1 ? baseDelay : Math.min(baseDelay * attempt, 1200)
      await new Promise<void>((r) => setTimeout(r, delay))
    }
    try {
      return await requestOnce<T>(method, path, body, timeoutMs)
    } catch (err) {
      lastErr = err
      // Only retry transient failures: gateway/restart 502/503/504, raw
      // network errors, and timeouts. A hung dev-proxy socket surfaces as a
      // timeout and a fresh attempt gets a live connection (→2777, →3109).
      // Intentional HTTP errors (4xx, 500) throw immediately on attempt 1.
      const isRetryable =
        (err instanceof ApiError && TRANSIENT_HTTP_STATUSES.has(err.status)) ||
        err instanceof TypeError ||
        err instanceof ApiTimeoutError
      if (!isRetryable || attempt >= maxRetries) throw err
    }
  }
  throw lastErr
}

// Per-attempt timeout for settings saves; see patchWithSettingsBarrier.
export const SETTINGS_SAVE_TIMEOUT_MS = 5000

// →2777: every PATCH /settings records its keys with the write barrier so
// a settings fetch that raced the save cannot overwrite the newer local
// value. Recording lives here, at the single chokepoint every caller
// (store setters, Settings handlers, wizard) already goes through.
function patchWithSettingsBarrier<T>(path: string, body: unknown): Promise<T> {
  if (path !== '/settings' || !body || typeof body !== 'object') {
    return request<T>('PATCH', path, body)
  }
  const keys = Object.keys(body as Record<string, unknown>)
  recordSettingsWriteStart(keys)
  // Short per-attempt timeout: a hung save gives up after 5s and retries
  // on a fresh connection instead of eating the full default timeout
  // before anyone notices (→2777).
  const req = request<T>('PATCH', path, body, { retryOn502: 2, retryDelayMs: 300, timeoutMs: SETTINGS_SAVE_TIMEOUT_MS })
  const settle = () => recordSettingsWriteSettled(keys)
  req.then(settle, settle)
  return req
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) => request<T>('GET', path, undefined, options),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) => request<T>('POST', path, body, options),
  put: <T>(path: string, body: unknown) => request<T>('PUT', path, body),
  patch: <T>(path: string, body: unknown) => patchWithSettingsBarrier<T>(path, body),
  delete: <T>(path: string) => request<T>('DELETE', path),
}
