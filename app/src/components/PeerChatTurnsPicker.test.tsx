import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { PeerChatTurnsPicker } from './PeerChatTurnsPicker'

// Mock the api module — the picker itself does not call api, but
// ChatPanel does. Import here so module resolution is consistent.
vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue({ tabs: [], active_tab_id: '' }),
    post: vi.fn().mockResolvedValue({ ok: true, turns: 2 }),
  },
}))

import { api } from '../lib/api'
import { ChatPanel } from './ChatPanel'
import { useAppStore } from '../stores/app'
import { useRunningAgentsStore } from '../stores/runningAgents'

const mockConnect = vi.fn()
const mockDisconnect = vi.fn()
const mockSend = vi.fn()
let mockLastMessage: Record<string, unknown> | null = null
let mockIsConnected = true

vi.mock('../hooks/useWebSocket', () => ({
  useWebSocket: () => ({
    connect: mockConnect,
    disconnect: mockDisconnect,
    send: mockSend,
    get lastMessage() { return mockLastMessage },
    isConnected: mockIsConnected,
  }),
}))

let uuidCounter = 0
vi.stubGlobal('crypto', {
  randomUUID: () => `test-uuid-${++uuidCounter}`,
})

Element.prototype.scrollIntoView = vi.fn()

function setupAppStore() {
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
}

describe('PeerChatTurnsPicker component', () => {
  it('renders buttons for 1, 2, and 3 turns', () => {
    const onPick = vi.fn()
    render(
      <PeerChatTurnsPicker
        pendingId="test-pending-1"
        participants={['claude', 'gemini']}
        prompt="chat with gemini"
        onPick={onPick}
      />
    )
    expect(screen.getByTestId('peer-chat-turns-picker')).toBeTruthy()
    expect(screen.getByTestId('turns-option-1')).toBeTruthy()
    expect(screen.getByTestId('turns-option-2')).toBeTruthy()
    expect(screen.getByTestId('turns-option-3')).toBeTruthy()
  })

  it('calls onPick with the chosen turn count', () => {
    const onPick = vi.fn()
    render(
      <PeerChatTurnsPicker
        pendingId="test-pending-2"
        participants={['claude', 'gemini']}
        prompt="chat with gemini"
        onPick={onPick}
      />
    )
    fireEvent.click(screen.getByTestId('turns-option-2'))
    expect(onPick).toHaveBeenCalledWith(2)
  })

  it('calls onPick with 1 when "1 turn" is clicked', () => {
    const onPick = vi.fn()
    render(
      <PeerChatTurnsPicker
        pendingId="test-pending-3"
        participants={['claude', 'gemini']}
        prompt="chat with gemini"
        onPick={onPick}
      />
    )
    fireEvent.click(screen.getByTestId('turns-option-1'))
    expect(onPick).toHaveBeenCalledWith(1)
  })

  it('calls onPick with 3 when "3 turns" is clicked', () => {
    const onPick = vi.fn()
    render(
      <PeerChatTurnsPicker
        pendingId="test-pending-4"
        participants={['claude', 'gemini']}
        prompt="chat with gemini"
        onPick={onPick}
      />
    )
    fireEvent.click(screen.getByTestId('turns-option-3'))
    expect(onPick).toHaveBeenCalledWith(3)
  })

  it('shows participant names in the label', () => {
    const onPick = vi.fn()
    render(
      <PeerChatTurnsPicker
        pendingId="test-pending-5"
        participants={['claude', 'gemini']}
        prompt="chat with gemini"
        onPick={onPick}
      />
    )
    const picker = screen.getByTestId('peer-chat-turns-picker')
    expect(picker.textContent).toContain('Claude')
    expect(picker.textContent).toContain('Gemini')
  })
})

describe('ChatPanel pre-send turns picker (→1035)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    uuidCounter = 0
    mockLastMessage = null
    mockIsConnected = true
    localStorage.clear()
    setupAppStore()
  })

  it('renders turns selector on fresh chat before any user send', () => {
    render(<ChatPanel />)
    expect(screen.getByTestId('peer-chat-turns-picker')).toBeTruthy()
  })

  it('hides pre-send picker after user pre-selects a turn count', async () => {
    render(<ChatPanel />)
    const picker = screen.getByTestId('peer-chat-turns-picker')
    expect(picker).toBeTruthy()
    await act(async () => {
      fireEvent.click(screen.getByTestId('turns-option-2'))
    })
    expect(screen.queryByTestId('peer-chat-turns-picker')).toBeNull()
  })

  it.skip('auto-confirms peer_chat_turns_required with pre-selected turns (→1037)', async () => {
    const mockPost = vi.mocked(api.post)
    render(<ChatPanel />)
    await act(async () => {
      fireEvent.click(screen.getByTestId('turns-option-3'))
    })
    await act(async () => {
      mockLastMessage = {
        type: 'peer_chat_turns_required',
        pending_id: 'pending-autoconfirm-1',
        participants: ['claude', 'gemini'],
        prompt: 'chat with gemini',
      }
    })
    expect(mockPost).toHaveBeenCalledWith('/chat/peer/start', {
      pending_id: 'pending-autoconfirm-1',
      turns: 3,
    })
    expect(screen.queryByTestId('peer-chat-turns-picker')).toBeNull()
  })
})

describe('ChatPanel peer_chat_turns_required integration', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    uuidCounter = 0
    mockLastMessage = null
    mockIsConnected = true
    localStorage.clear()
    setupAppStore()
  })

  it('renders the picker when peer_chat_turns_required arrives', async () => {
    render(<ChatPanel />)
    await act(async () => {
      mockLastMessage = {
        type: 'peer_chat_turns_required',
        pending_id: 'pending-abc-123',
        participants: ['claude', 'gemini'],
        prompt: '@gemini chat with claude',
      }
    })
    expect(screen.getByTestId('peer-chat-turns-picker')).toBeTruthy()
  })

  it('POSTs to /chat/peer/start with pending_id and turns when user picks', async () => {
    const mockPost = vi.mocked(api.post)
    render(<ChatPanel />)
    await act(async () => {
      mockLastMessage = {
        type: 'peer_chat_turns_required',
        pending_id: 'pending-xyz-456',
        participants: ['gemini', 'claude'],
        prompt: '@claude chat with gemini',
      }
    })
    const btn = screen.getByTestId('turns-option-2')
    await act(async () => { fireEvent.click(btn) })
    expect(mockPost).toHaveBeenCalledWith('/chat/peer/start', {
      pending_id: 'pending-xyz-456',
      turns: 2,
    })
  })

  it('hides the picker after the user picks turns', async () => {
    render(<ChatPanel />)
    await act(async () => {
      mockLastMessage = {
        type: 'peer_chat_turns_required',
        pending_id: 'pending-789',
        participants: ['claude', 'gemini'],
        prompt: '@gemini chat with claude about X',
      }
    })
    await act(async () => {
      fireEvent.click(screen.getByTestId('turns-option-1'))
    })
    expect(screen.queryByTestId('peer-chat-turns-picker')).toBeNull()
  })
})
