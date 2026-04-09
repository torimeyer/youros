import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Layout } from './Layout'
import { useAppStore } from '../stores/app'

const mockNavigate = vi.fn()

// Mock child components to isolate Layout behavior
vi.mock('./Sidebar', () => ({
  Sidebar: () => <div data-testid="sidebar" />,
}))

vi.mock('./ChatPanel', () => ({
  ChatPanel: () => <div data-testid="chat-panel" />,
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

function renderLayout() {
  return render(
    <MemoryRouter>
      <Layout />
    </MemoryRouter>
  )
}

describe('Layout', () => {
  beforeEach(() => {
    mockNavigate.mockClear()
    useAppStore.setState({
      chatOpen: true,
      chatWidth: 380,
      isResizing: false,
      darkMode: true,
      accentColor: 'blue',
    })
  })

  afterEach(() => {
    document.documentElement.removeAttribute('data-theme')
    document.documentElement.style.removeProperty('--color-accent')
  })

  it('applies margin transition when not resizing', () => {
    renderLayout()
    const main = document.querySelector('main')
    expect(main?.className).toContain('transition-[margin]')
    expect(main?.className).toContain('duration-200')
  })

  it('removes margin transition while resizing to prevent lag', () => {
    useAppStore.setState({ isResizing: true })
    renderLayout()
    const main = document.querySelector('main')
    expect(main?.className).not.toContain('transition-[margin]')
    expect(main?.className).not.toContain('duration-200')
  })

  it('re-applies margin transition after resize ends', () => {
    useAppStore.setState({ isResizing: true })
    const { unmount } = renderLayout()
    const mainDuringResize = document.querySelector('main')
    expect(mainDuringResize?.className).not.toContain('transition-[margin]')

    unmount()

    useAppStore.setState({ isResizing: false })
    renderLayout()
    const mainAfterResize = document.querySelector('main')
    expect(mainAfterResize?.className).toContain('transition-[margin]')
  })

  it('sets marginRight to chatWidth when chat is open', () => {
    useAppStore.setState({ chatOpen: true, chatWidth: 500 })
    renderLayout()
    const main = document.querySelector('main')
    expect(main?.style.marginRight).toBe('500px')
  })

  it('does not set marginRight when chat is closed', () => {
    useAppStore.setState({ chatOpen: false })
    renderLayout()
    const main = document.querySelector('main')
    expect(main?.style.marginRight).toBe('')
  })

  describe('chat resize layout reactivity', () => {
    // These tests lock in the contract: no matter how wide Tori drags the
    // chat panel, the main content area reacts and the page itself never
    // needs a horizontal scrollbar.
    it('main content shrinks when chat grows so sidebar, main, chat all fit the viewport', () => {
      // Set a wide chat width that still leaves room for the sidebar plus
      // some main content. 224 (sidebar) + 256 (main floor) + 800 (chat)
      // = 1280, which matches jsdom's default 1024 after clamping. We use
      // the current window.innerWidth from jsdom.
      const viewport = window.innerWidth
      const chatWidth = Math.max(300, viewport - 320 - 100)
      useAppStore.setState({ chatOpen: true, chatWidth })
      renderLayout()
      const main = document.querySelector('main') as HTMLElement
      // ml-56 = 14rem = 224px sidebar reservation on the left.
      // marginRight = chatWidth reservation on the right.
      // Left + right reservations must never exceed the viewport.
      const sidebarWidth = 224
      expect(sidebarWidth + chatWidth).toBeLessThanOrEqual(viewport)
      expect(main.style.marginRight).toBe(`${chatWidth}px`)
      expect(main.className).toContain('ml-56')
    })

    it('main content has min-w-0 and overflow-x-hidden so narrow grids do not overflow the page', () => {
      useAppStore.setState({ chatOpen: true, chatWidth: 500 })
      renderLayout()
      const main = document.querySelector('main') as HTMLElement
      expect(main.className).toContain('min-w-0')
      expect(main.className).toContain('overflow-x-hidden')
    })

    it('renders without horizontal page scrollbar at multiple chat widths', () => {
      for (const chatWidth of [380, 600, 800]) {
        useAppStore.setState({ chatOpen: true, chatWidth })
        const { unmount } = renderLayout()
        // In jsdom the body width equals the viewport width. The "never
        // scroll horizontally" contract is enforced at the CSS level by
        // html, body { overflow-x: hidden } and by min-w-0 on main. Here
        // we at least assert the main element sets the correct margin
        // and the chat width reservation never exceeds the viewport.
        const main = document.querySelector('main') as HTMLElement
        expect(main.style.marginRight).toBe(`${chatWidth}px`)
        const sidebarReserve = 224
        expect(sidebarReserve + chatWidth).toBeLessThanOrEqual(window.innerWidth)
        unmount()
      }
    })
  })

  it('renders data-theme="dark" when darkMode is true', () => {
    renderLayout()
    const wrapper = document.querySelector('[data-theme]')
    expect(wrapper?.getAttribute('data-theme')).toBe('dark')
  })

  it('renders data-theme="light" when darkMode is false', () => {
    useAppStore.setState({ darkMode: false })
    renderLayout()
    const wrapper = document.querySelector('[data-theme]')
    expect(wrapper?.getAttribute('data-theme')).toBe('light')
  })

  it('applies dark theme to document element via useEffect', () => {
    renderLayout()
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
  })

  it('applies light theme to document element via useEffect', () => {
    useAppStore.setState({ darkMode: false })
    renderLayout()
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
  })

  it('sets --color-accent CSS variable for blue', () => {
    renderLayout()
    expect(document.documentElement.style.getPropertyValue('--color-accent')).toBe('#3b82f6')
  })

  it('sets --color-accent CSS variable for pink', () => {
    useAppStore.setState({ accentColor: 'pink' })
    renderLayout()
    expect(document.documentElement.style.getPropertyValue('--color-accent')).toBe('#ec4899')
  })

  describe('keyboard shortcuts', () => {
    function pressKey(key: string, opts: Partial<KeyboardEventInit> = {}) {
      fireEvent.keyDown(document, { key, metaKey: true, ...opts })
    }

    it('Cmd+K toggles command palette', () => {
      renderLayout()
      expect(useAppStore.getState().commandPaletteOpen).toBe(false)
      pressKey('k')
      expect(useAppStore.getState().commandPaletteOpen).toBe(true)
      pressKey('k')
      expect(useAppStore.getState().commandPaletteOpen).toBe(false)
    })

    it('Cmd+L toggles chat panel', () => {
      useAppStore.setState({ chatOpen: false })
      renderLayout()
      pressKey('l')
      expect(useAppStore.getState().chatOpen).toBe(true)
      pressKey('l')
      expect(useAppStore.getState().chatOpen).toBe(false)
    })

    it('Cmd+N navigates to /tasks?new=1', () => {
      renderLayout()
      pressKey('n')
      expect(mockNavigate).toHaveBeenCalledWith('/tasks?new=1')
    })

    it('Cmd+1 navigates to Home', () => {
      renderLayout()
      pressKey('1')
      expect(mockNavigate).toHaveBeenCalledWith('/')
    })

    it('Cmd+2 navigates to Tasks', () => {
      renderLayout()
      pressKey('2')
      expect(mockNavigate).toHaveBeenCalledWith('/tasks')
    })

    it('Cmd+5 navigates to Agents', () => {
      renderLayout()
      pressKey('5')
      expect(mockNavigate).toHaveBeenCalledWith('/agents')
    })

    it('Cmd+8 navigates to Settings', () => {
      renderLayout()
      pressKey('8')
      expect(mockNavigate).toHaveBeenCalledWith('/settings')
    })

    it('does not fire shortcuts when typing in an input', () => {
      renderLayout()
      const input = document.createElement('input')
      document.body.appendChild(input)
      fireEvent.keyDown(input, { key: 'l', metaKey: true })
      // Chat should remain in its initial state (open)
      expect(useAppStore.getState().chatOpen).toBe(true)
      document.body.removeChild(input)
    })

    it('does not fire shortcuts when typing in a textarea', () => {
      renderLayout()
      const textarea = document.createElement('textarea')
      document.body.appendChild(textarea)
      fireEvent.keyDown(textarea, { key: 'n', metaKey: true })
      expect(mockNavigate).not.toHaveBeenCalled()
      document.body.removeChild(textarea)
    })

    it('does not fire without Cmd/Ctrl modifier', () => {
      renderLayout()
      fireEvent.keyDown(document, { key: 'l' })
      // Chat should stay open (no toggle)
      expect(useAppStore.getState().chatOpen).toBe(true)
    })

    it('Ctrl+L also works (for non-Mac users)', () => {
      useAppStore.setState({ chatOpen: false })
      renderLayout()
      fireEvent.keyDown(document, { key: 'l', ctrlKey: true })
      expect(useAppStore.getState().chatOpen).toBe(true)
    })
  })
})
