import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import TopBar from './TopBar'
import { useAppStore } from '../stores/app'

// Mock the api module so HTTP endpoints do not hit the network.
vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  },
}))

// jsdom does not provide window.matchMedia. Provide a minimal stub
// so the responsive desktop detection in TopBar does not crash.
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

function renderTopBar() {
  return render(
    <BrowserRouter>
      <TopBar title="Home" />
    </BrowserRouter>
  )
}

describe('TopBar What\'s New button', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({
      osName: 'myOS',
      chatOpen: false,
      chatWidth: 400,
      whatsNewLastSeen: '',
    })
  })

  it('the What\'s New sparkle button is no longer rendered inside the TopBar header', () => {
    renderTopBar()

    const header = document.querySelector('header')
    expect(header).toBeTruthy()
    const whatsNewButton = header!.querySelector('[data-testid="whats-new-button"]')
    expect(whatsNewButton).toBeNull()
  })
})


// ---------------------------------------------------------------------------
// Platform-specific modifier key display
// ---------------------------------------------------------------------------

describe('TopBar keyboard modifier key by platform', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ osName: 'myOS', chatOpen: false, chatWidth: 400 })
  })

  it('shows Ctrl+ on a non-Mac platform (Win32)', async () => {
    Object.defineProperty(navigator, 'platform', {
      value: 'Win32',
      configurable: true,
      writable: true,
    })
    renderTopBar()
    await act(async () => {})
    expect(document.body.textContent).toContain('Ctrl+')
    expect(document.body.textContent).not.toContain('⌘')
    Object.defineProperty(navigator, 'platform', { value: '', configurable: true, writable: true })
  })

  it('shows ⌘ on macOS (MacIntel)', async () => {
    Object.defineProperty(navigator, 'platform', {
      value: 'MacIntel',
      configurable: true,
      writable: true,
    })
    vi.resetModules()

    const { createElement } = await import('react')
    const { render: r, screen: s, cleanup, waitFor: wf } = await import('@testing-library/react')
    const { BrowserRouter: BR } = await import('react-router-dom')
    const { default: FreshTopBar } = await import('./TopBar')

    r(createElement(BR as any, null, createElement(FreshTopBar as any, { title: 'Home' })))

    await wf(() => expect(document.body.textContent).toContain('⌘'))
    expect(document.body.textContent).not.toContain('Ctrl+')

    cleanup()
    Object.defineProperty(navigator, 'platform', { value: '', configurable: true, writable: true })
    vi.resetModules()
  })
})

describe('TopBar Start tour button', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({
      osName: 'myOS',
      chatOpen: false,
      chatWidth: 400,
    })
  })

  it('shows Start tour button when tourComplete is false', () => {
    useAppStore.setState({ tourComplete: false })
    renderTopBar()
    expect(screen.getByTestId('start-tour-btn')).toBeInTheDocument()
  })

  it('does not show Start tour button when tourComplete is true', () => {
    useAppStore.setState({ tourComplete: true })
    renderTopBar()
    expect(screen.queryByTestId('start-tour-btn')).toBeNull()
  })

  it('clicking Start tour button calls setShowTour(true)', () => {
    const { fireEvent } = require('@testing-library/react')
    useAppStore.setState({ tourComplete: false, showTour: false })
    renderTopBar()
    fireEvent.click(screen.getByTestId('start-tour-btn'))
    expect(useAppStore.getState().showTour).toBe(true)
  })
})

describe('TopBar Team page entry point removed', () => {
  it('topbar-team-link is not rendered', () => {
    renderTopBar()
    expect(screen.queryByTestId('topbar-team-link')).toBeNull()
  })

  it('no button navigates to /team', () => {
    renderTopBar()
    const buttons = screen.getAllByRole('button')
    buttons.forEach((btn) => {
      expect(btn.getAttribute('data-testid')).not.toBe('topbar-team-link')
    })
  })
})

// Regression guard: TopBar must render a flow spacer so pages don't need
// per-page top padding that can be forgotten (→1731).
describe('TopBar flow spacer', () => {
  it('renders a topbar-spacer div matching the fixed header height', () => {
    render(
      <BrowserRouter>
        <TopBar title="Test" />
      </BrowserRouter>
    )
    const spacer = document.querySelector('[data-testid="topbar-spacer"]')
    expect(spacer).not.toBeNull()
    // Must carry both mobile (h-14) and sm (h-16) height classes so
    // content is never hidden under the fixed bar on any breakpoint.
    expect(spacer?.className).toContain('h-14')
    expect(spacer?.className).toContain('sm:h-16')
  })
})
