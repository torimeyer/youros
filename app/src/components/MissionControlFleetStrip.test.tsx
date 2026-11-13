import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import MissionControlFleetStrip from './MissionControlFleetStrip'
import { api } from '../lib/api'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn().mockResolvedValue({ ok: true }),
  },
}))

const mockedApiGet = vi.mocked(api.get)
const mockedApiPost = vi.mocked(api.post)

function renderStrip() {
  return render(
    <MemoryRouter>
      <MissionControlFleetStrip />
    </MemoryRouter>,
  )
}

describe('MissionControlFleetStrip (→3094)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedApiPost.mockResolvedValue({ ok: true })
  })

  it('renders active Claude and Gemini seats with runtime badges, repo basenames, touched files, and quiet stall indicator', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/sessions') {
        return Promise.resolve({
          sessions: [
            {
              id: 'claude-seat-1',
              name: 'claude-seat-1',
              label: 'claude-seat-1',
              runtime: 'claude',
              repo: '/Users/torimeyer/claude/torios',
              status: 'active',
              activity: 'Editing Dashboard.tsx',
              touched_files: ['app/src/pages/Dashboard.tsx'],
              stuck: false,
            },
            {
              id: 'gemini-seat-2',
              name: 'gemini-seat-2',
              label: 'gemini-seat-2',
              runtime: 'gemini',
              repo: '/Users/torimeyer/work/youros-core',
              status: 'active',
              activity: 'Running pytest suite',
              touched_files: ['api/routers/search.py'],
              stuck: true,
            },
          ],
          conflicts: [],
        })
      }
      if (path === '/secrets/key-status') {
        return Promise.resolve({
          ostk_available: true,
          humanfile_valid: true,
        })
      }
      return Promise.resolve(null)
    })

    renderStrip()

    const strip = await screen.findByTestId('mission-control-strip')
    expect(strip).toBeInTheDocument()

    await waitFor(() => {
      expect(within(strip).getByText(/2 active seats/i)).toBeInTheDocument()
    })
    expect(within(strip).getByText('Claude')).toBeInTheDocument()
    expect(within(strip).getByText('Gemini')).toBeInTheDocument()
    expect(within(strip).getByText('torios')).toBeInTheDocument()
    expect(within(strip).getByText('youros-core')).toBeInTheDocument()
    expect(within(strip).getByText('Editing Dashboard.tsx')).toBeInTheDocument()
    expect(within(strip).getByTestId('seat-quiet-badge')).toHaveTextContent('quiet')

    const trustPill = within(strip).getByTestId('mission-control-trust-pill')
    expect(trustPill).toHaveTextContent('ostk verified')
    expect(within(strip).queryByTestId('mission-control-conflict-banner')).toBeNull()
  })

  it('renders amber ostk trust warning pill when humanfile_valid is false', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/sessions') {
        return Promise.resolve({ sessions: [] })
      }
      if (path === '/secrets/key-status') {
        return Promise.resolve({
          ostk_available: true,
          humanfile_valid: false,
          humanfile_warning: 'HUMANFILE was modified since it was signed.',
        })
      }
      return Promise.resolve(null)
    })

    renderStrip()

    const trustPill = await screen.findByTestId('mission-control-trust-pill')
    await waitFor(() => {
      expect(trustPill).toHaveTextContent('HUMANFILE signature needs ostk trust sign')
    })
    expect(trustPill.className).toContain('amber')
  })

  it('detects overlapping touched_files across active seats, renders conflict banner, and invokes Acquire Lock and Nudge Agent endpoints', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/sessions') {
        return Promise.resolve({
          sessions: [
            {
              id: 'claude-alpha',
              name: 'claude-alpha',
              runtime: 'claude',
              repo: 'torios',
              status: 'active',
              touched_files: ['api/routers/search.py', 'README.md'],
            },
            {
              id: 'gemini-beta',
              name: 'gemini-beta',
              runtime: 'gemini',
              repo: 'torios',
              status: 'active',
              touched_files: ['api/routers/search.py'],
            },
          ],
        })
      }
      if (path === '/secrets/key-status') {
        return Promise.resolve({ ostk_available: true, humanfile_valid: true })
      }
      return Promise.resolve(null)
    })

    renderStrip()

    const banner = await screen.findByTestId('mission-control-conflict-banner')
    expect(banner).toBeInTheDocument()
    expect(banner.textContent).toContain('api/routers/search.py')
    expect(banner.textContent).toContain('claude-alpha')
    expect(banner.textContent).toContain('gemini-beta')

    fireEvent.click(within(banner).getByRole('button', { name: /Acquire Lock/i }))
    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/locks', {
        path: 'api/routers/search.py',
        holder: 'operator',
      })
    })

    fireEvent.click(within(banner).getByRole('button', { name: /Nudge Agent/i }))
    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith(
        '/agents/claude-alpha/nudge',
        expect.objectContaining({
          message: expect.stringContaining('api/routers/search.py'),
        }),
      )
    })
  })

  it('toggles TodayDigestPanel inline when clicking toggle-today-digest', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/sessions') {
        return Promise.resolve({
          sessions: [
            {
              id: 'claude-alpha',
              name: 'claude-alpha',
              runtime: 'claude',
              status: 'active',
              activity: 'Writing tests',
              touched_files: ['app/src/pages/Dashboard.tsx'],
            },
          ],
        })
      }
      if (path === '/sessions/digest') {
        return Promise.resolve({
          sessions: [
            {
              session_id: 'claude-alpha',
              label: 'claude-alpha',
              activity_count: 12,
              files_touched: ['app/src/pages/Dashboard.tsx'],
              recent_activity: 'Writing tests',
            },
          ],
          closed_tasks_today: [
            { id: '→3094', title: 'Unified Mission Control', closed_at: '2026-11-13T20:00:00Z' },
          ],
          generated_at: '2026-11-13T20:05:00Z',
        })
      }
      if (path === '/secrets/key-status') {
        return Promise.resolve({ ostk_available: true, humanfile_valid: true })
      }
      return Promise.resolve(null)
    })

    renderStrip()

    const toggleBtn = await screen.findByTestId('toggle-today-digest')
    expect(screen.queryByTestId('today-digest-panel')).toBeNull()

    fireEvent.click(toggleBtn)

    expect(await screen.findByTestId('today-digest-panel')).toBeInTheDocument()
  })

  it('swallows rejected /sessions and /secrets/key-status calls cleanly without throwing', async () => {
    mockedApiGet.mockRejectedValue(new Error('unmocked path'))

    renderStrip()

    expect(screen.getByTestId('mission-control-strip')).toBeInTheDocument()
    expect(screen.queryByTestId('mission-control-dogfood-pill')).toBeNull()
  })

  it('renders 14d Fleet Supervision pill and breakdown drawer when /dashboard/dogfood-telemetry?days=14 returns telemetry (→3097)', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/sessions') {
        return Promise.resolve({ sessions: [] })
      }
      if (path === '/secrets/key-status') {
        return Promise.resolve({ ostk_available: true, humanfile_valid: true })
      }
      if (path === '/dashboard/dogfood-telemetry?days=14') {
        return Promise.resolve({
          window_days: 14,
          freeze_target_days: 14,
          computed_at: '2026-11-13T20:00:00+00:00',
          tracked_spawns: 13,
          handoffs_count: 4,
          verified_merges: 9,
          untracked_terminal_sessions: 3,
          supervised_ratio: 0.812,
          by_runtime: { claude: 8, gemini: 5 },
          by_repo: {
            torios: {
              name: 'torios',
              path: '/Users/torimeyer/claude/torios',
              tracked_spawns: 10,
              handoffs_count: 3,
              verified_merges: 7,
              untracked_terminal_sessions: 2,
              supervised_ratio: 0.833,
              by_runtime: { claude: 6, gemini: 4 },
            },
            'youros-core': {
              name: 'youros-core',
              path: '/Users/torimeyer/work/youros-core',
              tracked_spawns: 3,
              handoffs_count: 1,
              verified_merges: 2,
              untracked_terminal_sessions: 1,
              supervised_ratio: 0.75,
              by_runtime: { claude: 2, gemini: 1 },
            },
          },
          daily: [
            {
              date: '2026-11-13',
              tracked_spawns: 13,
              untracked_terminal_sessions: 3,
              handoffs_count: 4,
              verified_merges: 9,
              supervised_ratio: 0.812,
            },
          ],
        })
      }
      return Promise.resolve(null)
    })

    renderStrip()

    const pill = await screen.findByTestId('mission-control-dogfood-pill')
    expect(mockedApiGet).toHaveBeenCalledWith('/dashboard/dogfood-telemetry?days=14')
    expect(pill).toHaveTextContent('81% supervised')
    expect(pill).toHaveTextContent('13 spawned')
    expect(pill).toHaveTextContent('4 handoffs')
    expect(pill).toHaveTextContent('9 merges')
    expect(pill).toHaveTextContent('3 raw terminal sessions')

    expect(screen.queryByTestId('mission-control-dogfood-drawer')).toBeNull()
    fireEvent.click(pill)

    const drawer = await screen.findByTestId('mission-control-dogfood-drawer')
    expect(drawer).toHaveTextContent('Claude: 8')
    expect(drawer).toHaveTextContent('Gemini: 5')
    expect(drawer).toHaveTextContent('torios')
    expect(drawer).toHaveTextContent('youros-core')

    fireEvent.click(pill)
    expect(screen.queryByTestId('mission-control-dogfood-drawer')).toBeNull()
  })

  it('omits mission-control-dogfood-pill cleanly without error when /dashboard/dogfood-telemetry?days=14 rejects (→3097)', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/sessions') {
        return Promise.resolve({ sessions: [] })
      }
      if (path === '/secrets/key-status') {
        return Promise.resolve({ ostk_available: true, humanfile_valid: true })
      }
      if (path === '/dashboard/dogfood-telemetry?days=14') {
        return Promise.reject(new Error('telemetry endpoint unavailable'))
      }
      return Promise.resolve(null)
    })

    renderStrip()

    const trustPill = await screen.findByTestId('mission-control-trust-pill')
    expect(trustPill).toHaveTextContent('ostk verified')
    expect(mockedApiGet).toHaveBeenCalledWith('/dashboard/dogfood-telemetry?days=14')
    expect(screen.queryByTestId('mission-control-dogfood-pill')).toBeNull()
  })
})
