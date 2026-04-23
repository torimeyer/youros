import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import Activity from './Activity'
import { buildStream, groupByDay, bundleEntries, isInternalAgent } from '../lib/activityStream'
import { useAppStore } from '../stores/app'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
  },
}))

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

function renderActivity() {
  return render(
    <MemoryRouter>
      <Activity />
    </MemoryRouter>
  )
}

// ─── Mixed raw events: some user-facing, some internal ──────────────────────

const MIXED_EVENTS = [
  // User-facing: task closed
  {
    timestamp: '2026-04-10T10:00:00Z',
    event: 'task.closed',
    label: 'Task closed',
    category: 'task',
    detail: '→087 Build activity timeline',
  },
  // User-facing: task created
  {
    timestamp: '2026-04-10T09:50:00Z',
    event: 'task.added',
    label: 'Task created',
    category: 'task',
    detail: '→088 Redesign activity stream',
  },
  // User-facing: idea captured
  {
    timestamp: '2026-04-10T09:40:00Z',
    event: 'hay.filed',
    label: 'Idea saved',
    category: 'idea',
    detail: 'source="user" straw="Group tasks by thread"',
  },
  // User-facing: named agent completed
  {
    timestamp: '2026-04-10T09:30:00Z',
    event: 'agent.completed',
    label: 'Agent finished',
    category: 'agent',
    detail: 'name="fix-delete-403" duration=12s',
  },
  // Internal: agent.spawned (always hidden)
  {
    timestamp: '2026-04-10T09:29:00Z',
    event: 'agent.spawned',
    label: 'Agent started',
    category: 'agent',
    detail: 'name="fix-delete-403"',
  },
  // Internal: session.shutdown (always hidden)
  {
    timestamp: '2026-04-10T09:00:00Z',
    event: 'session.shutdown',
    label: 'Session ended',
    category: 'system',
    detail: '',
  },
  // Internal: inferred CC background agent completed (filtered by name)
  {
    timestamp: '2026-04-10T08:50:00Z',
    event: 'agent.completed',
    label: 'Agent finished',
    category: 'agent',
    detail: 'name="claude-code-a1b2c3d4"',
  },
  // Internal: tack.resolved (always hidden)
  {
    timestamp: '2026-04-10T08:40:00Z',
    event: 'tack.resolved',
    label: 'Command resolved',
    category: 'system',
    detail: 'input=":status"',
  },
  // User-facing: spec promoted
  {
    timestamp: '2026-04-09T15:00:00Z',
    event: 'spec.promoted',
    label: 'Spec promoted',
    category: 'task',
    detail: 'from="docs/draft/activity-redesign.md" to="docs/spec/activity-redesign.md"',
  },
  // User-facing: idea converted
  {
    timestamp: '2026-04-09T14:00:00Z',
    event: 'hay.converted',
    label: 'Idea turned into task',
    category: 'idea',
    detail: 'straw="Group tasks by thread" task_id="→042"',
  },
]

// ─── Activity page rendering tests ──────────────────────────────────────────

