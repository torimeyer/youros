import { Component, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error?: Error
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: { componentStack: string }) {
    // Always log so errors surface in the browser console even when the
    // fallback UI has no text-expansion button. In production this is the
    // only trace available; in development it pairs with the overlay.
    console.error('[ErrorBoundary] render error:', error, info.componentStack)
  }

  render() {
    if (this.state.hasError) {
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
            onClick={() => window.location.reload()}
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
