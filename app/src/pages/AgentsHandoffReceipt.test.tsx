import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { HandoffRuntimeModal } from '../components/HandoffRuntimeModal'
import Agents from './Agents'
import Tasks from './Tasks'
import { _resetSidebarBus } from '../lib/sidebarBus'
import { useRunningAgentsStore } from '../stores/runningAgents'
import { useAppStore } from '../stores/app'

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

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)
const mockedApiPost = vi.mocked(api.post)

const MOCK_HEARTBEAT = new Date(Date.now() - 15_000).toISOString()

describe('HandoffRuntimeModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('auto-selects Gemini 3.1 Pro when currentModel is Claude and posts to /agents/{name}/handoff-runtime', async () => {
    const onClose = vi.fn()
    const onHandedOff = vi.fn()
    mockedApiPost.mockResolvedValueOnce({ ok: true, spawned_agent: 'builder-gemini' })

    render(
      <HandoffRuntimeModal
        isOpen={true}
        onClose={onClose}
        agentName="builder-claude"
        taskId="3096"
        currentModel="claude-sonnet-5-5"
        onHandedOff={onHandedOff}
      />
    )

    expect(screen.getByTestId('handoff-runtime-modal')).toBeInTheDocument()
    const runtimeSelect = screen.getByTestId('handoff-runtime-select') as HTMLSelectElement
    const modelSelect = screen.getByTestId('handoff-model-select') as HTMLSelectElement
    expect(runtimeSelect.value).toBe('gemini')
    expect(modelSelect.value).toBe('gemini-3.1-pro')

    fireEvent.change(screen.getByTestId('handoff-transition-note'), {
      target: { value: 'Hit Anthropic 429 rate limit during test suite' },
    })
    fireEvent.click(screen.getByTestId('confirm-handoff-btn'))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/agents/builder-claude/handoff-runtime', {
        target_runtime: 'gemini',
        target_model: 'gemini-3.1-pro',
        task_id: '3096',
        transition_note: 'Hit Anthropic 429 rate limit during test suite',
      })
    })
    expect(onHandedOff).toHaveBeenCalledWith({ ok: true, spawned_agent: 'builder-gemini' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('auto-selects Claude Sonnet 5.5 when currentModel is Gemini and posts to /tasks/{id}/handoff-runtime when agentName is omitted', async () => {
    const onClose = vi.fn()
    const onHandedOff = vi.fn()
    mockedApiPost.mockResolvedValueOnce({ ok: true, task_id: '→3096' })

    render(
      <HandoffRuntimeModal
        isOpen={true}
        onClose={onClose}
        taskId="→3096"
        currentModel="gemini-3.1-pro"
        onHandedOff={onHandedOff}
      />
    )

    const runtimeSelect = screen.getByTestId('handoff-runtime-select') as HTMLSelectElement
    const modelSelect = screen.getByTestId('handoff-model-select') as HTMLSelectElement
    expect(runtimeSelect.value).toBe('claude')
    expect(modelSelect.value).toBe('claude-sonnet-5-5')

    fireEvent.change(modelSelect, { target: { value: 'claude-opus-5-5' } })
    fireEvent.change(screen.getByTestId('handoff-transition-note'), {
      target: { value: 'Escalate deep refactor to Opus 5.5' },
    })
    fireEvent.click(screen.getByTestId('confirm-handoff-btn'))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/tasks/%E2%86%923096/handoff-runtime', {
        target_runtime: 'claude',
        target_model: 'claude-opus-5-5',
        transition_note: 'Escalate deep refactor to Opus 5.5',
      })
    })
    expect(onHandedOff).toHaveBeenCalledWith({ ok: true, task_id: '→3096' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('displays inline error and stays open when handoff request fails', async () => {
    const onClose = vi.fn()
    mockedApiPost.mockRejectedValueOnce(new Error('Gemini API key not configured'))

    render(
      <HandoffRuntimeModal
        isOpen={true}
        onClose={onClose}
        agentName="stalled-agent"
        currentModel="sonnet"
      />
    )

    fireEvent.click(screen.getByTestId('confirm-handoff-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('handoff-error')).toHaveTextContent('Gemini API key not configured')
    })
    expect(onClose).not.toHaveBeenCalled()
  })
})

