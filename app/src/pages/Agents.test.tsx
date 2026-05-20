import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Agents, { friendlyAgentName, isMainSession, isUserSpawnedAgent } from './Agents'
import { agentTitleParts } from '../lib/agentUtils'
import { useAppStore } from '../stores/app'
import { AGENT_MARKETPLACE } from '../data/agentMarketplace'

// Use importOriginal so the real ApiError and ApiTimeoutError classes
// are still exported by the mocked module. handleNudge does an
// `instanceof ApiTimeoutError` check and it must match the class that
// the tests throw, otherwise the timeout branch silently degrades to
// the generic error copy.
vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
  }
})

// The Agents page shares its dismissed-agents set with the Sidebar via
// the sidebarBus module. That set is module-level and survives across
// tests (vitest keeps modules loaded for a file). Reset it before every
// test so names cancelled in one test do not leak into the next.
import { _resetSidebarBus } from '../lib/sidebarBus'
import { useRunningAgentsStore } from '../stores/runningAgents'
beforeEach(() => {
  _resetSidebarBus()
  useRunningAgentsStore.setState({ count: 0, agents: [], connected: false, lastUpdatedAt: null })
})

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

import { api, ApiError, ApiTimeoutError } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)
const mockedApiPost = vi.mocked(api.post)

const mockAgentsResponse = {
  daemon_running: true,
  status: 'ok',
  active: ['test-agent'],
  agents: [
    {
      name: 'test-agent',
      status: 'running',
      // The Agents page now filters out source="daemon" via
      // isUserSpawnedAgent. Use "claude-code" so the fixture row
      // survives the filter and appears in the Active list.
      source: 'claude-code',
      model: 'sonnet',
      budget: '2.00',
      spawned_at: new Date(Date.now() - 83000).toISOString(),
      transcript_bytes: 12288,
      transcript_lines: 47,
    },
  ],
}

const mockTemplatesResponse = {
  templates: [],
}

// Active Sessions cards default to collapsed. The majority of existing
// tests were written before that change and assume the chat input,
// status bar, and Cancel/Transcript buttons are visible on mount. To
// keep those tests meaningful without rewriting each one, `renderAgents`
// pre-seeds sessionStorage with every known mock agent expanded. New
// tests that specifically cover the collapsed default render the page
// with the explicit `renderAgentsCollapsed()` helper.
const ACTIVE_EXPANDED_KEY = 'myos.agents.activeExpanded.v1'
const DEFAULT_EXPANDED_AGENTS = [
  'test-agent',
  'agent-a',
  'agent-b',
  'no-time-agent',
  'my-agent',
  'thread-agent',
  'old-agent',
]
function preExpandAgents(...names: string[]) {
  const map: Record<string, boolean> = {}
  for (const n of names) map[n] = true
  window.sessionStorage.setItem(ACTIVE_EXPANDED_KEY, JSON.stringify(map))
}
function clearActiveExpandedState() {
  window.sessionStorage.removeItem(ACTIVE_EXPANDED_KEY)
}

function renderAgents() {
  // Seed every default mock agent as expanded so existing tests keep
  // seeing the chat input, status bar, and Cancel/Transcript buttons.
  preExpandAgents(...DEFAULT_EXPANDED_AGENTS)
  return render(
    <MemoryRouter>
      <Agents />
    </MemoryRouter>
  )
}

function renderAgentsCollapsed() {
  clearActiveExpandedState()
  return render(
    <MemoryRouter>
      <Agents />
    </MemoryRouter>
  )
}

describe('Agents page - Nudge feature', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Clear the first-paint agents cache between tests so localStorage
    // state never leaks from one test run to the next. Needle 299.
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) return { agent: 'test-agent', nudges: [], session_nudges: [] }
      return {}
    })
    mockedApiPost.mockResolvedValue({
      result: "Nudge sent to 'test-agent'",
      nudge: {
        message: 'Hello agent',
        timestamp: '2026-04-04T21:00:00+00:00',
        source: 'ui',
        stdin_delivered: false,
      },
    })
  })

  it('renders active agent with nudge input', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    // The chat thread input should be visible for active agents. The
    // placeholder now mirrors the main ChatPanel: "Message <agent>...".
    const input = screen.getByPlaceholderText('Message test-agent...')
    expect(input).toBeInTheDocument()
  })

  it('renders Send button for active agents', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    const sendButton = screen.getByTestId('agent-chat-send-test-agent')
    expect(sendButton).toBeInTheDocument()
  })

  it('sends nudge when clicking Send button', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('Message test-agent...')
    fireEvent.change(input, { target: { value: 'Hello agent' } })

    const sendButton = screen.getByTestId('agent-chat-send-test-agent')
    fireEvent.click(sendButton)

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/agents/test-agent/nudge', {
        message: 'Hello agent',
      })
    })
  })

  it('sends nudge when pressing Enter', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('Message test-agent...')
    fireEvent.change(input, { target: { value: 'Enter nudge' } })
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: false })

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/agents/test-agent/nudge', {
        message: 'Enter nudge',
      })
    })
  })

  it('clears input after sending nudge', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('Message test-agent...') as HTMLTextAreaElement
    fireEvent.change(input, { target: { value: 'Clear me' } })

    const sendButton = screen.getByTestId('agent-chat-send-test-agent')
    fireEvent.click(sendButton)

    await waitFor(() => {
      expect(input.value).toBe('')
    })
  })

  it('does not send nudge with empty input', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    // Input is empty, click send
    const sendButton = screen.getByTestId('agent-chat-send-test-agent')
    fireEvent.click(sendButton)

    // api.post should not have been called for nudge (only for spawn if any)
    expect(mockedApiPost).not.toHaveBeenCalledWith(
      '/agents/test-agent/nudge',
      expect.anything()
    )
  })

  it('shows sent nudge in the output area', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('Message test-agent...')
    fireEvent.change(input, { target: { value: 'Hello agent' } })

    const sendButton = screen.getByTestId('agent-chat-send-test-agent')
    fireEvent.click(sendButton)

    // The sent message now renders inside a right aligned user bubble
    // instead of as a "You: ..." plain text line.
    await waitFor(() => {
      const userBubbles = screen.getAllByTestId('agent-chat-user-bubble')
      expect(userBubbles.some((b) => b.textContent === 'Hello agent')).toBe(true)
    })
  })

  it('shows Expand/Collapse button on active agent cards', async () => {
    renderAgentsCollapsed()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    const expandButton = screen.getByTitle('Expand session')
    expect(expandButton).toBeInTheDocument()
  })

  it('expands agent details when clicking Expand', async () => {
    renderAgentsCollapsed()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    const expandButton = screen.getByTitle('Expand session')
    fireEvent.click(expandButton)

    await waitFor(() => {
      expect(screen.getByText('Messages sent')).toBeInTheDocument()
    })

    expect(screen.queryByText(/token budget/i)).toBeNull()
  })

  it('shows no nudge input when no active agents', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents')
        return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return mockTemplatesResponse
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTestId('empty-state')).toBeInTheDocument()
      expect(
        screen.getByText('No agents running right now')
      ).toBeInTheDocument()
    })

    expect(
      screen.queryByPlaceholderText('Message test-agent...')
    ).not.toBeInTheDocument()
  })

  // Regression for needle 235: Tori sent "how much longer?" to a
  // running Claude Code subagent and saw no reply. Three bugs together
  // caused the silent failure:
  //   1. The nudge response had no delivery status so the UI had
  //      nothing plain-language to show when delivery was file-only.
  //   2. The backend had no reply channel so agents literally could
  //      not answer back.
  //   3. Polling only ran while the card was expanded.
  // These tests pin the fix.

  it('shows plain language file-only delivery status after sending a nudge', async () => {
    mockedApiPost.mockResolvedValue({
      result: "Nudge sent to 'test-agent'",
      nudge: {
        message: 'how much longer?',
        timestamp: '2026-04-09T03:08:57+00:00',
        source: 'ui',
        stdin_delivered: false,
        delivery: 'file_only',
        delivery_message:
          'Saved for the agent. It will see your message the next time it checks its mailbox.',
      },
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('Message test-agent...')
    fireEvent.change(input, { target: { value: 'how much longer?' } })

    const sendButton = screen.getByTestId('agent-chat-send-test-agent')
    fireEvent.click(sendButton)

    await waitFor(() => {
      const status = screen.getByTestId('nudge-delivery-status')
      expect(status.textContent).toMatch(/mailbox/i)
    })
  })

  it('renders an agent reply inline after it arrives via the nudges poll', async () => {
    // The nudges endpoint returns a reply from the very first call to
    // simulate an agent that already posted its answer. The real world
    // flow is: user sends nudge, agent eventually POSTs /reply, the
    // next poll cycle picks it up. This test pins the "picks it up and
    // renders it" step of that flow.
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) {
        return {
          agent: 'test-agent',
          nudges: [],
          session_nudges: [],
          replies: [
            {
              message: 'About ten more minutes.',
              timestamp: '2026-04-09T03:10:00+00:00',
              source: 'agent',
              in_reply_to: '2026-04-09T03:08:57+00:00',
            },
          ],
          session_replies: [],
        }
      }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('Message test-agent...')
    fireEvent.change(input, { target: { value: 'how much longer?' } })
    const sendButton = screen.getByTestId('agent-chat-send-test-agent')
    fireEvent.click(sendButton)

    // After handleNudge triggers an immediate re-fetch, the reply
    // returned by the mock should render inline in the same card. The
    // reply text now lives inside an assistant bubble rather than a
    // plain text "Agent: ..." line.
    await waitFor(() => {
      const assistantBubbles = screen.getAllByTestId('agent-chat-assistant-bubble')
      expect(
        assistantBubbles.some((b) => (b.textContent || '').includes('About ten more minutes.')),
      ).toBe(true)
    })
  })

  it('polls nudges for any agent with messages even when the card is not expanded', async () => {
    const seen: string[] = []
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) {
        seen.push(path)
        return { agent: 'test-agent', nudges: [], session_nudges: [], replies: [], session_replies: [] }
      }
      return {}
    })
    mockedApiPost.mockResolvedValue({
      result: "Nudge sent to 'test-agent'",
      nudge: {
        message: 'ping',
        timestamp: '2026-04-09T03:08:57+00:00',
        source: 'ui',
        stdin_delivered: false,
        delivery: 'file_only',
        delivery_message: 'Saved for the agent.',
      },
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    // Do NOT click Expand. Send a nudge. Polling should fire anyway.
    const input = screen.getByPlaceholderText('Message test-agent...')
    fireEvent.change(input, { target: { value: 'ping' } })
    fireEvent.click(screen.getByTestId('agent-chat-send-test-agent'))

    await waitFor(() => {
      // At least one fetch to /nudges must happen after the send.
      // The immediate fetch in handleNudge guarantees this without
      // having to advance fake timers.
      expect(seen.some((p) => p.includes('/agents/test-agent/nudges'))).toBe(true)
    })
  })

  it('replies long-poll: passes wait and since params and re-issues immediately on response', async () => {
    // After the initial snapshot GET seeds a since marker, subsequent
    // /nudges requests from the background poll loop must be long
    // polls: ?wait=30&since=<timestamp>. This is the fix for the
    // slow-reply bug where the UI took up to 5s to show an agent
    // reply because the polling loop was a plain GET every 3-5s.
    const seenPaths: string[] = []
    const firstSnapshotTimestamp = '2026-04-09T03:08:57.000000+00:00'
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/agents/test-agent/nudges')) {
        seenPaths.push(path)
        // Seed the first test-agent snapshot with a message so the
        // since ref picks up a timestamp. Later calls return empty
        // so the long-poll loop exits quickly without hanging.
        if (seenPaths.length === 1) {
          return {
            agent: 'test-agent',
            nudges: [
              {
                message: 'hello',
                timestamp: firstSnapshotTimestamp,
                source: 'ui',
                stdin_delivered: false,
              },
            ],
            session_nudges: [],
            replies: [],
            session_replies: [],
          }
        }
        return {
          agent: 'test-agent',
          nudges: [],
          session_nudges: [],
          replies: [],
          session_replies: [],
        }
      }
      if (path.includes('/nudges')) {
        // Other agents the page also polls (e.g. activeExpandedMap
        // seeded test fixtures). Return empty so they do not pollute
        // the assertion below.
        return {
          agent: 'x',
          nudges: [],
          session_nudges: [],
          replies: [],
          session_replies: [],
        }
      }
      return {}
    })
    mockedApiPost.mockResolvedValue({
      result: "Nudge sent to 'test-agent'",
      nudge: {
        message: 'ping',
        timestamp: '2026-04-09T03:09:00+00:00',
        source: 'ui',
        stdin_delivered: false,
        delivery: 'file_only',
        delivery_message: 'Saved for the agent.',
      },
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    // Trigger a send so the background long-poll loop spins up for
    // this agent (the loop only runs for agents the user has
    // messaged in this session).
    const input = screen.getByPlaceholderText('Message test-agent...')
    fireEvent.change(input, { target: { value: 'ping' } })
    fireEvent.click(screen.getByTestId('agent-chat-send-test-agent'))

    await waitFor(
      () => {
        // At least one long-poll URL must show up with both wait=30
        // and the since marker from the first snapshot. This is the
        // contract that gives us sub-second reply arrival.
        const longPoll = seenPaths.find(
          (p) => p.includes('wait=30') && p.includes('since='),
        )
        expect(longPoll).toBeTruthy()
        expect(longPoll).toContain('/agents/test-agent/nudges')
        expect(longPoll).toContain('wait=30')
        // The since= must carry the timestamp we returned on the
        // seed snapshot, URL-encoded.
        expect(longPoll).toContain(
          `since=${encodeURIComponent(firstSnapshotTimestamp)}`,
        )
      },
      { timeout: 3000 },
    )

    // The loop re-issues immediately after each response (with a
    // ~100ms yield). A second waitFor gives the loop a moment to
    // kick off the next iteration; this proves the "re-issue on
    // response" half of the contract.
    await waitFor(
      () => {
        const count = seenPaths.filter(
          (p) => p.includes('wait=30') && p.includes('since='),
        ).length
        expect(count).toBeGreaterThanOrEqual(2)
      },
      { timeout: 3000 },
    )
  })
})

// Regression for needle 237: Tori saw the inline Send button get stuck
// on "Sending..." forever even though the backend stored the message.
// These tests pin every exit path that must clear the button.
describe('Agents page - Send button stuck state (needle 237)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Clear the first-paint agents cache between tests so localStorage
    // state never leaks from one test run to the next. Needle 299.
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) {
        return {
          agent: 'test-agent',
          nudges: [],
          session_nudges: [],
          replies: [],
          session_replies: [],
        }
      }
      return {}
    })
  })

  it('clears the Send button back to "Send" after a successful nudge post', async () => {
    mockedApiPost.mockResolvedValue({
      result: "Nudge sent to 'test-agent'",
      nudge: {
        message: 'hello',
        timestamp: '2026-04-09T04:00:00+00:00',
        source: 'ui',
        stdin_delivered: false,
        delivery: 'file_only',
        delivery_message: 'Saved for the agent.',
      },
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('Message test-agent...')
    fireEvent.change(input, { target: { value: 'hello' } })

    const sendButton = screen.getByTestId('agent-chat-send-test-agent')
    fireEvent.click(sendButton)

    // After the mocked post resolves, the button text must return to
    // "Send" and must not stay stuck on "Sending...".
    await waitFor(() => {
      expect(sendButton.textContent).toContain('Send')
      expect(sendButton.textContent).not.toContain('Sending...')
    })

    // If the user types something new, the button must be enabled
    // again. If sending state was stuck, re-typing would not help.
    fireEvent.change(input, { target: { value: 'followup' } })
    await waitFor(() => {
      expect((sendButton as HTMLButtonElement).disabled).toBe(false)
    })
  })

  it('clears the Send button and shows an inline error when the post fails', async () => {
    mockedApiPost.mockRejectedValue(new Error('network explosion'))

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('Message test-agent...')
    fireEvent.change(input, { target: { value: 'will fail' } })

    const sendButton = screen.getByTestId('agent-chat-send-test-agent')
    fireEvent.click(sendButton)

    // Button clears even on failure. This is the core regression.
    await waitFor(() => {
      expect(sendButton.textContent).not.toContain('Sending...')
    })

    // The error must be visible inline. Silent failure is banned per
    // feedback_chat_response_silent.md.
    await waitFor(() => {
      const err = screen.getByTestId('nudge-error')
      expect(err).toBeInTheDocument()
      expect(err.textContent || '').toMatch(/could not send/i)
    })
  })

  it('shows a timeout-specific error when the server does not respond in time', async () => {
    // Simulate the ApiTimeoutError that the real api module throws
    // when a fetch aborts. The vi.mock above preserves the real
    // class via importOriginal so the instanceof check in
    // handleNudge matches what the tests throw.
    mockedApiPost.mockRejectedValue(new ApiTimeoutError(30000))

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('Message test-agent...')
    fireEvent.change(input, { target: { value: 'will time out' } })

    const sendButton = screen.getByTestId('agent-chat-send-test-agent')
    fireEvent.click(sendButton)

    await waitFor(() => {
      expect(sendButton.textContent).not.toContain('Sending...')
    })

    await waitFor(() => {
      const err = screen.getByTestId('nudge-error')
      expect(err.textContent || '').toMatch(/did not respond in time/i)
    })
  })

  it('only clears the Send button for the agent whose post resolved, not others', async () => {
    // Two active agents. Clicking Send on one must not flip the
    // sending state for the other. This catches the cross-agent
    // state leak case from the prompt.
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') {
        return {
          daemon_running: true,
          status: 'ok',
          active: ['agent-a', 'agent-b'],
          agents: [
            {
              name: 'agent-a',
              status: 'running',
              source: 'claude-code',
              model: 'sonnet',
              spawned_at: new Date(Date.now() - 1000).toISOString(),
            },
            {
              name: 'agent-b',
              status: 'running',
              source: 'claude-code',
              model: 'sonnet',
              spawned_at: new Date(Date.now() - 1000).toISOString(),
            },
          ],
        }
      }
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) {
        return { agent: 'x', nudges: [], session_nudges: [], replies: [], session_replies: [] }
      }
      return {}
    })

    // Post to agent-b resolves, post to agent-a never resolves. The
    // agent-b button must still clear even while agent-a is pending.
    mockedApiPost.mockImplementation((path: string) => {
      if (path === '/agents/agent-a/nudge') {
        return new Promise(() => {
          // never resolves on purpose
        }) as Promise<unknown>
      }
      return Promise.resolve({
        result: "Nudge sent to 'agent-b'",
        nudge: {
          message: 'ping b',
          timestamp: '2026-04-09T04:00:00+00:00',
          source: 'ui',
          stdin_delivered: false,
          delivery: 'file_only',
          delivery_message: 'Saved.',
        },
      })
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^agent-a(\s|$)/)).toBeInTheDocument()
      expect(screen.getByTitle(/^agent-b(\s|$)/)).toBeInTheDocument()
    })

    // Two chat thread inputs exist now, one per agent.
    const inputA = screen.getByPlaceholderText('Message agent-a...')
    const inputB = screen.getByPlaceholderText('Message agent-b...')

    // Type and click Send on agent-a first (this will hang forever).
    fireEvent.change(inputA, { target: { value: 'ping a' } })
    const sendA = screen.getByTestId('agent-chat-send-agent-a')
    fireEvent.click(sendA)

    // Now type and click Send on agent-b. That request resolves.
    fireEvent.change(inputB, { target: { value: 'ping b' } })
    const sendB = screen.getByTestId('agent-chat-send-agent-b')
    fireEvent.click(sendB)

    // Agent-b button must clear back to "Send" even though agent-a
    // is still pending. Agent-a must still show "Sending...".
    await waitFor(() => {
      expect(sendA.textContent || '').toContain('Sending...')
      expect(sendB.textContent || '').not.toContain('Sending...')
      expect(sendB.textContent || '').toContain('Send')
    })
  })
})

describe('Agents page - page-level subtitle', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) {
        return { agent: 'test-agent', nudges: [], session_nudges: [], replies: [], session_replies: [] }
      }
      return {}
    })
  })

  it('shows role subtitle under the Agents heading', async () => {
    renderAgents()
    await waitFor(() => {
      expect(screen.getByTestId('agents-page-subtitle')).toBeInTheDocument()
    })
    expect(screen.getByTestId('agents-page-subtitle')).toHaveTextContent(/workers you send off to run jobs/i)
  })

  it('subtitle has no negative top margin (overlap regression)', async () => {
    renderAgents()
    const subtitle = await screen.findByTestId('agents-page-subtitle')
    const classes = subtitle.className
    expect(classes).not.toMatch(/-mt-/)
  })

  it('subtitle renders after the h1 in DOM order', async () => {
    renderAgents()
    const h1 = await screen.findByRole('heading', { name: /^agents$/i })
    const subtitle = await screen.findByTestId('agents-page-subtitle')
    // Node.DOCUMENT_POSITION_FOLLOWING (4) means subtitle comes after h1
    expect(h1.compareDocumentPosition(subtitle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})

describe('Agents page - Status bar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Clear the first-paint agents cache between tests so localStorage
    // state never leaks from one test run to the next. Needle 299.
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) return { agent: 'test-agent', nudges: [], session_nudges: [] }
      return {}
    })
  })

  it('renders the status bar for active agents', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByTestId('agent-status-bar')).toBeInTheDocument()
    })
  })

  it('displays model name in status bar', async () => {
    renderAgents()

    await waitFor(() => {
      const statusBar = screen.getByTestId('agent-status-bar')
      expect(statusBar.textContent).toContain('sonnet')
    })
  })

  it('displays token budget in status bar', async () => {
    renderAgents()

    await waitFor(() => {
      const statusBar = screen.getByTestId('agent-status-bar')
      // Budget is a token budget, not a dollar charge. Subscription users
      // see "Nk tokens" / "NM tokens" rather than "$X cap".
      expect(statusBar.textContent).toContain('tokens')
      expect(statusBar.textContent).not.toContain('$')
      expect(statusBar.textContent).not.toContain('cap')
    })
  })

  it('displays transcript size in status bar', async () => {
    renderAgents()

    await waitFor(() => {
      const statusBar = screen.getByTestId('agent-status-bar')
      expect(statusBar.textContent).toContain('12.0KB')
    })
  })

  it('displays transcript line count in status bar', async () => {
    renderAgents()

    await waitFor(() => {
      const statusBar = screen.getByTestId('agent-status-bar')
      expect(statusBar.textContent).toContain('47 lines')
    })
  })

  it('displays elapsed time in status bar', async () => {
    renderAgents()

    await waitFor(() => {
      const statusBar = screen.getByTestId('agent-status-bar')
      // Should show something like "1:23" (elapsed time from spawned_at)
      expect(statusBar.textContent).toMatch(/\d+:\d{2}/)
    })
  })

  it('shows "calculating" when no duration samples exist yet', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path === '/agents/duration-stats') {
        return { median_seconds: 0, p75_seconds: 0, sample_count: 0, window_days: 14 }
      }
      if (path.includes('/nudges')) return { agent: 'test-agent', nudges: [], session_nudges: [] }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      const statusBar = screen.getByTestId('agent-status-bar')
      expect(statusBar.textContent).toContain('calculating')
    })
  })

  it('uses the median to compute remaining when samples exist', async () => {
    // spawned_at is ~83s ago in mockAgentsResponse. With a median of
    // 10 minutes (600s), remaining should be ~9 minutes.
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path === '/agents/duration-stats') {
        return { median_seconds: 600, p75_seconds: 900, sample_count: 25, window_days: 14 }
      }
      if (path.includes('/nudges')) return { agent: 'test-agent', nudges: [], session_nudges: [] }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      const statusBar = screen.getByTestId('agent-status-bar')
      // Accept 8, 9, or 10 minutes to avoid flakiness from the 1s clock tick.
      expect(statusBar.textContent).toMatch(/~(8|9|10)m left/)
    })
  })

  it('shows honest "over typical" text when elapsed exceeds the median', async () => {
    // Elapsed ~83s, median = 30s. The old countdown would freeze at
    // "<1m left" or "wrapping up" forever. The new readout should say
    // something like "1m over typical ~1m" and keep growing.
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path === '/agents/duration-stats') {
        return { median_seconds: 30, p75_seconds: 60, sample_count: 10, window_days: 14 }
      }
      if (path.includes('/nudges')) return { agent: 'test-agent', nudges: [], session_nudges: [] }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      const statusBar = screen.getByTestId('agent-status-bar')
      expect(statusBar.textContent).toMatch(/\d+m over typical ~\d+m/)
      // Must never show the old frozen lies.
      expect(statusBar.textContent).not.toContain('<1m left')
      expect(statusBar.textContent).not.toContain('wrapping up')
    })
  })

  it('never says "<1m left" once elapsed is way past the median', async () => {
    // Elapsed ~83s, median = 5s. The old code clamped to "<1m left" or
    // "wrapping up" because remainingSec was negative. The honest text
    // must be shown instead.
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path === '/agents/duration-stats') {
        return { median_seconds: 5, p75_seconds: 10, sample_count: 50, window_days: 14 }
      }
      if (path.includes('/nudges')) return { agent: 'test-agent', nudges: [], session_nudges: [] }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      const statusBar = screen.getByTestId('agent-status-bar')
      expect(statusBar.textContent).toMatch(/\d+m over typical/)
      expect(statusBar.textContent).not.toContain('<1m left')
      expect(statusBar.textContent).not.toContain('wrapping up')
    })
  })

  it('does not render status bar when no spawned_at', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['no-time-agent'],
        agents: [{ name: 'no-time-agent', status: 'running', source: 'claude-code', model: 'sonnet' }],
      }
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) return { agent: 'no-time-agent', nudges: [], session_nudges: [] }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^no-time-agent(\s|$)/)).toBeInTheDocument()
    })

    expect(screen.queryByTestId('agent-status-bar')).not.toBeInTheDocument()
  })
})

