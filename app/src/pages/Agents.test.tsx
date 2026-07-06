/**
 * Tests for the TodayDigestPanel component rendered in the Agents page
 * Active Sessions section (→2455 phase D).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TodayDigestPanel, type DigestData } from './TodayDigestPanel'

const makeDigest = (overrides: Partial<DigestData> = {}): DigestData => ({
  sessions: [
    {
      session_id: 'agent-foo',
      label: 'Build the feature',
      activity_count: 12,
      files_touched: ['api/routers/chat.py', 'api/tests/test_chat.py'],
      recent_activity: 'Writing files: chat.py',
    },
  ],
  closed_tasks_today: [],
  generated_at: '2026-07-06T12:00:00Z',
  ...overrides,
})

describe('TodayDigestPanel', () => {
  it('renders nothing when no sessions and no closed tasks', () => {
    const { container } = render(
      <TodayDigestPanel digest={{ sessions: [], closed_tasks_today: [], generated_at: '' }} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders the panel heading when there are sessions', () => {
    render(<TodayDigestPanel digest={makeDigest()} />)
    expect(screen.getByTestId('today-digest-panel')).toBeInTheDocument()
    expect(screen.getByText(/today across sessions/i)).toBeInTheDocument()
  })

  it('shows session label and activity count', () => {
    render(<TodayDigestPanel digest={makeDigest()} />)
    expect(screen.getByText(/Build the feature/i)).toBeInTheDocument()
    // Activity count appears at least once (summary or row)
    expect(screen.getAllByText(/12 actions/).length).toBeGreaterThan(0)
  })

  it('shows files touched', () => {
    render(<TodayDigestPanel digest={makeDigest()} />)
    // File chips appear in the hidden detail section; getAllByText works on hidden elements
    expect(screen.getAllByText(/chat\.py/).length).toBeGreaterThan(0)
  })

  it('is collapsed by default and expands on click', async () => {
    render(<TodayDigestPanel digest={makeDigest()} />)
    const toggle = screen.getByRole('button', { name: /today across sessions/i })
    // Detail rows hidden initially
    expect(screen.queryByTestId('digest-session-row')).not.toBeVisible()
    await userEvent.click(toggle)
    expect(screen.getByTestId('digest-session-row')).toBeVisible()
  })

  it('renders closed tasks when present', () => {
    const digest = makeDigest({
      closed_tasks_today: [
        { id: '→1234', title: 'Fix the login bug', closed_at: '2026-07-06T10:00:00Z' },
      ],
    })
    render(<TodayDigestPanel digest={digest} />)
    expect(screen.getByText(/Fix the login bug/)).toBeInTheDocument()
  })

  it('renders panel even with only closed tasks (no sessions)', () => {
    const digest: DigestData = {
      sessions: [],
      closed_tasks_today: [
        { id: '→9999', title: 'Ship the thing', closed_at: '2026-07-06T11:00:00Z' },
      ],
      generated_at: '',
    }
    render(<TodayDigestPanel digest={digest} />)
    expect(screen.getByTestId('today-digest-panel')).toBeInTheDocument()
    expect(screen.getByText(/Ship the thing/)).toBeInTheDocument()
  })
})
