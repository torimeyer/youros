import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue([]),
  },
}))

import { api } from '../lib/api'
import { useRunningAgentsFeed, EVENTS_STREAM_PATH } from './useRunningAgentsFeed'
import { useRunningAgentsStore } from '../stores/runningAgents'

// →2946: the Running Agents panel subscribes to the consolidated event
// stream at GET /api/events (SSE) instead of the old agents-only WebSocket.

// A tiny in-memory EventSource that lets tests drive the server side.
class FakeEventSource {
  static instances: FakeEventSource[] = []

  onopen: ((ev: unknown) => void) | null = null
  onerror: ((ev: unknown) => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  readyState = 0
  closeCallCount = 0
  url: string

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  close() {
    this.closeCallCount += 1
    this.readyState = 2
  }

  // Test helpers.
  serverOpen() {
    this.readyState = 1
    this.onopen?.({})
  }

  serverEvent(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) })
  }

  serverError() {
    this.onerror?.({})
  }
}

const mockedGet = vi.mocked(api.get)

function resetStore() {
  useRunningAgentsStore.setState({
    count: 0,
    agents: [],
    connected: false,
    lastUpdatedAt: null,
    lastTerminatedAgent: null,
  })
}

describe('useRunningAgentsFeed (consolidated event stream)', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeEventSource.instances = []
    ;(globalThis as { EventSource?: unknown }).EventSource = FakeEventSource
    mockedGet.mockReset()
    mockedGet.mockResolvedValue([])
    resetStore()
  })

  afterEach(() => {
    vi.useRealTimers()
    delete (globalThis as { EventSource?: unknown }).EventSource
  })

  it('opens the consolidated stream at /api/events', () => {
    const { unmount } = renderHook(() => useRunningAgentsFeed())
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(FakeEventSource.instances[0].url).toBe(EVENTS_STREAM_PATH)
    expect(EVENTS_STREAM_PATH).toBe('/api/events')
    unmount()
  })

  it('fetches a snapshot when the stream opens and stores active agents', async () => {
    mockedGet.mockResolvedValue([
      { name: 'runner', status: 'running' },
      { name: 'finished', status: 'completed' },
    ])
    const { unmount } = renderHook(() => useRunningAgentsFeed())
    const es = FakeEventSource.instances[0]

    await act(async () => {
      es.serverOpen()
      await vi.advanceTimersByTimeAsync(0)
    })

    expect(mockedGet).toHaveBeenCalledWith('/agents?user_spawned_only=true')
    const state = useRunningAgentsStore.getState()
    expect(state.connected).toBe(true)
    expect(state.count).toBe(1)
    expect(state.agents).toEqual([{ name: 'runner', status: 'running' }])
    unmount()
  })

  it('refetches the snapshot when an agent event arrives', async () => {
    const { unmount } = renderHook(() => useRunningAgentsFeed())
    const es = FakeEventSource.instances[0]

    await act(async () => {
      es.serverOpen()
      await vi.advanceTimersByTimeAsync(0)
    })
    const callsAfterOpen = mockedGet.mock.calls.length

    mockedGet.mockResolvedValue([{ name: 'fresh', status: 'running' }])
    await act(async () => {
      es.serverEvent({ type: 'agent.delta', payload: { name: 'fresh', status: 'running' } })
      await vi.advanceTimersByTimeAsync(500)
    })

    expect(mockedGet.mock.calls.length).toBeGreaterThan(callsAfterOpen)
    expect(useRunningAgentsStore.getState().agents).toEqual([
      { name: 'fresh', status: 'running' },
    ])
    unmount()
  })

  it('coalesces a burst of agent events into one refetch', async () => {
    const { unmount } = renderHook(() => useRunningAgentsFeed())
    const es = FakeEventSource.instances[0]

    await act(async () => {
      es.serverOpen()
      await vi.advanceTimersByTimeAsync(0)
    })
    const callsAfterOpen = mockedGet.mock.calls.length

    await act(async () => {
      es.serverEvent({ type: 'agent.delta', payload: { name: 'a', status: 'running' } })
      es.serverEvent({ type: 'agent.sweep', payload: {} })
      es.serverEvent({ type: 'agent.delta', payload: { name: 'b', status: 'running' } })
      await vi.advanceTimersByTimeAsync(500)
    })

    expect(mockedGet.mock.calls.length).toBe(callsAfterOpen + 1)
    unmount()
  })

  it('stores terminated-agent feedback from a terminal delta', async () => {
    const { unmount } = renderHook(() => useRunningAgentsFeed())
    const es = FakeEventSource.instances[0]

    await act(async () => {
      es.serverOpen()
      await vi.advanceTimersByTimeAsync(0)
      es.serverEvent({
        type: 'agent.delta',
        payload: {
          name: 'doomed',
          status: 'failed',
          terminal: true,
          feedback: 'The agent stopped before finishing.',
        },
      })
      await vi.advanceTimersByTimeAsync(500)
    })

    expect(useRunningAgentsStore.getState().lastTerminatedAgent).toEqual({
      name: 'doomed',
      status: 'failed',
      feedback: 'The agent stopped before finishing.',
    })
    unmount()
  })

  it('ignores events from other domains on the shared stream', async () => {
    const { unmount } = renderHook(() => useRunningAgentsFeed())
    const es = FakeEventSource.instances[0]

    await act(async () => {
      es.serverOpen()
      await vi.advanceTimersByTimeAsync(0)
    })
    const callsAfterOpen = mockedGet.mock.calls.length

    await act(async () => {
      es.serverEvent({ type: 'dashboard.snapshot', payload: { agents_count: 5 } })
      es.serverEvent({ type: 'task.created', payload: { task_id: '→1' } })
      await vi.advanceTimersByTimeAsync(500)
    })

    expect(mockedGet.mock.calls.length).toBe(callsAfterOpen)
    unmount()
  })

  it('flags the connection down on stream errors', async () => {
    const { unmount } = renderHook(() => useRunningAgentsFeed())
    const es = FakeEventSource.instances[0]

    await act(async () => {
      es.serverOpen()
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(useRunningAgentsStore.getState().connected).toBe(true)

    await act(async () => {
      es.serverError()
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(useRunningAgentsStore.getState().connected).toBe(false)
    unmount()
  })

  it('closes the stream on unmount', () => {
    const { unmount } = renderHook(() => useRunningAgentsFeed())
    const es = FakeEventSource.instances[0]
    unmount()
    expect(es.closeCallCount).toBeGreaterThan(0)
  })

  it('does not throw when EventSource is unavailable (jsdom/App tests)', () => {
    delete (globalThis as { EventSource?: unknown }).EventSource
    const { unmount } = renderHook(() => useRunningAgentsFeed())
    expect(useRunningAgentsStore.getState().connected).toBe(false)
    unmount()
  })
})
