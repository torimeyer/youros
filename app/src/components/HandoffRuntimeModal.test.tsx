import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { HandoffRuntimeModal } from './HandoffRuntimeModal'
import { api } from '../lib/api'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

const mockedGet = vi.mocked(api.get)
const mockedPost = vi.mocked(api.post)

describe('HandoffRuntimeModal theme and accent styling (→3106)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedGet.mockResolvedValue({
      available: false,
      pages: [],
      total_pages: 0,
      total_tokens: 0,
      hot_tokens: 0,
    })
  })

  it('uses accent-bg and !text-white on Confirm Handoff so light mode does not darken button text or clash with accent color', () => {
    document.documentElement.setAttribute('data-theme', 'light')
    document.documentElement.style.setProperty('--color-accent', '#ec4899')

    try {
      render(
        <HandoffRuntimeModal
          isOpen={true}
          onClose={vi.fn()}
          agentName="builder-claude"
          currentModel="claude-sonnet-5-5"
        />
      )

      const confirmBtn = screen.getByTestId('confirm-handoff-btn')
      expect(confirmBtn.className).toContain('accent-bg')
      expect(confirmBtn.className).toContain('!text-white')
      expect(confirmBtn.className).not.toContain('bg-blue-600')
    } finally {
      document.documentElement.removeAttribute('data-theme')
      document.documentElement.style.removeProperty('--color-accent')
    }
  })
})