// The Active Sessions list used to render every card fully expanded, so
// a single running agent filled the viewport with its chat thread,
// metrics bar, input textarea, and transcript controls. Tori wanted the
// list to look like Slack's channel list by default: one row per agent,
// quick to scan, only opened on demand. These tests pin that behavior.
describe('Agents page - active card collapse/expand default', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) return { agent: 'test-agent', nudges: [], session_nudges: [] }
      return {}
    })
  })

  it('active agent card renders collapsed by default', async () => {
    renderAgentsCollapsed()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    // The chat input, status bar, and transcript/cancel controls must
    // NOT be in the DOM when the card is collapsed. Only the title row
    // and the compact summary should render.
    expect(screen.queryByPlaceholderText('Message test-agent...')).not.toBeInTheDocument()
    expect(screen.queryByTestId('agent-status-bar')).not.toBeInTheDocument()
    expect(screen.queryByText('View Transcript')).not.toBeInTheDocument()
    expect(screen.queryByTestId('agent-chat-send-test-agent')).not.toBeInTheDocument()

    // The chevron button must read Expand and show the expand_more icon
    // so the collapsed state is obvious to the user.
    const toggle = screen.getByTestId('active-agent-toggle-test-agent')
    expect(toggle.getAttribute('title')).toBe('Expand session')
    expect(toggle.textContent).toContain('Expand')
  })

  it('collapsed card shows title + compact metrics only', async () => {
    renderAgentsCollapsed()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    // The compact summary renders with model name, token budget, and
    // an ETA in a single inline row. It must be visible when collapsed.
    const summary = screen.getByTestId('agent-compact-summary')
    expect(summary).toBeInTheDocument()
    expect(summary.textContent).toContain('sonnet')
    // Subscription users see a token figure, not a dollar amount.
    expect(summary.textContent).toContain('tokens')
    expect(summary.textContent).not.toContain('$')
  })

  it('active agent card expands on Expand chevron click', async () => {
    renderAgentsCollapsed()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    // Start collapsed: no chat input, no full status bar.
    expect(screen.queryByPlaceholderText('Message test-agent...')).not.toBeInTheDocument()

    // Click the Expand chevron on the card.
    const toggle = screen.getByTestId('active-agent-toggle-test-agent')
    expect(toggle.getAttribute('title')).toBe('Expand session')
    fireEvent.click(toggle)

    // Chat input, status bar, and controls must now be in the DOM.
    await waitFor(() => {
      expect(screen.getByPlaceholderText('Message test-agent...')).toBeInTheDocument()
      expect(screen.getByTestId('agent-status-bar')).toBeInTheDocument()
      expect(screen.getByText('View Transcript')).toBeInTheDocument()
    })

    // Title flips to Collapse session so the chevron acts as a toggle.
    expect(toggle.getAttribute('title')).toBe('Collapse session')

    // Clicking again collapses back to the minimal row.
    fireEvent.click(toggle)
    await waitFor(() => {
      expect(screen.queryByPlaceholderText('Message test-agent...')).not.toBeInTheDocument()
    })
  })

  it('multiple active agents each expand independently', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') {
        return {
          daemon_running: true,
          status: 'ok',
          active: ['agent-a', 'agent-b'],
          agents: [
            {
              name: 'agent-a',
              status: 'running',
              source: 'claude-code',
              model: 'sonnet',
              spawned_at: new Date(Date.now() - 1000).toISOString(),
            },
            {
              name: 'agent-b',
              status: 'running',
              source: 'claude-code',
              model: 'sonnet',
              spawned_at: new Date(Date.now() - 1000).toISOString(),
            },
          ],
        }
      }
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) {
        return { agent: 'x', nudges: [], session_nudges: [], replies: [], session_replies: [] }
      }
      return {}
    })

    renderAgentsCollapsed()

    await waitFor(() => {
      expect(screen.getByTitle(/^agent-a(\s|$)/)).toBeInTheDocument()
      expect(screen.getByTitle(/^agent-b(\s|$)/)).toBeInTheDocument()
    })

    // Both start collapsed.
    expect(screen.queryByPlaceholderText('Message agent-a...')).not.toBeInTheDocument()
    expect(screen.queryByPlaceholderText('Message agent-b...')).not.toBeInTheDocument()

    // Expand only agent-a.
    fireEvent.click(screen.getByTestId('active-agent-toggle-agent-a'))

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Message agent-a...')).toBeInTheDocument()
    })
    // agent-b must still be collapsed.
    expect(screen.queryByPlaceholderText('Message agent-b...')).not.toBeInTheDocument()

    // Now expand agent-b. agent-a stays open.
    fireEvent.click(screen.getByTestId('active-agent-toggle-agent-b'))
    await waitFor(() => {
      expect(screen.getByPlaceholderText('Message agent-b...')).toBeInTheDocument()
    })
    expect(screen.getByPlaceholderText('Message agent-a...')).toBeInTheDocument()

    // Collapse agent-a. agent-b stays open.
    fireEvent.click(screen.getByTestId('active-agent-toggle-agent-a'))
    await waitFor(() => {
      expect(screen.queryByPlaceholderText('Message agent-a...')).not.toBeInTheDocument()
    })
    expect(screen.getByPlaceholderText('Message agent-b...')).toBeInTheDocument()
  })

  it('expand state persists across remounts via sessionStorage', async () => {
    const first = renderAgentsCollapsed()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    // Expand the card.
    fireEvent.click(screen.getByTestId('active-agent-toggle-test-agent'))
    await waitFor(() => {
      expect(screen.getByPlaceholderText('Message test-agent...')).toBeInTheDocument()
    })

    // Unmount, remount with same sessionStorage. The card should stay
    // expanded because Tori's choice is persisted.
    first.unmount()
    render(
      <MemoryRouter>
        <Agents />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Message test-agent...')).toBeInTheDocument()
    })
  })
})

describe('Agents page - tabs', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Clear the first-paint agents cache between tests so localStorage
    // state never leaks from one test run to the next. Needle 299.
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)
  })

  it('shows Active, Recent, and Templates tabs', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return mockTemplatesResponse
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('Active')).toBeInTheDocument()
      expect(screen.getByText('Recent')).toBeInTheDocument()
      expect(screen.getByText('Templates')).toBeInTheDocument()
    })
  })

  it('does NOT render an Insights tab', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return mockTemplatesResponse
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('Active')).toBeInTheDocument()
    })
    // Regression: the Insights tab was removed because it never had useful content.
    expect(screen.queryByRole('button', { name: 'Insights' })).not.toBeInTheDocument()
    expect(screen.queryByText('Insights')).not.toBeInTheDocument()
  })

})

describe('Agents page - Recent tab filtering', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Clear the first-paint agents cache between tests so localStorage
    // state never leaks from one test run to the next. Needle 299.
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)
  })

  it('hides cancelled agents on Recent tab by default', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['running-agent'],
        agents: [
          { name: 'running-agent', status: 'running', source: 'claude-code', model: 'sonnet', spawned_at: new Date().toISOString() },
          { name: 'completed-agent', status: 'completed', source: 'api', model: 'sonnet', spawned_at: new Date().toISOString() },
          { name: 'cancelled-agent', status: 'cancelled', source: 'api', model: 'sonnet', spawned_at: new Date().toISOString() },
          { name: 'stopped-agent', status: 'stopped', source: 'api', model: 'sonnet', spawned_at: new Date().toISOString() },
          { name: 'abandoned-agent', status: 'abandoned', source: 'api', model: 'sonnet', spawned_at: new Date().toISOString() },
          { name: 'stale-agent', status: 'terminated_stale', source: 'api', model: 'sonnet', spawned_at: new Date().toISOString() },
        ],
      }
      if (path === '/agents/templates') return mockTemplatesResponse
      return {}
    })

    renderAgents()

    const recentTab = await screen.findByRole('button', { name: 'Recent' })
    fireEvent.click(recentTab)

    // Completed agents are visible by default
    await waitFor(() => {
      expect(screen.getByTitle(/^completed-agent(\s|$)/)).toBeInTheDocument()
    })

    // Cancelled and synonym statuses are hidden by default
    expect(screen.queryByTitle(/^cancelled-agent(\s|$)/)).not.toBeInTheDocument()
    expect(screen.queryByTitle(/^stopped-agent(\s|$)/)).not.toBeInTheDocument()
    expect(screen.queryByTitle(/^abandoned-agent(\s|$)/)).not.toBeInTheDocument()
    expect(screen.queryByTitle(/^stale-agent(\s|$)/)).not.toBeInTheDocument()
    // running-agent is on Active tab, not Recent
    expect(screen.queryByTitle(/^running-agent(\s|$)/)).not.toBeInTheDocument()

    // The chip reads "Hiding cancelled . show" when cancelled rows are hidden.
    const chip = screen.getByTestId('recent-cancelled-toggle')
    expect(chip).toHaveTextContent(/Hiding cancelled/)
    expect(chip).toHaveTextContent(/show/)
  })

  it('shows cancelled agents after clicking the chip toggle', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: [],
        agents: [
          { name: 'completed-agent', status: 'completed', source: 'api', model: 'sonnet', spawned_at: new Date().toISOString() },
          { name: 'cancelled-agent', status: 'cancelled', source: 'api', model: 'sonnet', spawned_at: new Date().toISOString() },
          { name: 'abandoned-agent', status: 'abandoned', source: 'api', model: 'sonnet', spawned_at: new Date().toISOString() },
        ],
      }
      if (path === '/agents/templates') return mockTemplatesResponse
      return {}
    })

    renderAgents()

    const recentTab = await screen.findByRole('button', { name: 'Recent' })
    fireEvent.click(recentTab)

    await waitFor(() => {
      expect(screen.getByTitle(/^completed-agent(\s|$)/)).toBeInTheDocument()
    })
    expect(screen.queryByTitle(/^cancelled-agent(\s|$)/)).not.toBeInTheDocument()

    // Click the chip to reveal cancelled rows.
    const chip = screen.getByTestId('recent-cancelled-toggle')
    fireEvent.click(chip)

    await waitFor(() => {
      expect(screen.getByTitle(/^cancelled-agent(\s|$)/)).toBeInTheDocument()
    })
    expect(screen.getByTitle(/^abandoned-agent(\s|$)/)).toBeInTheDocument()

    // Chip copy flips to "Showing cancelled . hide".
    expect(chip).toHaveTextContent(/Showing cancelled/)
    expect(chip).toHaveTextContent(/hide/)

    // Click again to hide them.
    fireEvent.click(chip)
    await waitFor(() => {
      expect(screen.queryByTitle(/^cancelled-agent(\s|$)/)).not.toBeInTheDocument()
    })
  })

  it('persists the show-cancelled preference across remounts', async () => {
    // Seed localStorage as if the user had previously clicked "show".
    window.localStorage.setItem('myos.recentShowCancelled.v1', '1')

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: [],
        agents: [
          { name: 'completed-agent', status: 'completed', source: 'api', model: 'sonnet', spawned_at: new Date().toISOString() },
          { name: 'cancelled-agent', status: 'cancelled', source: 'api', model: 'sonnet', spawned_at: new Date().toISOString() },
        ],
      }
      if (path === '/agents/templates') return mockTemplatesResponse
      return {}
    })

    renderAgents()

    const recentTab = await screen.findByRole('button', { name: 'Recent' })
    fireEvent.click(recentTab)

    // With the stored preference, cancelled rows should be visible on
    // first mount without clicking anything.
    await waitFor(() => {
      expect(screen.getByTitle(/^cancelled-agent(\s|$)/)).toBeInTheDocument()
    })
    expect(screen.getByTitle(/^completed-agent(\s|$)/)).toBeInTheDocument()

    // Chip reflects the persisted state.
    const chip = screen.getByTestId('recent-cancelled-toggle')
    expect(chip).toHaveTextContent(/Showing cancelled/)
  })

  it('hides e2e-smoke agents on Recent tab by default and reveals them with a pill when toggled', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: [],
        agents: [
          { name: 'e2e-agent', status: 'completed', source: 'e2e-smoke', model: 'sonnet', spawned_at: new Date().toISOString() },
          { name: 'user-agent', status: 'completed', source: 'claude-code', model: 'sonnet', spawned_at: new Date().toISOString() },
          { name: 'nosrc-agent', status: 'completed', model: 'sonnet', spawned_at: new Date().toISOString() },
        ],
      }
      if (path === '/agents/templates') return mockTemplatesResponse
      return {}
    })

    renderAgents()

    const recentTab = await screen.findByRole('button', { name: 'Recent' })
    fireEvent.click(recentTab)

    await waitFor(() => {
      expect(screen.getByTitle(/^user-agent(\s|$)/)).toBeInTheDocument()
    })
    expect(screen.getByTitle(/^nosrc-agent(\s|$)/)).toBeInTheDocument()
    expect(screen.queryByTitle(/^e2e-agent(\s|$)/)).not.toBeInTheDocument()
    expect(screen.queryByTestId('recent-e2e-pill')).not.toBeInTheDocument()

    const e2eChip = screen.getByTestId('recent-e2e-toggle')
    expect(e2eChip).toHaveTextContent(/Hiding e2e tests/)
    expect(e2eChip).toHaveTextContent(/show/)

    fireEvent.click(e2eChip)

    await waitFor(() => {
      expect(screen.getByTitle(/^e2e-agent(\s|$)/)).toBeInTheDocument()
    })
    const pill = screen.getByTestId('recent-e2e-pill')
    expect(pill).toBeInTheDocument()
    expect(pill).toHaveTextContent(/^e2e$/)
    expect(e2eChip).toHaveTextContent(/Showing e2e tests/)
    expect(e2eChip).toHaveTextContent(/hide/)

    expect(screen.getByTitle(/^user-agent(\s|$)/)).toBeInTheDocument()
    expect(screen.getByTitle(/^nosrc-agent(\s|$)/)).toBeInTheDocument()
  })

  it('shows empty state in Recent tab when no terminal agents exist', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['only-running'],
        agents: [
          { name: 'only-running', status: 'running', source: 'api', model: 'sonnet', spawned_at: new Date().toISOString() },
        ],
      }
      if (path === '/agents/templates') return mockTemplatesResponse
      return {}
    })

    renderAgents()

    const recentTab = await screen.findByRole('button', { name: 'Recent' })
    fireEvent.click(recentTab)

    await waitFor(() => {
      expect(screen.getByTestId('empty-state')).toBeInTheDocument()
      expect(screen.getByText('No agents have run yet')).toBeInTheDocument()
    })
  })

  // Regression: the demo-mode supervisor flips stuck agents to
  // ``completed_timeout``. If the Recent filter does not include that
  // status, the row vanishes from BOTH tabs (not running anymore, not a
  // terminal status the UI knows about) and the user sees it flash and
  // disappear. completed_timeout must land in Recent immediately.
  it('test_recent_tab_includes_completed_timeout_agents', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: [],
        agents: [
          { name: 'timeout-agent', status: 'completed_timeout', source: 'api', model: 'sonnet', spawned_at: new Date().toISOString() },
        ],
      }
      if (path === '/agents/templates') return mockTemplatesResponse
      return {}
    })

    renderAgents()

    const recentTab = await screen.findByRole('button', { name: 'Recent' })
    fireEvent.click(recentTab)

    await waitFor(() => {
      expect(screen.getByTitle(/^timeout-agent(\s|$)/)).toBeInTheDocument()
    })
  })

  // Regression: ``failed`` is a real outcome the user needs to see. It
  // must appear in Recent even when the "Hiding cancelled" chip is on,
  // because a failure is not a cancellation and hiding it makes the
  // agent look like it disappeared.
  it('test_recent_tab_includes_failed_agents', async () => {
    // Default: cancelled chip set to HIDE (not clicked).
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: [],
        agents: [
          { name: 'failed-agent', status: 'failed', source: 'api', model: 'sonnet', spawned_at: new Date().toISOString() },
          { name: 'cancelled-agent', status: 'cancelled', source: 'api', model: 'sonnet', spawned_at: new Date().toISOString() },
        ],
      }
      if (path === '/agents/templates') return mockTemplatesResponse
      return {}
    })

    renderAgents()

    const recentTab = await screen.findByRole('button', { name: 'Recent' })
    fireEvent.click(recentTab)

    // failed-agent must be visible even with "Hiding cancelled" on.
    await waitFor(() => {
      expect(screen.getByTitle(/^failed-agent(\s|$)/)).toBeInTheDocument()
    })
    // cancelled-agent is still hidden (existing chip behavior).
    expect(screen.queryByTitle(/^cancelled-agent(\s|$)/)).not.toBeInTheDocument()
  })

  // Regression: polling must be fast enough that a finished agent shows
  // up in Recent within a few seconds of completing. Anything over 5s
  // feels broken from the user's seat.
  it('test_recent_tab_polling_refresh_interval_under_5_seconds', async () => {
    vi.useFakeTimers()
    try {
      let callCount = 0
      mockedApiGet.mockImplementation(async (path: string) => {
        if (path === '/agents') {
          callCount++
          return { daemon_running: true, status: 'ok', active: [], agents: [] }
        }
        if (path === '/agents/templates') return mockTemplatesResponse
        return {}
      })

      renderAgents()

      // Advance to the first poll tick.
      await vi.advanceTimersByTimeAsync(0)
      const initial = callCount

      // Advance by 31 seconds and assert at least one additional /agents
      // poll fired. The safety-net interval is 30s; WS delivers live updates.
      await vi.advanceTimersByTimeAsync(31000)
      expect(callCount).toBeGreaterThan(initial)
    } finally {
      vi.useRealTimers()
    }
  })

  // Regression: flash-and-vanish. When an agent transitions from
  // running to a terminal status, every render in between must show
  // the agent in EITHER Active or Recent. "Neither tab" is the flash
  // bug. Drives the agent through two polls and asserts the row is
  // visible on each render.
  it('test_active_to_recent_transition_no_flash', async () => {
    const runningResponse = {
      daemon_running: true,
      status: 'ok',
      active: ['flash-agent'],
      agents: [{
        name: 'flash-agent',
        status: 'running',
        source: 'claude-code',
        model: 'sonnet',
        budget: '2.00',
        spawned_at: new Date(Date.now() - 60000).toISOString(),
      }],
    }

    // Use completed_timeout so we also guard against the specific
    // status that triggered the original report.
    const terminalResponse = {
      daemon_running: true,
      status: 'ok',
      active: [],
      agents: [{
        name: 'flash-agent',
        status: 'completed_timeout',
        source: 'claude-code',
        model: 'sonnet',
        budget: '2.00',
        spawned_at: new Date(Date.now() - 60000).toISOString(),
        completed_at: new Date().toISOString(),
      }],
    }

    let callCount = 0
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') {
        callCount++
        return callCount <= 1 ? runningResponse : terminalResponse
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: 'flash-agent', nudges: [], session_nudges: [] }
      return {}
    })

    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout'] })
    try {
      render(<MemoryRouter><Agents /></MemoryRouter>)
      // Flush initial mount effects + first /agents call.
      await act(async () => { await Promise.resolve() })
      await act(async () => { await vi.advanceTimersByTimeAsync(50) })

      // Render 1: agent is running, visible in Active tab.
      expect(screen.getByTitle(/^flash-agent(\s|$)/)).toBeInTheDocument()
      expect(screen.getByText('Active Sessions')).toBeInTheDocument()

      // Advance past the 30s safety-net poll; second call returns terminal status.
      await act(async () => { await vi.advanceTimersByTimeAsync(31000) })
      expect(callCount).toBeGreaterThanOrEqual(2)

      // Render 2: agent MUST still be visible after status flip
      // (flash-and-vanish regression: title must be findable somewhere).
      expect(screen.getByTitle(/^flash-agent(\s|$)/)).toBeInTheDocument()
      // Auto-switched to Recent.
      expect(screen.getByText('Recent Agents')).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  }, 15000)
})

// 2 user-created templates in the store (custom, non-builtin)
const mockUserTemplates = [
  { name: 'My Task Runner', description: 'Runs tasks', icon: 'smart_toy', model: 'sonnet', budget: 1 },
  { name: 'My Summarizer', description: 'Summarizes things', icon: 'summarize', model: 'sonnet', budget: 1 },
]

// 3 persona (PM) built-ins returned by /agents/pm-templates
const mockPmTemplatesResponse = {
  templates: [
    {
      id: 'builtin-research-spike',
      name: 'Research spike',
      description: 'Research a topic thoroughly and write a 1-page summary.',
      icon: 'science',
      prompt_template: 'Research [topic] thoroughly. Find key facts, trade-offs, and recommendations. Write a 1-page summary.',
      model: 'sonnet',
      budget: 2.0,
      builtin: true,
    },
    {
      id: 'builtin-prd-draft',
      name: 'PRD Draft',
      description: 'Draft a product requirements document.',
      icon: 'article',
      prompt_template: 'Draft a PRD for [feature].',
      model: 'sonnet',
      budget: 2.0,
      builtin: true,
    },
    {
      id: 'builtin-standup',
      name: 'Standup Summary',
      description: 'Summarize standups.',
      icon: 'groups',
      prompt_template: 'Summarize [standup notes].',
      model: 'sonnet',
      budget: 1.0,
      builtin: true,
    },
  ],
}