describe('Agents page Hand Off Runtime + ReceiptPanel integration', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
    _resetSidebarBus()
    useRunningAgentsStore.setState({ count: 0, agents: [], connected: false, lastUpdatedAt: null })
    useAppStore.setState({ chatOpen: true, osName: 'yourOS', darkMode: true })
  })

  it('renders Hand Off Runtime button and ReceiptPanel on expanded Active agent and executes handoff + receipt actions', async () => {
    window.sessionStorage.setItem(
      'myos.agents.activeExpanded.v1',
      JSON.stringify({ 'worker-active': true })
    )

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/agents' || path.startsWith('/agents?')) {
        return Promise.resolve({
          daemon_running: true,
          status: 'ok',
          active: ['worker-active'],
          agents: [
            {
              name: 'worker-active',
              status: 'running',
              source: 'claude-code',
              model: 'claude-sonnet-5-5',
              task: 'Implement runtime handoff',
              task_id: '3096',
              spawned_at: new Date(Date.now() - 10_000).toISOString(),
              last_heartbeat_at: MOCK_HEARTBEAT,
            },
          ],
        })
      }
      if (path.startsWith('/tasks/3096/receipt')) {
        return Promise.resolve({
          verified: false,
          commits: [],
          diff_stat: '',
          changed_files: [],
          trace_linked: false,
          test_runs: [{ command: 'vitest run', exit_code: 1, timestamp: '2026-11-13T20:00:00Z' }],
          missing_reasons: ['Latest recorded test command exited non-zero'],
          agent_name: 'worker-active',
        })
      }
      return Promise.resolve({})
    })
    mockedApiPost.mockResolvedValue({ ok: true })

    render(
      <MemoryRouter>
        <Agents />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByTestId('handoff-agent-worker-active')).toBeInTheDocument()
    })

    // ReceiptPanel is mounted inside the expanded agent detail view
    await waitFor(() => {
      expect(screen.getByTestId('receipt-status-unverified')).toHaveTextContent('Missing Proof')
    })

    // Journey 5: Reset to Save Point directly from /agents
    fireEvent.click(screen.getByTestId('receipt-reset-save-point-btn'))
    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/agents/worker-active/reset-save-point', {})
    })

    // Journey 3: Open HandoffRuntimeModal from /agents and confirm handoff
    fireEvent.click(screen.getByTestId('handoff-agent-worker-active'))
    expect(screen.getByTestId('handoff-runtime-modal')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('confirm-handoff-btn'))
    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/agents/worker-active/handoff-runtime', {
        target_runtime: 'gemini',
        target_model: 'gemini-3.1-pro',
        task_id: '3096',
        transition_note: '',
      })
    })
  })

  it('renders Hand Off Runtime button and ReceiptPanel on failed agent in Recent tab', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/agents' || path.startsWith('/agents?')) {
        return Promise.resolve({
          daemon_running: true,
          status: 'ok',
          active: [],
          agents: [
            {
              name: 'worker-failed',
              status: 'failed',
              source: 'claude-code',
              model: 'claude-opus-5-5',
              task: 'Fix flaky receipt parser',
              needle_id: '3099',
              spawned_at: new Date(Date.now() - 120_000).toISOString(),
            },
          ],
        })
      }
      if (path.startsWith('/tasks/3099/receipt')) {
        return Promise.resolve({
          verified: true,
          commits: [
            {
              sha: 'deadbeef12345678',
              short_sha: 'deadbeef',
              subject: 'fix: receipt parser (→3099)',
              timestamp: '2026-11-13T20:10:00Z',
            },
          ],
          diff_stat: '1 file changed',
          changed_files: ['api/services/git_receipts.py'],
          trace_linked: true,
          test_runs: [{ command: 'pytest', exit_code: 0, timestamp: '2026-11-13T20:11:00Z' }],
          missing_reasons: [],
          agent_name: 'worker-failed',
        })
      }
      return Promise.resolve({})
    })
    mockedApiPost.mockResolvedValue({ ok: true })

    render(
      <MemoryRouter initialEntries={['/agents?tab=Recent']}>
        <Agents />
      </MemoryRouter>
    )

    // Switch to Recent tab if not already active via query or tab button
    const recentTabBtn = await screen.findByRole('button', { name: /^Recent/i })
    fireEvent.click(recentTabBtn)

    await waitFor(() => {
      expect(screen.getByTestId('handoff-agent-worker-failed')).toBeInTheDocument()
    })

    // Expand failed agent detail to access ReceiptPanel
    fireEvent.click(screen.getByTestId('recent-agent-expand-worker-failed'))

    await waitFor(() => {
      expect(screen.getByTestId('receipt-status-verified')).toHaveTextContent('Verified')
    })

    fireEvent.click(screen.getByTestId('receipt-merge-reap-btn'))
    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/agents/worker-failed/merge-and-reap', {})
    })
  })
})

describe('Tasks page Hand Off Runtime integration', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    useAppStore.setState({ chatOpen: true, osName: 'yourOS', darkMode: true })
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/tasks' || path.startsWith('/tasks?')) {
        return Promise.resolve({
          tasks: [
            {
              id: '3096',
              title: 'Phase 3B Runtime Handoff UI',
              priority: 'P0',
              status: 'open',
              created_at: '2026-11-13T20:00:00.000Z',
              label_ids: [],
            },
          ],
        })
      }
      if (path === '/labels') return Promise.resolve({ labels: [] })
      return Promise.resolve({})
    })
    mockedApiPost.mockResolvedValue({ ok: true })
  })

  it('shows handoff-task-{id} inside expanded task detail drawer and submits POST /tasks/{id}/handoff-runtime', async () => {
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    )

    const taskRow = await screen.findByTestId('task-row-3096')
    fireEvent.click(taskRow)

    const handoffBtn = await screen.findByTestId('handoff-task-3096')
    expect(handoffBtn).toBeInTheDocument()

    fireEvent.click(handoffBtn)
    expect(screen.getByTestId('handoff-runtime-modal')).toBeInTheDocument()

    fireEvent.change(screen.getByTestId('handoff-transition-note'), {
      target: { value: 'Hand off task to Gemini 3.1 Pro' },
    })
    fireEvent.click(screen.getByTestId('confirm-handoff-btn'))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/tasks/3096/handoff-runtime', {
        target_runtime: 'gemini',
        target_model: 'gemini-3.1-pro',
        transition_note: 'Hand off task to Gemini 3.1 Pro',
      })
    })
  })
})
