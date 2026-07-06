/**
 * Tests for TodayDigestPanel rendered on the Sessions page (→2455 phase D).
 * Verifies the panel appears above the grid.
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { TodayDigestPanel, type DigestData } from './TodayDigestPanel'

describe('TodayDigestPanel on Sessions page', () => {
  it('renders nothing with empty digest', () => {
    const { container } = render(
      <TodayDigestPanel digest={{ sessions: [], closed_tasks_today: [], generated_at: '' }} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders with session activity', () => {
    const digest: DigestData = {
      sessions: [
        {
          session_id: 'agent-bar',
          label: 'Do the work',
          activity_count: 5,
          files_touched: [],
          recent_activity: 'Running command',
        },
      ],
      closed_tasks_today: [],
      generated_at: '2026-07-06T12:00:00Z',
    }
    render(<TodayDigestPanel digest={digest} />)
    expect(screen.getByTestId('today-digest-panel')).toBeInTheDocument()
    expect(screen.getByText(/Do the work/)).toBeInTheDocument()
    expect(screen.getAllByText(/5 actions/).length).toBeGreaterThan(0)
  })

  it('renders testid today-digest-panel when closed tasks exist', () => {
    const digest: DigestData = {
      sessions: [],
      closed_tasks_today: [{ id: '→42', title: 'A task', closed_at: '' }],
      generated_at: '',
    }
    render(<TodayDigestPanel digest={digest} />)
    expect(screen.getByTestId('today-digest-panel')).toBeInTheDocument()
  })
})
