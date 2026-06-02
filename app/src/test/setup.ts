import '@testing-library/jest-dom'

// Detect tests that make no expect() calls — they always pass silently even
// when the code under test is broken. Skip the check when there is no test
// name (e.g. top-level beforeAll/afterAll hooks running outside a test).
afterEach(() => {
  const { assertionCalls, currentTestName } = expect.getState()
  if (assertionCalls === 0 && currentTestName) {
    throw new Error(
      `"${currentTestName}" made no expect() calls — it will always pass ` +
      `even when the code is broken. Add at least one assertion, or if the ` +
      `test legitimately only checks that rendering does not throw, add ` +
      `expect(container).toBeInTheDocument() as an explicit smoke-check.`
    )
  }
})

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
    Object.defineProperty(window, 'localStorage', { value: mock, writable: true, configurable: true })
  }
}
