import { Component, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError(): State {
    return { hasError: true }
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
