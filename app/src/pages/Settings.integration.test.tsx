/**
 * Regression tests for settings integration.
 * These verify that settings changes propagate correctly across components.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { useAppStore } from '../stores/app'
import { Layout } from '../components/Layout'
import { Sidebar } from '../components/Sidebar'

// Mock child components for Layout tests
vi.mock('../components/ChatPanel', () => ({
  ChatPanel: () => <div data-testid="chat-panel" />,
}))

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue({ active: [] }),
    post: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
  },
}))

// jsdom does not provide window.matchMedia. Provide a minimal stub
// so components that use responsive breakpoints do not crash.
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

function renderSidebar() {
  return render(
    <MemoryRouter>
      <Sidebar />
    </MemoryRouter>
  )
}

function renderLayout() {
  return render(
    <MemoryRouter>
      <Layout />
    </MemoryRouter>
  )
}

describe('Settings integration: Dark mode', () => {
  beforeEach(() => {
    useAppStore.setState({ darkMode: true, chatOpen: false, chatWidth: 380, isResizing: false, accentColor: 'blue' })
  })

  afterEach(() => {
    document.documentElement.removeAttribute('data-theme')
    document.documentElement.style.removeProperty('--color-accent')
  })

  it('sets data-theme="dark" on documentElement when darkMode is true', () => {
    renderLayout()
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
  })

  it('sets data-theme="light" on documentElement when darkMode is false', () => {
    useAppStore.setState({ darkMode: false })
    renderLayout()
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
  })

  it('updates data-theme when darkMode toggles', () => {
    renderLayout()
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')

    useAppStore.getState().toggleDarkMode()
    // Re-render to trigger effect
    renderLayout()
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
  })
})

describe('Settings integration: Accent color', () => {
  beforeEach(() => {
    useAppStore.setState({ accentColor: 'blue', darkMode: true, chatOpen: false, chatWidth: 380, isResizing: false })
  })

  afterEach(() => {
    document.documentElement.removeAttribute('data-theme')
    document.documentElement.style.removeProperty('--color-accent')
  })

  it('sets --color-accent CSS variable to blue hex when accent is blue', () => {
    renderLayout()
    const value = document.documentElement.style.getPropertyValue('--color-accent')
    expect(value).toBe('#3b82f6')
  })

  it('sets --color-accent CSS variable to pink hex when accent is pink', () => {
    useAppStore.setState({ accentColor: 'pink' })
    renderLayout()
    const value = document.documentElement.style.getPropertyValue('--color-accent')
    expect(value).toBe('#ec4899')
  })

  it('sets --color-accent CSS variable to purple hex when accent is purple', () => {
    useAppStore.setState({ accentColor: 'purple' })
    renderLayout()
    const value = document.documentElement.style.getPropertyValue('--color-accent')
    expect(value).toBe('#8b5cf6')
  })

  it('sets --color-accent CSS variable to cyan hex when accent is cyan', () => {
    useAppStore.setState({ accentColor: 'cyan' })
    renderLayout()
    const value = document.documentElement.style.getPropertyValue('--color-accent')
    expect(value).toBe('#06b6d4')
  })

  it('sets --color-accent CSS variable to orange hex when accent is orange', () => {
    useAppStore.setState({ accentColor: 'orange' })
    renderLayout()
    const value = document.documentElement.style.getPropertyValue('--color-accent')
    expect(value).toBe('#f97316')
  })
})

describe('Settings integration: Feature toggles in Sidebar', () => {
  beforeEach(() => {
    useAppStore.setState({
      osName: 'myOS',
      features: [
        { label: 'Chat', enabled: true },
        { label: 'Tasks', enabled: true },
        { label: 'Ideas', enabled: true },
        { label: 'Agents', enabled: true },
        { label: 'Activity', enabled: true },
        { label: 'Projects', enabled: true },
        { label: 'Drive', enabled: true },
        { label: 'Calendar', enabled: true },
        { label: 'Gmail', enabled: true },
        { label: 'Slack', enabled: true },
        { label: 'GitHub', enabled: true },
        { label: 'Cost Tracking', enabled: true },
      ],
    })
  })

  it('hides Tasks nav item when Tasks feature is disabled', () => {
    useAppStore.setState({
      features: useAppStore.getState().features.map((f) =>
        f.label === 'Tasks' ? { ...f, enabled: false } : f
      ),
    })
    renderSidebar()
    expect(screen.queryByText('Tasks')).not.toBeInTheDocument()
  })

  it('hides Ideas nav item when Ideas feature is disabled', () => {
    useAppStore.setState({
      features: useAppStore.getState().features.map((f) =>
        f.label === 'Ideas' ? { ...f, enabled: false } : f
      ),
    })
    renderSidebar()
    expect(screen.queryByText('Ideas')).not.toBeInTheDocument()
  })

  it('hides Agents nav item when Agents feature is disabled', () => {
    useAppStore.setState({
      features: useAppStore.getState().features.map((f) =>
        f.label === 'Agents' ? { ...f, enabled: false } : f
      ),
    })
    renderSidebar()
    expect(screen.queryByText('Agents')).not.toBeInTheDocument()
  })

  it('hides Files nav item when Projects feature is disabled', () => {
    useAppStore.setState({
      features: useAppStore.getState().features.map((f) =>
        f.label === 'Projects' ? { ...f, enabled: false } : f
      ),
    })
    renderSidebar()
    expect(screen.queryByText('Files')).not.toBeInTheDocument()
  })

  it('hides Activity nav item when Activity feature is disabled', () => {
    useAppStore.setState({
      features: useAppStore.getState().features.map((f) =>
        f.label === 'Activity' ? { ...f, enabled: false } : f
      ),
    })
    renderSidebar()
    expect(screen.queryByText('Activity')).not.toBeInTheDocument()
  })

  it('always shows Home and Settings regardless of feature toggles', () => {
    useAppStore.setState({
      features: useAppStore.getState().features.map((f) => ({ ...f, enabled: false })),
    })
    renderSidebar()
    expect(screen.getByText('Home')).toBeInTheDocument()
    expect(screen.getByText('Settings')).toBeInTheDocument()
  })

  it('shows all items when all features are enabled', () => {
    renderSidebar()
    expect(screen.getByText('Tasks')).toBeInTheDocument()
    expect(screen.getByText('Ideas')).toBeInTheDocument()
    expect(screen.getByText('Agents')).toBeInTheDocument()
    expect(screen.getByText('Files')).toBeInTheDocument()
    expect(screen.getByText('Activity')).toBeInTheDocument()
  })

  it('re-enabling a feature shows the nav item again', () => {
    // Start with Tasks disabled
    useAppStore.setState({
      features: useAppStore.getState().features.map((f) =>
        f.label === 'Tasks' ? { ...f, enabled: false } : f
      ),
    })
    const { unmount } = renderSidebar()
    expect(screen.queryByText('Tasks')).not.toBeInTheDocument()
    unmount()

    // Re-enable Tasks
    useAppStore.setState({
      features: useAppStore.getState().features.map((f) =>
        f.label === 'Tasks' ? { ...f, enabled: true } : f
      ),
    })
    renderSidebar()
    expect(screen.getByText('Tasks')).toBeInTheDocument()
  })
})

describe('Settings integration: OS name in Sidebar', () => {
  beforeEach(() => {
    useAppStore.setState({
      osName: 'myOS',
      features: [
        { label: 'Chat', enabled: true },
        { label: 'Tasks', enabled: true },
        { label: 'Ideas', enabled: true },
        { label: 'Agents', enabled: true },
        { label: 'Activity', enabled: true },
        { label: 'Projects', enabled: true },
        { label: 'Drive', enabled: true },
        { label: 'Calendar', enabled: true },
        { label: 'Gmail', enabled: true },
        { label: 'Slack', enabled: true },
        { label: 'GitHub', enabled: true },
        { label: 'Cost Tracking', enabled: true },
      ],
    })
  })

  it('Sidebar displays the osName from the store', () => {
    renderSidebar()
    expect(screen.getByText('myOS')).toBeInTheDocument()
  })

  it('Sidebar updates when osName changes', () => {
    useAppStore.setState({ osName: 'MyCustomOS' })
    renderSidebar()
    expect(screen.getByText('MyCustomOS')).toBeInTheDocument()
  })
})
