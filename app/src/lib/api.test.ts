import { describe, it, expect, vi, beforeEach } from 'vitest'
import { api } from './api'

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

      expect(fetchMock).toHaveBeenCalledWith('/api/tasks', {
        method: 'GET',
        headers: undefined,
        body: undefined,
      })
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

      expect(fetchMock).toHaveBeenCalledWith('/api/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
    })

    it('sends POST without body when no body provided', async () => {
      const fetchMock = mockFetch({ ok: true })
      await api.post('/tasks/1/close')

      expect(fetchMock).toHaveBeenCalledWith('/api/tasks/1/close', {
        method: 'POST',
        headers: undefined,
        body: undefined,
      })
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

      expect(fetchMock).toHaveBeenCalledWith('/api/tasks/1', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
    })
  })

  describe('api.patch', () => {
    it('sends body with PATCH method', async () => {
      const fetchMock = mockFetch({ patched: true })
      const payload = { status: 'closed' }
      await api.patch('/tasks/1', payload)

      expect(fetchMock).toHaveBeenCalledWith('/api/tasks/1', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
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
})
