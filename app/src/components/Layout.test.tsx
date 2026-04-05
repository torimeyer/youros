import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Layout } from './Layout'
import { useAppStore } from '../stores/app'

// Mock child components to isolate Layout behavior
vi.mock('./Sidebar', () => ({
  Sidebar: () => <div data-testid="sidebar" />,
}))

vi.mock('./ChatPanel', () => ({
  ChatPanel: () => <div data-testid="chat-panel" />,
}))

function renderLayout() {
  return render(
    <MemoryRouter>
      <Layout />
    </MemoryRouter>
  )
}

describe('Layout', () => {
  beforeEach(() => {
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
})