describe('Activity page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
  })

  it('renders the page title', async () => {
    mockedApiGet.mockResolvedValue({ events: [], count: 0 })
    renderActivity()
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Activity' })).toBeInTheDocument()
    })
  })

  it('shows a Transcripts tab', async () => {
    mockedApiGet.mockResolvedValue({ events: [], count: 0 })
    renderActivity()
    await waitFor(() => {
      expect(screen.getByTestId('transcripts-tab')).toBeInTheDocument()
    })
    expect(screen.getByTestId('transcripts-tab')).toHaveTextContent('Transcripts')
  })

  it('shows plain-language empty state', async () => {
    mockedApiGet.mockResolvedValue({ events: [], count: 0 })
    renderActivity()
    await waitFor(() => {
      expect(screen.getByTestId('empty-state')).toBeInTheDocument()
    })
    expect(
      screen.getByText('Your activity will show up here as you work')
    ).toBeInTheDocument()
  })

  it('curated view only shows user-facing events, not internal ones', async () => {
    mockedApiGet.mockResolvedValue({ events: MIXED_EVENTS, count: MIXED_EVENTS.length })
    renderActivity()

    await waitFor(() => {
      expect(screen.getByText('Closed: Build activity timeline')).toBeInTheDocument()
    })

    // User-facing entries must be present
    expect(screen.getByText('New task: Redesign activity stream')).toBeInTheDocument()
    expect(screen.getByText('Finished: Fix Delete 403')).toBeInTheDocument()
    expect(screen.getByText(/Promoted spec/)).toBeInTheDocument()

    // Hay events must NOT appear (ideas feature removed)
    expect(screen.queryByText(/Captured idea/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Converted idea to task/)).not.toBeInTheDocument()

    // Internal events must NOT appear as summary text
    expect(screen.queryByText('Agent started')).not.toBeInTheDocument()
    expect(screen.queryByText('Session ended')).not.toBeInTheDocument()
    expect(screen.queryByText('Command resolved')).not.toBeInTheDocument()
    // The inferred CC agent completed must be hidden
    expect(screen.queryByText(/claude-code-a1b2c3d4/)).not.toBeInTheDocument()
  })

  it('internal events appear when "Show technical details" is toggled on', async () => {
    mockedApiGet.mockResolvedValue({ events: MIXED_EVENTS, count: MIXED_EVENTS.length })
    const user = userEvent.setup()
    renderActivity()

    await waitFor(() => {
      expect(screen.getByTestId('show-technical-toggle')).toBeInTheDocument()
    })

    // Enable technical view
    await user.click(screen.getByTestId('show-technical-toggle'))

    // Now raw labels surface
    await waitFor(() => {
      expect(screen.getByText('Agent started')).toBeInTheDocument()
    })
    expect(screen.getByText('Session ended')).toBeInTheDocument()
  })

  it('day grouping works across multiple dates', async () => {
    mockedApiGet.mockResolvedValue({ events: MIXED_EVENTS, count: MIXED_EVENTS.length })
    renderActivity()

    await waitFor(() => {
      // Two distinct date groups should exist (Apr 10 and Apr 9)
      const rows = screen.getAllByTestId('stream-row')
      expect(rows.length).toBeGreaterThanOrEqual(2)
    })
  })

  it('clicking a row expands technical details', async () => {
    mockedApiGet.mockResolvedValue({ events: [MIXED_EVENTS[0]], count: 1 })
    const user = userEvent.setup()
    renderActivity()

    await waitFor(() => {
      expect(screen.getByTestId('stream-row')).toBeInTheDocument()
    })

    // Detail drawer is not visible yet
    expect(screen.queryByTestId('stream-row-detail')).not.toBeInTheDocument()

    // Click to expand
    await user.click(screen.getByTestId('stream-row'))

    expect(screen.getByTestId('stream-row-detail')).toBeInTheDocument()
    // Raw event type is shown in the drawer
    expect(screen.getByText('task.closed')).toBeInTheDocument()
  })

  it('clicking an expanded row collapses it', async () => {
    mockedApiGet.mockResolvedValue({ events: [MIXED_EVENTS[0]], count: 1 })
    const user = userEvent.setup()
    renderActivity()

    await waitFor(() => {
      expect(screen.getByTestId('stream-row')).toBeInTheDocument()
    })

    const row = screen.getByTestId('stream-row')
    await user.click(row)
    expect(screen.getByTestId('stream-row-detail')).toBeInTheDocument()

    await user.click(row)
    expect(screen.queryByTestId('stream-row-detail')).not.toBeInTheDocument()
  })
})

// ─── activityStream unit tests ───────────────────────────────────────────────

describe('isInternalAgent', () => {
  it('marks claude-code- prefix as internal', () => {
    expect(isInternalAgent('claude-code-a1b2c3d4')).toBe(true)
  })
  it('marks hook- prefix as internal', () => {
    expect(isInternalAgent('hook-summarizer')).toBe(true)
  })
  it('marks audit- prefix as internal', () => {
    expect(isInternalAgent('audit-watcher')).toBe(true)
  })
  it('does not mark a named user agent as internal', () => {
    expect(isInternalAgent('fix-delete-403')).toBe(false)
    expect(isInternalAgent('redesign-activity-stream')).toBe(false)
  })
})

