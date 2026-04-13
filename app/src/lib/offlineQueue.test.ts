import { describe, it, expect, beforeEach, vi } from 'vitest'

// Mock indexedDB for tests since jsdom does not provide it.
// We use a simple in-memory store that mirrors the IDB interface.
const mockStore: Record<string, unknown>[] = []
let autoId = 0

const mockObjectStore = {
  add: vi.fn((entry: Record<string, unknown>) => {
    const id = ++autoId
    mockStore.push({ ...entry, id })
    return { set onsuccess(fn: () => void) { fn() }, set onerror(_fn: () => void) {} }
  }),
  getAll: vi.fn(() => ({
    result: [...mockStore],
    set onsuccess(fn: () => void) { fn() },
    set onerror(_fn: () => void) {},
  })),
  delete: vi.fn((id: number) => {
    const idx = mockStore.findIndex((e) => (e as { id: number }).id === id)
    if (idx !== -1) mockStore.splice(idx, 1)
    return { set onsuccess(fn: () => void) { fn() }, set onerror(_fn: () => void) {} }
  }),
  clear: vi.fn(() => {
    mockStore.length = 0
    return { set onsuccess(fn: () => void) { fn() }, set onerror(_fn: () => void) {} }
  }),
}

const mockTransaction = {
  objectStore: vi.fn(() => mockObjectStore),
  set oncomplete(fn: () => void) { fn() },
}

const mockDb = {
  transaction: vi.fn(() => mockTransaction),
  close: vi.fn(),
  objectStoreNames: { contains: () => true },
  createObjectStore: vi.fn(),
}

vi.stubGlobal('indexedDB', {
  open: vi.fn(() => ({
    set onupgradeneeded(_fn: () => void) {},
    set onsuccess(fn: () => void) { fn() },
    set onerror(_fn: () => void) {},
    result: mockDb,
  })),
})

// Import after mocking indexedDB
import { enqueueWrite, getPendingWrites, clearQueue, pendingCount } from './offlineQueue'

describe('offlineQueue', () => {
  beforeEach(() => {
    mockStore.length = 0
    autoId = 0
    vi.clearAllMocks()
  })

  it('enqueues a write', async () => {
    await enqueueWrite('POST', '/tasks', { title: 'Test' })
    expect(mockObjectStore.add).toHaveBeenCalledTimes(1)
    const arg = mockObjectStore.add.mock.calls[0][0]
    expect(arg.method).toBe('POST')
    expect(arg.path).toBe('/tasks')
    expect(arg.body).toEqual({ title: 'Test' })
    expect(arg.createdAt).toBeTruthy()
  })

  it('gets pending writes', async () => {
    await enqueueWrite('POST', '/tasks', { title: 'A' })
    await enqueueWrite('PATCH', '/tasks/1', { status: 'done' })
    const pending = await getPendingWrites()
    expect(pending.length).toBe(2)
  })

  it('returns count of pending writes', async () => {
    await enqueueWrite('POST', '/tasks', { title: 'A' })
    const count = await pendingCount()
    expect(count).toBe(1)
  })

  it('clears the queue', async () => {
    await enqueueWrite('POST', '/tasks', { title: 'A' })
    await clearQueue()
    expect(mockObjectStore.clear).toHaveBeenCalled()
  })
})
