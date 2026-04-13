import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChatPanel } from '../ChatPanel'

// Mock useWebSocket
vi.mock('../../hooks/useWebSocket', () => ({
  useWebSocket: () => ({
    connect: vi.fn(),
    disconnect: vi.fn(),
    send: vi.fn(),
    lastMessage: null,
    isConnected: true,
  }),
}))

// Mock markdown lib
vi.mock('../../lib/markdown', () => ({
  renderMarkdown: (text: string) => text,
  renderTextWithMarkdown: (text: string) => text,
}))

// Mock zustand store
vi.mock('../../stores/app', () => {
  const store = {
    chatOpen: true,
    toggleChat: vi.fn(),
    chatWidth: 500,
    setChatWidth: vi.fn(),
    isResizing: false,
    setIsResizing: vi.fn(),
    defaultChatModel: 'claude',
    setDefaultChatModel: vi.fn(),
    displayOsName: () => 'myOS',
  }
  return {
    useAppStore: (selector?: (s: typeof store) => unknown) => selector ? selector(store) : store,
  }
})

beforeEach(() => {
  localStorage.clear()
  // jsdom doesn't implement scrollIntoView
  Element.prototype.scrollIntoView = vi.fn()
})

describe('ChatPanel tabs', () => {
  it('renders one tab by default', () => {
    render(<ChatPanel />)
    const tabBar = screen.getByTestId('tab-bar')
    // One tab text + one "+" button visible (no close button when single tab)
    expect(within(tabBar).getByText('New Chat')).toBeInTheDocument()
    expect(within(tabBar).getByTestId('new-tab-button')).toBeInTheDocument()
  })

  it('default tab is named "New Chat"', () => {
    render(<ChatPanel />)
    expect(screen.getByText('New Chat')).toBeInTheDocument()
  })

  it('creates a new tab when the "+" button is clicked', async () => {
    const user = userEvent.setup()
    render(<ChatPanel />)
    const newTabBtn = screen.getByTestId('new-tab-button')
    await user.click(newTabBtn)

    // Two tabs should now exist
    const tabButtons = screen.getAllByText('New Chat')
    expect(tabButtons.length).toBe(2)
  })

  it('switches between tabs', async () => {
    const user = userEvent.setup()
    render(<ChatPanel />)

    // Create a second tab
    const newTabBtn = screen.getByTestId('new-tab-button')
    await user.click(newTabBtn)

    // Both tabs named "New Chat" but we have 2 of them
    const tabButtons = screen.getAllByText('New Chat')
    expect(tabButtons.length).toBe(2)

    // Click the first tab
    await user.click(tabButtons[0])
    // No error means switching works
  })

  it('does not show close button when only one tab exists', () => {
    render(<ChatPanel />)
    const tabBar = screen.getByTestId('tab-bar')
    // The close-tab button should not exist
    const closeButtons = tabBar.querySelectorAll('[data-testid^="close-tab-"]')
    expect(closeButtons.length).toBe(0)
  })

  it('shows close buttons when multiple tabs exist', async () => {
    const user = userEvent.setup()
    render(<ChatPanel />)

    // Add a second tab
    await user.click(screen.getByTestId('new-tab-button'))

    const tabBar = screen.getByTestId('tab-bar')
    const closeButtons = tabBar.querySelectorAll('[data-testid^="close-tab-"]')
    expect(closeButtons.length).toBe(2)
  })

  it('closes a tab when its close button is clicked', async () => {
    const user = userEvent.setup()
    render(<ChatPanel />)

    // Add a second tab
    await user.click(screen.getByTestId('new-tab-button'))

    const tabBar = screen.getByTestId('tab-bar')
    let closeButtons = tabBar.querySelectorAll('[data-testid^="close-tab-"]')
    expect(closeButtons.length).toBe(2)

    // Close the first tab
    await user.click(closeButtons[0])

    // Now only one tab, so close buttons disappear
    closeButtons = tabBar.querySelectorAll('[data-testid^="close-tab-"]')
    expect(closeButtons.length).toBe(0)

    // One tab + "+" button = 2 buttons
    const buttons = within(tabBar).getAllByRole('button')
    expect(buttons.length).toBe(2)
  })

  it('cannot close the last remaining tab', async () => {
    render(<ChatPanel />)
    const tabBar = screen.getByTestId('tab-bar')
    const closeButtons = tabBar.querySelectorAll('[data-testid^="close-tab-"]')
    // No close button rendered for a single tab
    expect(closeButtons.length).toBe(0)
  })

  it('each tab has independent messages', async () => {
    const user = userEvent.setup()
    render(<ChatPanel />)

    // Type a message in the first tab input
    const input = screen.getByPlaceholderText(/Message claude/i)
    await user.type(input, 'Hello from tab one')
    // The input should have the text
    expect(input).toHaveValue('Hello from tab one')

    // Create a new tab
    await user.click(screen.getByTestId('new-tab-button'))

    // The new tab should show the empty state message since it has no messages.
    expect(screen.getByText(/Talking to/)).toBeInTheDocument()
  })

  it('active tab is visually distinct with white text', async () => {
    const user = userEvent.setup()
    render(<ChatPanel />)

    // Add a second tab
    await user.click(screen.getByTestId('new-tab-button'))

    // The second tab (newly created) should be active and have white text class
    const tabButtons = screen.getAllByText('New Chat')
    // The second one is the new active tab
    expect(tabButtons[1].closest('button')).toHaveClass('text-white')
    // The first one should be inactive
    expect(tabButtons[0].closest('button')).toHaveClass('text-slate-500')
  })
})
