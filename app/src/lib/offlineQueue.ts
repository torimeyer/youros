/**
 * Offline write queue.
 *
 * When the network is down, mutations (task creation, status changes, etc.)
 * are saved to IndexedDB. When connectivity returns, they replay in order.
 *
 * The queue is intentionally simple: each entry is a { method, path, body }
 * triple that maps directly to an api.post / api.put / api.patch call.
 */

const DB_NAME = 'myos-offline'
const DB_VERSION = 1
const STORE_NAME = 'pending-writes'

interface QueuedWrite {
  id?: number
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  path: string
  body?: unknown
  createdAt: string
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: 'id', autoIncrement: true })
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

/**
 * Enqueue a write to be replayed when online.
 */
export async function enqueueWrite(
  method: QueuedWrite['method'],
  path: string,
  body?: unknown,
): Promise<void> {
  const db = await openDb()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite')
    const store = tx.objectStore(STORE_NAME)
    const entry: QueuedWrite = {
      method,
      path,
      body,
      createdAt: new Date().toISOString(),
    }
    const req = store.add(entry)
    req.onsuccess = () => resolve()
    req.onerror = () => reject(req.error)
    tx.oncomplete = () => db.close()
  })
}

/**
 * Return all pending writes in FIFO order.
 */
export async function getPendingWrites(): Promise<QueuedWrite[]> {
  const db = await openDb()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly')
    const store = tx.objectStore(STORE_NAME)
    const req = store.getAll()
    req.onsuccess = () => resolve(req.result as QueuedWrite[])
    req.onerror = () => reject(req.error)
    tx.oncomplete = () => db.close()
  })
}

/**
 * Remove a single entry by its auto-incremented id.
 */
async function removeWrite(id: number): Promise<void> {
  const db = await openDb()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite')
    const store = tx.objectStore(STORE_NAME)
    const req = store.delete(id)
    req.onsuccess = () => resolve()
    req.onerror = () => reject(req.error)
    tx.oncomplete = () => db.close()
  })
}

/**
 * Replay all pending writes against the live API.
 * Removes each entry after a successful request.
 * Returns the number of successfully replayed writes.
 */
export async function replayPendingWrites(): Promise<number> {
  const pending = await getPendingWrites()
  if (pending.length === 0) return 0

  let replayed = 0
  for (const entry of pending) {
    try {
      const res = await fetch(`/api${entry.path}`, {
        method: entry.method,
        headers: entry.body ? { 'Content-Type': 'application/json' } : undefined,
        body: entry.body ? JSON.stringify(entry.body) : undefined,
      })
      if (res.ok && entry.id !== undefined) {
        await removeWrite(entry.id)
        replayed++
      }
    } catch {
      // Network still down or server error. Stop replaying so we
      // preserve order. The remaining entries stay queued.
      break
    }
  }
  return replayed
}

/**
 * Clear the entire queue. Useful for manual reset.
 */
export async function clearQueue(): Promise<void> {
  const db = await openDb()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite')
    const store = tx.objectStore(STORE_NAME)
    const req = store.clear()
    req.onsuccess = () => resolve()
    req.onerror = () => reject(req.error)
    tx.oncomplete = () => db.close()
  })
}

/**
 * Return the number of pending writes.
 */
export async function pendingCount(): Promise<number> {
  const writes = await getPendingWrites()
  return writes.length
}

// ---------------------------------------------------------------
// Auto-replay on reconnect
// ---------------------------------------------------------------
let _replayInProgress = false

function onOnline() {
  if (_replayInProgress) return
  _replayInProgress = true
  replayPendingWrites()
    .catch(() => {})
    .finally(() => {
      _replayInProgress = false
    })
}

if (typeof window !== 'undefined') {
  window.addEventListener('online', onOnline)
}