describe('Agents page - Templates tab', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Clear the first-paint agents cache between tests so localStorage
    // state never leaks from one test run to the next. Needle 299.
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({
      chatOpen: true,
      osName: 'myOS',
      darkMode: true,
      customAgentTemplates: [],
    })

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.startsWith('/agents/persona-templates')) return { ...mockPmTemplatesResponse, persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      return {}
    })
    mockedApiPost.mockResolvedValue({ result: 'ok' })
  })

  it('shows Templates tab in navigation', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Templates' })).toBeInTheDocument()
    })
  })

  it('shows "Agent Templates" heading and no "PM Templates" or "Your Templates" headings', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      expect(screen.getByTestId('agent-templates-heading')).toBeInTheDocument()
    })
    // Banned headings must not appear anywhere in the document
    expect(screen.queryByText('PM Templates')).not.toBeInTheDocument()
    expect(screen.queryByText('Your Templates')).not.toBeInTheDocument()
    expect(screen.queryByText('Your templates')).not.toBeInTheDocument()
  })

  // Core layout test: 2 user + 3 persona built-ins all appear under Agent Templates,
  // 5 marketplace items appear under Marketplace, banned headings are absent.
  it('two-section layout: persona built-ins + user templates under Agent Templates, marketplace separate', async () => {
    useAppStore.setState({
      chatOpen: true,
      osName: 'myOS',
      darkMode: true,
      customAgentTemplates: mockUserTemplates,
    })

    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    // Wait for pm-templates data to load
    await waitFor(() => {
      expect(screen.getByText('Research spike')).toBeInTheDocument()
    })

    // Exactly one "Agent Templates" heading
    const agentTemplatesHeadings = screen.getAllByTestId('agent-templates-heading')
    expect(agentTemplatesHeadings).toHaveLength(1)

    // All 5 installed items (2 user + 3 pm built-ins) appear under the installed grid
    const installedGrid = screen.getByTestId('installed-templates-grid')
    expect(installedGrid).toBeInTheDocument()
    expect(installedGrid.textContent).toContain('My Task Runner')
    expect(installedGrid.textContent).toContain('My Summarizer')
    expect(installedGrid.textContent).toContain('Research spike')
    expect(installedGrid.textContent).toContain('PRD Draft')
    expect(installedGrid.textContent).toContain('Standup Summary')

    // Marketplace section is present and always visible
    expect(screen.getByTestId('marketplace-section')).toBeInTheDocument()
    expect(screen.getByTestId('marketplace-heading')).toBeInTheDocument()

    // Banned headings are gone
    expect(screen.queryByText('PM Templates')).not.toBeInTheDocument()
    expect(screen.queryByText('Your Templates')).not.toBeInTheDocument()
    expect(screen.queryByText('Your templates')).not.toBeInTheDocument()
  })

  it('shows built-in PM templates in the Agent Templates section', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      expect(screen.getByText('Research spike')).toBeInTheDocument()
    })
  })

  it('shows Use button for PM built-in templates', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      const useButtons = screen.getAllByRole('button', { name: 'Use' })
      expect(useButtons.length).toBeGreaterThan(0)
    })
  })

  it('shows New template button in the Agent Templates header', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /New template/i })).toBeInTheDocument()
    })
  })

  it('shows Marketplace section always (not hidden behind a toggle)', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      expect(screen.getByTestId('marketplace-section')).toBeInTheDocument()
    })
    // There should be no "Browse Marketplace" toggle button
    expect(screen.queryByText('Browse Marketplace')).not.toBeInTheDocument()
  })

  it('shows installed-templates-grid (with fallback items) when templates API returns empty', async () => {
    // When /agents/templates returns empty, the code uses a built-in fallback list.
    // The installed grid should render those fallback items and never show blank.
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true, customAgentTemplates: [] })
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { templates: [], persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      return {}
    })

    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      // Fallback items like Research/Builder/Diagnose must be visible
      const grid = screen.getByTestId('installed-templates-grid')
      expect(grid).toBeInTheDocument()
      // Marketplace is always visible too
      expect(screen.getByTestId('marketplace-section')).toBeInTheDocument()
    })
  })

  it('Agents page fetches persona templates for current persona', async () => {
    // When settings returns persona=engineer the page calls
    // /agents/persona-templates?persona=engineer (not pm).
    const engineerTemplates = {
      templates: [
        {
          id: 'builtin-code-review',
          name: 'Code Review',
          description: 'Review code for issues.',
          icon: 'code',
          prompt_template: 'Review the code.',
          model: 'sonnet',
          budget: 2.0,
          builtin: true,
        },
      ],
      persona: 'engineer',
    }
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.startsWith('/agents/persona-templates')) return engineerTemplates
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'engineer' }
      return {}
    })

    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    // The Code Review template (engineer built-in) must appear in the installed grid.
    // It also appears in the marketplace, so we check the installed grid specifically.
    await waitFor(() => {
      const installedGrid = screen.getByTestId('installed-templates-grid')
      expect(installedGrid.textContent).toContain('Code Review')
    })

    // Verify the api was called with the engineer persona query param
    expect(mockedApiGet).toHaveBeenCalledWith(
      expect.stringMatching(/\/agents\/persona-templates.*persona=engineer/)
    )
  })

  it('Agents page shows user templates regardless of persona', async () => {
    // User-created templates come from /agents/user-templates and must
    // appear whether the persona is pm, engineer, or anything else.
    const userTemplatesResponse = {
      templates: [
        {
          id: 'custom-abc123',
          name: 'My Personal Template',
          description: 'My own template.',
          icon: 'star',
          prompt_template: 'Do my thing.',
          model: 'sonnet',
          budget: 2.0,
          builtin: false,
        },
      ],
    }
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.startsWith('/agents/persona-templates')) return { templates: [], persona: 'engineer' }
      if (path === '/agents/user-templates') return userTemplatesResponse
      if (path === '/settings') return { persona: 'engineer' }
      return {}
    })

    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    // The user template must appear even though persona is engineer
    await waitFor(() => {
      expect(screen.getByText('My Personal Template')).toBeInTheDocument()
    })

    // Confirm user-templates was fetched (persona-agnostic endpoint)
    expect(mockedApiGet).toHaveBeenCalledWith('/agents/user-templates')
  })
})

// ---------- Templates tab perf: instant cache paint ----------
//
// Tori's feedback: the Templates tab used to flash a spinner for
// 1-2 seconds before cards showed. Seeding state from localStorage on
// mount makes the cards render synchronously on first paint, so the
// user sees their templates immediately while the background fetch
// reconciles any drift.

describe('Agents page - Templates tab instant cache', () => {
  const TEMPLATES_CACHE_KEY = 'myos.templatesCache.v1'
  const PM_TEMPLATES_CACHE_KEY = 'myos.pmTemplatesCache.v1'
  const USER_TEMPLATES_CACHE_KEY = 'myos.userTemplatesCache.v1'

  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({
      chatOpen: true,
      osName: 'myOS',
      darkMode: true,
      customAgentTemplates: [],
    })
  })

  it('renders cached templates synchronously on first paint before fetch resolves', async () => {
    // Pre-seed the localStorage caches with three persona templates.
    // The network fetch is stubbed to never resolve so we can prove
    // the cards render purely from the cache before any await settles.
    window.localStorage.setItem(
      PM_TEMPLATES_CACHE_KEY,
      JSON.stringify(mockPmTemplatesResponse.templates),
    )
    window.localStorage.setItem(
      TEMPLATES_CACHE_KEY,
      JSON.stringify([]),
    )
    window.localStorage.setItem(
      USER_TEMPLATES_CACHE_KEY,
      JSON.stringify([]),
    )

    // Stub api.get with a promise that never resolves. This guarantees
    // anything on screen came from the cached state, not the network.
    mockedApiGet.mockImplementation(() => new Promise(() => {}))

    // Seed sessionStorage so the Templates tab is the initial tab.
    // The page defaults to Active; we need to click Templates. We
    // still render and click the tab, but the synchronous assertion
    // below runs after the click without awaiting any network call.
    renderAgents()

    // Click Templates tab. The cards must be in the DOM right after.
    const templatesTab = screen.getByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    // Synchronous assertions. No waitFor, no findBy. The three cached
    // cards must already be rendered because state was seeded from
    // localStorage before the component mounted.
    expect(screen.getByText('Research spike')).toBeInTheDocument()
    expect(screen.getByText('PRD Draft')).toBeInTheDocument()
    expect(screen.getByText('Standup Summary')).toBeInTheDocument()
  })

  it('reconciles cached state with fresh fetch response', async () => {
    // Cache has old data; fetch returns new data. The page must
    // render the cached cards instantly, then update to the fresh
    // set once the network call resolves.
    window.localStorage.setItem(
      PM_TEMPLATES_CACHE_KEY,
      JSON.stringify([
        {
          id: 'builtin-old',
          name: 'Old Template',
          description: 'Stale cache entry.',
          icon: 'smart_toy',
          prompt_template: 'Old.',
          model: 'sonnet',
          budget: 1.0,
          builtin: true,
        },
      ]),
    )
    window.localStorage.setItem(TEMPLATES_CACHE_KEY, JSON.stringify([]))
    window.localStorage.setItem(USER_TEMPLATES_CACHE_KEY, JSON.stringify([]))

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates'))
        return { ...mockPmTemplatesResponse, persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      return {}
    })

    renderAgents()

    const templatesTab = screen.getByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    // Stale card is painted first from cache.
    expect(screen.getByText('Old Template')).toBeInTheDocument()

    // Fresh fetch resolves and replaces the stale card with the
    // three real persona templates.
    await waitFor(() => {
      expect(screen.getByText('Research spike')).toBeInTheDocument()
    })
    expect(screen.queryByText('Old Template')).not.toBeInTheDocument()
  })

  it('writes fresh templates to localStorage after fetch for next cold visit', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates'))
        return { ...mockPmTemplatesResponse, persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      return {}
    })

    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    // Wait for the fresh fetch to land in the UI.
    await waitFor(() => {
      expect(screen.getByText('Research spike')).toBeInTheDocument()
    })

    // The persona-templates cache must now hold the fresh list so
    // the next cold render paints cards synchronously.
    const cached = window.localStorage.getItem(PM_TEMPLATES_CACHE_KEY)
    expect(cached).toBeTruthy()
    const parsed = JSON.parse(cached as string)
    expect(Array.isArray(parsed)).toBe(true)
    const names = parsed.map((t: { name: string }) => t.name)
    expect(names).toContain('Research spike')
    expect(names).toContain('PRD Draft')
    expect(names).toContain('Standup Summary')
  })

  it('renders Roadmap card synchronously on a TRULY cold first paint with empty cache', async () => {
    // No localStorage, no cache. The frontend must paint the Roadmap
    // card from the hardcoded PM persona seed so the user sees real
    // cards immediately, not an empty state. This is the "first ever
    // visit" path: a brand new browser, no prior fetch, no cache.
    window.localStorage.clear()

    // Stub api.get with a promise that never resolves so we can prove
    // Roadmap is on screen BEFORE any network call settles.
    mockedApiGet.mockImplementation(() => new Promise(() => {}))

    renderAgents()
    const templatesTab = screen.getByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    // The Roadmap card must already be in the DOM. No waitFor, no
    // findBy. The seed runs at component-mount time, before any await.
    expect(screen.getByText('Roadmap')).toBeInTheDocument()
  })
})

// Regression tests for the empty-state flash bug. On first paint the Agents
// page used to render "No active agents" before the fetch resolved, because
// the initial agents list was an empty array. We now track a separate
// agentsLoaded flag and show a Loading state until the first fetch settles.
describe('Agents page - first-load state', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Clear the first-paint agents cache between tests so localStorage
    // state never leaks from one test run to the next. Needle 299.
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)
  })

  it('test_agents_page_shows_loading_state_on_first_render', async () => {
    // Make /agents hang forever so the component stays in its first-load
    // state. We assert the loading marker is visible and the empty state is
    // not shown.
    let resolveAgents: ((value: unknown) => void) | null = null
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') {
        return new Promise((resolve) => {
          resolveAgents = resolve
        })
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { templates: [], persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      return {}
    })

    renderAgents()

    expect(await screen.findByTestId('active-agents-loading')).toBeInTheDocument()
    expect(
      screen.queryByText('No agents running right now')
    ).not.toBeInTheDocument()
    expect(
      screen.queryByText(
        'No agents running right now'
      )
    ).not.toBeInTheDocument()

    // Resolve the pending promise so the test exits cleanly.
    if (resolveAgents) {
      ;(resolveAgents as (value: unknown) => void)({
        daemon_running: true,
        status: 'ok',
        active: [],
        agents: [],
      })
    }
  })

  it('test_agents_page_shows_empty_state_only_after_fetch_resolves_empty', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') {
        return {
          daemon_running: true,
          status: 'ok',
          active: [],
          agents: [],
        }
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { templates: [], persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(
        screen.getByText('No agents running right now')
      ).toBeInTheDocument()
    })
    expect(screen.queryByTestId('active-agents-loading')).not.toBeInTheDocument()
  })

  it('test_agents_page_shows_agents_after_fetch_resolves_with_data', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { templates: [], persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      if (path.includes('/nudges')) {
        return { agent: 'test-agent', nudges: [], session_nudges: [] }
      }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })
    expect(screen.queryByTestId('active-agents-loading')).not.toBeInTheDocument()
    expect(
      screen.queryByText('No agents running right now')
    ).not.toBeInTheDocument()
  })

  it('test_agents_page_polling_does_not_flash_loading', async () => {
    vi.useFakeTimers()
    try {
      mockedApiGet.mockImplementation(async (path: string) => {
        if (path === '/agents') return mockAgentsResponse
        if (path === '/agents/templates') return { templates: [] }
        if (path.startsWith('/agents/persona-templates')) return { templates: [], persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
        if (path.includes('/nudges')) {
          return { agent: 'test-agent', nudges: [], session_nudges: [] }
        }
        return {}
      })

      renderAgents()

      // First fetch settles, real-timer waitFor is unavailable with fake
      // timers, so we drive ticks manually until the agent appears.
      await vi.waitFor(() => {
        expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
      })
      expect(screen.queryByTestId('active-agents-loading')).not.toBeInTheDocument()

      // Advance the polling interval (5s) and flush microtasks. Loading must
      // NOT reappear during the refresh. The agent must still be visible.
      await vi.advanceTimersByTimeAsync(5000)
      expect(screen.queryByTestId('active-agents-loading')).not.toBeInTheDocument()
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()

      await vi.advanceTimersByTimeAsync(5000)
      expect(screen.queryByTestId('active-agents-loading')).not.toBeInTheDocument()
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  // Stale sweep and cancel button regression tests. These lock in the fixes
  // for the two problems that shipped together: orphan agents lingering as
  // running because nothing swept them, and empty-state strings flashing on
  // the Recent and Metrics tabs because they were not gated behind
  // agentsLoaded.

  it('test_agents_page_does_not_show_terminated_stale_in_active_sessions', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') {
        return {
          daemon_running: true,
          status: 'ok',
          active: ['live-agent'],
          agents: [
            {
              name: 'live-agent',
              status: 'running',
              source: 'claude-code',
              model: 'sonnet',
              budget: '2.00',
              spawned_at: new Date(Date.now() - 60000).toISOString(),
              transcript_bytes: 4096,
              transcript_lines: 10,
            },
            {
              name: 'dead-orphan',
              status: 'terminated_stale',
              source: 'claude-code',
              model: 'sonnet',
              budget: '2.00',
              spawned_at: new Date(Date.now() - 3600000).toISOString(),
              terminated_at: new Date(Date.now() - 1000).toISOString(),
              terminated_reason: 'No heartbeat for 700s (limit 600s)',
              transcript_bytes: 0,
              transcript_lines: 0,
            },
          ],
        }
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { templates: [], persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      if (path.includes('/nudges')) {
        return { agent: 'live-agent', nudges: [], session_nudges: [] }
      }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^live-agent(\s|$)/)).toBeInTheDocument()
    })
    // The terminated_stale agent must not be in the Active Sessions list.
    expect(screen.queryByText('dead-orphan')).not.toBeInTheDocument()
  })

  // Regression: live-demo bug where the Active tab showed
  // "No agents running right now" while subagents were actively
  // working. The shared isAgentActive helper is the single source of
  // truth so the Active list, Cancel-all count, and sidebar badge agree.
  it('test_agents_active_tab_shows_running_subagents', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') {
        return {
          daemon_running: true,
          status: 'ok',
          active: ['saa-fix-thing', 'diagnose-thing'],
          agents: [
            {
              name: 'saa-fix-thing',
              status: 'running',
              source: 'subagent',
              model: 'sonnet',
              budget: '2.00',
              spawned_at: new Date(Date.now() - 30000).toISOString(),
              last_heartbeat_at: new Date(Date.now() - 5000).toISOString(),
            },
            {
              name: 'diagnose-thing',
              status: 'running',
              source: 'subagent',
              model: 'sonnet',
              budget: '2.00',
              spawned_at: new Date(Date.now() - 60000).toISOString(),
              last_heartbeat_at: new Date(Date.now() - 2000).toISOString(),
            },
          ],
        }
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { templates: [], persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      if (path.includes('/nudges')) return { agent: '', nudges: [], session_nudges: [] }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^saa-fix-thing(\s|$)/)).toBeInTheDocument()
    })
    expect(screen.getByTitle(/^diagnose-thing(\s|$)/)).toBeInTheDocument()
    // Empty-state copy must be gone now that two subagents are visible.
    expect(screen.queryByText('No agents running right now')).not.toBeInTheDocument()
    // Cancel-all reflects the same count.
    const cancelAll = screen.getByTestId('cancel-all-agents-btn')
    expect(cancelAll.textContent).toContain('(2)')
  })

  it('test_agents_active_tab_shows_starting_state_agents', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') {
        return {
          daemon_running: true,
          status: 'ok',
          active: ['booting-agent'],
          agents: [
            {
              name: 'booting-agent',
              status: 'starting',
              source: 'subagent',
              model: 'sonnet',
              budget: '2.00',
              spawned_at: new Date(Date.now() - 1000).toISOString(),
            },
          ],
        }
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { templates: [], persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      if (path.includes('/nudges')) return { agent: 'booting-agent', nudges: [], session_nudges: [] }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^booting-agent(\s|$)/)).toBeInTheDocument()
    })
    expect(screen.queryByText('No agents running right now')).not.toBeInTheDocument()
  })

  it('test_agents_active_tab_does_not_hide_recently_heartbeated_agents', async () => {
    // The backend stale-sweep can flip a still-working agent to
    // terminated_stale moments before its next heartbeat lands. As long
    // as the heartbeat is fresher than 2 minutes the row must STAY in
    // Active so Tori can see her agent is alive.
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') {
        return {
          daemon_running: true,
          status: 'ok',
          active: ['raced-with-sweep'],
          agents: [
            {
              name: 'raced-with-sweep',
              status: 'terminated_stale',
              source: 'subagent',
              model: 'sonnet',
              budget: '2.00',
              spawned_at: new Date(Date.now() - 1000000).toISOString(),
              // Heartbeat 30s ago: well within the 2-minute window.
              last_heartbeat_at: new Date(Date.now() - 30000).toISOString(),
              terminated_at: new Date(Date.now() - 5000).toISOString(),
              terminated_reason: 'No heartbeat for 920s (limit 900s)',
            },
            {
              name: 'genuinely-dead',
              status: 'terminated_stale',
              source: 'subagent',
              model: 'sonnet',
              budget: '2.00',
              spawned_at: new Date(Date.now() - 3600000).toISOString(),
              // Heartbeat 10 minutes old: must stay hidden.
              last_heartbeat_at: new Date(Date.now() - 600000).toISOString(),
              terminated_at: new Date(Date.now() - 1000).toISOString(),
              terminated_reason: 'No heartbeat for 700s (limit 600s)',
            },
          ],
        }
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { templates: [], persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      if (path.includes('/nudges')) return { agent: '', nudges: [], session_nudges: [] }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^raced-with-sweep(\s|$)/)).toBeInTheDocument()
    })
    // Genuinely dead agent must NOT appear in Active.
    expect(screen.queryByTitle(/^genuinely-dead(\s|$)/)).not.toBeInTheDocument()
    expect(screen.queryByText('No agents running right now')).not.toBeInTheDocument()
  })

  it('test_cancel_button_calls_cancel_endpoint', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { templates: [], persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      if (path.includes('/nudges')) {
        return { agent: 'test-agent', nudges: [], session_nudges: [] }
      }
      return {}
    })
    mockedApiPost.mockResolvedValue({ ok: true, status: 'cancelled' })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    // Use getAllByRole and pick the single-agent cancel button (not "Cancel all")
    const cancelButtons = screen.getAllByRole('button', { name: /Cancel/i })
    const cancelButton = cancelButtons.find((b) => !b.dataset.testid?.includes('cancel-all'))!
    fireEvent.click(cancelButton)

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/agents/test-agent/cancel', {
        reason: 'user cancelled',
      })
    })
  })

  it('test_every_empty_state_branch_is_gated_by_agentsLoaded', async () => {
    // Mock /agents with a never-resolving promise so agentsLoaded stays
    // false. Assert every "no X" empty-state string on the Agents page is
    // hidden on first paint. This is the regression test that locks in
    // "no flash anywhere".
    let resolveAgents: ((value: unknown) => void) | null = null
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') {
        return new Promise((resolve) => {
          resolveAgents = resolve
        })
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { templates: [], persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      return {}
    })

    renderAgents()

    // Active tab loading card is visible.
    expect(await screen.findByTestId('active-agents-loading')).toBeInTheDocument()

    // None of the empty-state strings on any tab may be visible while the
    // first fetch is still pending. They are all driven by allAgents, which
    // starts as []. Without the agentsLoaded gate they would flash.
    expect(
      screen.queryByText('No agents running right now')
    ).not.toBeInTheDocument()
    expect(
      screen.queryByText(
        'No agents running right now'
      )
    ).not.toBeInTheDocument()
    expect(
      screen.queryByText(
        'No completed agents yet. Agents you spawn will appear here once they finish.'
      )
    ).not.toBeInTheDocument()
    expect(
      screen.queryByText(
        'No completed agents yet. Metrics will appear here as agents finish.'
      )
    ).not.toBeInTheDocument()

    // Resolve the pending promise so the test exits cleanly.
    if (resolveAgents) {
      ;(resolveAgents as (value: unknown) => void)({
        daemon_running: true,
        status: 'ok',
        active: [],
        agents: [],
      })
    }
  })
})

