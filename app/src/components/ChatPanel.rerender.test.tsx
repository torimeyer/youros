// →2982 ChatPanel is always mounted, so it must subscribe to the app store
// with narrow selectors. A whole-store subscription re-renders the 3600-line
// panel on EVERY store write (dashboard polls, calendar polls, settings).
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { Profiler } from 'react'
import { render, act } from '@testing-library/react'
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

vi.mock('../hooks/useWebSocket', () => ({
  useWebSocket: () => ({
    connect: vi.fn(),
    disconnect: vi.fn(),
    send: vi.fn(),
    lastMessage: null,
    isConnected: true,
  }),
}))

vi.mock('./MemoryPill', () => ({ default: () => null }))

describe('→2982 ChatPanel store subscription', () => {
  beforeEach(() => {
    localStorage.clear()
    useAppStore.setState({ chatOpen: true, chatWidth: 380, isResizing: false, defaultChatModel: 'claude' })
    useRunningAgentsStore.setState({ count: 0, agents: [], connected: false, lastUpdatedAt: null, lastTerminatedAgent: null })
  })

  it('does not re-render when an unrelated app-store field changes', async () => {
    const commits = vi.fn()
    render(
      <Profiler id="chat-panel" onRender={commits}>
        <ChatPanel />
      </Profiler>
    )
    // Let mount-time hydration (chat tabs fetch) settle before measuring.
    await act(async () => { await Promise.resolve() })
    await act(async () => { await Promise.resolve() })
    commits.mockClear()

    // whatsNewLastSeen is not read anywhere inside ChatPanel. Writing it
    // must not commit a ChatPanel update once the selectors are narrow.
    act(() => {
      useAppStore.setState({ whatsNewLastSeen: new Date().toISOString() })
    })
    expect(commits).not.toHaveBeenCalled()
  })

  it('still re-renders when a field it displays changes', async () => {
    const commits = vi.fn()
    render(
      <Profiler id="chat-panel" onRender={commits}>
        <ChatPanel />
      </Profiler>
    )
    await act(async () => { await Promise.resolve() })
    await act(async () => { await Promise.resolve() })
    commits.mockClear()

    act(() => {
      useAppStore.setState({ chatWidth: 512 })
    })
    expect(commits).toHaveBeenCalled()
  })
})