describe('buildStream – curated event taxonomy', () => {
  it('task.closed → "Closed: {title}"', () => {
    const stream = buildStream([{
      timestamp: '2026-04-10T10:00:00Z',
      event: 'task.closed',
      label: 'Task closed',
      category: 'task',
      detail: '→087 Build activity timeline',
    }])
    expect(stream).toHaveLength(1)
    expect(stream[0].kind).toBe('task.closed')
    expect(stream[0].summary).toBe('Closed: Build activity timeline')
    expect(stream[0].palette).toBe('emerald')
  })

  it('task.added → "New task: {title}"', () => {
    const stream = buildStream([{
      timestamp: '2026-04-10T09:50:00Z',
      event: 'task.added',
      label: 'Task created',
      category: 'task',
      detail: '→088 Redesign activity stream',
    }])
    expect(stream[0].kind).toBe('task.created')
    expect(stream[0].summary).toBe('New task: Redesign activity stream')
    expect(stream[0].palette).toBe('blue')
  })

  it('hay.filed → filtered (ideas feature removed)', () => {
    const stream = buildStream([{
      timestamp: '2026-04-10T09:40:00Z',
      event: 'hay.filed',
      label: 'Idea saved',
      category: 'idea',
      detail: 'source="user" straw="Group tasks by thread"',
    }])
    expect(stream).toHaveLength(0)
  })

  it('hay.converted → filtered (ideas feature removed)', () => {
    const stream = buildStream([{
      timestamp: '2026-04-10T09:30:00Z',
      event: 'hay.converted',
      label: 'Idea turned into task',
      category: 'idea',
      detail: 'straw="Group tasks by thread" task_id="→042"',
    }])
    expect(stream).toHaveLength(0)
  })

  it('spec.promoted → "Promoted spec: {title}"', () => {
    const stream = buildStream([{
      timestamp: '2026-04-09T15:00:00Z',
      event: 'spec.promoted',
      label: 'Spec promoted',
      category: 'task',
      detail: 'from="docs/draft/activity-redesign.md" to="docs/spec/activity-redesign.md"',
    }])
    expect(stream[0].kind).toBe('spec.promoted')
    expect(stream[0].summary).toContain('Promoted spec')
    expect(stream[0].summary).toContain('Activity Redesign')
    expect(stream[0].palette).toBe('purple')
  })

  it('agent.completed (named) → "Finished: {title}"', () => {
    const stream = buildStream([{
      timestamp: '2026-04-10T09:30:00Z',
      event: 'agent.completed',
      label: 'Agent finished',
      category: 'agent',
      detail: 'name="fix-delete-403" duration=12s',
    }])
    expect(stream[0].kind).toBe('agent.finished')
    expect(stream[0].summary).toBe('Finished: Fix Delete 403')
  })

  it('agent.completed (inferred claude-code- name) → filtered out', () => {
    const stream = buildStream([{
      timestamp: '2026-04-10T08:50:00Z',
      event: 'agent.completed',
      label: 'Agent finished',
      category: 'agent',
      detail: 'name="claude-code-a1b2c3d4"',
    }])
    expect(stream).toHaveLength(0)
  })

  it('agent.spawned → always filtered out', () => {
    const stream = buildStream([{
      timestamp: '2026-04-10T09:29:00Z',
      event: 'agent.spawned',
      label: 'Agent started',
      category: 'agent',
      detail: 'name="fix-delete-403"',
    }])
    expect(stream).toHaveLength(0)
  })

  it('session.shutdown → always filtered out', () => {
    const stream = buildStream([{
      timestamp: '2026-04-10T09:00:00Z',
      event: 'session.shutdown',
      label: 'Session ended',
      category: 'system',
      detail: '',
    }])
    expect(stream).toHaveLength(0)
  })

  it('tack.resolved → always filtered out', () => {
    const stream = buildStream([{
      timestamp: '2026-04-10T08:40:00Z',
      event: 'tack.resolved',
      label: 'Command resolved',
      category: 'system',
      detail: 'input=":status"',
    }])
    expect(stream).toHaveLength(0)
  })

  it('decision.recorded → "Decision: ..."', () => {
    const stream = buildStream([{
      timestamp: '2026-04-10T11:00:00Z',
      event: 'decision.recorded',
      label: 'Decision recorded',
      category: 'decision',
      detail: 'Use Tailwind for styling',
    }])
    expect(stream[0].kind).toBe('decision.recorded')
    expect(stream[0].summary).toContain('Decision')
    expect(stream[0].palette).toBe('cyan')
  })

  it('builds stream with only user-facing events from mixed input', () => {
    const stream = buildStream(MIXED_EVENTS)
    const kinds = stream.map((e) => e.kind)
    // Should include these
    expect(kinds).toContain('task.closed')
    expect(kinds).toContain('task.created')
    expect(kinds).toContain('agent.finished')
    expect(kinds).toContain('spec.promoted')
    // Hay events must NOT appear (ideas feature removed)
    expect(kinds).not.toContain('idea.captured')
    expect(kinds).not.toContain('idea.converted')
    // agent.spawned and session.shutdown and tack.resolved must be gone
    // (none of these map to any kind we keep)
    // claude-code- agent.completed must be gone
    const agentFinishedSummaries = stream
      .filter((e) => e.kind === 'agent.finished')
      .map((e) => e.summary)
    expect(agentFinishedSummaries.every((s) => !s.includes('claude-code'))).toBe(true)
  })
})