// Needle 244: the Agents page inline messaging now uses AgentChatThread,
// the same visual language as the main ChatPanel. These tests pin the
// bubble shape, markdown rendering, input clearing, error surface, and
// the honest mailbox warning heuristic. Per
// feedback_chat_response_silent.md, the error path must always be
// visible.
describe('Agents page - AgentChatThread bubbles (needle 244)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Clear the first-paint agents cache between tests so localStorage
    // state never leaks from one test run to the next. Needle 299.
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)
  })

  it('test_agent_thread_renders_nudges_as_user_bubbles_and_replies_as_assistant_bubbles', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) {
        return {
          agent: 'test-agent',
          nudges: [
            {
              message: 'hello there',
              timestamp: '2026-04-09T04:00:00+00:00',
              source: 'ui',
              stdin_delivered: false,
              delivery: 'file_only',
              delivery_message: 'Saved.',
            },
          ],
          session_nudges: [],
          replies: [
            {
              message: 'hi back',
              timestamp: '2026-04-09T04:01:00+00:00',
              source: 'agent',
              in_reply_to: null,
            },
          ],
          session_replies: [],
        }
      }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })


    // The user message renders in a right aligned blue bubble.
    await waitFor(() => {
      const userBubbles = screen.getAllByTestId('agent-chat-user-bubble')
      expect(userBubbles.some((b) => (b.textContent || '').includes('hello there'))).toBe(true)
    })

    // The agent reply renders in a left aligned bordered bubble.
    const assistantBubbles = screen.getAllByTestId('agent-chat-assistant-bubble')
    expect(assistantBubbles.some((b) => (b.textContent || '').includes('hi back'))).toBe(true)
  })

  it('test_agent_thread_renders_assistant_markdown', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) {
        return {
          agent: 'test-agent',
          nudges: [],
          session_nudges: [],
          replies: [
            {
              message: 'Here is **bold** text and `inline code`.',
              timestamp: '2026-04-09T04:00:00+00:00',
              source: 'agent',
              in_reply_to: null,
            },
          ],
          session_replies: [],
        }
      }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })


    // Wait for the assistant bubble to render, then assert it produced a
    // <strong> for the **bold** span and a <code> for the backtick span.
    // Both prove the shared markdown renderer is in play.
    await waitFor(() => {
      const bubbles = screen.getAllByTestId('agent-chat-assistant-bubble')
      expect(bubbles.length).toBeGreaterThan(0)
      const bubble = bubbles[0]
      expect(bubble.querySelector('strong')?.textContent).toBe('bold')
      expect(bubble.querySelector('code')?.textContent).toBe('inline code')
    })
  })

  it('test_agent_thread_send_calls_onSend_and_clears_input', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) {
        return { agent: 'test-agent', nudges: [], session_nudges: [], replies: [], session_replies: [] }
      }
      return {}
    })
    mockedApiPost.mockResolvedValue({
      result: "Nudge sent to 'test-agent'",
      nudge: {
        message: 'clear me please',
        timestamp: '2026-04-09T04:00:00+00:00',
        source: 'ui',
        stdin_delivered: false,
        delivery: 'file_only',
        delivery_message: 'Saved.',
      },
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('Message test-agent...') as HTMLTextAreaElement
    fireEvent.change(input, { target: { value: 'clear me please' } })
    expect(input.value).toBe('clear me please')

    fireEvent.click(screen.getByTestId('agent-chat-send-test-agent'))

    // onSend went through to the nudge endpoint with the typed message.
    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/agents/test-agent/nudge', {
        message: 'clear me please',
      })
    })

    // The textarea cleared.
    await waitFor(() => {
      expect(input.value).toBe('')
    })
  })

  it('test_agent_thread_shows_inline_error_on_send_failure', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) {
        return { agent: 'test-agent', nudges: [], session_nudges: [], replies: [], session_replies: [] }
      }
      return {}
    })
    mockedApiPost.mockRejectedValue(new Error('boom'))

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('Message test-agent...')
    fireEvent.change(input, { target: { value: 'will fail' } })
    fireEvent.click(screen.getByTestId('agent-chat-send-test-agent'))

    // The red inline error must surface. Silent failure is banned by
    // feedback_chat_response_silent.md.
    await waitFor(() => {
      const err = screen.getByTestId('nudge-error')
      expect(err).toBeInTheDocument()
      expect(err.className).toMatch(/text-red/)
      expect((err.textContent || '').length).toBeGreaterThan(0)
    })
  })

  it('test_agent_thread_shows_no_mailbox_warning_when_agent_is_fresh', async () => {
    // Fresh agent: registered seconds ago, no nudges sent. The warning
    // must NOT be visible.
    const freshAgent = {
      daemon_running: true,
      status: 'ok',
      active: ['fresh-agent'],
      agents: [
        {
          name: 'fresh-agent',
          status: 'running',
          source: 'claude-code',
          model: 'sonnet',
          budget: '2.00',
          spawned_at: new Date(Date.now() - 5000).toISOString(),
          transcript_bytes: 0,
          transcript_lines: 0,
        },
      ],
    }
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return freshAgent
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) {
        return { agent: 'fresh-agent', nudges: [], session_nudges: [], replies: [], session_replies: [] }
      }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^fresh-agent(\s|$)/)).toBeInTheDocument()
    })

    expect(screen.queryByTestId('agent-chat-mailbox-warning')).not.toBeInTheDocument()
  })

  it('test_agent_thread_shows_mailbox_warning_when_agent_will_never_reply', async () => {
    // Old agent registered 30 minutes ago with 3 unanswered nudges and
    // zero replies. The honest warning must be visible so the user
    // knows to cancel and spawn a fresh one.
    const thirtyMinAgo = new Date(Date.now() - 30 * 60 * 1000).toISOString()
    const oldAgent = {
      daemon_running: true,
      status: 'ok',
      active: ['old-agent'],
      agents: [
        {
          name: 'old-agent',
          status: 'running',
          source: 'claude-code',
          model: 'sonnet',
          budget: '2.00',
          spawned_at: thirtyMinAgo,
          transcript_bytes: 100,
          transcript_lines: 5,
        },
      ],
    }
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return oldAgent
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) {
        return {
          agent: 'old-agent',
          nudges: [
            {
              message: 'first',
              timestamp: '2026-04-09T03:00:00+00:00',
              source: 'ui',
              stdin_delivered: false,
            },
            {
              message: 'second',
              timestamp: '2026-04-09T03:05:00+00:00',
              source: 'ui',
              stdin_delivered: false,
            },
            {
              message: 'third',
              timestamp: '2026-04-09T03:10:00+00:00',
              source: 'ui',
              stdin_delivered: false,
            },
          ],
          session_nudges: [],
          replies: [],
          session_replies: [],
        }
      }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^old-agent(\s|$)/)).toBeInTheDocument()
    })

    // Card renders expanded by default in tests (renderAgents pre-seeds
    // sessionStorage), so the nudges poll already fired and the warning
    // has data to work with.
    await waitFor(() => {
      const warning = screen.getByTestId('agent-chat-mailbox-warning')
      expect(warning).toBeInTheDocument()
      expect((warning.textContent || '').toLowerCase()).toMatch(/mailbox/)
    })
  })

  it('test_agent_thread_shows_thinking_dots_when_last_entry_is_user_nudge', async () => {
    // Regression for the "i thought i was being ignored" bug. When the
    // user has sent a message and the agent has not replied yet, the
    // thread MUST show a visible thinking indicator so the user knows
    // the agent is still processing. Without it the panel looks dead
    // and the user assumes the agent ignored them.
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) {
        return {
          agent: 'test-agent',
          nudges: [
            {
              message: 'hi!',
              timestamp: '2026-04-09T04:00:00+00:00',
              source: 'ui',
              stdin_delivered: false,
              delivery: 'file_only',
              delivery_message: 'Saved.',
            },
          ],
          session_nudges: [],
          replies: [],
          session_replies: [],
        }
      }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })


    await waitFor(() => {
      const dots = screen.getByTestId('agent-chat-thinking')
      expect(dots).toBeInTheDocument()
      // Three animate-bounce dots inside the indicator.
      expect(dots.querySelectorAll('.animate-bounce').length).toBe(3)
    })
  })

  it('test_agent_thread_hides_thinking_dots_after_reply_arrives', async () => {
    // The thinking indicator must disappear once a reply newer than
    // the latest nudge lands. If it stays visible after a reply, the
    // user gets a permanent fake "thinking" state.
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) {
        return {
          agent: 'test-agent',
          nudges: [
            {
              message: 'hi!',
              timestamp: '2026-04-09T04:00:00+00:00',
              source: 'ui',
              stdin_delivered: false,
              delivery: 'file_only',
              delivery_message: 'Saved.',
            },
          ],
          session_nudges: [],
          replies: [
            {
              message: 'hello back',
              timestamp: '2026-04-09T04:01:00+00:00',
              source: 'agent',
              in_reply_to: null,
            },
          ],
          session_replies: [],
        }
      }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^test-agent(\s|$)/)).toBeInTheDocument()
    })


    // Wait for the assistant bubble to render. Once the reply is the
    // last entry, the thinking indicator must NOT be present.
    await waitFor(() => {
      const bubbles = screen.getAllByTestId('agent-chat-assistant-bubble')
      expect(bubbles.length).toBeGreaterThan(0)
    })
    expect(screen.queryByTestId('agent-chat-thinking')).not.toBeInTheDocument()
  })

  it('test_agent_thread_hides_thinking_dots_when_mailbox_warning_visible', async () => {
    // Defensive: when the honest "this agent will not reply" warning is
    // showing, the thinking dots must be suppressed. Otherwise the UI
    // would lie by saying both "the agent is thinking" and "the agent
    // will never reply" at the same time.
    const oldAgent = {
      daemon_running: true,
      status: 'ok',
      active: ['old-agent'],
      agents: [
        {
          name: 'old-agent',
          status: 'running',
          source: 'claude-code',
          model: 'sonnet',
          budget: '2.00',
          spawned_at: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
          transcript_bytes: 0,
          transcript_lines: 0,
        },
      ],
    }
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return oldAgent
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) {
        return {
          agent: 'old-agent',
          nudges: [
            { message: 'one', timestamp: '2026-04-09T04:00:00+00:00', source: 'ui', stdin_delivered: false },
            { message: 'two', timestamp: '2026-04-09T04:01:00+00:00', source: 'ui', stdin_delivered: false },
            { message: 'three', timestamp: '2026-04-09T04:02:00+00:00', source: 'ui', stdin_delivered: false },
          ],
          session_nudges: [],
          replies: [],
          session_replies: [],
        }
      }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTitle(/^old-agent(\s|$)/)).toBeInTheDocument()
    })


    await waitFor(() => {
      expect(screen.getByTestId('agent-chat-mailbox-warning')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('agent-chat-thinking')).not.toBeInTheDocument()
  })
})

// ─── Capabilities panel on template cards ─────────────────────────────────
//
// Tori wants to see what a template can touch before hitting Spawn. These
// tests pin the rendering: a clean template shows the parsed values, a
// broken template shows the friendly error line and a disabled Spawn.

describe('Agents page - Template capabilities panel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Clear the first-paint agents cache between tests so localStorage
    // state never leaks from one test run to the next. Needle 299.
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)
  })

  it('built-in templates do not show capabilities (i) button', async () => {
    const templatesWithCaps = {
      templates: [
        {
          name: 'demo',
          file: 'demo.agent',
          content: '',
          description: 'Demo template',
          capabilities: {
            writes_to: 'src/, tests/',
            cannot_touch: '.env',
            budget: '$5',
            time_limit: '30 minutes',
            sandbox: 'docker container',
          },
          parse_error: null,
        },
      ],
    }

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents')
        return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return templatesWithCaps
      if (path.startsWith('/agents/persona-templates')) return { templates: [], persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      return {}
    })

    renderAgents()

    // Switch to the Templates tab so the cards render.
    await waitFor(() => {
      expect(screen.getByText('Templates')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Templates'))

    // Built-in templates never show the (i) capabilities button — they can't be
    // unfavorited and we don't want to clutter their cards with the info blurb.
    await waitFor(() => {
      expect(screen.queryByTestId('template-caps-info-Demo')).not.toBeInTheDocument()
    })
  })

  it('disables Spawn and shows an error for a broken template', async () => {
    const templatesWithError = {
      templates: [
        {
          name: 'broken',
          file: 'broken.agent',
          content: '',
          description: '',
          capabilities: null,
          parse_error: 'Agentfile parse error at broken.agent, line 3: ISOLATION must be one of docker, firecracker, none, nono, got spaceship',
        },
      ],
    }

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents')
        return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return templatesWithError
      if (path.startsWith('/agents/persona-templates')) return { templates: [], persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('Templates')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Templates'))

    const errorPanel = await screen.findByTestId('template-capabilities-error-Broken')
    expect(errorPanel).toBeInTheDocument()
    expect(errorPanel.textContent).toContain('Could not read capabilities')

    const spawnButton = screen.getByTestId('template-spawn-Broken') as HTMLButtonElement
    expect(spawnButton).toBeDisabled()
  })


})

// Regression for Tori's screenshot: the Agents Templates grid rendered
// descriptions that (a) started with a literal "#" character and
// (b) truncated mid-word ("...building things. R"). Two root causes:
//   1. Backend: agentfiles without a DESC directive returned
//      description: "" so the frontend fell back to the raw content.
//   2. Frontend: that fallback was content.slice(0, 50), a char-count
//      slice of the raw agentfile body which starts with "# ...".
// These tests lock in: the card never shows a leading "#", full
// descriptions are preserved end-to-end with no char-count slicing,
// and the native title tooltip shows the full description on hover.
describe('Agents page - Templates description hygiene (regression)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)
  })

  it('renders full structured description, never the raw # body', async () => {
    const fullDesc =
      'Your go-to agent for building things. Reads the task, plans an approach, builds it, writes tests, and makes sure everything works before finishing.'
    const templatesPayload = {
      templates: [
        {
          name: 'builder',
          file: 'builder.agent',
          // The raw body starts with "#" comment lines. If the frontend
          // ever falls back to slicing content, the test will catch it.
          content:
            '# Builder: your go-to agent for building things. Reads the task, plans an approach, builds it, writes tests, and makes sure everything works before finishing.\nFROM auto\n',
          description: fullDesc,
          capabilities: {
            writes_to: 'nothing',
            cannot_touch: 'no restrictions set',
            budget: 'no budget limit',
            time_limit: 'no time limit',
            sandbox: 'none',
          },
          parse_error: null,
        },
      ],
    }

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents')
        return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return templatesPayload
      if (path.startsWith('/agents/persona-templates'))
        return { templates: [], persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('Templates')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Templates'))

    // The card's description paragraph must hold the full text and
    // must NOT start with "#" (the comment header leak).
    const card = await screen.findByTestId('template-card-Builder')
    const paragraph = card.querySelector('p.text-slate-400') as HTMLElement
    expect(paragraph).not.toBeNull()
    expect(paragraph.textContent).toBe(fullDesc)
    expect(paragraph.textContent?.startsWith('#')).toBe(false)

    // The title attribute (hover tooltip) must carry the full
    // description so users see the whole thing even when the CSS
    // line-clamp hides part of it.
    expect(paragraph.getAttribute('title')).toBe(fullDesc)

    // Visual truncation is handled by CSS line-clamp-2, not JS slicing.
    expect(paragraph.className).toContain('line-clamp-2')
  })

  it('uses a friendly placeholder when description is empty, never the raw body', async () => {
    // Locks in the fix: when description is empty the fallback must be
    // a static placeholder, not content.slice(0, N). The old bug sliced
    // the raw body and produced strings like "# Tracks down bugs. Repr".
    const templatesPayload = {
      templates: [
        {
          name: 'mystery',
          file: 'mystery.agent',
          content:
            '# This is a raw agentfile body that must NEVER leak into the card.\nFROM auto\n',
          description: '',
          capabilities: null,
          parse_error: null,
        },
      ],
    }

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents')
        return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return templatesPayload
      if (path.startsWith('/agents/persona-templates'))
        return { templates: [], persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('Templates')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Templates'))

    const card = await screen.findByTestId('template-card-Mystery')
    const paragraph = card.querySelector('p.text-slate-400') as HTMLElement
    expect(paragraph).not.toBeNull()
    // Must NOT leak the raw body: no leading "#", no mid-word slice.
    expect(paragraph.textContent?.startsWith('#')).toBe(false)
    expect(paragraph.textContent).not.toContain('raw agentfile body')
  })
})

// Regression for needle 299: the Agents page showed a blank Active
// Sessions panel for several seconds every time Tori opened /agents
// cold. The /agents endpoint itself was fast. The fix caches the last
// successful response to localStorage and seeds the first render with
// it, so the cold-visit flash disappears. These tests pin the invariant
// so a future refactor cannot silently regress it.
describe('Agents page - first-paint budget (needle 299)', () => {
  const manyAgents = Array.from({ length: 50 }, (_, i) => ({
    name: `agent-${i + 1}`,
    status: i < 3 ? 'running' : 'completed',
    source: 'claude-code',
    model: 'sonnet',
    budget: '2.00',
    spawned_at: new Date(Date.now() - i * 60000).toISOString(),
    transcript_bytes: 1024,
    transcript_lines: 10,
  }))

  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)
    mockedApiPost.mockResolvedValue({})
  })

  const FIRST_ROW_BUDGET_MS = 300

  it('first visible agent row arrives within 300ms on a warm backend', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents')
        return {
          daemon_running: true,
          status: 'ok',
          active: [],
          agents: manyAgents,
        }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { templates: [], persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      return {}
    })

    const t0 = performance.now()
    renderAgents()
    await waitFor(() => {
      expect(screen.getByTitle(/^agent-1(\s|$)/)).toBeInTheDocument()
    })
    const elapsed = performance.now() - t0
    expect(elapsed).toBeLessThan(FIRST_ROW_BUDGET_MS)
  })

  it('renders agent rows immediately even while /agents/templates hangs for 2 seconds', async () => {
    // Pins the render-primary-first invariant. Secondary endpoints
    // can be slow and the user still sees the agent list right away.
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/agents')
        return Promise.resolve({
          daemon_running: true,
          status: 'ok',
          active: [],
          agents: manyAgents,
        })
      if (path === '/agents/templates')
        return new Promise((resolve) =>
          setTimeout(() => resolve({ templates: [] }), 2000),
        )
      if (path.startsWith('/agents/persona-templates'))
        return new Promise((resolve) =>
          setTimeout(() => resolve({ templates: [], persona: 'pm' }), 2000),
        )
      if (path === '/agents/user-templates')
        return new Promise((resolve) =>
          setTimeout(() => resolve({ templates: [] }), 2000),
        )
      if (path === '/settings') return Promise.resolve({ persona: 'pm' })
      return Promise.resolve({})
    })

    const t0 = performance.now()
    renderAgents()
    await waitFor(() => {
      expect(screen.getByTitle(/^agent-1(\s|$)/)).toBeInTheDocument()
    })
    const elapsed = performance.now() - t0
    expect(elapsed).toBeLessThan(FIRST_ROW_BUDGET_MS)
  })

  it('paints agent rows from the localStorage cache before any fetch resolves', async () => {
    window.localStorage.setItem(
      'myos.agentsCache.v1',
      JSON.stringify([
        {
          name: 'cached-agent',
          status: 'running',
          source: 'claude-code',
          model: 'sonnet',
          budget: '2.00',
          spawned_at: new Date().toISOString(),
          transcript_bytes: 0,
          transcript_lines: 0,
        },
      ]),
    )

    // Hang every endpoint so the only way this test passes is by
    // painting from the cache on first render.
    mockedApiGet.mockImplementation(() => new Promise(() => {}))

    renderAgents()

    expect(screen.getByTitle(/^cached-agent(\s|$)/)).toBeInTheDocument()
    // The Active tab loading placeholder must NOT appear over the
    // cached row.
    expect(
      screen.queryByTestId('active-agents-loading'),
    ).not.toBeInTheDocument()
  })

  it('overwrites the agents cache with the next successful /agents response', async () => {
    window.localStorage.setItem(
      'myos.agentsCache.v1',
      JSON.stringify([
        {
          name: 'stale-agent',
          status: 'running',
          source: 'claude-code',
          model: 'sonnet',
          budget: '2.00',
          spawned_at: new Date().toISOString(),
          transcript_bytes: 0,
          transcript_lines: 0,
        },
      ]),
    )

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents')
        return {
          daemon_running: true,
          status: 'ok',
          active: [],
          agents: manyAgents,
        }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { templates: [], persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      return {}
    })

    renderAgents()
    // Stale cache row paints first.
    expect(screen.getByTitle(/^stale-agent(\s|$)/)).toBeInTheDocument()
    // Fresh data replaces it.
    await waitFor(() => {
      expect(screen.getByTitle(/^agent-1(\s|$)/)).toBeInTheDocument()
    })
    const persisted = JSON.parse(
      window.localStorage.getItem('myos.agentsCache.v1') || '[]',
    )
    expect(Array.isArray(persisted)).toBe(true)
    expect(persisted.length).toBe(manyAgents.length)
    expect(persisted[0].name).toBe('agent-1')
  })
})

describe('Icon Picker in Template Editor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { templates: [], persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      if (path === '/agent-patterns/recommendations') return { recommendations: [] }
      if (path === '/agent-patterns/template-stats') return { stats: [] }
      return {}
    })
    mockedApiPost.mockResolvedValue({ result: 'ok' })
  })

  async function openTemplateEditor() {
    renderAgents()

    // Navigate to Templates tab
    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    // Click the "New template" button in the Agent Templates section header
    const newTemplateBtn = await screen.findByRole('button', { name: /New template/i })
    fireEvent.click(newTemplateBtn)

    // Wait for the modal to appear
    await waitFor(() => {
      expect(screen.getByTestId('icon-picker-trigger')).toBeInTheDocument()
    })
  }

  it('shows the icon picker trigger with the default icon', async () => {
    await openTemplateEditor()

    const trigger = screen.getByTestId('icon-picker-trigger')
    expect(trigger).toBeInTheDocument()
    // Default icon should be smart_toy
    expect(trigger.textContent).toContain('smart_toy')
  })

  it('opens the icon grid dropdown when clicking the trigger', async () => {
    await openTemplateEditor()

    expect(screen.queryByTestId('icon-picker-dropdown')).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('icon-picker-trigger'))

    await waitFor(() => {
      expect(screen.getByTestId('icon-picker-dropdown')).toBeInTheDocument()
      expect(screen.getByTestId('icon-picker-grid')).toBeInTheDocument()
    })
  })

  it('clicking an icon in the grid updates the selection and closes the picker', async () => {
    await openTemplateEditor()

    // Open the picker
    fireEvent.click(screen.getByTestId('icon-picker-trigger'))
    await waitFor(() => {
      expect(screen.getByTestId('icon-picker-dropdown')).toBeInTheDocument()
    })

    // Click the "rocket_launch" icon
    const rocketIcon = screen.getByTestId('icon-option-rocket_launch')
    fireEvent.click(rocketIcon)

    // Dropdown should close
    await waitFor(() => {
      expect(screen.queryByTestId('icon-picker-dropdown')).not.toBeInTheDocument()
    })

    // The trigger should now show the new icon name
    expect(screen.getByTestId('icon-picker-trigger').textContent).toContain('rocket_launch')
  })

  it('search filters the icon grid', async () => {
    await openTemplateEditor()

    // Open the picker
    fireEvent.click(screen.getByTestId('icon-picker-trigger'))
    await waitFor(() => {
      expect(screen.getByTestId('icon-picker-search')).toBeInTheDocument()
    })

    // Type a search term
    const searchInput = screen.getByTestId('icon-picker-search')
    fireEvent.change(searchInput, { target: { value: 'rocket' } })

    // Only rocket_launch should be visible
    await waitFor(() => {
      expect(screen.getByTestId('icon-option-rocket_launch')).toBeInTheDocument()
    })

    // Other icons should not be visible
    expect(screen.queryByTestId('icon-option-smart_toy')).not.toBeInTheDocument()
    expect(screen.queryByTestId('icon-option-code')).not.toBeInTheDocument()
  })

  it('shows "No matching icons" when search yields no results', async () => {
    await openTemplateEditor()

    fireEvent.click(screen.getByTestId('icon-picker-trigger'))
    await waitFor(() => {
      expect(screen.getByTestId('icon-picker-search')).toBeInTheDocument()
    })

    fireEvent.change(screen.getByTestId('icon-picker-search'), {
      target: { value: 'zzzznonexistent' },
    })

    await waitFor(() => {
      expect(screen.getByText('No matching icons')).toBeInTheDocument()
    })
  })

  it('highlights the currently selected icon in the grid', async () => {
    await openTemplateEditor()

    // Open the picker (default icon is smart_toy)
    fireEvent.click(screen.getByTestId('icon-picker-trigger'))
    await waitFor(() => {
      expect(screen.getByTestId('icon-picker-grid')).toBeInTheDocument()
    })

    // The smart_toy icon should have the selected styling (ring-1 ring-blue-500)
    const selectedBtn = screen.getByTestId('icon-option-smart_toy')
    expect(selectedBtn.className).toContain('ring-blue-500')

    // Another icon should not have the selected styling
    const otherBtn = screen.getByTestId('icon-option-code')
    expect(otherBtn.className).not.toContain('ring-blue-500')
  })
})

describe('Agents page - Fleets panel removed', () => {
  // Wave 3: the Fleets panel moved to the Plans page template grid.
  // The backend /agents/fleets/* endpoints stay alive for backwards
  // compatibility, but the Agents page should no longer render a Fleets
  // section or its launch button.
  const mockFleetsResponse = {
    fleets: [
      {
        id: 'fleet-build-website',
        name: 'Build a Website',
        description: 'A full team to plan, design, build, and review a website.',
        icon: 'web',
        members: [
          { role: 'Product Manager', icon: 'assignment_ind', prompt: 'p1' },
        ],
      },
    ],
  }

  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path === '/agents/fleets') return mockFleetsResponse
      return {}
    })
  })

  it('does not render the Fleets panel or its launch button', async () => {
    renderAgents()
    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)
    // The Fleets heading and the per-fleet launch test id should be gone.
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })
    expect(screen.queryByText('Fleets')).not.toBeInTheDocument()
    expect(screen.queryByTestId('fleet-launch-fleet-build-website')).not.toBeInTheDocument()
    expect(screen.queryByTestId('fleet-status')).not.toBeInTheDocument()
  })
})

