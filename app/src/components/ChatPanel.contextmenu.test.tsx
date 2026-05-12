import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ChatPanel } from './ChatPanel'
import { useAppStore } from '../stores/app'
import { useRunningAgentsStore } from '../stores/runningAgents'
import { useNotificationStore } from '../stores/notifications'
import { api } from '../lib/api'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue({ tabs: [], active_tab_id: '' }),
    post: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  },
}))

Element.prototype.scrollIntoView = vi.fn()

vi.mock('../hooks/useWebSocket', () => ({
  useWebSocket: () => ({
    connect: vi.fn(),
    disconnect: vi.fn(),
    send: vi.fn(),
    lastMessage: null,
    isConnected: true,
  }),
}))

let uuidCounter = 0
vi.stubGlobal('crypto', {
  randomUUID: () => `test-uuid-${++uuidCounter}`,
})

const CHAT_CACHE_KEY = 'myos-chat-messages'

function seedMessage(id: string, role: 'user' | 'assistant', content: string) {
  localStorage.setItem(CHAT_CACHE_KEY, JSON.stringify([{ id, role, content }]))
}

describe('ChatPanel right-click context menu', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    uuidCounter = 0
    localStorage.clear()
    useAppStore.setState({
      chatOpen: true,
      chatWidth: 380,
      isResizing: false,
      defaultChatModel: 'claude',
    })
    useRunningAgentsStore.setState({
      count: 0,
      agents: [],
      connected: false,
      lastUpdatedAt: null,
    })
    useNotificationStore.setState({
      notifications: [],
      toastIds: [],
      firedKeys: new Set(),
      persistentToastIds: new Set(),
      lastFeatureLive: null,
    })
  })

  it('shows context menu on right-click of a message bubble', () => {
    seedMessage('msg-1', 'user', 'Hello world')
    render(<ChatPanel />)

    const bubble = screen.getByTestId('bubble-msg-1')
    fireEvent.contextMenu(bubble)

    expect(screen.getByTestId('chat-message-context-menu')).toBeTruthy()
    expect(screen.getByTestId('context-menu-create-needle')).toBeTruthy()
    expect(screen.getByText('Turn into needle')).toBeTruthy()
  })

  it('suppresses the browser default context menu on message right-click', () => {
    seedMessage('msg-1', 'user', 'Hello world')
    render(<ChatPanel />)

    const bubble = screen.getByTestId('bubble-msg-1')
    const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
    bubble.dispatchEvent(event)

    expect(event.defaultPrevented).toBe(true)
  })

  it('calls POST /tasks with message content and P2 priority when Turn into needle is clicked', async () => {
    seedMessage('msg-1', 'user', 'This is my task')
    render(<ChatPanel />)

    fireEvent.contextMenu(screen.getByTestId('bubble-msg-1'))
    fireEvent.click(screen.getByTestId('context-menu-create-needle'))

    await waitFor(() => {
      expect(vi.mocked(api.post)).toHaveBeenCalledWith('/tasks', {
        title: 'This is my task',
        priority: 'P2',
      })
    })
  })

  it('truncates long messages to 120 chars when filing a needle', async () => {
    const longContent = 'A'.repeat(200)
    seedMessage('msg-1', 'user', longContent)
    render(<ChatPanel />)

    fireEvent.contextMenu(screen.getByTestId('bubble-msg-1'))
    fireEvent.click(screen.getByTestId('context-menu-create-needle'))

    await waitFor(() => {
      expect(vi.mocked(api.post)).toHaveBeenCalledWith('/tasks', {
        title: 'A'.repeat(120),
        priority: 'P2',
      })
    })
  })

  it('shows a success toast after needle is created', async () => {
    seedMessage('msg-1', 'user', 'My task content')
    render(<ChatPanel />)

    fireEvent.contextMenu(screen.getByTestId('bubble-msg-1'))
    fireEvent.click(screen.getByTestId('context-menu-create-needle'))

    await waitFor(() => {
      const state = useNotificationStore.getState()
      expect(state.toastIds.length).toBeGreaterThan(0)
    })
  })

  it('closes the context menu after clicking Turn into needle', async () => {
    seedMessage('msg-1', 'user', 'My task')
    render(<ChatPanel />)

    fireEvent.contextMenu(screen.getByTestId('bubble-msg-1'))
    expect(screen.getByTestId('chat-message-context-menu')).toBeTruthy()

    fireEvent.click(screen.getByTestId('context-menu-create-needle'))

    await waitFor(() => {
      expect(screen.queryByTestId('chat-message-context-menu')).toBeNull()
    })
  })

  it('closes the context menu on click outside', () => {
    seedMessage('msg-1', 'user', 'Hello')
    render(<ChatPanel />)

    fireEvent.contextMenu(screen.getByTestId('bubble-msg-1'))
    expect(screen.getByTestId('chat-message-context-menu')).toBeTruthy()

    fireEvent.click(document.body)

    expect(screen.queryByTestId('chat-message-context-menu')).toBeNull()
  })

  it('works on assistant messages too', () => {
    seedMessage('msg-1', 'assistant', 'Here is my response')
    render(<ChatPanel />)

    fireEvent.contextMenu(screen.getByTestId('bubble-msg-1'))

    expect(screen.getByTestId('chat-message-context-menu')).toBeTruthy()
  })
})