// ─── groupByDay unit tests ───────────────────────────────────────────────────

describe('groupByDay', () => {
  it('groups entries by ISO date', () => {
    const entries = [
      { key: 'a', kind: 'task.closed' as const, summary: 'Closed: Foo', icon: 'check_circle', palette: 'emerald' as const, timestamp: '2026-04-10T10:00:00Z', raw: {} as any },
      { key: 'b', kind: 'task.created' as const, summary: 'New task: Bar', icon: 'add_task', palette: 'blue' as const, timestamp: '2026-04-10T09:00:00Z', raw: {} as any },
      { key: 'c', kind: 'spec.promoted' as const, summary: 'Promoted spec', icon: 'upgrade', palette: 'purple' as const, timestamp: '2026-04-09T15:00:00Z', raw: {} as any },
    ]
    const groups = groupByDay(entries)
    expect(groups).toHaveLength(2)
    expect(groups[0].dateKey).toBe('2026-04-10')
    expect(groups[0].entries).toHaveLength(2)
    expect(groups[1].dateKey).toBe('2026-04-09')
    expect(groups[1].entries).toHaveLength(1)
  })

  it('returns newest day first', () => {
    const entries = [
      { key: 'a', kind: 'task.closed' as const, summary: 'Closed: Foo', icon: 'check_circle', palette: 'emerald' as const, timestamp: '2026-04-08T10:00:00Z', raw: {} as any },
      { key: 'b', kind: 'task.created' as const, summary: 'New task: Bar', icon: 'add_task', palette: 'blue' as const, timestamp: '2026-04-10T09:00:00Z', raw: {} as any },
    ]
    const groups = groupByDay(entries)
    expect(groups[0].dateKey).toBe('2026-04-10')
    expect(groups[1].dateKey).toBe('2026-04-08')
  })

  it('returns empty array for empty input', () => {
    expect(groupByDay([])).toHaveLength(0)
  })
})

// ─── bundleEntries unit tests ───────────────────────────────────────────────

