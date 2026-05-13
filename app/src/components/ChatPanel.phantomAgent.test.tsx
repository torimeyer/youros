/**
 * Regression test for →1291.
 *
 * Bug: ChatPanel.tsx writes the agent name into `trackedAgents` the
 * moment it observes a `spawn_agent` `tool_use` frame. If the backend
 * rejects the spawn (HTTP 409 lock conflict, 400 validation error, name
 * collision with a previously-completed agent, network error), the
 * agent never registers and never appears in the running WebSocket
 * snapshot. The `seenRunningRef` cleanup gate then permanently protects
 * the entry from removal, leaving the "<name> is running" banner stuck
 * on screen while `/api/agents` returns nothing.
 *
 * Fix: when the `tool_result` for a `spawn_agent` tool call arrives,
 * inspect the result string. If it indicates failure (does not start
 * with "Agent '<name>' spawned"), drop the entry from `trackedAgents`
 * so the banner clears immediately.
 *
 * This test reproduces the exact sequence from the report: chat
 * receives a `spawn_agent` `tool_use` with name "agents-layout-fix",
 * then a `tool_result` with a 409 conflict failure string. The banner
 * must not show.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ChatPanel } from './ChatPanel'
import { useAppStore } from '../stores/app'

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
const mockSend = vi.fn()
let mockLastMessage: { type: string; data?: unknown } | null = null

vi.mock('../hooks/useWebSocket', () => ({
  useWebSocket: () => ({
    connect: mockConnect,
    send: mockSend,
    get lastMessage() {
      return mockLastMessage
    },
    isConnected: true,
  }),
}))

let uuidCounter = 0
vi.stubGlobal('crypto', {
  randomUUID: () => `test-uuid-${++uuidCounter}`,
})

describe('ChatPanel phantom agent banner (→1291)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    uuidCounter = 0
    mockLastMessage = null
    localStorage.clear()
    useAppStore.setState({
      chatOpen: true,
      chatWidth: 380,
      isResizing: false,
      defaultChatModel: 'claude',
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('drops trackedAgents entry when spawn_agent tool_result reports failure', () => {
    const { rerender } = render(<ChatPanel />)

    // User asks the assistant to spawn an agent.
    const input = screen.getByPlaceholderText(/Message claude/i)
    fireEvent.change(input, { target: { value: 'spawn agents-layout-fix to fix the layout' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    // Assistant turn starts.
    mockLastMessage = { type: 'token', data: 'Spawning the agent.' }
    rerender(<ChatPanel />)

    // 1. Assistant emits the spawn_agent tool_use frame. ChatPanel adds
    //    "agents-layout-fix" to trackedAgents and the banner appears.
    mockLastMessage = {
      type: 'tool_use',
      data: {
        tool: 'spawn_agent',
        id: 'tu_spawn_1',
        input: { name: 'agents-layout-fix', prompt: 'fix the layout' },
      },
    }
    rerender(<ChatPanel />)

    // Sanity: banner is visible while the spawn is in flight.
    expect(screen.getByTestId('agent-running-banner')).toBeTruthy()
    expect(screen.getByTestId('agent-running-banner').textContent).toContain(
      'agents-layout-fix is running',
    )

    // 2. Backend rejects the spawn with a 409 conflict (the same name
    //    matches a previously-completed agent). The tool_result text
    //    matches the failure string produced by _spawn_agent in
    //    api/services/tool_executor.py.
    mockLastMessage = {
      type: 'tool_result',
      data: {
        id: 'tu_spawn_1',
        result:
          "Failed to spawn agent 'agents-layout-fix' (HTTP 409): " +
          "lock conflict. Check the Agents page for details or try a different agent name.",
      },
    }
    rerender(<ChatPanel />)

    // 3. Banner must be gone. The agent never registered, never
    //    appeared in the WS snapshot, and never produced a terminal
    //    event. The chat panel must reconcile this from the tool_result
    //    text instead of waiting forever.
    expect(screen.queryByTestId('agent-running-banner')).toBeNull()

    // End the stream so the test cleans up.
    mockLastMessage = { type: 'done' }
    rerender(<ChatPanel />)
  })

  it('keeps trackedAgents entry when spawn_agent tool_result reports success', () => {
    const { rerender } = render(<ChatPanel />)

    const input = screen.getByPlaceholderText(/Message claude/i)
    fireEvent.change(input, { target: { value: 'spawn roadmap to plan the next sprint' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    mockLastMessage = { type: 'token', data: 'Spawning the agent.' }
    rerender(<ChatPanel />)

    // tool_use adds the agent to trackedAgents.
    mockLastMessage = {
      type: 'tool_use',
      data: {
        tool: 'spawn_agent',
        id: 'tu_spawn_ok',
        input: { name: 'roadmap', prompt: 'plan the next sprint' },
      },
    }
    rerender(<ChatPanel />)

    expect(screen.getByTestId('agent-running-banner')).toBeTruthy()

    // Success result. Must match the success-prefix produced by
    // _spawn_agent in api/services/tool_executor.py.
    mockLastMessage = {
      type: 'tool_result',
      data: {
        id: 'tu_spawn_ok',
        result:
          "Agent 'roadmap' spawned and running (PID 12345). " +
          'Check the Agents page to monitor it.',
      },
    }
    rerender(<ChatPanel />)

    // Banner must persist. Removal is the WebSocket terminal-event
    // path's job, not the tool_result path's.
    expect(screen.getByTestId('agent-running-banner')).toBeTruthy()
    expect(screen.getByTestId('agent-running-banner').textContent).toContain(
      'roadmap is running',
    )

    mockLastMessage = { type: 'done' }
    rerender(<ChatPanel />)
  })
})