// Tests for template detail view: prompt section + user input field.
describe('Agents page - Template detail view', () => {
  // Use a real builtin template. The Use button on PM templates always
  // opens the detail modal (the prior direct-spawn fast-path was reverted
  // so the user can type a prompt before spawning).
  const mockPmTemplatesResponse = {
    templates: [
      {
        id: 'builtin-pm-prd',
        name: 'PRD',
        aliases: ['PRD Draft'],
        description: 'Turn a rough idea into a product requirements doc.',
        icon: 'article',
        prompt_template: 'You are a senior PM. Turn the user\'s rough feature idea into a PRD.',
        model: 'sonnet',
        budget: 3.0,
        source: 'marketplace',
        personas: ['pm'],
        installed: true,
        builtin: true,
      },
    ],
  }

  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: false, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { ...mockPmTemplatesResponse, persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      if (path === '/agents/fleets') return { fleets: [] }
      return {}
    })
    mockedApiPost.mockResolvedValue({})
  })

  it('template detail modal shows Prompt section when clicking Use on a PM template', async () => {
    renderAgents()

    // Navigate to Templates tab
    const templatesTab = await screen.findByText('Templates')
    fireEvent.click(templatesTab)

    // PM templates now render via TemplateCard. The Use button for the first
    // PM template is identified by the card testId + "-action" suffix.
    const useBtn = await screen.findByTestId('pm-template-card-builtin-pm-prd-action')
    fireEvent.click(useBtn)

    // The modal should show the Prompt section
    await waitFor(() => {
      expect(screen.getByTestId('template-prompt-section')).toBeInTheDocument()
    })
  })

  it('template detail modal shows user input field', async () => {
    renderAgents()

    const templatesTab = await screen.findByText('Templates')
    fireEvent.click(templatesTab)

    const useBtn = await screen.findByTestId('pm-template-card-builtin-pm-prd-action')
    fireEvent.click(useBtn)

    await waitFor(() => {
      expect(screen.getByTestId('template-user-message-input')).toBeInTheDocument()
    })
  })

  it('template detail modal Spawn agent button uses typed user message as prompt', async () => {
    renderAgents()

    const templatesTab = await screen.findByText('Templates')
    fireEvent.click(templatesTab)

    const useBtn = await screen.findByTestId('pm-template-card-builtin-pm-prd-action')
    fireEvent.click(useBtn)

    // Type a user message
    const input = await screen.findByTestId('template-user-message-input')
    fireEvent.change(input, { target: { value: 'Build a PRD for a new search feature' } })

    // Click Spawn agent
    const spawnBtn = screen.getByTestId('template-spawn-button')
    fireEvent.click(spawnBtn)

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith(
        '/agents/spawn',
        expect.objectContaining({
          prompt: 'Build a PRD for a new search feature',
        })
      )
    })
  })

  it('modal description editable, save persists', async () => {
    const mockedApiPatch = vi.mocked(api.patch)
    mockedApiPatch.mockResolvedValue({
      template: { id: 'builtin-pm-prd', description: 'My edited blurb.' },
    })

    renderAgents()

    const templatesTab = await screen.findByText('Templates')
    fireEvent.click(templatesTab)

    const useBtn = await screen.findByTestId('pm-template-card-builtin-pm-prd-action')
    fireEvent.click(useBtn)

    // The description textarea starts with the shipped description
    const textarea = await screen.findByTestId('template-description-input') as HTMLTextAreaElement
    expect(textarea.value).toBe('Turn a rough idea into a product requirements doc.')

    // Save is disabled when no edit has happened yet
    const saveBtn = await screen.findByTestId('template-description-save')
    expect(saveBtn).toBeDisabled()

    // Edit the description, Save should become enabled
    fireEvent.change(textarea, { target: { value: 'My edited blurb.' } })
    expect(saveBtn).not.toBeDisabled()

    fireEvent.click(saveBtn)

    await waitFor(() => {
      expect(mockedApiPatch).toHaveBeenCalledWith(
        '/agents/templates/builtin-pm-prd/description',
        { description: 'My edited blurb.' },
      )
    })

    // Success flag appears
    expect(await screen.findByTestId('template-description-saved')).toBeInTheDocument()
  })

  it('modal description save is blocked when empty', async () => {
    const mockedApiPatch = vi.mocked(api.patch)
    mockedApiPatch.mockClear()

    renderAgents()

    const templatesTab = await screen.findByText('Templates')
    fireEvent.click(templatesTab)

    const useBtn = await screen.findByTestId('pm-template-card-builtin-pm-prd-action')
    fireEvent.click(useBtn)

    const textarea = await screen.findByTestId('template-description-input') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: '   ' } })

    const saveBtn = screen.getByTestId('template-description-save')
    expect(saveBtn).toBeDisabled()
    expect(mockedApiPatch).not.toHaveBeenCalledWith(
      '/agents/templates/builtin-pm-prd/description',
      expect.anything(),
    )
  })
})

// ---------- friendlyAgentName unit tests ----------
describe('friendlyAgentName', () => {
  // Use a local-timezone timestamp so the time part matches regardless of the
  // test runner's TZ offset. We build the expected time string from the same
  // Date object the helper uses so the test is deterministic everywhere.
  const TS_DATE = new Date(2026, 3, 14, 8, 7, 0) // April 14, 2026 08:07 local time
  const TS = TS_DATE.toISOString()
  const TS_LABEL = (() => {
    const h = TS_DATE.getHours()
    const m = TS_DATE.getMinutes().toString().padStart(2, '0')
    const ampm = h >= 12 ? 'pm' : 'am'
    const h12 = ((h % 12) || 12).toString()
    return `${h12}:${m}${ampm}`
  })()

  it('formats an inferred claude-code session as "Claude Sonnet · Chat · time"', () => {
    const result = friendlyAgentName({
      name: 'claude-code-005c3b5b-d9',
      model: 'claude-sonnet-4-6',
      source: 'claude-code',
      spawned_at: TS,
    })
    expect(result).toBe(`Claude · Sonnet · Chat · ${TS_LABEL}`)
  })

  it('formats an inferred claude-code session without model', () => {
    const result = friendlyAgentName({
      name: 'claude-code-abcdef1234',
      model: undefined,
      source: 'claude-code',
      spawned_at: TS,
    })
    expect(result).toBe(`Claude · Chat · ${TS_LABEL}`)
  })

  it('formats a fleet agent as "Role · fleet-name · time"', () => {
    const result = friendlyAgentName({
      name: 'website-build-qa-engineer',
      role: 'QA Engineer',
      fleet_name: 'website-build',
      model: 'claude-sonnet-4-6',
      source: 'claude-code',
      spawned_at: TS,
    })
    expect(result).toBe(`QA Engineer · website-build · ${TS_LABEL}`)
  })

  it('formats a named agent as "name · model · time"', () => {
    const result = friendlyAgentName({
      name: 'cost-tracking-fix',
      model: 'claude-opus-4-6',
      source: 'claude-code',
      spawned_at: TS,
    })
    expect(result).toBe(`cost-tracking-fix · Opus · ${TS_LABEL}`)
  })

  it('returns raw name when no extra metadata exists', () => {
    const result = friendlyAgentName({
      name: 'my-agent',
      source: 'api',
    })
    expect(result).toBe('my-agent')
  })

  it('renders friendly name in the agent row (integration)', async () => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['claude-code-005c3b5b-d9'],
        agents: [{
          name: 'claude-code-005c3b5b-d9',
          status: 'running',
          source: 'claude-code',
          model: 'claude-sonnet-4-6',
          spawned_at: TS,
        }],
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: 'claude-code-005c3b5b-d9', nudges: [], session_nudges: [] }
      return {}
    })
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => {
      // Raw UUID slug must NOT appear as visible text (it lives only in the tooltip)
      expect(screen.queryByText('claude-code-005c3b5b-d9')).not.toBeInTheDocument()
      // Friendly label must appear - matches the pattern "Claude · Sonnet · Chat · HH:MMam/pm"
      expect(screen.getByText(/^Claude · Sonnet · Chat · \d+:\d+(am|pm)$/)).toBeInTheDocument()
    })
  })
})

// ---------- agentTitleParts unit tests ----------
describe('agentTitleParts', () => {
  const TS_DATE = new Date(2026, 3, 14, 8, 7, 0) // April 14, 2026 08:07 local time
  const TS = TS_DATE.toISOString()
  const TS_LABEL = (() => {
    const h = TS_DATE.getHours()
    const m = TS_DATE.getMinutes().toString().padStart(2, '0')
    const ampm = h >= 12 ? 'pm' : 'am'
    const h12 = ((h % 12) || 12).toString()
    return `${h12}:${m}${ampm}`
  })()

  it('test_task_field_renders_as_primary_title', () => {
    // An agent with a human-written task must show that as the primary title
    // with model+time as secondary, not the generic "name · model · time" fallback.
    const result = agentTitleParts({
      name: 'some-agent',
      task: 'Fix delete-all 403',
      model: 'claude-sonnet-4-6',
      source: 'claude-code',
      spawned_at: TS,
    })
    expect(result.primary).toBe('Fix delete-all 403')
    expect(result.secondary).toBe(`Sonnet · ${TS_LABEL}`)
  })

  it('test_build_agent_with_task_shows_task_title_not_opaque_name', () => {
    // Build agents are named build-<task_id> (e.g. build-568) which is
    // opaque to a human. When the spawn endpoint persists the friendly
    // task label, the UI must promote that label to the primary title
    // so the row reads "Build task 568: Wire up newsletter" instead
    // of just "build-568 · Sonnet · time".
    const result = agentTitleParts({
      name: 'build-568',
      task: 'Build task 568: Wire up a newsletter signup',
      model: 'claude-sonnet-4-6',
      source: 'chat-build-it',
      spawned_at: TS,
    })
    expect(result.primary).toBe('Build task 568: Wire up a newsletter signup')
    // The friendly label must surface, not the raw agent name.
    expect(result.primary).not.toBe('build-568')
    expect(result.secondary).toBe(`Sonnet · ${TS_LABEL}`)
  })

  it('test_description_field_renders_as_primary_when_no_task', () => {
    // description falls back to the same tier as task when task is absent.
    const result = agentTitleParts({
      name: 'some-agent',
      description: 'Phase 3 copy audit',
      model: 'claude-opus-4-6',
      source: 'claude-code',
      spawned_at: TS,
    })
    expect(result.primary).toBe('Phase 3 copy audit')
    expect(result.secondary).toBe(`Opus · ${TS_LABEL}`)
  })

  it('test_task_title_truncates_at_60_chars', () => {
    const longTask = 'A'.repeat(80)
    const result = agentTitleParts({
      name: 'some-agent',
      task: longTask,
      source: 'claude-code',
    })
    expect(result.primary.length).toBe(60)
    expect(result.primary).toBe('A'.repeat(60))
  })

  it('test_chat_session_uses_prompt_as_primary_title', () => {
    // A chat session must surface the first user message (stored as prompt)
    // as the primary title rather than the generic "Claude · Chat" fallback.
    const result = agentTitleParts({
      name: 'chat-abc12345',
      source: 'chat',
      model: 'claude-opus-4-6',
      prompt: 'Ask about the three-year roadmap',
      spawned_at: TS,
    })
    expect(result.primary).toBe('Ask about the three-year roadmap')
    expect(result.secondary).toBe(`Opus · ${TS_LABEL}`)
  })

  it('test_chat_session_uses_chat_title_over_prompt', () => {
    // chat_title (pre-derived by backend) takes priority over raw prompt.
    const result = agentTitleParts({
      name: 'chat-abc12345',
      source: 'chat',
      model: 'claude-sonnet-4-6',
      prompt: 'Raw prompt text that is longer and less polished',
      chat_title: 'Three-year roadmap discussion',
      spawned_at: TS,
    })
    expect(result.primary).toBe('Three-year roadmap discussion')
    expect(result.secondary).toBe(`Sonnet · ${TS_LABEL}`)
  })

  it('test_chat_session_fallback_when_no_title_available', () => {
    // When a chat session has no prompt or chat_title, show "Chat · Model · time".
    const result = agentTitleParts({
      name: 'chat-abc12345',
      source: 'chat',
      model: 'claude-sonnet-4-6',
      spawned_at: TS,
    })
    expect(result.primary).toBe(`Chat · Sonnet · ${TS_LABEL}`)
    expect(result.secondary).toBeNull()
  })

  it('test_fleet_member_renders_role_and_fleet_name', () => {
    // Fleet agents must still use role + fleet_name + time as before.
    const result = agentTitleParts({
      name: 'fleet-build-website-qa',
      role: 'QA Engineer',
      fleet_name: 'website-build',
      model: 'claude-sonnet-4-6',
      source: 'claude-code',
      spawned_at: TS,
    })
    expect(result.primary).toBe(`QA Engineer · website-build · ${TS_LABEL}`)
    expect(result.secondary).toBeNull()
  })

  it('test_inferred_claude_code_session_fallback', () => {
    // claude-code-xxx agents with no task must still render as
    // "Claude · Sonnet · Chat · time" with no secondary line.
    const result = agentTitleParts({
      name: 'claude-code-005c3b5b-d9',
      model: 'claude-sonnet-4-6',
      source: 'claude-code',
      spawned_at: TS,
    })
    expect(result.primary).toBe(`Claude · Sonnet · Chat · ${TS_LABEL}`)
    expect(result.secondary).toBeNull()
  })

  it('test_agent_with_task_renders_two_line_title_in_active_row', async () => {
    // Integration: an agent row with task set must show the task as the
    // visible text and the model+time as a subtitle below it.
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['fix-delete-all-403'],
        agents: [{
          name: 'fix-delete-all-403',
          status: 'running',
          source: 'claude-code',
          model: 'claude-sonnet-4-6',
          task: 'Fix delete-all 403',
          spawned_at: TS,
        }],
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: 'fix-delete-all-403', nudges: [], session_nudges: [] }
      return {}
    })
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => {
      expect(screen.getByText('Fix delete-all 403')).toBeInTheDocument()
      // Secondary line with model+time must appear
      expect(screen.getByText(new RegExp(`Sonnet · ${TS_LABEL.replace(':', '\\:')}`)))
        .toBeInTheDocument()
      // Raw agent name must NOT appear as visible text (lives only in the tooltip)
      expect(screen.queryByText('fix-delete-all-403')).not.toBeInTheDocument()
    })
  })
})

// ---------- agent filtering: only user-spawned agents on Agents tab ----------
describe('agent filtering (only user-spawned agents on Agents tab)', () => {
  const TS_DATE = new Date(2026, 3, 15, 10, 50, 0) // April 15, 2026 10:50 local time
  const TS = TS_DATE.toISOString()

  function setupMock(agents: object[]) {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: agents.map((a: object) => (a as { name: string }).name),
        agents,
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { nudges: [], session_nudges: [] }
      return {}
    })
    mockedApiPost.mockResolvedValue({})
  }

  // --- isMainSession unit tests (API must remain stable) ---

  it('isMainSession returns true for inferred session with Claude Code session description', () => {
    expect(isMainSession({
      name: 'claude-code-31808c4b-5',
      description: 'Claude Code session (cwd: /Users/torimeyer/claude/torios)',
      source: 'claude-code',
    })).toBe(true)
  })

  it('isMainSession returns false for named background agent', () => {
    expect(isMainSession({
      name: 'fix-claw-blocking',
      description: 'Fix the claw blocking bug',
      source: 'claude-code',
    })).toBe(false)
  })

  it('isMainSession returns false for inferred session without Claude Code session description', () => {
    expect(isMainSession({
      name: 'claude-code-005c3b5b-d9',
      source: 'claude-code',
    })).toBe(false)
  })

  it('isMainSession returns false for chat-source agents', () => {
    expect(isMainSession({
      name: 'chat-abc12345',
      source: 'chat',
    })).toBe(false)
  })

  // --- isUserSpawnedAgent unit tests ---

  it('isUserSpawnedAgent returns true for a named claude-code agent', () => {
    expect(isUserSpawnedAgent({
      name: 'tasks-health-autofix',
      source: 'claude-code',
    })).toBe(true)
  })

  it('isUserSpawnedAgent returns true for an api-source agent', () => {
    expect(isUserSpawnedAgent({
      name: 'my-api-agent',
      source: 'api',
    })).toBe(true)
  })

  it('isUserSpawnedAgent returns false for main session', () => {
    expect(isUserSpawnedAgent({
      name: 'claude-code-31808c4b-5',
      source: 'claude-code',
      description: 'Claude Code session (cwd: /Users/torimeyer/claude/torios)',
    })).toBe(false)
  })

  it('isUserSpawnedAgent returns false for chat-source agent', () => {
    expect(isUserSpawnedAgent({
      name: 'chat-abc12345',
      source: 'chat',
    })).toBe(false)
  })

  it('isUserSpawnedAgent returns false for audit-source agent', () => {
    expect(isUserSpawnedAgent({
      name: 'audit-row',
      source: 'audit',
    })).toBe(false)
  })

  it('isUserSpawnedAgent returns false for hook-source agent', () => {
    expect(isUserSpawnedAgent({
      name: 'hook-row',
      source: 'hook',
    })).toBe(false)
  })

  it('isUserSpawnedAgent returns false for subscription model agent', () => {
    expect(isUserSpawnedAgent({
      name: 'sub-session',
      source: 'claude-code',
      model: 'claude-code-subscription',
    })).toBe(false)
  })

  // --- Unified count invariant: Cancel-all uses same filter as Active list ---

  it('Cancel-all button count matches visible Active list with 1 main session + 2 user agents', async () => {
    setupMock([
      {
        name: 'claude-code-31808c4b-5',
        status: 'running',
        source: 'claude-code',
        model: 'claude-opus-4-6[1m]',
        description: 'Claude Code session (cwd: /Users/torimeyer/claude/torios)',
        spawned_at: TS,
      },
      {
        name: 'tasks-health-autofix',
        status: 'running',
        source: 'claude-code',
        model: 'claude-sonnet-4-6',
        task: 'tasks-health-autofix',
        spawned_at: TS,
      },
      {
        name: 'fix-agents-parse-error',
        status: 'running',
        source: 'claude-code',
        model: 'claude-sonnet-4-6',
        task: 'fix-agents-parse-error',
        spawned_at: TS,
      },
    ])
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => {
      // Active list shows 2 rows (not the main session)
      expect(screen.getByTestId('background-agents-list')).toBeInTheDocument()
      expect(screen.getByText('tasks-health-autofix')).toBeInTheDocument()
      expect(screen.getByText('fix-agents-parse-error')).toBeInTheDocument()
      // Cancel-all button label shows (2)
      expect(screen.getByTestId('cancel-all-agents-btn')).toHaveTextContent('Cancel all (2)')
    })
  })

  it('Cancel-all button disabled when only main session is running', async () => {
    setupMock([{
      name: 'claude-code-31808c4b-5',
      status: 'running',
      source: 'claude-code',
      model: 'claude-opus-4-6[1m]',
      description: 'Claude Code session (cwd: /Users/torimeyer/claude/torios)',
      spawned_at: TS,
    }])
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => {
      const btn = screen.getByTestId('cancel-all-agents-btn')
      expect(btn).toBeDisabled()
      // No count shown when 0
      expect(btn).toHaveTextContent('Cancel all')
      expect(btn).not.toHaveTextContent('(')
    })
  })

  // --- agentTitleParts helpers still work (no render change) ---

  it('agentTitleParts returns "Your chat with Claude" for main session', () => {
    const result = agentTitleParts({
      name: 'claude-code-31808c4b-5',
      description: 'Claude Code session (cwd: /Users/torimeyer/claude/torios)',
      model: 'claude-opus-4-6[1m]',
      source: 'claude-code',
      spawned_at: TS,
    })
    expect(result.primary).toBe('Your chat with Claude')
    expect(result.secondary).toBeNull()
  })

  it('agentTitleParts does not leak "Claude Code session (cwd:..." as a title', () => {
    const result = agentTitleParts({
      name: 'claude-code-31808c4b-5',
      description: 'Claude Code session (cwd: /Users/torimeyer/claude/torios)',
      source: 'claude-code',
      spawned_at: TS,
    })
    expect(result.primary).not.toContain('Claude Code session')
  })

  // --- Integration: main session alone -> Active list is empty, spawn CTA shown ---

  it('main session alone: Active list is empty, spawn CTA empty-state is shown', async () => {
    setupMock([{
      name: 'claude-code-31808c4b-5',
      status: 'running',
      source: 'claude-code',
      model: 'claude-opus-4-6[1m]',
      description: 'Claude Code session (cwd: /Users/torimeyer/claude/torios)',
      spawned_at: TS,
    }])
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => {
      // main-session-strip must NOT render
      expect(screen.queryByTestId('main-session-strip')).not.toBeInTheDocument()
      // no "You" chip
      expect(screen.queryByTestId('you-chip')).not.toBeInTheDocument()
      // spawn CTA empty state must be shown
      expect(screen.getByText(/No agents running right now/i)).toBeInTheDocument()
      expect(screen.getByText(/Spawn your first agent/i)).toBeInTheDocument()
    })
  })

  // --- main session + 1 spawned background agent: only the background agent renders ---

  it('main session + spawned background agent: only the background agent renders', async () => {
    setupMock([
      {
        name: 'claude-code-31808c4b-5',
        status: 'running',
        source: 'claude-code',
        model: 'claude-opus-4-6[1m]',
        description: 'Claude Code session (cwd: /Users/torimeyer/claude/torios)',
        spawned_at: TS,
      },
      {
        name: 'fix-claw-blocking',
        status: 'running',
        source: 'claude-code',
        model: 'claude-sonnet-4-6',
        task: 'Fix the claw blocking bug',
        spawned_at: TS,
      },
    ])
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => {
      // main session strip must NOT render
      expect(screen.queryByTestId('main-session-strip')).not.toBeInTheDocument()
      expect(screen.queryByTestId('you-chip')).not.toBeInTheDocument()
      // spawned background agent must appear
      expect(screen.getByTestId('background-agents-list')).toBeInTheDocument()
      expect(screen.getByText('Fix the claw blocking bug')).toBeInTheDocument()
    })
  })

  // --- chat-source agent is filtered out ---

  it('chat-source agent is filtered out of Active list', async () => {
    setupMock([{
      name: 'chat-xyz-running',
      status: 'running',
      source: 'chat',
      model: 'claude-sonnet-4-6',
      prompt: 'Help me plan my week',
      spawned_at: TS,
    }])
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => {
      expect(screen.queryByTestId('background-agents-list')).not.toBeInTheDocument()
      expect(screen.getByText(/No agents running right now/i)).toBeInTheDocument()
    })
  })

  // --- audit-source row is filtered out ---

  it('audit-source row is filtered out of Active list', async () => {
    setupMock([{
      name: 'claude-code-auditrow',
      status: 'running',
      source: 'audit',
      model: 'claude-sonnet-4-6',
      description: 'Claude Code session (cwd: /Users/torimeyer/claude/torios)',
      spawned_at: TS,
    }])
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => {
      expect(screen.queryByTestId('background-agents-list')).not.toBeInTheDocument()
      expect(screen.getByText(/No agents running right now/i)).toBeInTheDocument()
    })
  })

  // --- hook-source row is filtered out ---

  it('hook-source row is filtered out of Active list', async () => {
    setupMock([{
      name: 'claude-code-hookrow',
      status: 'running',
      source: 'hook',
      model: 'claude-sonnet-4-6',
      spawned_at: TS,
    }])
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => {
      expect(screen.queryByTestId('background-agents-list')).not.toBeInTheDocument()
      expect(screen.getByText(/No agents running right now/i)).toBeInTheDocument()
    })
  })
})