describe('bundleEntries', () => {
  function agentKilled(name: string, iso: string, idx = 0) {
    return {
      key: `${iso}-${idx}-agent.killed`,
      kind: 'agent.failed' as const,
      summary: `Stopped: ${name}`,
      icon: 'stop_circle',
      palette: 'slate' as const,
      timestamp: iso,
      raw: {
        timestamp: iso,
        event: 'agent.killed',
        label: 'Agent killed',
        category: 'agent',
        detail: `name="${name}"`,
      },
    }
  }

  it('collapses 53 same-summary same-minute entries into one bundle of size 53', () => {
    const entries = Array.from({ length: 53 }, (_, i) =>
      agentKilled('Agent Cancelled', `2026-04-09T22:38:${String(i % 60).padStart(2, '0')}.000Z`, i)
    )
    entries.forEach((e) => { e.summary = 'Agent Cancelled' })
    const bundles = bundleEntries(entries)
    expect(bundles).toHaveLength(1)
    expect(bundles[0].entries).toHaveLength(53)
    expect(bundles[0].lead.summary).toBe('Agent Cancelled')
  })

  it('does not bundle entries with different kinds', () => {
    const a = agentKilled('Agent Cancelled', '2026-04-09T22:38:00.000Z', 0)
    a.summary = 'Agent Cancelled'
    const b = {
      key: 'b',
      kind: 'task.closed' as const,
      summary: 'Agent Cancelled',
      icon: 'check_circle',
      palette: 'emerald' as const,
      timestamp: '2026-04-09T22:38:10.000Z',
      raw: {
        timestamp: '2026-04-09T22:38:10.000Z',
        event: 'task.closed',
        label: 'Task closed',
        category: 'task',
        detail: 'title="Agent Cancelled"',
      },
    }
    const bundles = bundleEntries([a, b])
    expect(bundles).toHaveLength(2)
  })

  it('does not bundle entries in different minute buckets', () => {
    const a = agentKilled('Agent Cancelled', '2026-04-09T22:38:59.000Z', 0)
    a.summary = 'Agent Cancelled'
    const b = agentKilled('Agent Cancelled', '2026-04-09T22:39:00.000Z', 1)
    b.summary = 'Agent Cancelled'
    const bundles = bundleEntries([a, b])
    expect(bundles).toHaveLength(2)
  })

  it('bundles entries within the same minute across seconds', () => {
    const a = agentKilled('Agent Cancelled', '2026-04-09T22:38:00.000Z', 0)
    const b = agentKilled('Agent Cancelled', '2026-04-09T22:38:45.000Z', 1)
    a.summary = 'Agent Cancelled'
    b.summary = 'Agent Cancelled'
    const bundles = bundleEntries([a, b])
    expect(bundles).toHaveLength(1)
    expect(bundles[0].entries).toHaveLength(2)
  })

  it('does not bundle non-consecutive runs even with the same summary and bucket', () => {
    const a = agentKilled('Agent Cancelled', '2026-04-09T22:38:00.000Z', 0)
    a.summary = 'Agent Cancelled'
    const c = {
      key: 'c',
      kind: 'task.closed' as const,
      summary: 'Closed: Something else',
      icon: 'check_circle',
      palette: 'emerald' as const,
      timestamp: '2026-04-09T22:38:05.000Z',
      raw: {
        timestamp: '2026-04-09T22:38:05.000Z',
        event: 'task.closed',
        label: 'Task closed',
        category: 'task',
        detail: 'title="Other"',
      },
    }
    const b = agentKilled('Agent Cancelled', '2026-04-09T22:38:10.000Z', 2)
    b.summary = 'Agent Cancelled'
    const bundles = bundleEntries([a, c, b])
    expect(bundles).toHaveLength(3)
    expect(bundles.map((x) => x.entries.length)).toEqual([1, 1, 1])
  })

  it('returns empty list for empty input', () => {
    expect(bundleEntries([])).toHaveLength(0)
  })
})

// ─── Activity page bundling rendering tests ─────────────────────────────────

function rawAgentCancelled(iso: string): any {
  return {
    timestamp: iso,
    event: 'agent.killed',
    label: 'Agent killed',
    category: 'agent',
    detail: `name="agent-cancelled-${iso}"`,
  }
}

