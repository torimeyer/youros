import '@testing-library/jest-dom'

// Node.js 25+ passes --localstorage-file to vitest worker processes, which
// overrides the jsdom localStorage implementation with the Node built-in one.
// The Node built-in localStorage is missing clear(), setItem(), getItem(), and
// removeItem() when the path is invalid or empty. Guard against this by
// replacing window.localStorage with a plain in-memory implementation whenever
// the real one is missing these standard methods.
if (typeof window !== 'undefined') {
  const ls = window.localStorage
  if (typeof ls?.clear !== 'function' || typeof ls?.setItem !== 'function') {
    const store: Record<string, string> = {}
    const mock: Storage = {
      get length() { return Object.keys(store).length },
      clear() { Object.keys(store).forEach((k) => delete store[k]) },
      getItem(k: string) { return k in store ? store[k] : null },
      setItem(k: string, v: string) { store[k] = String(v) },
      removeItem(k: string) { delete store[k] },
      key(n: number) { return Object.keys(store)[n] ?? null },
    }
    Object.defineProperty(window, 'localStorage', { value: mock, writable: true })
  }
}
