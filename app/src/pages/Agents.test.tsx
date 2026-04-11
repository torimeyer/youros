import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Agents from './Agents'
import { useAppStore } from '../stores/app'

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

import { api, ApiTimeoutError } from '../lib/api'

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
      source: 'daemon',
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

function renderAgents() {
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
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })

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
      expect(screen.getByText('test-agent')).toBeInTheDocument()
    })

    // The chat thread input should be visible for active agents. The
    // placeholder now mirrors the main ChatPanel: "Message <agent>...".
    const input = screen.getByPlaceholderText('Message test-agent...')
    expect(input).toBeInTheDocument()
  })

  it('renders Send button for active agents', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('test-agent')).toBeInTheDocument()
    })

    const sendButton = screen.getByTestId('agent-chat-send-test-agent')
    expect(sendButton).toBeInTheDocument()
  })

  it('sends nudge when clicking Send button', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('test-agent')).toBeInTheDocument()
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
      expect(screen.getByText('test-agent')).toBeInTheDocument()
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
      expect(screen.getByText('test-agent')).toBeInTheDocument()
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
      expect(screen.getByText('test-agent')).toBeInTheDocument()
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
      expect(screen.getByText('test-agent')).toBeInTheDocument()
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
    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('test-agent')).toBeInTheDocument()
    })

    const expandButton = screen.getByTitle('Expand session')
    expect(expandButton).toBeInTheDocument()
  })

  it('expands agent details when clicking Expand', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('test-agent')).toBeInTheDocument()
    })

    const expandButton = screen.getByTitle('Expand session')
    fireEvent.click(expandButton)

    await waitFor(() => {
      expect(screen.getByText('Messages sent')).toBeInTheDocument()
    })
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
      expect(
        screen.getByText('No active agents.')
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
      expect(screen.getByText('test-agent')).toBeInTheDocument()
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
      expect(screen.getByText('test-agent')).toBeInTheDocument()
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
      expect(screen.getByText('test-agent')).toBeInTheDocument()
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
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })

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
      expect(screen.getByText('test-agent')).toBeInTheDocument()
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
      expect(screen.getByText('test-agent')).toBeInTheDocument()
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
      expect(screen.getByText('test-agent')).toBeInTheDocument()
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
              source: 'daemon',
              model: 'sonnet',
              spawned_at: new Date(Date.now() - 1000).toISOString(),
            },
            {
              name: 'agent-b',
              status: 'running',
              source: 'daemon',
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
      expect(screen.getByText('agent-a')).toBeInTheDocument()
      expect(screen.getByText('agent-b')).toBeInTheDocument()
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

describe('Agents page - Status bar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Clear the first-paint agents cache between tests so localStorage
    // state never leaks from one test run to the next. Needle 299.
    window.localStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })

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

  it('displays budget cap in status bar', async () => {
    renderAgents()

    await waitFor(() => {
      const statusBar = screen.getByTestId('agent-status-bar')
      expect(statusBar.textContent).toContain('$2.00 cap')
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

  it('does not render status bar when no spawned_at', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['no-time-agent'],
        agents: [{ name: 'no-time-agent', status: 'running', source: 'daemon', model: 'sonnet' }],
      }
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path.includes('/nudges')) return { agent: 'no-time-agent', nudges: [], session_nudges: [] }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('no-time-agent')).toBeInTheDocument()
    })

    expect(screen.queryByTestId('agent-status-bar')).not.toBeInTheDocument()
  })
})

describe('Agents page - tabs', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Clear the first-paint agents cache between tests so localStorage
    // state never leaks from one test run to the next. Needle 299.
    window.localStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
  })

  it('shows Active, Recent, and Metrics tabs', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return mockTemplatesResponse
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('Active')).toBeInTheDocument()
      expect(screen.getByText('Recent')).toBeInTheDocument()
      expect(screen.getByText('Metrics')).toBeInTheDocument()
    })
  })

})