describe('Activity page - bundling', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ chatOpen: true, osName: 'myOS', darkMode: true })
  })

  it('collapses 53 consecutive same-minute "Stopped" events into one row with a count badge', async () => {
    const events = Array.from({ length: 53 }, (_, i) =>
      rawAgentCancelled(`2026-04-09T22:38:${String(i % 60).padStart(2, '0')}.000Z`)
    )
    events.forEach((e) => { e.detail = 'name="agent-cancelled"' })

    mockedApiGet.mockResolvedValue({ events, count: events.length })
    renderActivity()

    await waitFor(() => {
      expect(screen.getByTestId('stream-bundle')).toBeInTheDocument()
    })
    expect(screen.getAllByTestId('stream-bundle')).toHaveLength(1)
    expect(screen.queryByTestId('stream-row')).not.toBeInTheDocument()

    const badge = screen.getByTestId('bundle-count')
    expect(badge.textContent).toContain('53')

    expect(screen.getByTestId('day-count').textContent).toContain('53 items')
    expect(screen.getByTestId('day-count').textContent).toContain('1 group')
  })

  it('clicking the bundle row reveals the individual entries', async () => {
    const events = Array.from({ length: 3 }, (_, i) =>
      rawAgentCancelled(`2026-04-09T22:38:${String(i * 10).padStart(2, '0')}.000Z`)
    )
    events.forEach((e) => { e.detail = 'name="agent-cancelled"' })
    mockedApiGet.mockResolvedValue({ events, count: events.length })
    const user = userEvent.setup()
    renderActivity()

    await waitFor(() => {
      expect(screen.getByTestId('stream-bundle')).toBeInTheDocument()
    })

    expect(screen.queryByTestId('stream-bundle-detail')).not.toBeInTheDocument()
    await user.click(screen.getByTestId('stream-bundle'))

    expect(screen.getByTestId('stream-bundle-detail')).toBeInTheDocument()
    expect(screen.getAllByTestId('bundle-entry')).toHaveLength(3)
  })

  it('different kinds at the same time do not bundle together', async () => {
    const events = [
      {
        timestamp: '2026-04-09T22:38:00.000Z',
        event: 'agent.killed',
        label: 'Agent killed',
        category: 'agent',
        detail: 'name="agent-cancelled"',
      },
      {
        timestamp: '2026-04-09T22:38:05.000Z',
        event: 'task.closed',
        label: 'Task closed',
        category: 'task',
        detail: '→042 Fix the thing',
      },
    ]
    mockedApiGet.mockResolvedValue({ events, count: events.length })
    renderActivity()

    await waitFor(() => {
      expect(screen.getByText('Closed: Fix the thing')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('stream-bundle')).not.toBeInTheDocument()
    expect(screen.getAllByTestId('stream-row')).toHaveLength(2)
  })

  it('different minute buckets render as separate rows, not one bundle', async () => {
    const events = [
      {
        timestamp: '2026-04-09T22:38:59.000Z',
        event: 'agent.killed',
        label: 'Agent killed',
        category: 'agent',
        detail: 'name="agent-cancelled"',
      },
      {
        timestamp: '2026-04-09T22:39:00.000Z',
        event: 'agent.killed',
        label: 'Agent killed',
        category: 'agent',
        detail: 'name="agent-cancelled"',
      },
    ]
    mockedApiGet.mockResolvedValue({ events, count: events.length })
    renderActivity()

    await waitFor(() => {
      expect(screen.getAllByTestId('stream-row')).toHaveLength(2)
    })
    expect(screen.queryByTestId('stream-bundle')).not.toBeInTheDocument()
  })

  it('single-entry bundle renders as a normal row with no count badge', async () => {
    const events = [
      {
        timestamp: '2026-04-09T22:38:00.000Z',
        event: 'task.closed',
        label: 'Task closed',
        category: 'task',
        detail: '→042 Fix the thing',
      },
    ]
    mockedApiGet.mockResolvedValue({ events, count: events.length })
    renderActivity()

    await waitFor(() => {
      expect(screen.getByTestId('stream-row')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('stream-bundle')).not.toBeInTheDocument()
    expect(screen.queryByTestId('bundle-count')).not.toBeInTheDocument()
  })
})

describe('Activity trace filter', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ onboarded: true })
  })

  it('renders the trace filter input', async () => {
    mockedApiGet.mockResolvedValue({ events: [], count: 0 })
    renderActivity()
    await waitFor(() => {
      expect(screen.getByTestId('trace-filter-input')).toBeInTheDocument()
    })
  })

  it('filters out rows whose trace_id does not match', async () => {
    const events = [
      {
        timestamp: '2026-04-10T10:00:00Z',
        event: 'task.closed',
        label: 'Task closed',
        category: 'task',
        detail: '→100 Matching task',
        trace_id: 'abc-111',
      },
      {
        timestamp: '2026-04-10T10:01:00Z',
        event: 'task.closed',
        label: 'Task closed',
        category: 'task',
        detail: '→101 Non matching',
        trace_id: 'xyz-222',
      },
    ]
    mockedApiGet.mockResolvedValue({ events, count: events.length })
    renderActivity()

    await waitFor(() => {
      expect(screen.getAllByTestId('stream-row').length).toBeGreaterThan(0)
    })

    const input = screen.getByTestId('trace-filter-input') as HTMLInputElement
    await userEvent.type(input, 'abc-111')

    await waitFor(() => {
      const rows = screen.getAllByTestId('stream-row')
      // Only the matching row should remain.
      expect(rows.length).toBe(1)
    })
  })
})
