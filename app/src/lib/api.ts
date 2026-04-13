const BASE = '/api'

// Hard upper bound on how long any API call can hang before the UI
// treats it as a failure. A stuck fetch (proxy timeout, dropped socket,
// buggy server) must never pin a button in its "sending" state forever.
// Exported so tests can shorten it via Object.defineProperty on a mock
// or override the module entirely.
export const REQUEST_TIMEOUT_MS = 30000

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

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const res = await fetch(`${BASE}${path}`, {
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
    return await res.json()
  } catch (err) {
    // AbortController.abort() causes fetch to reject with a DOMException
    // whose name is "AbortError". Convert it to our own typed error so
    // the UI can detect timeouts specifically and surface plain language.
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new ApiTimeoutError(REQUEST_TIMEOUT_MS)
    }
    throw err
  } finally {
    clearTimeout(timeoutId)
  }
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
  put: <T>(path: string, body: unknown) => request<T>('PUT', path, body),
  patch: <T>(path: string, body: unknown) => request<T>('PATCH', path, body),
  delete: <T>(path: string) => request<T>('DELETE', path),
}