describe('Agents page - Recent tab filtering', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Clear the first-paint agents cache between tests so localStorage
    // state never leaks from one test run to the next. Needle 299.
    window.localStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
  })

  it('shows all terminal agents in the Recent tab', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return {
        daemon_running: true,
        status: 'ok',
        active: ['running-agent'],
        agents: [
          { name: 'running-agent', status: 'running', source: 'daemon', model: 'sonnet', spawned_at: new Date().toISOString() },
          { name: 'completed-agent', status: 'completed', source: 'api', model: 'sonnet', spawned_at: new Date().toISOString() },
          { name: 'stopped-agent', status: 'stopped', source: 'api', model: 'sonnet', spawned_at: new Date().toISOString() },
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
      expect(screen.getByText('completed-agent')).toBeInTheDocument()
    })

    // All terminal statuses now show on the Recent tab
    expect(screen.getByText('stopped-agent')).toBeInTheDocument()
    expect(screen.getByText('abandoned-agent')).toBeInTheDocument()
    // running-agent is on Active tab, not Recent
    expect(screen.queryByText('running-agent')).not.toBeInTheDocument()
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
      expect(screen.getByText('No agents have run yet.')).toBeInTheDocument()
    })
  })
})

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
      id: 'custom-abc123',
      name: 'My Custom',
      description: 'Does things',
      icon: 'smart_toy',
      prompt_template: 'Do [thing] for me.',
      model: 'sonnet',
      budget: 1.0,
      builtin: false,
    },
  ],
}

describe('Agents page - Templates tab', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Clear the first-paint agents cache between tests so localStorage
    // state never leaks from one test run to the next. Needle 299.
    window.localStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return mockTemplatesResponse
      if (path === '/agents/pm-templates') return mockPmTemplatesResponse
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

  it('shows PM Templates heading when Templates tab is active', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      expect(screen.getByText('PM Templates')).toBeInTheDocument()
    })
  })

  it('shows built-in templates when Templates tab is active', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      expect(screen.getByText('Research spike')).toBeInTheDocument()
    })
  })

  it('shows custom templates in "Your templates" section', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      expect(screen.getByText('My Custom')).toBeInTheDocument()
    })
  })

  it('shows Use button for built-in templates', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      const useButtons = screen.getAllByRole('button', { name: 'Use' })
      expect(useButtons.length).toBeGreaterThan(0)
    })
  })

  it('clicking Use on a built-in template switches to Active tab and pre-fills name', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      expect(screen.getByText('Research spike')).toBeInTheDocument()
    })

    const useButtons = screen.getAllByRole('button', { name: 'Use' })
    fireEvent.click(useButtons[0])

    await waitFor(() => {
      // Should switch back to Active tab and show the spawn form
      expect(screen.getByText('Active Sessions')).toBeInTheDocument()
    })
  })

  it('shows filter input on Templates tab', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Find a template...')).toBeInTheDocument()
    })
  })

  it('filters templates by search term', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      expect(screen.getByText('Research spike')).toBeInTheDocument()
    })

    const searchInput = screen.getByPlaceholderText('Find a template...')
    fireEvent.change(searchInput, { target: { value: 'research' } })

    await waitFor(() => {
      expect(screen.getByText('Research spike')).toBeInTheDocument()
    })
  })

  it('shows New template button in Your templates section', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /New template/i })).toBeInTheDocument()
    })
  })

  it('shows prompt_template text on template cards', async () => {
    renderAgents()

    const templatesTab = await screen.findByRole('button', { name: 'Templates' })
    fireEvent.click(templatesTab)

    await waitFor(() => {
      expect(screen.getByText(/Research \[topic\] thoroughly/)).toBeInTheDocument()
    })
  })
})

