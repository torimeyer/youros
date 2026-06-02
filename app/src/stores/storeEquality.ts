// Shallow equality helpers for zustand store array setters.
//
// Every WS/poll-fed store used to replace its array field by reference on
// every tick (`set({ agents })`), so the array got a new identity even when
// its contents were unchanged. Any consumer with a `useEffect(… setState …,
// [storeArray])` then re-ran every frame, and one missing identity-guard in
// that effect produced React's "Maximum update depth exceeded" white-screen
// (→1730, previously patched consumer-by-consumer in Tasks.tsx, Agents.tsx
// and useRunningAgentsFeed.ts). Guarding the setter at the source with these
// helpers preserves the previous reference when nothing changed, so the store
// never notifies subscribers for a no-op update.

/** Object.is on every own-enumerable key; different key sets -> not equal. */
export function shallowEqualObject(
  a: Record<string, unknown>,
  b: Record<string, unknown>,
): boolean {
  if (a === b) return true;
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  for (const k of ka) {
    if (!Object.is(a[k], b[k])) return false;
  }
  return true;
}

/**
 * Same length and shallow-equal element-by-element (order-sensitive).
 *
 * Errs toward "changed": a nested array/object recreated each tick compares
 * unequal, so the guard can only ever collapse a genuinely-identical snapshot,
 * never suppress a real update.
 */
export function shallowEqualArray<T>(a: readonly T[], b: readonly T[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i];
    const y = b[i];
    if (x === y) continue;
    if (x && y && typeof x === "object" && typeof y === "object") {
      if (
        !shallowEqualObject(
          x as Record<string, unknown>,
          y as Record<string, unknown>,
        )
      ) {
        return false;
      }
    } else if (!Object.is(x, y)) {
      return false;
    }
  }
  return true;
}