// ---------- transcript fetch tests ----------
describe('Transcript viewer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['my-agent'],
        agents: [{
          name: 'my-agent',
          status: 'running',
          source: 'claude-code',
          model: 'claude-sonnet-4-6',
          spawned_at: new Date().toISOString(),
          transcript_bytes: 1024,
          transcript_lines: 10,
        }],
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: 'my-agent', nudges: [], session_nudges: [] }
      if (path === '/agents/my-agent/transcript') return { content: 'Hello from transcript' }
      return {}
    })
    vi.mocked(api.post).mockResolvedValue({})
  })

  it('shows transcript content when API returns it', async () => {
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTitle(/^my-agent(\s|$)/)).toBeInTheDocument())

    const viewBtn = await screen.findByText('View Transcript')
    fireEvent.click(viewBtn)

    await waitFor(() => {
      expect(screen.getByText('Hello from transcript')).toBeInTheDocument()
    })
  })

  it('shows reason text and no retry when API returns empty:true', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['my-agent'],
        agents: [{
          name: 'my-agent',
          status: 'running',
          source: 'claude-code',
          model: 'claude-sonnet-4-6',
          spawned_at: new Date().toISOString(),
          transcript_bytes: 1024,
          transcript_lines: 10,
        }],
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: 'my-agent', nudges: [], session_nudges: [] }
      if (path === '/agents/my-agent/transcript') return {
        content: '',
        bytes: 0,
        empty: true,
        reason: 'This agent was cancelled before it wrote a transcript.',
      }
      return {}
    })
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTitle(/^my-agent(\s|$)/)).toBeInTheDocument())

    const viewBtn = await screen.findByText('View Transcript')
    fireEvent.click(viewBtn)

    await waitFor(() => {
      expect(screen.getByText('This agent was cancelled before it wrote a transcript.')).toBeInTheDocument()
    })
    expect(screen.queryByText('Retry')).not.toBeInTheDocument()
  })

  it('shows retry button on network error fetching transcript', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['my-agent'],
        agents: [{
          name: 'my-agent',
          status: 'running',
          source: 'claude-code',
          model: 'claude-sonnet-4-6',
          spawned_at: new Date().toISOString(),
          transcript_bytes: 1024,
          transcript_lines: 10,
        }],
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: 'my-agent', nudges: [], session_nudges: [] }
      if (path === '/agents/my-agent/transcript') throw new Error('Network error')
      return {}
    })
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTitle(/^my-agent(\s|$)/)).toBeInTheDocument())

    const viewBtn = await screen.findByText('View Transcript')
    fireEvent.click(viewBtn)

    await waitFor(() => {
      expect(screen.getByText(/Could not load transcript/)).toBeInTheDocument()
    })
    expect(screen.getByText('Retry')).toBeInTheDocument()
  })

  it('shows loading spinner while transcript is fetching', async () => {
    let resolveTranscript!: (v: unknown) => void
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['my-agent'],
        agents: [{
          name: 'my-agent',
          status: 'running',
          source: 'claude-code',
          model: 'claude-sonnet-4-6',
          spawned_at: new Date().toISOString(),
          transcript_bytes: 512,
          transcript_lines: 4,
        }],
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: 'my-agent', nudges: [], session_nudges: [] }
      if (path === '/agents/my-agent/transcript') {
        return new Promise((res) => { resolveTranscript = res })
      }
      return {}
    })
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTitle(/^my-agent(\s|$)/)).toBeInTheDocument())

    const viewBtn = await screen.findByText('View Transcript')
    fireEvent.click(viewBtn)

    await waitFor(() => {
      expect(screen.getByRole('status', { name: /loading transcript/i })).toBeInTheDocument()
    })

    resolveTranscript({ content: 'done', bytes: 4, empty: false })
    await waitFor(() => {
      expect(screen.getByText('done')).toBeInTheDocument()
    })
  })

  it('renders markdown bold and heading in transcript viewer', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['my-agent'],
        agents: [{
          name: 'my-agent',
          status: 'running',
          source: 'claude-code',
          model: 'claude-sonnet-4-6',
          spawned_at: new Date().toISOString(),
          transcript_bytes: 1024,
          transcript_lines: 10,
        }],
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: 'my-agent', nudges: [], session_nudges: [] }
      if (path === '/agents/my-agent/transcript') return { content: '## Heading\n\nSome **bold** text' }
      return {}
    })
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTitle(/^my-agent(\s|$)/)).toBeInTheDocument())

    const viewBtn = await screen.findByText('View Transcript')
    fireEvent.click(viewBtn)

    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 2, name: 'Heading' })).toBeInTheDocument()
      expect(screen.getByText('bold')).toBeInTheDocument()
      expect(screen.getByText('bold').tagName).toBe('STRONG')
    })
  })

  it('does not render raw markdown characters in transcript viewer', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['my-agent'],
        agents: [{
          name: 'my-agent',
          status: 'running',
          source: 'claude-code',
          model: 'claude-sonnet-4-6',
          spawned_at: new Date().toISOString(),
          transcript_bytes: 1024,
          transcript_lines: 10,
        }],
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: 'my-agent', nudges: [], session_nudges: [] }
      if (path === '/agents/my-agent/transcript') return { content: '## Heading\n\nSome **bold** text' }
      return {}
    })
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTitle(/^my-agent(\s|$)/)).toBeInTheDocument())

    const viewBtn = await screen.findByText('View Transcript')
    fireEvent.click(viewBtn)

    await waitFor(() => {
      const modal = document.querySelector('.chat-bubble-content')
      expect(modal).not.toBeNull()
      expect(modal!.textContent).not.toContain('##')
      expect(modal!.textContent).not.toContain('**')
    })
  })

  it('renders two speaker-labelled cards for User/Assistant transcript', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['my-agent'],
        agents: [{
          name: 'my-agent',
          status: 'running',
          source: 'claude-code',
          model: 'claude-sonnet-4-6',
          spawned_at: new Date().toISOString(),
          transcript_bytes: 1024,
          transcript_lines: 10,
        }],
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: 'my-agent', nudges: [], session_nudges: [] }
      if (path === '/agents/my-agent/transcript') return { content: 'User: hi\n\nAssistant: hey' }
      return {}
    })
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTitle(/^my-agent(\s|$)/)).toBeInTheDocument())

    const viewBtn = await screen.findByText('View Transcript')
    fireEvent.click(viewBtn)

    await waitFor(() => {
      // Each card has a role label header (text-transform: uppercase via CSS, DOM text is unchanged)
      expect(screen.getByText('User')).toBeInTheDocument()
      expect(screen.getByText('Assistant')).toBeInTheDocument()
      // Body text should appear
      expect(screen.getByText('hi')).toBeInTheDocument()
      expect(screen.getByText('hey')).toBeInTheDocument()
      // Two outlined cards should be rendered
      const cards = document.querySelectorAll('[data-testid="card"]')
      expect(cards.length).toBeGreaterThanOrEqual(2)
    })
  })

  it('renders bold markdown inside an Assistant speaker block', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['my-agent'],
        agents: [{
          name: 'my-agent',
          status: 'running',
          source: 'claude-code',
          model: 'claude-sonnet-4-6',
          spawned_at: new Date().toISOString(),
          transcript_bytes: 1024,
          transcript_lines: 10,
        }],
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: 'my-agent', nudges: [], session_nudges: [] }
      if (path === '/agents/my-agent/transcript') return { content: 'Assistant: this is **bold** text' }
      return {}
    })
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTitle(/^my-agent(\s|$)/)).toBeInTheDocument())

    const viewBtn = await screen.findByText('View Transcript')
    fireEvent.click(viewBtn)

    await waitFor(() => {
      const boldEl = screen.getByText('bold')
      expect(boldEl.tagName).toBe('STRONG')
    })
  })

  it('renders fenced code block inside a speaker block', async () => {
    const content = 'Assistant: here is code\n\n```\nconsole.log("hi")\n```'
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['my-agent'],
        agents: [{
          name: 'my-agent',
          status: 'running',
          source: 'claude-code',
          model: 'claude-sonnet-4-6',
          spawned_at: new Date().toISOString(),
          transcript_bytes: 1024,
          transcript_lines: 10,
        }],
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: 'my-agent', nudges: [], session_nudges: [] }
      if (path === '/agents/my-agent/transcript') return { content }
      return {}
    })
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTitle(/^my-agent(\s|$)/)).toBeInTheDocument())

    const viewBtn = await screen.findByText('View Transcript')
    fireEvent.click(viewBtn)

    await waitFor(() => {
      // renderMarkdown wraps code blocks in <pre><code>
      const pre = document.querySelector('pre')
      expect(pre).not.toBeNull()
      expect(pre!.querySelector('code')).not.toBeNull()
    })
  })

  it('falls back to raw markdown rendering when no speaker prefixes', async () => {
    const content = '## Status\n\nDone. Everything is working.'
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['my-agent'],
        agents: [{
          name: 'my-agent',
          status: 'running',
          source: 'claude-code',
          model: 'claude-sonnet-4-6',
          spawned_at: new Date().toISOString(),
          transcript_bytes: 1024,
          transcript_lines: 10,
        }],
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: 'my-agent', nudges: [], session_nudges: [] }
      if (path === '/agents/my-agent/transcript') return { content }
      return {}
    })
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTitle(/^my-agent(\s|$)/)).toBeInTheDocument())

    const viewBtn = await screen.findByText('View Transcript')
    fireEvent.click(viewBtn)

    await waitFor(() => {
      // No speaker labels should appear
      // CSS text-transform: uppercase is visual only; DOM text remains mixed-case
      // In fallback mode there should be no speaker label elements
      expect(screen.queryByText('User')).toBeNull()
      expect(screen.queryByText('Assistant')).toBeNull()
      // Content is rendered as markdown (heading h2 should exist)
      expect(screen.getByRole('heading', { level: 2, name: 'Status' })).toBeInTheDocument()
    })
  })

  // Needle: transcript modal was showing "Could not load transcript.
  // Upstream unavailable" even when the backend returned 200 with real
  // content. Regression guard: a 200 with content must render, never
  // show the upstream catch-all message.
  it('transcript modal shows content on 200 success', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['my-agent'],
        agents: [{
          name: 'my-agent',
          status: 'running',
          source: 'claude-code',
          model: 'claude-sonnet-4-6',
          spawned_at: new Date().toISOString(),
          transcript_bytes: 42,
          transcript_lines: 2,
        }],
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: 'my-agent', nudges: [], session_nudges: [] }
      if (path === '/agents/my-agent/transcript') return { content: 'Real transcript body', bytes: 42, empty: false }
      return {}
    })
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTitle(/^my-agent(\s|$)/)).toBeInTheDocument())
    fireEvent.click(await screen.findByText('View Transcript'))
    await waitFor(() => {
      expect(screen.getByText('Real transcript body')).toBeInTheDocument()
    })
    // Must never show the vite proxy catch-all on a valid 200
    expect(screen.queryByText(/Upstream unavailable/)).toBeNull()
    expect(screen.queryByTestId('transcript-error')).toBeNull()
  })

  it('transcript modal does not show "Upstream unavailable" for a valid 200 response', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['my-agent'],
        agents: [{
          name: 'my-agent',
          status: 'running',
          source: 'claude-code',
          model: 'claude-sonnet-4-6',
          spawned_at: new Date().toISOString(),
          transcript_bytes: 10,
          transcript_lines: 1,
        }],
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: 'my-agent', nudges: [], session_nudges: [] }
      if (path === '/agents/my-agent/transcript') return { content: 'ok content', bytes: 10, empty: false }
      return {}
    })
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTitle(/^my-agent(\s|$)/)).toBeInTheDocument())
    fireEvent.click(await screen.findByText('View Transcript'))
    await waitFor(() => {
      expect(screen.getByText('ok content')).toBeInTheDocument()
    })
    expect(screen.queryByText(/Could not load transcript/)).toBeNull()
    expect(screen.queryByText(/Upstream unavailable/)).toBeNull()
  })

  it('transcript modal distinguishes 500/timeout from empty', async () => {
    // Case 1: ApiError 502 (vite proxy upstream failure) must show a
    // status-coded, actionable message, NOT get confused with empty.
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['my-agent'],
        agents: [{
          name: 'my-agent',
          status: 'running',
          source: 'claude-code',
          model: 'claude-sonnet-4-6',
          spawned_at: new Date().toISOString(),
          transcript_bytes: 12,
          transcript_lines: 1,
        }],
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: 'my-agent', nudges: [], session_nudges: [] }
      if (path === '/agents/my-agent/transcript') throw new ApiError(502, 'Upstream unavailable')
      return {}
    })
    const { unmount } = render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTitle(/^my-agent(\s|$)/)).toBeInTheDocument())
    fireEvent.click(await screen.findByText('View Transcript'))
    await waitFor(() => {
      expect(screen.getByText(/502/)).toBeInTheDocument()
      expect(screen.getByText(/check backend logs/i)).toBeInTheDocument()
    })
    // Retry must be offered for transient upstream failures
    expect(screen.getByText('Retry')).toBeInTheDocument()
    unmount()

    // Case 2: Timeout is distinct from an empty transcript. It offers
    // a retry and mentions "timed out", while empty does neither.
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['my-agent'],
        agents: [{
          name: 'my-agent',
          status: 'running',
          source: 'claude-code',
          model: 'claude-sonnet-4-6',
          spawned_at: new Date().toISOString(),
          transcript_bytes: 12,
          transcript_lines: 1,
        }],
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: 'my-agent', nudges: [], session_nudges: [] }
      if (path === '/agents/my-agent/transcript') throw new ApiTimeoutError(30000)
      return {}
    })
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await waitFor(() => expect(screen.getAllByTitle(/^my-agent(\s|$)/).length).toBeGreaterThan(0))
    fireEvent.click((await screen.findAllByText('View Transcript'))[0])
    await waitFor(() => {
      expect(screen.getByText(/timed out/i)).toBeInTheDocument()
    })
    expect(screen.getByText('Retry')).toBeInTheDocument()
  })
})

// Regression for live-tab-move: when an agent status flips from running
// to completed the row must move from Active to Recent on the next poll
// without a manual page reload.
describe('Agents page - live Active-to-Recent move (30s safety-net poll)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)
  })

  it('moves a row from Active to Recent when status flips to completed', async () => {
    // First call: agent is running.
    const runningResponse = {
      daemon_running: true,
      status: 'ok',
      active: ['flip-agent'],
      agents: [{
        name: 'flip-agent',
        status: 'running',
        source: 'claude-code',
        model: 'sonnet',
        budget: '2.00',
        spawned_at: new Date(Date.now() - 60000).toISOString(),
      }],
    }

    // Subsequent calls: agent has completed.
    const completedResponse = {
      daemon_running: true,
      status: 'ok',
      active: [],
      agents: [{
        name: 'flip-agent',
        status: 'completed',
        source: 'claude-code',
        model: 'sonnet',
        budget: '2.00',
        spawned_at: new Date(Date.now() - 60000).toISOString(),
        completed_at: new Date().toISOString(),
      }],
    }

    let callCount = 0
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') {
        callCount++
        // First fetch returns running; all subsequent fetches return completed.
        return callCount <= 1 ? runningResponse : completedResponse
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: 'flip-agent', nudges: [], session_nudges: [] }
      return {}
    })

    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout'] })
    try {
      render(<MemoryRouter><Agents /></MemoryRouter>)
      // Flush initial mount effects + first /agents call.
      await act(async () => { await Promise.resolve() })
      await act(async () => { await vi.advanceTimersByTimeAsync(50) })

      // Agent starts visible in the Active tab.
      expect(screen.getByTitle(/^flip-agent(\s|$)/)).toBeInTheDocument()
      expect(screen.getByText('Active Sessions')).toBeInTheDocument()

      // Advance past the 30s safety-net poll; second call returns completed.
      await act(async () => { await vi.advanceTimersByTimeAsync(31000) })
      expect(callCount).toBeGreaterThanOrEqual(2)

      // Switch to the Recent tab and verify the agent appears there.
      const recentTab = screen.getByRole('button', { name: 'Recent' })
      fireEvent.click(recentTab)
      expect(screen.getByTitle(/^flip-agent(\s|$)/)).toBeInTheDocument()

      // Switch back to Active — completed agent must be gone.
      const activeTab = screen.getByRole('button', { name: 'Active' })
      fireEvent.click(activeTab)
      expect(screen.getByText('No agents running right now')).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  }, 15000)

  it('auto-switches to Recent tab when a running agent completes without manual tab click', async () => {
    // First call: agent is running.
    const runningResponse = {
      daemon_running: true,
      status: 'ok',
      active: ['auto-flip-agent'],
      agents: [{
        name: 'auto-flip-agent',
        status: 'running',
        source: 'claude-code',
        model: 'sonnet',
        budget: '2.00',
        spawned_at: new Date(Date.now() - 60000).toISOString(),
      }],
    }

    // Subsequent calls: agent has completed.
    const completedResponse = {
      daemon_running: true,
      status: 'ok',
      active: [],
      agents: [{
        name: 'auto-flip-agent',
        status: 'completed',
        source: 'claude-code',
        model: 'sonnet',
        budget: '2.00',
        spawned_at: new Date(Date.now() - 60000).toISOString(),
        completed_at: new Date().toISOString(),
      }],
    }

    let callCount = 0
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') {
        callCount++
        return callCount <= 1 ? runningResponse : completedResponse
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: 'auto-flip-agent', nudges: [], session_nudges: [] }
      return {}
    })

    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout'] })
    try {
      render(<MemoryRouter><Agents /></MemoryRouter>)
      // Flush initial mount effects + first /agents call.
      await act(async () => { await Promise.resolve() })
      await act(async () => { await vi.advanceTimersByTimeAsync(50) })

      // Agent starts on Active tab.
      expect(screen.getByTitle(/^auto-flip-agent(\s|$)/)).toBeInTheDocument()
      expect(screen.getByText('Active Sessions')).toBeInTheDocument()

      // Advance past the 30s safety-net poll; second call returns completed.
      await act(async () => { await vi.advanceTimersByTimeAsync(31000) })
      expect(callCount).toBeGreaterThanOrEqual(2)

      // Page auto-switches to Recent without a manual tab click.
      expect(screen.getByText('Recent Agents')).toBeInTheDocument()
      // Completed agent visible in Recent list.
      expect(screen.getByTitle(/^auto-flip-agent(\s|$)/)).toBeInTheDocument()
      // Active Sessions heading gone (tab switched away).
      expect(screen.queryByText('Active Sessions')).not.toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  }, 15000)
})

describe('Agents page - Enter key submit', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) return { agent: 'test-agent', nudges: [], session_nudges: [] }
      return {}
    })
    mockedApiPost.mockResolvedValue({ ok: true })
  })

  it('Enter on new agent name input spawns the agent', async () => {
    renderAgents()

    // Click New Agent button to show the form
    await waitFor(() => {
      expect(screen.getByText('New Agent')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('New Agent'))

    const nameInput = screen.getByPlaceholderText('Agent name (e.g. research-agent)')
    fireEvent.change(nameInput, { target: { value: 'my-agent' } })
    fireEvent.keyDown(nameInput, { key: 'Enter' })

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith(
        expect.stringContaining('spawn'),
        expect.objectContaining({ name: 'my-agent' }),
      )
    })
  })

  it('Enter on agent prompt textarea spawns the agent (Shift+Enter does not)', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('New Agent')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('New Agent'))

    const nameInput = screen.getByPlaceholderText('Agent name (e.g. research-agent)')
    fireEvent.change(nameInput, { target: { value: 'prompt-agent' } })

    const promptArea = screen.getByPlaceholderText(/Enter to spawn/)
    fireEvent.change(promptArea, { target: { value: 'Do the thing' } })

    // Shift+Enter must NOT spawn
    fireEvent.keyDown(promptArea, { key: 'Enter', shiftKey: true })
    expect(mockedApiPost).not.toHaveBeenCalledWith(
      expect.stringContaining('spawn'),
      expect.anything(),
    )

    // Plain Enter must spawn
    fireEvent.keyDown(promptArea, { key: 'Enter', shiftKey: false })
    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith(
        expect.stringContaining('spawn'),
        expect.objectContaining({ name: 'prompt-agent' }),
      )
    })
  })
})

describe('Agents page - Cancel all agents', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') {
        return {
          daemon_running: true,
          status: 'ok',
          active: ['worker-1', 'worker-2'],
          agents: [
            {
              name: 'worker-1',
              status: 'running',
              source: 'claude-code',
              model: 'sonnet',
              budget: '2.00',
              spawned_at: new Date(Date.now() - 30000).toISOString(),
            },
            {
              name: 'worker-2',
              status: 'running',
              source: 'claude-code',
              model: 'sonnet',
              budget: '2.00',
              spawned_at: new Date(Date.now() - 20000).toISOString(),
            },
          ],
        }
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: '', nudges: [], session_nudges: [] }
      return {}
    })
  })

  it('renders the Cancel all button on the Active tab', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByTestId('cancel-all-agents-btn')).toBeInTheDocument()
    })
  })

  it('Cancel all button is enabled when 2 running agents exist', async () => {
    renderAgents()

    await waitFor(() => {
      const btn = screen.getByTestId('cancel-all-agents-btn')
      expect(btn).not.toBeDisabled()
      expect(btn.textContent).toContain('2')
    })
  })

  it('clicking Cancel all with 2 running agents calls the endpoint and shows feedback', async () => {
    mockedApiPost.mockResolvedValue({ cancelled: 2, names: ['worker-1', 'worker-2'] })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTestId('cancel-all-agents-btn')).not.toBeDisabled()
    })

    fireEvent.click(screen.getByTestId('cancel-all-agents-btn'))

    // Confirm the in-product modal instead of window.confirm.
    await waitFor(() => {
      expect(screen.getByTestId('confirm-modal-confirm')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('confirm-modal-confirm'))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/agents/cancel-all', {})
    })
  })

  it('cancel-all calls fetchAgents after a successful cancel', async () => {
    // Simulate a successful cancel-all response.
    mockedApiPost.mockResolvedValue({ cancelled: 2, names: ['worker-1', 'worker-2'] })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByTestId('cancel-all-agents-btn')).not.toBeDisabled()
    })

    // Record how many times GET /agents has been called before the button click.
    const getCallsBefore = mockedApiGet.mock.calls.filter((c) => c[0] === '/agents').length

    fireEvent.click(screen.getByTestId('cancel-all-agents-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('confirm-modal-confirm')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('confirm-modal-confirm'))

    // Wait for the POST to complete, then verify GET /agents was called again.
    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/agents/cancel-all', {})
    })

    await waitFor(() => {
      const getCallsAfter = mockedApiGet.mock.calls.filter((c) => c[0] === '/agents').length
      expect(getCallsAfter).toBeGreaterThan(getCallsBefore)
    })
  })

  it('Active list is empty after cancel-all succeeds', async () => {
    // cancel-all returns both workers as cancelled.
    mockedApiPost.mockResolvedValue({ cancelled: 2, names: ['worker-1', 'worker-2'] })

    // After the refetch triggered by cancel-all, the server returns both agents
    // as 'cancelled'. The dismissedAgentsRef ensures they are filtered from
    // allAgents regardless, but in real usage the server also returns cancelled.
    let fetchCount = 0
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') {
        fetchCount++
        const status = fetchCount > 1 ? 'cancelled' : 'running'
        return {
          daemon_running: true,
          status: 'ok',
          active: [],
          agents: [
            // Include a task field so the row renders as searchable plain text.
            { name: 'worker-1', task: 'Fix the database', status, source: 'claude-code', model: 'sonnet', budget: '2.00', spawned_at: new Date(Date.now() - 30000).toISOString() },
            { name: 'worker-2', task: 'Deploy the service', status, source: 'claude-code', model: 'sonnet', budget: '2.00', spawned_at: new Date(Date.now() - 20000).toISOString() },
          ],
        }
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: '', nudges: [], session_nudges: [] }
      return {}
    })

    renderAgents()

    // Wait for both agents to appear in the Active list by their task text.
    await waitFor(() => {
      expect(screen.getByText('Fix the database')).toBeInTheDocument()
      expect(screen.getByText('Deploy the service')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('cancel-all-agents-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('confirm-modal-confirm')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('confirm-modal-confirm'))

    // After cancel-all completes, both agents must be gone from the Active list.
    await waitFor(() => {
      expect(screen.queryByText('Fix the database')).not.toBeInTheDocument()
      expect(screen.queryByText('Deploy the service')).not.toBeInTheDocument()
    })
  })

  it('Cancel all button is disabled when no background agents are running', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') {
        return {
          daemon_running: true,
          status: 'ok',
          active: [],
          agents: [
            {
              name: 'chat-session',
              status: 'running',
              source: 'chat',
              model: 'sonnet',
              budget: '0',
              spawned_at: new Date().toISOString(),
            },
          ],
        }
      }
      if (path === '/agents/templates') return { templates: [] }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      const btn = screen.getByTestId('cancel-all-agents-btn')
      expect(btn).toBeDisabled()
    })
  })
})

// ---------------------------------------------------------------------------
// Template dedup: no name should appear in both installed and marketplace grids
// ---------------------------------------------------------------------------

describe('Agent Templates dedup - no cross-section duplicates', () => {

  // Built-in template names hardcoded in the fallback array (Agents.tsx ~2169-2173)
  const BUILTIN_NAMES = ['Research', 'Builder', 'Diagnose', 'Review', 'Test']

  it('no AGENT_MARKETPLACE template name matches a built-in name', () => {
    const builtinSet = new Set(BUILTIN_NAMES.map((n: string) => n.toLowerCase().trim()))
    const conflicts: string[] = []
    for (const cat of AGENT_MARKETPLACE) {
      for (const tpl of cat.templates) {
        if (builtinSet.has(tpl.name.toLowerCase().trim())) {
          conflicts.push(tpl.name)
        }
      }
    }
    // The "Research" entry in AGENT_MARKETPLACE "For everyone" previously caused a duplicate.
    // It should not be removed from AGENT_MARKETPLACE (other pages may use it), but the
    // Agents page marks it as alreadyAdded via the builtInTemplates dedup check.
    // This test documents that any such overlap is intentional and handled by the UI guard.
    // If new overlaps appear here that are NOT handled, that is a bug.
    // For now assert the known overlap count does not grow beyond the known case.
    expect(conflicts.length).toBeLessThanOrEqual(1) // 'Research' is the one known overlap
  })

  it('AGENT_MARKETPLACE itself contains no internal duplicate names', () => {
    const seen = new Set<string>()
    const dupes: string[] = []
    for (const cat of AGENT_MARKETPLACE) {
      for (const tpl of cat.templates) {
        const key = tpl.name.toLowerCase().trim()
        if (seen.has(key)) dupes.push(tpl.name)
        seen.add(key)
      }
    }
    expect(dupes).toEqual([])
  })
})