describe('Agents page - Insights tab', () => {
  const mockRecommendations = [
    {
      type: 'underbudgeted',
      severity: 'warning' as const,
      message: 'The "PRD Draft" template keeps hitting its budget cap. Bump the default budget to $4.50 so it stops running out of room.',
      related_template_id: 'tmpl-prd',
      suggested_value: 4.5,
    },
    {
      type: 'wrong_model',
      severity: 'tip' as const,
      message: 'The "Code Review" template works far better on sonnet (95% success) than on haiku (40% success). Switch the default model to sonnet.',
      related_template_id: 'tmpl-code',
      suggested_value: 'sonnet',
    },
    {
      type: 'high_success',
      severity: 'info' as const,
      message: '"Daily Planner" is one of your most reliable templates: 100% success across 4 runs.',
      related_template_id: 'tmpl-plan',
      suggested_value: null,
    },
  ]

  const mockTemplateStats = [
    {
      template_id: 'tmpl-plan',
      template_name: 'Daily Planner',
      spawn_count: 4,
      completed_count: 4,
      success_rate: 1.0,
      median_duration_sec: 90,
      median_cost: 0.5,
      best_model: 'sonnet',
    },
    {
      template_id: 'tmpl-prd',
      template_name: 'PRD Draft',
      spawn_count: 3,
      completed_count: 2,
      success_rate: 0.667,
      median_duration_sec: 240,
      median_cost: 2.8,
      best_model: 'sonnet',
    },
  ]

  beforeEach(() => {
    vi.clearAllMocks()
    // Clear the first-paint agents cache between tests so localStorage
    // state never leaks from one test run to the next. Needle 299.
    window.localStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return { daemon_running: true, status: 'ok', active: [], agents: [] }
      if (path === '/agents/templates') return { templates: [] }
      if (path === '/agents/pm-templates') return { templates: [] }
      if (path === '/agent-patterns/recommendations') return { recommendations: mockRecommendations }
      if (path === '/agent-patterns/template-stats') return { stats: mockTemplateStats }
      return {}
    })
    mockedApiPost.mockResolvedValue({ result: 'ok' })
  })

  it('shows Insights tab in navigation without power user mode', async () => {
    renderAgents()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Insights' })).toBeInTheDocument()
    })
  })

  it('renders recommendations from the API when Insights tab is active', async () => {
    renderAgents()

    const insightsTab = await screen.findByRole('button', { name: 'Insights' })
    fireEvent.click(insightsTab)

    await waitFor(() => {
      expect(screen.getByText(/PRD Draft.*keeps hitting its budget cap/)).toBeInTheDocument()
    })
    expect(screen.getByText(/Code Review.*works far better on sonnet/)).toBeInTheDocument()
    expect(screen.getByText(/Daily Planner.*most reliable/)).toBeInTheDocument()
  })

  it('groups recommendations by severity with warnings before tips before info', async () => {
    renderAgents()

    const insightsTab = await screen.findByRole('button', { name: 'Insights' })
    fireEvent.click(insightsTab)

    await waitFor(() => {
      expect(screen.getByTestId('insight-warnings')).toBeInTheDocument()
    })

    const warnings = screen.getByTestId('insight-warnings')
    const tips = screen.getByTestId('insight-tips')
    const infos = screen.getByTestId('insight-infos')

    // All three groups should be present with the matching entries
    expect(warnings).toHaveTextContent('PRD Draft')
    expect(tips).toHaveTextContent('Code Review')
    expect(infos).toHaveTextContent('Daily Planner')

    // Warnings should appear before tips, tips before infos in DOM order
    const html = document.body.innerHTML
    const warningIdx = html.indexOf('insight-warnings')
    const tipsIdx = html.indexOf('insight-tips')
    const infosIdx = html.indexOf('insight-infos')
    expect(warningIdx).toBeLessThan(tipsIdx)
    expect(tipsIdx).toBeLessThan(infosIdx)
  })

  it('renders high-success template cards at the top', async () => {
    renderAgents()

    const insightsTab = await screen.findByRole('button', { name: 'Insights' })
    fireEvent.click(insightsTab)

    await waitFor(() => {
      const cards = screen.getAllByTestId('insight-high-success-card')
      expect(cards.length).toBeGreaterThan(0)
    })
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
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
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
      if (path === '/agents/pm-templates') return { templates: [] }
      return {}
    })

    renderAgents()

    expect(await screen.findByTestId('active-agents-loading')).toBeInTheDocument()
    expect(
      screen.queryByText('No active agents.')
    ).not.toBeInTheDocument()
    expect(
      screen.queryByText(
        'No active agents.'
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
      if (path === '/agents/pm-templates') return { templates: [] }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(
        screen.getByText('No active agents.')
      ).toBeInTheDocument()
    })
    expect(screen.queryByTestId('active-agents-loading')).not.toBeInTheDocument()
  })

  it('test_agents_page_shows_agents_after_fetch_resolves_with_data', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return { templates: [] }
      if (path === '/agents/pm-templates') return { templates: [] }
      if (path.includes('/nudges')) {
        return { agent: 'test-agent', nudges: [], session_nudges: [] }
      }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('test-agent')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('active-agents-loading')).not.toBeInTheDocument()
    expect(
      screen.queryByText('No active agents.')
    ).not.toBeInTheDocument()
  })

  it('test_agents_page_polling_does_not_flash_loading', async () => {
    vi.useFakeTimers()
    try {
      mockedApiGet.mockImplementation(async (path: string) => {
        if (path === '/agents') return mockAgentsResponse
        if (path === '/agents/templates') return { templates: [] }
        if (path === '/agents/pm-templates') return { templates: [] }
        if (path.includes('/nudges')) {
          return { agent: 'test-agent', nudges: [], session_nudges: [] }
        }
        return {}
      })

      renderAgents()

      // First fetch settles, real-timer waitFor is unavailable with fake
      // timers, so we drive ticks manually until the agent appears.
      await vi.waitFor(() => {
        expect(screen.getByText('test-agent')).toBeInTheDocument()
      })
      expect(screen.queryByTestId('active-agents-loading')).not.toBeInTheDocument()

      // Advance the polling interval (5s) and flush microtasks. Loading must
      // NOT reappear during the refresh. The agent must still be visible.
      await vi.advanceTimersByTimeAsync(5000)
      expect(screen.queryByTestId('active-agents-loading')).not.toBeInTheDocument()
      expect(screen.getByText('test-agent')).toBeInTheDocument()

      await vi.advanceTimersByTimeAsync(5000)
      expect(screen.queryByTestId('active-agents-loading')).not.toBeInTheDocument()
      expect(screen.getByText('test-agent')).toBeInTheDocument()
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
      if (path === '/agents/pm-templates') return { templates: [] }
      if (path.includes('/nudges')) {
        return { agent: 'live-agent', nudges: [], session_nudges: [] }
      }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('live-agent')).toBeInTheDocument()
    })
    // The terminated_stale agent must not be in the Active Sessions list.
    expect(screen.queryByText('dead-orphan')).not.toBeInTheDocument()
  })

  it('test_cancel_button_calls_cancel_endpoint', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/agents') return mockAgentsResponse
      if (path === '/agents/templates') return { templates: [] }
      if (path === '/agents/pm-templates') return { templates: [] }
      if (path.includes('/nudges')) {
        return { agent: 'test-agent', nudges: [], session_nudges: [] }
      }
      return {}
    })
    mockedApiPost.mockResolvedValue({ ok: true, status: 'cancelled' })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('test-agent')).toBeInTheDocument()
    })

    const cancelButton = screen.getByRole('button', { name: /Cancel/i })
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
      if (path === '/agents/pm-templates') return { templates: [] }
      return {}
    })

    renderAgents()

    // Active tab loading card is visible.
    expect(await screen.findByTestId('active-agents-loading')).toBeInTheDocument()

    // None of the empty-state strings on any tab may be visible while the
    // first fetch is still pending. They are all driven by allAgents, which
    // starts as []. Without the agentsLoaded gate they would flash.
    expect(
      screen.queryByText('No active agents.')
    ).not.toBeInTheDocument()
    expect(
      screen.queryByText(
        'No active agents.'
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
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
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
      expect(screen.getByText('test-agent')).toBeInTheDocument()
    })

    // Expand the card so the nudges poll fetches the mocked history.
    fireEvent.click(screen.getByTitle('Expand session'))

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
      expect(screen.getByText('test-agent')).toBeInTheDocument()
    })

    // Expand the card so the nudges poll fetches the mocked reply.
    fireEvent.click(screen.getByTitle('Expand session'))

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
      expect(screen.getByText('test-agent')).toBeInTheDocument()
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
      expect(screen.getByText('test-agent')).toBeInTheDocument()
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
          source: 'daemon',
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
      expect(screen.getByText('fresh-agent')).toBeInTheDocument()
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
          source: 'daemon',
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
      expect(screen.getByText('old-agent')).toBeInTheDocument()
    })

    // Expand the card so the nudges poll fires and the warning has
    // data to work with. In real usage the card would already have
    // been polled if the user had sent messages, but the test can
    // trigger it deterministically this way.
    const expandButton = screen.getByTitle('Expand session')
    fireEvent.click(expandButton)

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
      expect(screen.getByText('test-agent')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTitle('Expand session'))

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
      expect(screen.getByText('test-agent')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTitle('Expand session'))

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
          source: 'daemon',
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
      expect(screen.getByText('old-agent')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTitle('Expand session'))

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
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
  })

  it('renders parsed capabilities for a clean template', async () => {
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
      if (path === '/agents/pm-templates') return { templates: [] }
      return {}
    })

    renderAgents()

    // Switch to the Templates tab so the cards render.
    await waitFor(() => {
      expect(screen.getByText('Templates')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Templates'))

    const panel = await screen.findByTestId('template-capabilities-demo')
    expect(panel).toBeInTheDocument()
    expect(panel.textContent).toContain('Writes to:')
    expect(panel.textContent).toContain('src/, tests/')
    expect(panel.textContent).toContain('Cannot touch:')
    expect(panel.textContent).toContain('.env')
    expect(panel.textContent).toContain('Budget:')
    expect(panel.textContent).toContain('$5')
    expect(panel.textContent).toContain('Time limit:')
    expect(panel.textContent).toContain('30 minutes')
    expect(panel.textContent).toContain('Sandbox:')
    expect(panel.textContent).toContain('docker container')
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
      if (path === '/agents/pm-templates') return { templates: [] }
      return {}
    })

    renderAgents()

    await waitFor(() => {
      expect(screen.getByText('Templates')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Templates'))

    const errorPanel = await screen.findByTestId('template-capabilities-error-broken')
    expect(errorPanel).toBeInTheDocument()
    expect(errorPanel.textContent).toContain('Could not read capabilities')

    const spawnButton = screen.getByTestId('template-spawn-broken') as HTMLButtonElement
    expect(spawnButton).toBeDisabled()
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
    source: 'daemon',
    model: 'sonnet',
    budget: '2.00',
    spawned_at: new Date(Date.now() - i * 60000).toISOString(),
    transcript_bytes: 1024,
    transcript_lines: 10,
  }))

  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
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
      if (path === '/agents/pm-templates') return { templates: [] }
      return {}
    })

    const t0 = performance.now()
    renderAgents()
    await waitFor(() => {
      expect(screen.getByText('agent-1')).toBeInTheDocument()
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
      if (path === '/agents/pm-templates')
        return new Promise((resolve) =>
          setTimeout(() => resolve({ templates: [] }), 2000),
        )
      return Promise.resolve({})
    })

    const t0 = performance.now()
    renderAgents()
    await waitFor(() => {
      expect(screen.getByText('agent-1')).toBeInTheDocument()
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
          source: 'daemon',
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

    expect(screen.getByText('cached-agent')).toBeInTheDocument()
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
          source: 'daemon',
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
      if (path === '/agents/pm-templates') return { templates: [] }
      return {}
    })

    renderAgents()
    // Stale cache row paints first.
    expect(screen.getByText('stale-agent')).toBeInTheDocument()
    // Fresh data replaces it.
    await waitFor(() => {
      expect(screen.getByText('agent-1')).toBeInTheDocument()
    })
    const persisted = JSON.parse(
      window.localStorage.getItem('myos.agentsCache.v1') || '[]',
    )
    expect(Array.isArray(persisted)).toBe(true)
    expect(persisted.length).toBe(manyAgents.length)
    expect(persisted[0].name).toBe('agent-1')
  })
})
