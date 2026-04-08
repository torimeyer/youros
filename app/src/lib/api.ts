const BASE = '/api'

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

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new ApiError(res.status, text)
  }
  return res.json()
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
  put: <T>(path: string, body: unknown) => request<T>('PUT', path, body),
  patch: <T>(path: string, body: unknown) => request<T>('PATCH', path, body),
  delete: <T>(path: string) => request<T>('DELETE', path),
}
