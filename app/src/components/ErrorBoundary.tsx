import { Component, type ReactNode } from 'react'

// →2958: after a dev-server restart (or a redeploy), a browser tab that
// stayed open can mix module chunks from two different builds. The
// 2026-07-19 incident: the react chunk carried version stamp v=c7ea90ff
// while react-dom carried v=0233d0f5, so the page held two React copies,
// the hook dispatcher was null, and the first lazy-loaded game crashed
// with "Cannot read properties of null (reading 'useState')". A plain
// page reload always fixes this class, so the boundary reloads once on
// its own instead of stranding the user on the crash screen. A per-tab
// guard (sessionStorage timestamp) makes sure a crash that survives one
// reload is a real bug and still shows the screen.
export const STALE_CHUNK_RELOAD_KEY = 'youros-stale-chunk-reload-at'
const RELOAD_GUARD_WINDOW_MS = 60_000

/**
 * True when the error looks like the stale/mixed chunk class rather than
 * an app bug:
 *  - a React hook read off a null dispatcher (two React copies on the
 *    page), in any browser's phrasing, or
 *  - a lazy chunk whose URL went stale, failing at import time.
 */
export function isStaleChunkError(error: Error): boolean {
  const text = `${error.message}\n${error.stack ?? ''}`
  const nullHookRead =
    /Cannot read properties of (null|undefined) \(reading 'use[A-Z]\w*'\)|Cannot read property 'use[A-Z]\w*' of (null|undefined)|null is not an object \(evaluating '\w+\.use[A-Z]\w*'\)/
  const failedChunkLoad =
    /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|Outdated Optimize Dep/i
  return nullHookRead.test(text) || failedChunkLoad.test(text)
}

function autoReloadedRecently(): boolean {
  try {
    const at = sessionStorage.getItem(STALE_CHUNK_RELOAD_KEY)
    return at !== null && Date.now() - Number(at) < RELOAD_GUARD_WINDOW_MS
  } catch {
    // No storage means no loop guard, so never auto-reload.
    return true
  }
}

interface Props {
  children: ReactNode
  /** Injectable for tests; defaults to a real page reload. */
  reload?: () => void
}

interface State {
  hasError: boolean
  error?: Error
  autoReloading?: boolean
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError(error: Error): State {
    return {
      hasError: true,
      error,
      autoReloading: isStaleChunkError(error) && !autoReloadedRecently(),
    }
  }

  componentDidCatch(error: Error, info: { componentStack: string }) {
    // Always log so errors surface in the browser console even when the
    // fallback UI has no text-expansion button. In production this is the
    // only trace available; in development it pairs with the overlay.
    console.error('[ErrorBoundary] render error:', error, info.componentStack)
    if (this.state.autoReloading) {
      try {
        sessionStorage.setItem(STALE_CHUNK_RELOAD_KEY, String(Date.now()))
      } catch {
        // Storage refused the write; reload anyway — the guard check
        // above already treats missing storage as "do not loop".
      }
      const reload = this.props.reload ?? (() => window.location.reload())
      reload()
    }
  }

  render() {
    if (this.state.hasError) {
      if (this.state.autoReloading) {
        // The page is about to reload itself; a flash of the crash
        // screen would only alarm the user.
        return (
          <div
            style={{
              position: 'fixed',
              inset: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: '#0b0d10',
              color: '#9ca3af',
              fontFamily:
                '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
              fontSize: 14,
            }}
          >
            <p style={{ margin: 0 }}>Reloading…</p>
          </div>
        )
      }
      return (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 12,
            background: '#0b0d10',
            color: '#9ca3af',
            fontFamily:
              '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
            fontSize: 14,
          }}
        >
          <p style={{ margin: 0, color: '#e5e7eb' }}>Something went wrong.</p>
          {import.meta.env.DEV && this.state.error && (
            <pre
              style={{
                margin: 0,
                fontSize: 11,
                color: '#f87171',
                maxWidth: '80vw',
                maxHeight: '40vh',
                overflow: 'auto',
                textAlign: 'left',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {this.state.error.message}
              {this.state.error.stack ? `\n\n${this.state.error.stack}` : ''}
            </pre>
          )}
          <button
            onClick={this.props.reload ?? (() => window.location.reload())}
            style={{
              padding: '6px 16px',
              background: '#1f2937',
              color: '#e5e7eb',
              border: '1px solid #374151',
              borderRadius: 6,
              cursor: 'pointer',
              fontSize: 13,
            }}
          >
            Reload
          </button>
        </div>
      )
    }

    return this.props.children
  }
}