describe('HandoffRuntimeModal ostk tiered context pages integration (→3114)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches GET /ostk/context-pages on open, default-selects hot pages, and displays tier badges and token counts', async () => {
    mockedGet.mockResolvedValueOnce({
      available: true,
      pages: [
        {
          name: 'atlassian-widget-research',
          tier: 'hot',
          tokens: 2524,
          owner: 'claude-code-p73643',
          pinned: false,
        },
        {
          name: 's015-google-setup-inflight-survey',
          tier: 'warm',
          tokens: 1648,
          owner: 'claude-code-p50248',
          pinned: false,
        },
        {
          name: 'archived-schema-notes',
          tier: 'cold',
          tokens: 410,
          owner: null,
          pinned: true,
        },
      ],
      total_pages: 3,
      total_tokens: 4582,
      hot_tokens: 2524,
    })

    render(
      <HandoffRuntimeModal
        isOpen={true}
        onClose={vi.fn()}
        agentName="builder-claude"
        taskId="3114"
        currentModel="claude-sonnet-5-5"
      />
    )

    await waitFor(() => {
      expect(mockedGet).toHaveBeenCalledWith('/ostk/context-pages')
      expect(screen.getByTestId('handoff-context-pages-section')).toBeInTheDocument()
    })

    const hotCheckbox = screen.getByTestId(
      'context-page-checkbox-atlassian-widget-research'
    ) as HTMLInputElement
    const warmCheckbox = screen.getByTestId(
      'context-page-checkbox-s015-google-setup-inflight-survey'
    ) as HTMLInputElement
    const coldCheckbox = screen.getByTestId(
      'context-page-checkbox-archived-schema-notes'
    ) as HTMLInputElement

    expect(hotCheckbox.checked).toBe(true)
    expect(warmCheckbox.checked).toBe(false)
    expect(coldCheckbox.checked).toBe(false)

    expect(screen.getByTestId('context-page-tier-atlassian-widget-research')).toHaveTextContent('hot')
    expect(screen.getByTestId('context-page-tier-s015-google-setup-inflight-survey')).toHaveTextContent('warm')
    expect(screen.getByTestId('context-page-tier-archived-schema-notes')).toHaveTextContent('cold')
    expect(screen.getByText(/2,524 tok|2524 tok/)).toBeInTheDocument()
    expect(screen.getByText(/1,648 tok|1648 tok/)).toBeInTheDocument()
    expect(screen.getByText(/410 tok/)).toBeInTheDocument()
  })

  it('triggers inline Restore to hot and Pin actions via /ostk/context-pages endpoints', async () => {
    mockedGet.mockResolvedValueOnce({
      available: true,
      pages: [
        {
          name: 'warm-survey',
          tier: 'warm',
          tokens: 920,
          owner: 'agent-1',
          pinned: false,
        },
      ],
      total_pages: 1,
      total_tokens: 920,
      hot_tokens: 0,
    })
    mockedPost.mockImplementation(async (url: string) => {
      if (url === '/ostk/context-pages/warm-survey/restore') {
        return { ok: true, name: 'warm-survey', tier: 'hot' }
      }
      if (url === '/ostk/context-pages/warm-survey/pin') {
        return { ok: true, name: 'warm-survey', pinned: true, unpin: false }
      }
      return { ok: true }
    })

    render(
      <HandoffRuntimeModal
        isOpen={true}
        onClose={vi.fn()}
        agentName="builder-claude"
        currentModel="claude-sonnet-5-5"
      />
    )

    await waitFor(() => {
      expect(screen.getByTestId('context-page-restore-warm-survey')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('context-page-restore-warm-survey'))
    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith('/ostk/context-pages/warm-survey/restore', {})
      expect(screen.getByTestId('context-page-tier-warm-survey')).toHaveTextContent('hot')
    })

    fireEvent.click(screen.getByTestId('context-page-pin-warm-survey'))
    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith('/ostk/context-pages/warm-survey/pin', { unpin: false })
      expect(screen.getByTestId('context-page-pin-warm-survey')).toHaveTextContent(/Pinned/i)
    })
  })

  it('includes selected ostk context-load pointers and context_pages in the handoff submission', async () => {
    const onClose = vi.fn()
    const onHandedOff = vi.fn()
    mockedGet.mockResolvedValueOnce({
      available: true,
      pages: [
        {
          name: 'hot-research',
          tier: 'hot',
          tokens: 1500,
          owner: 'agent-1',
          pinned: false,
        },
        {
          name: 'warm-design',
          tier: 'warm',
          tokens: 800,
          owner: 'agent-2',
          pinned: false,
        },
      ],
      total_pages: 2,
      total_tokens: 2300,
      hot_tokens: 1500,
    })
    mockedPost.mockResolvedValueOnce({ ok: true, to_agent: 'builder-claude-to-gemini' })

    render(
      <HandoffRuntimeModal
        isOpen={true}
        onClose={onClose}
        agentName="builder-claude"
        taskId="3114"
        currentModel="claude-sonnet-5-5"
        onHandedOff={onHandedOff}
      />
    )

    await waitFor(() => {
      expect(screen.getByTestId('context-page-checkbox-warm-design')).toBeInTheDocument()
    })

    // Select warm-design in addition to default-selected hot-research
    fireEvent.click(screen.getByTestId('context-page-checkbox-warm-design'))
    fireEvent.change(screen.getByTestId('handoff-transition-note'), {
      target: { value: 'Switch to Gemini for final tests' },
    })
    fireEvent.click(screen.getByTestId('confirm-handoff-btn'))

    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledTimes(1)
    })

    const [calledUrl, calledBody] = mockedPost.mock.calls[0] as [
      string,
      {
        target_runtime: string
        target_model: string
        task_id?: string
        transition_note: string
        context_pages?: string[]
      },
    ]
    expect(calledUrl).toBe('/agents/builder-claude/handoff-runtime')
    expect(calledBody.target_runtime).toBe('gemini')
    expect(calledBody.target_model).toBe('gemini-3.1-pro')
    expect(calledBody.task_id).toBe('3114')
    expect(calledBody.context_pages).toEqual(['hot-research', 'warm-design'])
    expect(calledBody.transition_note).toContain('Switch to Gemini for final tests')
    expect(calledBody.transition_note).toContain('ostk context-load hot-research')
    expect(calledBody.transition_note).toContain('ostk context-load warm-design')
    expect(onHandedOff).toHaveBeenCalledWith({ ok: true, to_agent: 'builder-claude-to-gemini' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('degrades gracefully and never blocks handoff when GET /ostk/context-pages rejects', async () => {
    const onClose = vi.fn()
    mockedGet.mockRejectedValueOnce(new Error('ostk unavailable'))
    mockedPost.mockResolvedValueOnce({ ok: true })

    render(
      <HandoffRuntimeModal
        isOpen={true}
        onClose={onClose}
        agentName="builder-claude"
        taskId="3114"
        currentModel="claude-sonnet-5-5"
      />
    )

    await waitFor(() => {
      expect(mockedGet).toHaveBeenCalledWith('/ostk/context-pages')
    })
    expect(screen.queryByTestId('handoff-error')).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('confirm-handoff-btn'))
    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith('/agents/builder-claude/handoff-runtime', {
        target_runtime: 'gemini',
        target_model: 'gemini-3.1-pro',
        task_id: '3114',
        transition_note: '',
      })
    })
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