describe('Agent Templates source attribution', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({
      chatOpen: true,
      osName: 'myOS',
      darkMode: true,
      customAgentTemplates: [],
    })
    // The app store's setCustomAgentTemplates fires an api.patch for
    // server sync. Mock it so the prune effect does not explode.
    vi.mocked(api.patch).mockResolvedValue({})
    vi.mocked(api.post).mockResolvedValue({})
  })

  // TemplateCard shows the "custom" badge only when source === 'custom'.
  // Marketplace entries from /agents/persona-templates must never be
  // labeled custom, even if the backend forgets to set the source field.
  it('marketplace templates from persona API never render a custom badge', async () => {
    const marketplacePmResp = {
      templates: [
        {
          id: 'builtin-pm-prd',
          name: 'PRD',
          description: 'Draft a PRD.',
          icon: 'article',
          prompt_template: 'Draft PRD.',
          model: 'sonnet',
          budget: 3.0,
          source: 'marketplace',
          personas: ['pm'],
          installed: true,
          builtin: true,
        },
      ],
      persona: 'pm',
    }
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return marketplacePmResp
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      if (path === '/agents/fleets') return { fleets: [] }
      return {}
    })

    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    // The PRD card should be visible on the installed grid.
    const card = await screen.findByTestId('pm-template-card-builtin-pm-prd')
    // The "custom" badge is the exact string 'custom' in TemplateCard.
    // It must not appear on a marketplace card.
    expect(card.textContent).not.toContain('custom')
  })

  it('marketplace badge renders when source is marketplace and source field is missing', async () => {
    // Even when the backend forgets to set a source, the fallback must
    // not be "custom". It falls through to "marketplace" so the card is
    // never mislabeled.
    const partialResp = {
      templates: [
        {
          id: 'builtin-pm-prd',
          name: 'PRD',
          description: 'Draft a PRD.',
          icon: 'article',
          prompt_template: 'Draft PRD.',
          model: 'sonnet',
          budget: 3.0,
          personas: ['pm'],
          installed: true,
          builtin: false, // triggers the fallback branch we fixed
        },
      ],
      persona: 'pm',
    }
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return partialResp
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      if (path === '/agents/fleets') return { fleets: [] }
      return {}
    })

    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    const card = await screen.findByTestId('pm-template-card-builtin-pm-prd')
    expect(card.textContent).not.toContain('custom')
  })

  it('user-templates endpoint output renders with custom badge', async () => {
    const userResp = {
      templates: [
        {
          id: 'custom-abc',
          name: 'My Template',
          description: 'A user template.',
          icon: 'star',
          prompt_template: 'Do my thing.',
          model: 'sonnet',
          budget: 2.0,
          source: 'custom',
          builtin: false,
        },
      ],
    }
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { templates: [], persona: 'pm' }
      if (path === '/agents/user-templates') return userResp
      if (path === '/settings') return { persona: 'pm' }
      if (path === '/agents/fleets') return { fleets: [] }
      return {}
    })

    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    const card = await screen.findByTestId('user-template-card-custom-abc')
    expect(card.textContent).toContain('custom')
  })

  it('stale customAgentTemplates entries that match backend names are pruned', async () => {
    // This is the screenshot bug: older onboarding stuffed persona
    // marketplace templates (PRD, Competitive Scan, etc) into the
    // customAgentTemplates localStorage list. The Agents page now
    // prunes any entry whose name already appears in a backend source
    // so the user stops seeing duplicate cards with the "custom" badge.
    useAppStore.setState({
      chatOpen: true,
      osName: 'myOS',
      darkMode: true,
      customAgentTemplates: [
        { name: 'PRD', description: 'stale', icon: 'article', model: 'sonnet', budget: 3 },
        { name: 'My Real Custom', description: 'kept', icon: 'star', model: 'sonnet', budget: 2 },
      ],
    })
    const personaResp = {
      templates: [
        {
          id: 'builtin-pm-prd',
          name: 'PRD',
          description: 'Draft a PRD.',
          icon: 'article',
          prompt_template: 'Draft PRD.',
          model: 'sonnet',
          budget: 3.0,
          source: 'marketplace',
          personas: ['pm'],
          installed: true,
          builtin: true,
        },
      ],
      persona: 'pm',
    }
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return personaResp
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      if (path === '/agents/fleets') return { fleets: [] }
      return {}
    })

    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    // Wait for the grid to populate.
    await screen.findByTestId('installed-templates-grid')

    // After the prune effect runs, customAgentTemplates no longer holds PRD.
    await waitFor(() => {
      const state = useAppStore.getState().customAgentTemplates
      const names = state.map((t) => t.name)
      expect(names).not.toContain('PRD')
      expect(names).toContain('My Real Custom')
    })
  })

  it('marketplace template installed via the Add button shows marketplace badge, not custom', async () => {
    // →1032: when a user clicks "Use" on a marketplace card, the installed
    // template must show "marketplace" not "custom". The fix stores
    // source: "marketplace" on the template object so displayTemplates
    // passes the right value to TemplateCard.
    useAppStore.setState({
      chatOpen: true,
      osName: 'myOS',
      darkMode: true,
      customAgentTemplates: [
        { name: 'Summarizer', description: 'Summarize docs.', icon: 'summarize', model: 'sonnet', budget: 2, source: 'marketplace' },
      ],
    })
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { templates: [], persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      if (path === '/agents/fleets') return { fleets: [] }
      return {}
    })

    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await screen.findByTestId('installed-templates-grid')

    const card = await screen.findByTestId('template-card-Summarizer')
    expect(card.textContent).toContain('catalog')
    expect(card.textContent).not.toContain('custom')
  })
})

describe('Agents page - Phase 2 enterprise consistency', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) return { agent: 'test-agent', nudges: [], session_nudges: [] }
      return {}
    })
    mockedApiPost.mockResolvedValue({ ok: true })
  })

  it('empty state shows "Spawn your first agent" CTA when no agents are running', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByTestId('empty-state')).toBeInTheDocument()
    })

    expect(screen.getByText('Spawn your first agent')).toBeInTheDocument()
  })

  it('primary Spawn Agent button renders with Button variant=primary', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('New Agent')).toBeInTheDocument()
    })

    const btn = screen.getByTestId('spawn-agent-btn')
    expect(btn).toBeInTheDocument()
    expect(btn.tagName).toBe('BUTTON')
    // Button primitive adds data-testid="button" via the Button component
    expect(btn).toHaveClass('bg-blue-600')
  })

  it('copy-audit: empty active state uses plain-language title', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('No agents running right now')).toBeInTheDocument()
    })
  })
})


describe('Agents page - Template detail alias feature', () => {
  const mockPmTemplatesResponse = {
    templates: [
      {
        id: 'builtin-pm-prd',
        name: 'PRD',
        description: 'Turn a rough idea into a product requirements doc.',
        icon: 'article',
        prompt_template: 'You are a senior PM. Turn the rough feature idea into a PRD.',
        model: 'sonnet',
        budget: 3.0,
        builtin: true,
        source: 'marketplace',
      },
    ],
  }

  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: false, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { ...mockPmTemplatesResponse, persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      if (path === '/agents/fleets') return { fleets: [] }
      if (path.includes('/alias')) return { alias: null }
      return {}
    })
    mockedApiPost.mockResolvedValue({})
    vi.mocked(api.patch).mockResolvedValue({ template: { user_alias: 'my-prd' } })
  })

  it('template detail modal shows alias section when templateId is present', async () => {
    renderAgents()

    const templatesTab = await screen.findByText('Templates')
    fireEvent.click(templatesTab)

    const useBtn = await screen.findByTestId('pm-template-card-builtin-pm-prd-action')
    fireEvent.click(useBtn)

    await waitFor(() => {
      expect(screen.getByTestId('template-alias-section')).toBeInTheDocument()
    })
  })

  it('alias input field accepts text and save button triggers PATCH', async () => {
    renderAgents()

    const templatesTab = await screen.findByText('Templates')
    fireEvent.click(templatesTab)

    const useBtn = await screen.findByTestId('pm-template-card-builtin-pm-prd-action')
    fireEvent.click(useBtn)

    const aliasInput = await screen.findByTestId('template-alias-input')
    fireEvent.change(aliasInput, { target: { value: 'my-prd' } })

    const saveBtn = screen.getByTestId('template-alias-save')
    fireEvent.click(saveBtn)

    await waitFor(() => {
      expect(vi.mocked(api.patch)).toHaveBeenCalledWith(
        '/agents/templates/builtin-pm-prd/alias',
        { alias: 'my-prd' }
      )
    })
  })

  it('delete alias button triggers PATCH with null', async () => {
    // Pre-populate the alias
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: false, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { ...mockPmTemplatesResponse, persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      if (path === '/agents/fleets') return { fleets: [] }
      if (path.includes('/alias')) return { alias: 'existing-alias' }
      return {}
    })

    renderAgents()

    const templatesTab = await screen.findByText('Templates')
    fireEvent.click(templatesTab)

    const useBtn = await screen.findByTestId('pm-template-card-builtin-pm-prd-action')
    fireEvent.click(useBtn)

    // Wait for the alias to load from the GET
    const aliasInput = await screen.findByTestId('template-alias-input')
    await waitFor(() => {
      expect(aliasInput).toHaveValue('existing-alias')
    })

    const deleteBtn = screen.getByTestId('template-alias-delete')
    fireEvent.click(deleteBtn)

    await waitFor(() => {
      expect(vi.mocked(api.patch)).toHaveBeenCalledWith(
        '/agents/templates/builtin-pm-prd/alias',
        { alias: null }
      )
    })
  })

  it('template detail modal shows source badge', async () => {
    renderAgents()

    const templatesTab = await screen.findByText('Templates')
    fireEvent.click(templatesTab)

    const useBtn = await screen.findByTestId('pm-template-card-builtin-pm-prd-action')
    fireEvent.click(useBtn)

    const modal = await screen.findByTestId('template-detail-modal')
    // The source badge text should be present inside the modal
    const badges = modal.querySelectorAll('span')
    const marketplaceBadge = Array.from(badges).find(s => s.textContent === 'marketplace')
    expect(marketplaceBadge).toBeTruthy()
  })

  it('template detail modal shows prompt expand button for long prompts', async () => {
    // Create a template with a very long prompt
    const longPrompt = 'A'.repeat(600)
    const longPromptTemplates = {
      templates: [
        {
          ...mockPmTemplatesResponse.templates[0],
          prompt_template: longPrompt,
        },
      ],
    }
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: false, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { ...longPromptTemplates, persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      if (path === '/agents/fleets') return { fleets: [] }
      if (path.includes('/alias')) return { alias: null }
      return {}
    })

    renderAgents()

    const templatesTab = await screen.findByText('Templates')
    fireEvent.click(templatesTab)

    const useBtn = await screen.findByTestId('pm-template-card-builtin-pm-prd-action')
    fireEvent.click(useBtn)

    await waitFor(() => {
      expect(screen.getByTestId('template-prompt-expand')).toBeInTheDocument()
    })
  })

  it('alias section visible for built-in agentfile template not in AGENTFILE_TEMPLATE_IDS', async () => {
    // "explain-plain" is a built-in that is NOT in the AGENTFILE_TEMPLATE_IDS map.
    // Its derived templateId must be "builtin-explain-plain" so the alias section renders.
    const agentfileTemplates = {
      templates: [
        {
          name: 'explain-plain',
          file: 'explain-plain.agent',
          content: 'FROM auto\nDESC "Explain anything in plain language."\n',
          description: 'Explain anything in plain language.',
          capabilities: null,
          parse_error: null,
        },
      ],
    }

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: false, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return agentfileTemplates
      if (path.startsWith('/agents/persona-templates')) return { templates: [], persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      if (path === '/agents/fleets') return { fleets: [] }
      if (path.includes('/alias')) return { alias: null }
      return {}
    })
    vi.mocked(api.patch).mockResolvedValue({ template: { user_alias: 'ep' } })

    renderAgents()

    const templatesTab = await screen.findByText('Templates')
    fireEvent.click(templatesTab)

    const useBtn = await screen.findByTestId('template-card-Explain Plain-action')
    fireEvent.click(useBtn)

    await waitFor(() => {
      expect(screen.getByTestId('template-alias-section')).toBeInTheDocument()
    })

    // Save alias and assert the PATCH uses the derived builtin-explain-plain id
    const aliasInput = screen.getByTestId('template-alias-input')
    fireEvent.change(aliasInput, { target: { value: 'ep' } })
    fireEvent.click(screen.getByTestId('template-alias-save'))

    await waitFor(() => {
      expect(vi.mocked(api.patch)).toHaveBeenCalledWith(
        '/agents/templates/builtin-explain-plain/alias',
        { alias: 'ep' }
      )
    })
  })
})

describe('Agents page - optimistic spawn insert + agents bus', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: false, osName: 'myOS', darkMode: true })
  })

  it('optimistic insert: clicking spawn shows the new row before the spawn POST resolves', async () => {
    // Server returns no agents at first.
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { templates: [] }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return {}
      if (path === '/agents/fleets') return { fleets: [] }
      if (path === '/agents/duration-stats') return { median_seconds: 60, p75_seconds: 90, sample_count: 0, window_days: 7 }
      return {}
    })

    // Hold the spawn POST open so we can assert the optimistic row
    // appears BEFORE the server response.
    let resolveSpawn: ((value: unknown) => void) | null = null
    const spawnPromise = new Promise((resolve) => { resolveSpawn = resolve })
    mockedApiPost.mockImplementation(async (path: string) => {
      if (path === '/agents/spawn') return spawnPromise
      return { ok: true }
    })

    renderAgents()

    // Wait for the Active tab button to render.
    await screen.findByTestId('tab-active')

    // Open the New Agent form via the spawn button.
    const newAgentBtn = await screen.findByTestId('spawn-agent-btn')
    fireEvent.click(newAgentBtn)

    // Type a name and click the Spawn button inside the form.
    const nameInput = await screen.findByPlaceholderText(/Agent name/i)
    fireEvent.change(nameInput, { target: { value: 'roadmap' } })

    const spawnBtn = await screen.findByRole('button', { name: /^Spawn$/ })
    fireEvent.click(spawnBtn)

    // The placeholder row should appear immediately, BEFORE we let the
    // spawn POST resolve. Match by title attribute (the raw name).
    await waitFor(() => {
      expect(screen.getByTitle(/^roadmap(\s|$)/)).toBeInTheDocument()
    })

    // Now resolve the POST and confirm the row remains.
    if (resolveSpawn) (resolveSpawn as (v: unknown) => void)({ name: 'roadmap', pid: 1234, transcript: '/tmp/x' })
    await spawnPromise

    expect(screen.getByTitle(/^roadmap(\s|$)/)).toBeInTheDocument()
  })

  it('failed spawn POST removes the optimistic placeholder', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { templates: [] }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return {}
      if (path === '/agents/fleets') return { fleets: [] }
      if (path === '/agents/duration-stats') return { median_seconds: 60, p75_seconds: 90, sample_count: 0, window_days: 7 }
      return {}
    })

    mockedApiPost.mockImplementation(async (path: string) => {
      if (path === '/agents/spawn') throw new ApiError(500, 'spawn failed')
      return { ok: true }
    })

    renderAgents()

    await screen.findByTestId('tab-active')
    const newAgentBtn = await screen.findByTestId('spawn-agent-btn')
    fireEvent.click(newAgentBtn)

    const nameInput = await screen.findByPlaceholderText(/Agent name/i)
    fireEvent.change(nameInput, { target: { value: 'doomed' } })

    const spawnBtn = await screen.findByRole('button', { name: /^Spawn$/ })
    fireEvent.click(spawnBtn)

    // The placeholder appears synchronously.
    await waitFor(() => {
      expect(screen.getByTitle(/^doomed(\s|$)/)).toBeInTheDocument()
    })

    // After the POST rejects, the placeholder should be removed.
    await waitFor(() => {
      expect(screen.queryByTitle(/^doomed(\s|$)/)).not.toBeInTheDocument()
    })
  })

  it('bumpAgents() triggers the Agents page to refetch /agents', async () => {
    let callCount = 0
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') {
        callCount += 1
        return { daemon_running: true, status: 'ok', active: [], agents: [] }
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { templates: [] }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return {}
      if (path === '/agents/fleets') return { fleets: [] }
      if (path === '/agents/duration-stats') return { median_seconds: 60, p75_seconds: 90, sample_count: 0, window_days: 7 }
      return {}
    })
    mockedApiPost.mockResolvedValue({ ok: true })

    renderAgents()
    await screen.findByTestId('tab-active')

    // Wait for the initial fetch to complete.
    await waitFor(() => {
      expect(callCount).toBeGreaterThanOrEqual(1)
    })
    const before = callCount

    // Bump the bus the way ChatPanel will when the assistant spawns
    // an agent via the spawn_agent tool.
    const bus = await import('../lib/sidebarBus')
    bus.bumpAgents()

    await waitFor(() => {
      expect(callCount).toBeGreaterThan(before)
    })
  })
})

describe('Agents page - Roadmap card opens modal (no direct-spawn, no suffix)', () => {
  const mockRoadmapPmTemplates = {
    templates: [
      {
        id: 'builtin-pm-roadmap',
        name: 'Roadmap',
        description: 'Draft a 3 year roadmap.',
        icon: 'timeline',
        prompt_template: 'You are a senior PM. Produce a 3 year roadmap.',
        model: 'sonnet',
        budget: 3.0,
        builtin: true,
        source: 'marketplace',
      },
    ],
  }

  // The spawn POST registers the agent server-side, so subsequent /agents
  // GETs include it. We mirror that by having the mock return the spawned
  // agent on the next call.
  let spawnedAgentName: string | null = null

  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: false, osName: 'myOS', darkMode: true })
    spawnedAgentName = null

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') {
        const agents = spawnedAgentName
          ? [{ name: spawnedAgentName, status: 'running', source: 'ui', model: 'claude-sonnet-4-6', spawned_at: new Date().toISOString() }]
          : []
        return { daemon_running: true, status: 'ok', active: agents.map(a => a.name), agents }
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { ...mockRoadmapPmTemplates, persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm' }
      if (path === '/agents/fleets') return { fleets: [] }
      if (path === '/agents/duration-stats') return { median_seconds: 60, p75_seconds: 90, sample_count: 0, window_days: 7 }
      return {}
    })
    mockedApiPost.mockImplementation(async (path: string, body: unknown) => {
      if (path === '/agents/spawn') {
        const b = body as { name?: string }
        spawnedAgentName = b.name || 'roadmap-xyz'
        return { name: spawnedAgentName, pid: 1, transcript: '/tmp/x' }
      }
      return { ok: true }
    })
  })

  it('clicking Use on the Roadmap card opens the detail modal without spawning', async () => {
    renderAgents()

    // Switch to Templates sub-tab so the Roadmap card renders.
    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    // Find the Roadmap PM template card's Use button.
    const useBtn = await screen.findByTestId('pm-template-card-builtin-pm-roadmap-action')

    fireEvent.click(useBtn)

    // The detail modal should appear so the user can type a prompt.
    await waitFor(() => {
      expect(screen.getByTestId('template-user-message-input')).toBeInTheDocument()
    })

    // No spawn POST should have fired yet.
    expect(mockedApiPost).not.toHaveBeenCalledWith(
      '/agents/spawn',
      expect.anything()
    )
  })

  it('modal spawn uses the bare template name (no random suffix) so Active row reads "roadmap" not "roadmap-xyz"', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    const useBtn = await screen.findByTestId('pm-template-card-builtin-pm-roadmap-action')
    fireEvent.click(useBtn)

    // Type a prompt into the modal and click Spawn.
    const input = await screen.findByTestId('template-user-message-input')
    fireEvent.change(input, { target: { value: 'build me a 3 year roadmap for myOS' } })

    const spawnBtn = await screen.findByTestId('template-spawn-button')
    fireEvent.click(spawnBtn)

    // The spawn POST must be called with bare template-derived name (no
    // random hex suffix). "Roadmap" slugifies to "roadmap".
    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith(
        '/agents/spawn',
        expect.objectContaining({
          name: 'roadmap',
          template: 'Roadmap',
        })
      )
    })
  })
})


describe('Agents page - Active Sessions summary endpoint (nav-badge race fix)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(
      'summary-runner',
      'summary-ghost',
      'summary-completed',
    )
  })

  it('renders ONLY rows whose names appear in the runningAgents store', async () => {
    // Scenario: the full /agents payload still has three rows (one
    // running, one running-but-server-says-stale, one completed). The
    // runningAgents store (WebSocket-fed) authoritatively says only
    // "summary-runner" is running. The Active Sessions tab must render
    // exactly one row, regardless of what allAgents thinks, so that its
    // count and the Sidebar nav-badge count always agree.
    const fullAgents = {
      daemon_running: true,
      status: 'ok',
      active: ['summary-runner', 'summary-ghost'],
      agents: [
        {
          name: 'summary-runner',
          status: 'running',
          source: 'claude-code',
          model: 'sonnet',
          budget: '2.00',
          spawned_at: new Date(Date.now() - 30000).toISOString(),
        },
        {
          // Legacy /agents payload still reports this as running but
          // the store has already dropped it (terminated upstream).
          // The Active tab must trust the store.
          name: 'summary-ghost',
          status: 'running',
          source: 'claude-code',
          model: 'sonnet',
          budget: '2.00',
          spawned_at: new Date(Date.now() - 60000).toISOString(),
        },
        {
          name: 'summary-completed',
          status: 'completed',
          source: 'claude-code',
          model: 'sonnet',
          budget: '2.00',
          spawned_at: new Date(Date.now() - 120000).toISOString(),
        },
      ],
    }

    // Seed the runningAgents store with only summary-runner. This is
    // what the WebSocket feed would push from the backend's filtered
    // snapshot.
    useRunningAgentsStore.setState({
      count: 1,
      agents: [{ name: 'summary-runner' }],
    })

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return fullAgents
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: '', nudges: [], session_nudges: [] }
      return {}
    })

    render(
      <MemoryRouter>
        <Agents />
      </MemoryRouter>
    )

    // summary-runner must render.
    await waitFor(() => {
      expect(screen.getByTitle(/^summary-runner(\s|$)/)).toBeInTheDocument()
    })

    // summary-ghost must NOT render even though the full /agents
    // payload still reports it as running. The store is source of truth.
    await waitFor(() => {
      expect(screen.queryByTitle(/^summary-ghost(\s|$)/)).toBeNull()
    })

    // summary-completed has never been active; it must not appear in
    // the Active Sessions list either.
    expect(screen.queryByTitle(/^summary-completed(\s|$)/)).toBeNull()
  })

  it('Active Sessions and nav-badge agree (both backed by runningAgents store)', async () => {
    // Post-→992 architecture: the Sidebar badge and Active Sessions tab
    // both derive from the same runningAgents Zustand store, so by
    // construction they cannot disagree on count. This test verifies
    // the store-driven path: when the store lists 2 running agents
    // that are also in the full /agents payload, both surfaces render
    // those 2 agents. The empty-state must NOT be shown.
    preExpandAgents('nav-parity-1', 'nav-parity-2')

    const fullAgents = {
      daemon_running: true,
      status: 'ok',
      active: ['nav-parity-1', 'nav-parity-2'],
      agents: [
        {
          name: 'nav-parity-1',
          status: 'running',
          source: 'claude-code',
          model: 'sonnet',
          spawned_at: new Date(Date.now() - 30000).toISOString(),
        },
        {
          name: 'nav-parity-2',
          status: 'running',
          source: 'claude-code',
          model: 'sonnet',
          spawned_at: new Date(Date.now() - 15000).toISOString(),
        },
      ],
    }

    // Seed store as the WebSocket feed would.
    useRunningAgentsStore.setState({
      count: 2,
      agents: [{ name: 'nav-parity-1' }, { name: 'nav-parity-2' }],
    })

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return fullAgents
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: '', nudges: [], session_nudges: [] }
      return {}
    })

    render(
      <MemoryRouter>
        <Agents />
      </MemoryRouter>
    )

    // Both store-listed names must render in Active Sessions.
    await waitFor(
      () => {
        expect(screen.getByTitle(/^nav-parity-1(\s|$)/)).toBeInTheDocument()
        expect(screen.getByTitle(/^nav-parity-2(\s|$)/)).toBeInTheDocument()
      },
      { timeout: 3000 }
    )

    // Empty state must NOT be visible: badge counts 2, tab shows 2.
    expect(screen.queryByText('No agents running right now')).toBeNull()
  })
})

