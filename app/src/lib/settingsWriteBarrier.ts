// →2777: write barrier for /settings fields.
//
// The app saves settings eagerly (a PATCH per change) and separately
// re-reads them (hydration on boot, plus background retries armed by a
// slow start, →2687). A re-read that starts just after a change can
// still be answered from the server's pre-save state while the save is
// on the wire; applying that reply silently reverts what the user just
// did, and the next blur/Enter save writes the reverted value to disk.
// The snapshot guards on the appliers only catch an edit made while a
// request was in flight, not this direction of the race.
//
// The barrier closes it at the api layer: every PATCH /settings records
// its keys when it starts and when its response lands. A fetched value
// for a key must be discarded while a save for that key is unconfirmed,
// or when one was confirmed after the fetch started (the reply may
// predate the save). A fetch that starts after the confirmation applies
// normally, so the server remains the source of truth at rest.

const pendingWrites = new Map<string, number>()
const lastSettledAt = new Map<string, number>()

export function recordSettingsWriteStart(keys: string[]): void {
  for (const k of keys) pendingWrites.set(k, (pendingWrites.get(k) ?? 0) + 1)
}

// Called when the PATCH response lands, success or failure. A failed or
// timed-out save may still have been applied by a slow server, so both
// outcomes mark the key: the local value stays authoritative until a
// fetch that started after this moment says otherwise.
export function recordSettingsWriteSettled(keys: string[]): void {
  const now = Date.now()
  for (const k of keys) {
    const n = (pendingWrites.get(k) ?? 1) - 1
    if (n <= 0) pendingWrites.delete(k)
    else pendingWrites.set(k, n)
    lastSettledAt.set(k, now)
  }
}

// True when a fetched value for `key` must not be applied because it may
// predate a local save: a save is still in flight, or one settled at or
// after the moment the fetch started.
export function fetchedSettingsValueIsStale(key: string, fetchStartedAt: number): boolean {
  if ((pendingWrites.get(key) ?? 0) > 0) return true
  const settled = lastSettledAt.get(key)
  return settled !== undefined && settled >= fetchStartedAt
}

// Test-only: clear all barrier state between tests.
export function resetSettingsWriteBarrier(): void {
  pendingWrites.clear()
  lastSettledAt.clear()
}
