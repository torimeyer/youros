import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import PageShell from './PageShell'

// PageShell renders TopBar, which touches the api module, the notifications
// feed/WS hook, push notifications, and window.matchMedia. Stub them all so
// the shell can render in jsdom without network or browser APIs.
vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue({}),
    post: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  },
}))

vi.mock('../hooks/useNotificationsFeed', () => ({
  useNotificationsFeed: vi.fn(),
}))

vi.mock('../lib/pushNotifications', () => ({
  isPushSupported: vi.fn().mockReturnValue(false),
  isSubscribed: vi.fn().mockResolvedValue(false),
  subscribe: vi.fn().mockResolvedValue(false),
  unsubscribe: vi.fn().mockResolvedValue(false),
}))

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

function renderShell(props: { fullHeight?: boolean } = {}) {
  return render(
    <BrowserRouter>
      <PageShell title="Test Page" {...props}>
        <div data-testid="page-child">hello</div>
      </PageShell>
    </BrowserRouter>
  )
}

describe('PageShell', () => {
  it('renders its children', () => {
    renderShell()
    expect(screen.getByTestId('page-child')).toBeInTheDocument()
  })

  it('renders the TopBar spacer (single source of truth for header offset)', () => {
    renderShell()
    expect(screen.getByTestId('topbar-spacer')).toBeInTheDocument()
  })

  it('wraps content in the canonical centered, padded container', () => {
    renderShell()
    const content = screen.getByTestId('page-child').parentElement as HTMLElement
    // Top breathing room below the header bar (the consistency fix).
    expect(content.className).toContain('pt-6')
    expect(content.className).toContain('sm:pt-8')
    // Standard side + bottom padding shared by every page.
    expect(content.className).toContain('px-4')
    expect(content.className).toContain('sm:px-8')
    expect(content.className).toContain('pb-4')
    expect(content.className).toContain('sm:pb-8')
    // Centered max width (confirmed design decision).
    expect(content.className).toContain('max-w-6xl')
    expect(content.className).toContain('mx-auto')
  })

  it('applies the dark/light page background on the outer wrapper', () => {
    renderShell()
    const content = screen.getByTestId('page-child').parentElement as HTMLElement
    const outer = content.parentElement as HTMLElement
    expect(outer.className).toContain('min-h-dvh')
    expect(outer.className).toContain('dark:bg-slate-950')
  })

  it('fullHeight swaps to a stretching flex column without max-width centering', () => {
    renderShell({ fullHeight: true })
    const content = screen.getByTestId('page-child').parentElement as HTMLElement
    expect(content.className).toContain('flex-1')
    expect(content.className).toContain('flex')
    expect(content.className).toContain('flex-col')
    expect(content.className).not.toContain('max-w-6xl')
    // Top padding still applies in the full-height variant.
    expect(content.className).toContain('pt-6')
  })
})
