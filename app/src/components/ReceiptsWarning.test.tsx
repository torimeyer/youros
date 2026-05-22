/**
 * RED tests — ReceiptsWarning bubble must render when receipts-warning WS message arrives.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { ChatPanel } from './ChatPanel'
import { useAppStore } from '../stores/app'
import { useRunningAgentsStore } from '../stores/runningAgents'

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

const mockConnect = vi.fn()
const mockDisconnect = vi.fn()
const mockSend = vi.fn()
let mockLastMessage: { type: string; [key: string]: unknown } | null = null

vi.mock('../hooks/useWebSocket', () => ({
  useWebSocket: () => ({
    connect: mockConnect,
    disconnect: mockDisconnect,
    send: mockSend,
    get lastMessage() { return mockLastMessage },
    isConnected: true,
  }),
}))

vi.mock('./MemoryPill', () => ({
  default: () => null,
}))

let uuidCounter = 0
vi.stubGlobal('crypto', {
  randomUUID: () => `test-uuid-${++uuidCounter}`,
})

function setup() {
  uuidCounter = 0
  mockLastMessage = null
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
    lastTerminatedAgent: null,
  })
}

describe('ReceiptsWarning bubble', () => {
  beforeEach(setup)

  it('shows a warning bubble when receipts-warning WS message arrives', () => {
    // Pre-seed an assistant message so the warning has something to attach to.
    const messages = [
      { id: 'msg-1', role: 'user', content: 'Ship it' },
      { id: 'msg-2', role: 'assistant', content: 'The feature is done.', model: 'claude' },
    ]
    localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

    const { rerender } = render(<ChatPanel />)

    // Simulate the receipts-warning arriving after done
    mockLastMessage = {
      type: 'receipts-warning',
      trigger_word: 'done',
      message: "This reply says 'done' but I don't see a commit hash or test output.",
    }
    rerender(<ChatPanel />)

    const warning = screen.getByTestId('receipts-warning-bubble')
    expect(warning).toBeTruthy()
    expect(warning.textContent).toContain("done")
  })

  it('does not show warning bubble when no receipts-warning arrives', () => {
    const messages = [
      { id: 'msg-1', role: 'user', content: 'What is 2+2?' },
      { id: 'msg-2', role: 'assistant', content: 'It is 4.', model: 'claude' },
    ]
    localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

    render(<ChatPanel />)

    expect(screen.queryByTestId('receipts-warning-bubble')).toBeNull()
  })
})
