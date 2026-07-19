// →2958: the crash screen must recognize the stale-chunk failure class
// (mixed optimized-dep versions after a dev-server restart producing two
// React copies, or a lazy chunk whose URL went stale) and reload the page
// once instead of stranding the user on "Something went wrong." A loop
// guard makes sure a crash that survives one reload still shows the screen.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { ErrorBoundary, STALE_CHUNK_RELOAD_KEY } from './ErrorBoundary'

function Bomb({ error }: { error: Error }): never {
  throw error
}

function staleChunkError(): Error {
  // Verbatim shape from the 2026-07-19 16:10 incident: react chunk and
  // react-dom chunk carried two different version stamps in one stack.
  const err = new TypeError(
    "Cannot read properties of null (reading 'useState')",
  )
  err.stack = [
    "TypeError: Cannot read properties of null (reading 'useState')",
    '    at Object.useState (https://127.0.0.1:3010/node_modules/.vite/deps/react-3_O8oni9.js?v=c7ea90ff:1069:29)',
    '    at Mastermind (https://127.0.0.1:3010/src/components/breakroom/games/Mastermind.tsx:98:30)',
    '    at renderWithHooks (https://127.0.0.1:3010/node_modules/.vite/deps/react-dom_client.js?v=0233d0f5:11548:26)',
  ].join('\n')
  return err
}

describe('ErrorBoundary stale-chunk auto-reload (→2958)', () => {
  let reload: ReturnType<typeof vi.fn>

  beforeEach(() => {
    sessionStorage.clear()
    reload = vi.fn()
    // React logs every boundary-caught error; keep test output readable.
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    sessionStorage.clear()
  })

  it('reloads once on a null-hook crash from mixed dep chunks and hides the crash screen', () => {
    render(
      <ErrorBoundary reload={reload}>
        <Bomb error={staleChunkError()} />
      </ErrorBoundary>,
    )
    expect(reload).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('Something went wrong.')).toBeNull()
    // The guard is written so a second crash cannot loop.
    expect(sessionStorage.getItem(STALE_CHUNK_RELOAD_KEY)).not.toBeNull()
  })

  it('reloads once on a failed dynamic import (stale lazy chunk URL)', () => {
    const err = new TypeError(
      'Failed to fetch dynamically imported module: https://127.0.0.1:3010/src/components/breakroom/games/Mastermind.tsx',
    )
    render(
      <ErrorBoundary reload={reload}>
        <Bomb error={err} />
      </ErrorBoundary>,
    )
    expect(reload).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('Something went wrong.')).toBeNull()
  })

  it('does NOT reload again when it already auto-reloaded moments ago (loop guard)', () => {
    sessionStorage.setItem(STALE_CHUNK_RELOAD_KEY, String(Date.now()))
    render(
      <ErrorBoundary reload={reload}>
        <Bomb error={staleChunkError()} />
      </ErrorBoundary>,
    )
    expect(reload).not.toHaveBeenCalled()
    expect(screen.getByText('Something went wrong.')).toBeTruthy()
  })

  it('reloads again when the previous auto-reload was long ago (guard expired)', () => {
    sessionStorage.setItem(
      STALE_CHUNK_RELOAD_KEY,
      String(Date.now() - 10 * 60_000),
    )
    render(
      <ErrorBoundary reload={reload}>
        <Bomb error={staleChunkError()} />
      </ErrorBoundary>,
    )
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('shows the crash screen for an ordinary error without reloading', () => {
    render(
      <ErrorBoundary reload={reload}>
        <Bomb error={new Error('boom: something unrelated broke')} />
      </ErrorBoundary>,
    )
    expect(reload).not.toHaveBeenCalled()
    expect(screen.getByText('Something went wrong.')).toBeTruthy()
  })

  it('does not treat an app-code null read on a non-hook property as stale chunks', () => {
    const err = new TypeError(
      "Cannot read properties of null (reading 'name')",
    )
    render(
      <ErrorBoundary reload={reload}>
        <Bomb error={err} />
      </ErrorBoundary>,
    )
    expect(reload).not.toHaveBeenCalled()
    expect(screen.getByText('Something went wrong.')).toBeTruthy()
  })

  it('Reload button on the crash screen triggers a reload', () => {
    render(
      <ErrorBoundary reload={reload}>
        <Bomb error={new Error('boom')} />
      </ErrorBoundary>,
    )
    screen.getByText('Reload').click()
    expect(reload).toHaveBeenCalledTimes(1)
  })
})