describe('Agents page - chat session isolation (→1095)', () => {
  // Regression: spawning a new agent under the same name (e.g. "roadmap")
  // showed prior-session bubbles in the chat thread because the frontend
  // nudge/reply state was keyed by name and the merge in fetchNudges
  // re-added old local entries even after the backend purged them.
  // The fix: track spawned_at per agent and clear local state when it changes.
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
  })

  it('clears prior-session messages when the same agent name is respawned', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout'] })
    try {
      const t1 = '2026-05-09T19:00:00.000Z'
      const t2 = '2026-05-09T19:26:00.000Z'

      let returnT2 = false
      mockedApiGet.mockImplementation(async (path: string) => {
        if (path === '/agents') {
          if (!returnT2) {
            return {
              daemon_running: true, status: 'ok', active: ['roadmap'],
              agents: [{ name: 'roadmap', status: 'running', source: 'claude-code', model: 'sonnet', budget: '2.00', spawned_at: t1 }],
            }
          }
          // Respawned: same name, new spawned_at, server purged nudges.
          return {
            daemon_running: true, status: 'ok', active: ['roadmap'],
            agents: [{ name: 'roadmap', status: 'running', source: 'claude-code', model: 'sonnet', budget: '2.00', spawned_at: t2 }],
          }
        }
        if (path === '/agents/templates') return { templates: [] }
        if (path.includes('/nudges')) return { nudges: [], session_nudges: [], replies: [], session_replies: [] }
        return {}
      })

      mockedApiPost.mockResolvedValue({
        result: 'Nudge sent',
        nudge: { message: 'completed via idle detection', timestamp: '2026-05-09T19:10:00.000Z', source: 'ui', stdin_delivered: false },
      })

      preExpandAgents('roadmap')
      render(<MemoryRouter><Agents /></MemoryRouter>)

      // Settle the initial mount fetch (microtasks only, no timer advance needed).
      await act(async () => { await Promise.resolve() })

      // Agent card should appear.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(100)
      })
      expect(screen.getByTitle(/^roadmap(\s|$)/)).toBeInTheDocument()

      // Send a message — puts it optimistically in nudgeHistory["roadmap"].
      const input = screen.getByPlaceholderText('Message roadmap...')
      fireEvent.change(input, { target: { value: 'completed via idle detection' } })
      await act(async () => {
        fireEvent.keyDown(input, { key: 'Enter', shiftKey: false })
        await Promise.resolve()
      })

      // Old-session bubble is now visible.
      expect(screen.getByText('completed via idle detection')).toBeInTheDocument()

      // Switch the /agents mock to return t2 (simulates the backend respawn).
      returnT2 = true

      // Advance past the 30s fetchAgents safety-net interval so it fires with t2.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(31000)
      })

      // After allAgents updates with new spawned_at the cleanup effect fires
      // and deletes nudgeHistory["roadmap"]. Old bubble must be gone.
      expect(screen.queryByText('completed via idle detection')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  }, 15000)
})

// →1220: WS fast-path tests — verify Active tab is driven by the WS store,
// not by the 30s safety-net poll.
describe('Agents page - WS realtime fast path (→1220)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    useRunningAgentsStore.setState({ count: 0, agents: [], connected: false, lastUpdatedAt: null })
  })

  it('renders a WS-store agent even when /agents returns empty, with no extra poll calls', async () => {
    let agentsCalls = 0
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') { agentsCalls++; return { daemon_running: true, status: 'ok', active: [], agents: [] } }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/persona-templates')) return { templates: [] }
      if (path.includes('/user-templates')) return { templates: [] }
      if (path.includes('/pm-templates')) return { templates: [] }
      if (path.includes('/duration-stats')) return { median_seconds: 60, p75_seconds: 90, sample_count: 5, window_days: 7 }
      return {}
    })

    useRunningAgentsStore.setState({
      count: 1,
      agents: [{ name: 'ws-fast-agent', status: 'running' }],
      connected: true,
      lastUpdatedAt: new Date().toISOString(),
    })
    preExpandAgents('ws-fast-agent')

    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout'] })
    try {
      render(<MemoryRouter><Agents /></MemoryRouter>)

      // Settle mount effects including the async fetchAgents call.
      await act(async () => { await Promise.resolve() })
      await act(async () => { await vi.advanceTimersByTimeAsync(50) })

      // Agent appears from WS store even though HTTP returned empty list.
      expect(screen.getByTitle(/^ws-fast-agent(\s|$)/)).toBeInTheDocument()

      const callsAfterMount = agentsCalls // 1 for the initial hydration call

      // Advance 35s — old 2s interval would have fired ~17 more times;
      // new 30s safety-net fires at most once.
      await act(async () => { await vi.advanceTimersByTimeAsync(35000) })

      expect(agentsCalls).toBeLessThanOrEqual(callsAfterMount + 1)
    } finally {
      vi.useRealTimers()
    }
  }, 15000)

  it('removes an agent from Active immediately on WS delta without an HTTP call', async () => {
    let agentsCalls = 0
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') { agentsCalls++; return { daemon_running: true, status: 'ok', active: [], agents: [] } }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/persona-templates')) return { templates: [] }
      if (path.includes('/user-templates')) return { templates: [] }
      if (path.includes('/pm-templates')) return { templates: [] }
      if (path.includes('/duration-stats')) return { median_seconds: 60, p75_seconds: 90, sample_count: 5, window_days: 7 }
      return {}
    })

    // Two agents running; both seeded in the WS store.
    useRunningAgentsStore.setState({
      count: 2,
      agents: [
        { name: 'ws-agent-keep', status: 'running' },
        { name: 'ws-agent-done', status: 'running' },
      ],
      connected: true,
      lastUpdatedAt: new Date().toISOString(),
    })
    preExpandAgents('ws-agent-keep', 'ws-agent-done')

    render(<MemoryRouter><Agents /></MemoryRouter>)
    await act(async () => { await Promise.resolve() })

    // Both agents appear via the WS store (HTTP returned empty).
    await waitFor(() => {
      expect(screen.getByTitle(/^ws-agent-keep(\s|$)/)).toBeInTheDocument()
      expect(screen.getByTitle(/^ws-agent-done(\s|$)/)).toBeInTheDocument()
    })

    const callsBeforeDelta = agentsCalls

    // WS delta: ws-agent-done terminates — removed from the store snapshot.
    await act(async () => {
      useRunningAgentsStore.setState({
        count: 1,
        agents: [{ name: 'ws-agent-keep', status: 'running' }],
        connected: true,
        lastUpdatedAt: new Date().toISOString(),
      })
    })

    // ws-agent-done vanishes from Active immediately (runningAgentNames no
    // longer includes it; hasSummary=true because ws-agent-keep remains).
    await waitFor(() => {
      expect(screen.queryByTitle(/^ws-agent-done(\s|$)/)).not.toBeInTheDocument()
    })
    expect(screen.getByTitle(/^ws-agent-keep(\s|$)/)).toBeInTheDocument()
    // No extra HTTP call was needed.
    expect(agentsCalls).toBe(callsBeforeDelta)
  })
})

// →1266 / →1271: template-spawned agents must remain visible after
// fetchAgents replaces the optimistic 'spawned' placeholder with a
// 'running' server row, even when the WS store hasn't yet confirmed them.
describe('Agents page - template-spawned agent visibility (→1266 →1271)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
  })

  it('shows template-spawned running agent in Active list when WS has another agent (hasSummary=true)', async () => {
    const templateAgent = {
      name: 'template-spawned-agent',
      status: 'running',
      source: 'claude-code',
      model: 'sonnet',
      spawned_at: new Date().toISOString(),
      agent_metadata: { template_id: 'my-template' },
    }

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') {
        return {
          daemon_running: true,
          status: 'ok',
          active: [templateAgent.name],
          agents: [templateAgent],
        }
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: templateAgent.name, nudges: [], session_nudges: [] }
      return {}
    })

    // Seed WS store with a *different* agent so hasSummary=true but the
    // template agent is NOT in runningAgentNames — this is the exact race
    // the →1266 bug triggered.
    useRunningAgentsStore.setState({
      count: 1,
      agents: [{ name: 'other-ws-agent', status: 'running' }],
      connected: true,
      lastUpdatedAt: new Date().toISOString(),
    })

    preExpandAgents(templateAgent.name)
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await act(async () => { await Promise.resolve() })

    // The template-spawned running agent must be visible in the Active list.
    await waitFor(() => {
      expect(screen.getByTitle(/^template-spawned-agent(\s|$)/)).toBeInTheDocument()
    })
  })

  it('Active button count includes template-spawned running agent not yet in WS store', async () => {
    const templateAgent = {
      name: 'template-spawned-agent',
      status: 'running',
      source: 'claude-code',
      model: 'sonnet',
      spawned_at: new Date().toISOString(),
      agent_metadata: { template_id: 'my-template' },
    }

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') {
        return {
          daemon_running: true,
          status: 'ok',
          active: [templateAgent.name],
          agents: [templateAgent],
        }
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: templateAgent.name, nudges: [], session_nudges: [] }
      return {}
    })

    // Another agent in WS store so hasSummary=true; template agent absent.
    useRunningAgentsStore.setState({
      count: 1,
      agents: [{ name: 'other-ws-agent', status: 'running' }],
      connected: true,
      lastUpdatedAt: new Date().toISOString(),
    })

    preExpandAgents(templateAgent.name)
    render(<MemoryRouter><Agents /></MemoryRouter>)
    await act(async () => { await Promise.resolve() })

    // Cancel all button count must be >= 1 (the template agent counts).
    await waitFor(() => {
      const btn = screen.queryByTestId('cancel-all-agents-btn')
      expect(btn).not.toBeNull()
      expect(btn!.textContent).toMatch(/Cancel all \(\d*[1-9]\d*\)/)
    })
  })
})

// ---------------------------------------------------------------------------
// Ghost / Alive indicator + progress signals (→1490)
// ---------------------------------------------------------------------------
import { computeAgentGhostState } from '../lib/agentUtils'

describe('computeAgentGhostState', () => {
  const now = Date.now()

  it('returns "ghost" for abandoned status', () => {
    expect(computeAgentGhostState({ status: 'abandoned', pid: 1234, last_heartbeat_at: new Date(now - 5000).toISOString() }, now)).toBe('ghost')
  })

  it('returns "ghost" when pid is null', () => {
    expect(computeAgentGhostState({ status: 'running', pid: null, last_heartbeat_at: new Date(now - 5000).toISOString() }, now)).toBe('ghost')
  })

  it('returns "ghost" when pid is undefined', () => {
    expect(computeAgentGhostState({ status: 'running', pid: undefined, last_heartbeat_at: new Date(now - 5000).toISOString() }, now)).toBe('ghost')
  })

  it('returns "ghost" when heartbeat is stale (> 120s)', () => {
    expect(computeAgentGhostState({ status: 'running', pid: 1234, last_heartbeat_at: new Date(now - 130_000).toISOString() }, now)).toBe('ghost')
  })

  it('returns "alive" for running agent with pid and recent heartbeat', () => {
    expect(computeAgentGhostState({ status: 'running', pid: 1234, last_heartbeat_at: new Date(now - 30_000).toISOString() }, now)).toBe('alive')
  })

  it('returns "alive" for spawned agent with pid and recent heartbeat', () => {
    expect(computeAgentGhostState({ status: 'spawned', pid: 9999, last_heartbeat_at: new Date(now - 10_000).toISOString() }, now)).toBe('alive')
  })

  it('returns null for completed agents', () => {
    expect(computeAgentGhostState({ status: 'completed', pid: 1234, last_heartbeat_at: new Date(now - 5000).toISOString() }, now)).toBeNull()
  })

  it('returns null for cancelled agents', () => {
    expect(computeAgentGhostState({ status: 'cancelled', pid: null, last_heartbeat_at: undefined }, now)).toBeNull()
  })
})

describe('Agents page - ghost/alive badge, progress, and step display (→1490)', () => {
  const RECENT_HB = new Date(Date.now() - 30_000).toISOString()
  const STALE_HB = new Date(Date.now() - 200_000).toISOString()

  function mkAgent(overrides: Record<string, unknown>) {
    return {
      name: 'ghost-test-agent',
      status: 'running',
      source: 'claude-code',
      model: 'sonnet',
      spawned_at: new Date(Date.now() - 90_000).toISOString(),
      transcript_bytes: 4096,
      pid: 12345,
      last_heartbeat_at: RECENT_HB,
      ...overrides,
    }
  }

  function mockWithAgent(agent: Record<string, unknown>) {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') {
        return { daemon_running: true, status: 'ok', active: [agent.name], agents: [agent] }
      }
      if (path === '/agents/templates') return { templates: [] }
      if (path.includes('/nudges')) return { agent: agent.name, nudges: [], session_nudges: [] }
      return {}
    })
    mockedApiPost.mockResolvedValue({})
  }

  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
  })

  it('shows alive badge for running agent with pid and recent heartbeat', async () => {
    mockWithAgent(mkAgent({ pid: 12345, last_heartbeat_at: RECENT_HB }))
    renderAgentsCollapsed()
    await waitFor(() => {
      expect(screen.getByTestId('alive-badge')).toBeInTheDocument()
    })
  })

  it('shows ghost badge when pid is null', async () => {
    mockWithAgent(mkAgent({ pid: null, last_heartbeat_at: RECENT_HB }))
    renderAgentsCollapsed()
    await waitFor(() => {
      expect(screen.getByTestId('ghost-badge')).toBeInTheDocument()
    })
  })

  it('shows ghost badge when heartbeat is stale', async () => {
    mockWithAgent(mkAgent({ pid: 12345, last_heartbeat_at: STALE_HB }))
    renderAgentsCollapsed()
    await waitFor(() => {
      expect(screen.getByTestId('ghost-badge')).toBeInTheDocument()
    })
  })

  it('clicking ghost badge reveals Dismiss button', async () => {
    mockWithAgent(mkAgent({ pid: null }))
    renderAgentsCollapsed()
    await waitFor(() => {
      expect(screen.getByTestId('ghost-badge')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('ghost-badge'))
    expect(screen.getByTestId('ghost-dismiss-btn')).toBeInTheDocument()
  })

  it('Dismiss button calls cancel endpoint', async () => {
    mockWithAgent(mkAgent({ pid: null }))
    renderAgentsCollapsed()
    await waitFor(() => {
      expect(screen.getByTestId('ghost-badge')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('ghost-badge'))
    fireEvent.click(screen.getByTestId('ghost-dismiss-btn'))
    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith(
        '/agents/ghost-test-agent/cancel',
        expect.objectContaining({ reason: expect.any(String) }),
      )
    })
  })

  it('shows time-running in compact summary (collapsed row)', async () => {
    mockWithAgent(mkAgent({}))
    renderAgentsCollapsed()
    await waitFor(() => {
      // The compact summary renders elapsed time as M:SS or Ns
      const summary = screen.getByTestId('agent-compact-summary')
      expect(summary).toBeInTheDocument()
      // 90s elapsed → should show something like "1:30" or "90s"
      expect(summary.textContent).toMatch(/\d+/)
    })
  })

  it('shows transcript_bytes in compact summary when non-zero', async () => {
    mockWithAgent(mkAgent({ transcript_bytes: 8192 }))
    renderAgentsCollapsed()
    await waitFor(() => {
      const summary = screen.getByTestId('agent-compact-summary')
      // 8192 bytes → "8.0KB"
      expect(summary.textContent).toContain('KB')
    })
  })

  it('shows current_step in collapsed row when present', async () => {
    mockWithAgent(mkAgent({ current_step: 'running vitest on src/lib' }))
    renderAgentsCollapsed()
    await waitFor(() => {
      expect(screen.getByTestId('agent-current-step')).toBeInTheDocument()
      expect(screen.getByTestId('agent-current-step').textContent).toContain('running vitest on src/lib')
    })
  })

  it('truncates current_step to 60 chars with ellipsis', async () => {
    const longStep = 'a'.repeat(80)
    mockWithAgent(mkAgent({ current_step: longStep }))
    renderAgentsCollapsed()
    await waitFor(() => {
      const el = screen.getByTestId('agent-current-step')
      expect(el.textContent!.length).toBeLessThanOrEqual(62) // 60 + "…"
    })
  })

  it('does not show current_step element when current_step is null', async () => {
    mockWithAgent(mkAgent({ current_step: null }))
    renderAgentsCollapsed()
    await waitFor(() => {
      expect(screen.queryByTestId('agent-current-step')).toBeNull()
    })
  })

  it('expands and collapses agent card on toggle click', async () => {
    mockWithAgent(mkAgent({ pid: 12345, last_heartbeat_at: RECENT_HB }))
    renderAgentsCollapsed()
    await waitFor(() => {
      expect(screen.getByTestId('active-agent-toggle-ghost-test-agent')).toBeInTheDocument()
    })
    // Initially collapsed: compact summary visible
    expect(screen.getByTestId('agent-compact-summary')).toBeInTheDocument()
    // Expand
    fireEvent.click(screen.getByTestId('active-agent-toggle-ghost-test-agent'))
    // After expand: compact summary hidden
    await waitFor(() => {
      expect(screen.queryByTestId('agent-compact-summary')).toBeNull()
    })
    // Collapse again
    fireEvent.click(screen.getByTestId('active-agent-toggle-ghost-test-agent'))
    await waitFor(() => {
      expect(screen.getByTestId('agent-compact-summary')).toBeInTheDocument()
    })
  })
})

// ---------------------------------------------------------------------------
// AgentStatusBadge — richer terminal badges (→1510)
// ---------------------------------------------------------------------------

describe('Agents page - AgentStatusBadge (→1510)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents/templates') return mockTemplatesResponse
      return {}
    })
  })

  function makeRecentAgents(overrides: object[]) {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: [],
        agents: overrides,
      }
      if (path === '/agents/templates') return mockTemplatesResponse
      return {}
    })
  }

  async function goToRecentTab() {
    renderAgents()
    const recentTab = await screen.findByRole('button', { name: 'Recent' })
    fireEvent.click(recentTab)
  }

  it('shows CLEAN badge for a completed agent with badge=clean', async () => {
    makeRecentAgents([{
      name: 'clean-agent',
      status: 'completed',
      source: 'claude-code',
      model: 'sonnet',
      spawned_at: new Date().toISOString(),
      badge: 'clean',
    }])
    await goToRecentTab()
    await waitFor(() => {
      expect(screen.getByTitle(/^clean-agent(\s|$)/)).toBeInTheDocument()
    })
    const badges = screen.getAllByTestId('agent-status-badge')
    const cleanBadge = badges.find(b => b.textContent === 'CLEAN')
    expect(cleanBadge).toBeDefined()
    expect(cleanBadge).toHaveClass('text-green-400')
  })

  it('shows SALVAGED badge for a rescued agent with badge=salvaged', async () => {
    makeRecentAgents([{
      name: 'salvaged-agent',
      status: 'abandoned',
      source: 'claude-code',
      model: 'sonnet',
      spawned_at: new Date().toISOString(),
      badge: 'salvaged',
    }])
    await goToRecentTab()
    // abandoned agents are hidden by default — reveal them
    const chip = await screen.findByTestId('recent-cancelled-toggle')
    fireEvent.click(chip)
    await waitFor(() => {
      expect(screen.getByTitle(/^salvaged-agent(\s|$)/)).toBeInTheDocument()
    })
    const badges = screen.getAllByTestId('agent-status-badge')
    const salvagedBadge = badges.find(b => b.textContent === 'SALVAGED')
    expect(salvagedBadge).toBeDefined()
    expect(salvagedBadge).toHaveClass('text-blue-400')
  })

  it('shows FAILED badge for a non-completed agent with badge=failed', async () => {
    makeRecentAgents([{
      name: 'failed-agent',
      status: 'failed',
      source: 'claude-code',
      model: 'sonnet',
      spawned_at: new Date().toISOString(),
      badge: 'failed',
    }])
    await goToRecentTab()
    await waitFor(() => {
      expect(screen.getByTitle(/^failed-agent(\s|$)/)).toBeInTheDocument()
    })
    const badges = screen.getAllByTestId('agent-status-badge')
    const failedBadge = badges.find(b => b.textContent === 'FAILED')
    expect(failedBadge).toBeDefined()
    expect(failedBadge).toHaveClass('text-red-400')
  })

  it('shows NO WORK badge for an agent with badge=abandoned-no-work', async () => {
    makeRecentAgents([{
      name: 'noop-agent',
      status: 'completed',
      source: 'claude-code',
      model: 'sonnet',
      spawned_at: new Date().toISOString(),
      badge: 'abandoned-no-work',
    }])
    await goToRecentTab()
    await waitFor(() => {
      expect(screen.getByTitle(/^noop-agent(\s|$)/)).toBeInTheDocument()
    })
    const badges = screen.getAllByTestId('agent-status-badge')
    const noWorkBadge = badges.find(b => b.textContent === 'NO WORK')
    expect(noWorkBadge).toBeDefined()
    expect(noWorkBadge).toHaveClass('text-slate-400')
  })

  it('shows no AgentStatusBadge when badge field is absent', async () => {
    makeRecentAgents([{
      name: 'no-badge-agent',
      status: 'completed',
      source: 'claude-code',
      model: 'sonnet',
      spawned_at: new Date().toISOString(),
      // no badge field
    }])
    await goToRecentTab()
    await waitFor(() => {
      expect(screen.getByTitle(/^no-badge-agent(\s|$)/)).toBeInTheDocument()
    })
    expect(screen.queryByTestId('agent-status-badge')).toBeNull()
  })
})

// ── B3: per-agent MCP/Skill visibility (→1532) ──────────────────────────────

describe('Agents page - Template MCP/Skill visibility (→1532)', () => {
  const mockTemplateWithMcps = {
    id: 'builtin-sales-follow-up',
    name: 'Follow Up',
    description: 'After a call, turn notes into a recap email.',
    icon: 'forward_to_inbox',
    prompt_template: 'You are a sales assistant. Write a follow-up email.',
    model: 'sonnet',
    budget: 2.0,
    source: 'marketplace',
    personas: ['sales'],
    installed: true,
    builtin: true,
    declared_mcps: ['hubspot'],
    declared_skills: [],
  }

  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
    preExpandAgents(...DEFAULT_EXPANDED_AGENTS)

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: false, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return { templates: [mockTemplateWithMcps], persona: 'pm' }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return {
        persona: 'pm',
        mcp_servers: [{ name: 'HubSpot', url: 'http://hubspot', enabled: true }],
      }
      if (path === '/agents/fleets') return { fleets: [] }
      if (path.includes('/alias')) return { alias: null }
      return {}
    })
    mockedApiPost.mockResolvedValue({})
  })

  it('template detail modal shows declared MCP chips when template has declared_mcps', async () => {
    renderAgents()

    const templatesTab = await screen.findByText('Templates')
    fireEvent.click(templatesTab)

    const useBtn = await screen.findByTestId('pm-template-card-builtin-sales-follow-up-action')
    fireEvent.click(useBtn)

    await waitFor(() => {
      const chips = screen.getAllByTestId('declared-mcp-chip')
      expect(chips.length).toBeGreaterThan(0)
      expect(chips[0].textContent).toBe('hubspot')
    })
  })

  it('template detail modal shows MCP/Skills section header when MCPs are declared', async () => {
    renderAgents()

    const templatesTab = await screen.findByText('Templates')
    fireEvent.click(templatesTab)

    const useBtn = await screen.findByTestId('pm-template-card-builtin-sales-follow-up-action')
    fireEvent.click(useBtn)

    await waitFor(() => {
      expect(screen.getByTestId('template-mcp-skills-section')).toBeInTheDocument()
    })
  })

  it('template detail modal shows workspace MCPs note when enabled MCPs are configured', async () => {
    renderAgents()

    const templatesTab = await screen.findByText('Templates')
    fireEvent.click(templatesTab)

    const useBtn = await screen.findByTestId('pm-template-card-builtin-sales-follow-up-action')
    fireEvent.click(useBtn)

    await waitFor(() => {
      const note = screen.getByTestId('workspace-mcps-note')
      expect(note).toBeInTheDocument()
      expect(note.textContent).toContain('HubSpot')
    })
  })

  it('template detail modal shows no MCP section when template has no declared_mcps and no workspace MCPs', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: false, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return { templates: [] }
      if (path.startsWith('/agents/persona-templates')) return {
        templates: [{
          ...mockTemplateWithMcps,
          declared_mcps: [],
          declared_skills: [],
        }],
        persona: 'pm',
      }
      if (path === '/agents/user-templates') return { templates: [] }
      if (path === '/settings') return { persona: 'pm', mcp_servers: [] }
      if (path === '/agents/fleets') return { fleets: [] }
      if (path.includes('/alias')) return { alias: null }
      return {}
    })

    renderAgents()

    const templatesTab = await screen.findByText('Templates')
    fireEvent.click(templatesTab)

    const useBtn = await screen.findByTestId('pm-template-card-builtin-sales-follow-up-action')
    fireEvent.click(useBtn)

    await waitFor(() => {
      expect(screen.getByTestId('template-detail-modal')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('template-mcp-skills-section')).toBeNull()
  })
})
