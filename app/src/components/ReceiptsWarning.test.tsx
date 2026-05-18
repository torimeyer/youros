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

  it('shows a warning bubble when receipts-warning WS message arrives', async () => {
    render(<ChatPanel />)

    // Simulate an assistant message arriving
    await act(async () => {
      mockLastMessage = { type: 'text', data: 'The feature is done.' }
    })
    await act(async () => {
      mockLastMessage = { type: 'done' }
    })

    // Simulate the receipts-warning arriving after done
    await act(async () => {
      mockLastMessage = {
        type: 'receipts-warning',
        trigger_word: 'done',
        message: "This reply says 'done' but I don't see a commit hash or test output.",
      }
    })

    const warning = screen.getByTestId('receipts-warning-bubble')
    expect(warning).toBeTruthy()
    expect(warning.textContent).toContain("done")
  })

  it('does not show warning bubble when no receipts-warning arrives', async () => {
    render(<ChatPanel />)

    await act(async () => {
      mockLastMessage = { type: 'text', data: 'Here is some info.' }
    })
    await act(async () => {
      mockLastMessage = { type: 'done' }
    })

    expect(screen.queryByTestId('receipts-warning-bubble')).toBeNull()
  })
})
