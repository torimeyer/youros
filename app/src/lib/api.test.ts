import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { api, ApiTimeoutError } from './api'

describe('api client', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  function mockFetch(body: unknown, ok = true, status = 200) {
    const fn = vi.fn().mockResolvedValue({
      ok,
      status,
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(typeof body === 'string' ? body : JSON.stringify(body)),
    })
    global.fetch = fn
    return fn
  }

  describe('api.get', () => {
    it('calls fetch with correct URL and GET method', async () => {
      const fetchMock = mockFetch({ data: 'hello' })
      await api.get('/tasks')

      expect(fetchMock).toHaveBeenCalledWith(
        '/api/tasks',
        expect.objectContaining({
          method: 'GET',
          headers: undefined,
          body: undefined,
        }),
      )
    })

    it('returns parsed JSON response', async () => {
      mockFetch({ tasks: [{ id: '1', title: 'Test' }] })
      const result = await api.get<{ tasks: { id: string; title: string }[] }>('/tasks')

      expect(result).toEqual({ tasks: [{ id: '1', title: 'Test' }] })
    })

    it('does not send Content-Type header for GET requests', async () => {
      const fetchMock = mockFetch({})
      await api.get('/tasks')

      const callArgs = fetchMock.mock.calls[0][1]
      expect(callArgs.headers).toBeUndefined()
    })
  })

  describe('api.post', () => {
    it('sends body as JSON with correct Content-Type header', async () => {
      const fetchMock = mockFetch({ ok: true })
      const payload = { title: 'New task', priority: 'P1' }
      await api.post('/tasks', payload)

      expect(fetchMock).toHaveBeenCalledWith(
        '/api/tasks',
        expect.objectContaining({
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        }),
      )
    })

    it('sends POST without body when no body provided', async () => {
      const fetchMock = mockFetch({ ok: true })
      await api.post('/tasks/1/close')

      expect(fetchMock).toHaveBeenCalledWith(
        '/api/tasks/1/close',
        expect.objectContaining({
          method: 'POST',
          headers: undefined,
          body: undefined,
        }),
      )
    })

    it('returns parsed JSON response', async () => {
      mockFetch({ id: '42', title: 'Created' })
      const result = await api.post<{ id: string; title: string }>('/tasks', { title: 'Created' })

      expect(result).toEqual({ id: '42', title: 'Created' })
    })
  })

  describe('api.put', () => {
    it('sends body with PUT method', async () => {
      const fetchMock = mockFetch({ updated: true })
      const payload = { title: 'Updated task' }
      await api.put('/tasks/1', payload)

      expect(fetchMock).toHaveBeenCalledWith(
        '/api/tasks/1',
        expect.objectContaining({
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        }),
      )
    })
  })

  describe('api.patch', () => {
    it('sends body with PATCH method', async () => {
      const fetchMock = mockFetch({ patched: true })
      const payload = { status: 'closed' }
      await api.patch('/tasks/1', payload)

      expect(fetchMock).toHaveBeenCalledWith(
        '/api/tasks/1',
        expect.objectContaining({
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        }),
      )
    })
  })

  describe('sidebar bus notifications', () => {
    it('POST /agents/spawn bumps the agents bus so the sidebar refetches', async () => {
      mockFetch({ ok: true })
      const bus = await import('./sidebarBus')
      const listener = vi.fn()
      const off = bus.onAgentsChange(listener)
      try {
        await api.post('/agents/spawn', { name: 'x' })
        expect(listener).toHaveBeenCalledTimes(1)
      } finally {
        off()
      }
    })

    it('POST /tasks bumps the tasks bus so the sidebar refetches', async () => {
      mockFetch({ ok: true })
      const bus = await import('./sidebarBus')
      const listener = vi.fn()
      const off = bus.onTasksChange(listener)
      try {
        await api.post('/tasks', { title: 'hi' })
        expect(listener).toHaveBeenCalledTimes(1)
      } finally {
        off()
      }
    })

    it('GET /agents does NOT bump the bus (reads are not writes)', async () => {
      mockFetch({ active: [], agents: [] })
      const bus = await import('./sidebarBus')
      const listener = vi.fn()
      const off = bus.onAgentsChange(listener)
      try {
        await api.get('/agents')
        expect(listener).not.toHaveBeenCalled()
      } finally {
        off()
      }
    })

    it('unrelated endpoints do not bump either bus', async () => {
      mockFetch({ ok: true })
      const bus = await import('./sidebarBus')
      const aListener = vi.fn()
      const tListener = vi.fn()
      const offA = bus.onAgentsChange(aListener)
      const offT = bus.onTasksChange(tListener)
      try {
        await api.post('/settings', { theme: 'dark' })
        expect(aListener).not.toHaveBeenCalled()
        expect(tListener).not.toHaveBeenCalled()
      } finally {
        offA()
        offT()
      }
    })
  })

  describe('error handling', () => {
    it('throws on non-ok response', async () => {
      mockFetch('Not Found', false, 404)

      await expect(api.get('/nonexistent')).rejects.toThrow('Not Found')
    })

    it('throws with error text from response body', async () => {
      mockFetch('Internal Server Error', false, 500)

      await expect(api.post('/tasks', { title: 'fail' })).rejects.toThrow('Internal Server Error')
    })

    it('throws on 401 unauthorized', async () => {
      mockFetch('Unauthorized', false, 401)

      await expect(api.get('/secret')).rejects.toThrow('Unauthorized')
    })
  })

  describe('retryOn502 behavior', () => {
    it('retries once on 502 and returns the second response', async () => {
      let calls = 0
      global.fetch = vi.fn().mockImplementation(() => {
        calls++
        if (calls === 1) {
          return Promise.resolve({
            ok: false,
            status: 502,
            text: () => Promise.resolve('Bad Gateway'),
          })
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ ok: true }),
        })
      }) as unknown as typeof fetch

      const result = await api.get('/status/clock', { retryOn502: 1, retryDelayMs: 0 })
      expect(result).toEqual({ ok: true })
      expect(calls).toBe(2)
    })

    it('throws after exhausting all retries on persistent 502', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        text: () => Promise.resolve('Bad Gateway'),
      }) as unknown as typeof fetch

      await expect(api.get('/status/clock', { retryOn502: 2, retryDelayMs: 0 })).rejects.toThrow()
    })

    it('retries on network error (TypeError) and succeeds on retry', async () => {
      let calls = 0
      global.fetch = vi.fn().mockImplementation(() => {
        calls++
        if (calls === 1) return Promise.reject(new TypeError('Failed to fetch'))
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ up: true }),
        })
      }) as unknown as typeof fetch

      const result = await api.get('/status/clock', { retryOn502: 1, retryDelayMs: 0 })
      expect(result).toEqual({ up: true })
      expect(calls).toBe(2)
    })

    it('does NOT retry on 401 — intentional auth failures throw immediately', async () => {
      let calls = 0
      global.fetch = vi.fn().mockImplementation(() => {
        calls++
        return Promise.resolve({
          ok: false,
          status: 401,
          text: () => Promise.resolve('Unauthorized'),
        })
      }) as unknown as typeof fetch

      await expect(api.get('/secret', { retryOn502: 2, retryDelayMs: 0 })).rejects.toThrow('Unauthorized')
      expect(calls).toBe(1)
    })

    it('does NOT retry on 404 — resource not found throws immediately', async () => {
      let calls = 0
      global.fetch = vi.fn().mockImplementation(() => {
        calls++
        return Promise.resolve({
          ok: false,
          status: 404,
          text: () => Promise.resolve('Not Found'),
        })
      }) as unknown as typeof fetch

      await expect(api.get('/missing', { retryOn502: 1, retryDelayMs: 0 })).rejects.toThrow('Not Found')
      expect(calls).toBe(1)
    })
  })

  describe('abort and timeout behavior', () => {
    afterEach(() => {
      vi.useRealTimers()
    })

    it('passes an AbortSignal to fetch so hung requests can be cancelled', async () => {
      const fetchMock = mockFetch({ ok: true })
      await api.get('/tasks')

      const callArgs = fetchMock.mock.calls[0][1]
      expect(callArgs.signal).toBeDefined()
      expect(typeof callArgs.signal.aborted).toBe('boolean')
    })

    it('converts an AbortError from a timeout into ApiTimeoutError', async () => {
      // Simulate a fetch that rejects with an AbortError the way
      // fetch() does when AbortController.abort() fires.
      const abortError = new DOMException('The operation was aborted.', 'AbortError')
      global.fetch = vi.fn().mockRejectedValue(abortError) as unknown as typeof fetch

      await expect(api.get('/slow')).rejects.toBeInstanceOf(ApiTimeoutError)
    })

    it('still surfaces non-abort fetch errors unchanged', async () => {
      const networkError = new TypeError('Failed to fetch')
      global.fetch = vi.fn().mockRejectedValue(networkError) as unknown as typeof fetch

      await expect(api.get('/broken')).rejects.toBe(networkError)
    })
  })
})
