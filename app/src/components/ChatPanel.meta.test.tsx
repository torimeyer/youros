// →1733 live time+token status, →1734 per-message timestamps
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent } from '@testing-library/react'
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
let mockLastMessage: { type: string; data?: unknown; usage?: unknown } | null = null

vi.mock('../hooks/useWebSocket', () => ({
  useWebSocket: () => ({
    connect: mockConnect,
    disconnect: mockDisconnect,
    send: mockSend,
    get lastMessage() { return mockLastMessage },
    isConnected: true,
  }),
}))

vi.mock('./MemoryPill', () => ({ default: () => null }))

let uuidCounter = 0
vi.stubGlobal('crypto', { randomUUID: () => `meta-uuid-${++uuidCounter}` })

function setup() {
  uuidCounter = 0
  mockLastMessage = null
  localStorage.clear()
  useAppStore.setState({ chatOpen: true, chatWidth: 380, isResizing: false, defaultChatModel: 'claude' })
  useRunningAgentsStore.setState({ count: 0, agents: [], connected: false, lastUpdatedAt: null, lastTerminatedAgent: null })
}

describe('→1733 live status line', () => {
  beforeEach(setup)
  afterEach(() => vi.useRealTimers())

  it('shows live status with elapsed time and token count while streaming', async () => {
    vi.useFakeTimers()
    render(<ChatPanel />)

    const input = screen.getByPlaceholderText(/Type/)
    fireEvent.change(input, { target: { value: 'hello' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    // Streaming starts immediately on send
    expect(screen.getByTestId('chat-live-status')).toBeInTheDocument()

    // Simulate a streaming token arriving
    await act(async () => {
      mockLastMessage = { type: 'token', data: 'Hello world! This is a longer response to test token counting.' }
    })

    // Advance 3 seconds so elapsed ticks
    act(() => { vi.advanceTimersByTime(3000) })

    const statusEl = screen.getByTestId('chat-live-status')
    expect(statusEl.textContent).toMatch(/\d+s|\d+m/)  // has elapsed time
    expect(statusEl.textContent).toContain('tokens')    // has token count
  })

  it('hides live status after done event', async () => {
    vi.useFakeTimers()
    render(<ChatPanel />)

    const input = screen.getByPlaceholderText(/Type/)
    fireEvent.change(input, { target: { value: 'hello' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    expect(screen.getByTestId('chat-live-status')).toBeInTheDocument()

    await act(async () => {
      mockLastMessage = { type: 'done' }
    })

    expect(screen.queryByTestId('chat-live-status')).toBeNull()
  })
})

describe('→1734 per-message timestamps', () => {
  beforeEach(setup)

  it('stamps the user bubble with a timestamp element', async () => {
    render(<ChatPanel />)

    const input = screen.getByPlaceholderText(/Type/)
    fireEvent.change(input, { target: { value: 'hello timestamps' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    const timestamps = screen.getAllByTestId(/^msg-timestamp-/)
    expect(timestamps.length).toBeGreaterThan(0)
    // Should display a time string like "2:30 PM"
    expect(timestamps[0].textContent).toMatch(/\d{1,2}:\d{2}/)
  })

  it('stamps the assistant placeholder with a timestamp element', async () => {
    render(<ChatPanel />)

    const input = screen.getByPlaceholderText(/Type/)
    fireEvent.change(input, { target: { value: 'hello' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    const timestamps = screen.getAllByTestId(/^msg-timestamp-/)
    // Both user and assistant bubbles should have timestamps
    expect(timestamps.length).toBeGreaterThanOrEqual(2)
  })
})
