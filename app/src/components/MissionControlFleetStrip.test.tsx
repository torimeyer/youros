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

  it('renders inline Re-sign HUMANFILE button when humanfile_valid is false and refreshes key-status on success (->3115)', async () => {
    let signed = false
    mockedApiGet.mockImplementation((urlPath: string) => {
      if (urlPath === '/sessions') {
        return Promise.resolve({ sessions: [] })
      }
      if (urlPath === '/secrets/key-status') {
        return Promise.resolve({
          ostk_available: true,
          humanfile_valid: signed,
          humanfile_warning: signed ? null : 'HUMANFILE was modified since it was signed.',
        })
      }
      return Promise.resolve(null)
    })
    mockedApiPost.mockImplementation((urlPath: string) => {
      if (urlPath === '/secrets/trust-sign') {
        signed = true
        return Promise.resolve({
          signed: true,
          humanfile_valid: true,
          trust: { ostk_available: true, humanfile_valid: true, humanfile_warning: null },
          message: 'HUMANFILE signed and verified.',
          manual_command: 'ostk trust sign --humanfile',
          requires_tty: false,
        })
      }
      return Promise.resolve({ ok: true })
    })

    renderStrip()

    const resignBtn = await screen.findByTestId('mission-control-resign-btn')
    expect(resignBtn).toHaveTextContent(/Re-sign HUMANFILE/i)

    fireEvent.click(resignBtn)

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/secrets/trust-sign')
    })
    await waitFor(() => {
      expect(screen.getByTestId('mission-control-trust-pill')).toHaveTextContent('ostk verified')
    })
    expect(screen.queryByTestId('mission-control-resign-btn')).toBeNull()
  })

  it('surfaces passphrase fallback message when inline Re-sign HUMANFILE returns signed: false (->3115)', async () => {
    mockedApiGet.mockImplementation((urlPath: string) => {
      if (urlPath === '/sessions') {
        return Promise.resolve({ sessions: [] })
      }
      if (urlPath === '/secrets/key-status') {
        return Promise.resolve({
          ostk_available: true,
          humanfile_valid: false,
          humanfile_warning: 'HUMANFILE was modified since it was signed.',
        })
      }
      return Promise.resolve(null)
    })
    mockedApiPost.mockImplementation((urlPath: string) => {
      if (urlPath === '/secrets/trust-sign') {
        return Promise.resolve({
          signed: false,
          humanfile_valid: false,
          trust: { ostk_available: true, humanfile_valid: false },
          message: 'Signing key requires an interactive passphrase.',
          manual_command: 'ostk trust sign --humanfile',
          requires_tty: true,
        })
      }
      return Promise.resolve({ ok: true })
    })

    renderStrip()

    const resignBtn = await screen.findByTestId('mission-control-resign-btn')
    fireEvent.click(resignBtn)

    const feedback = await screen.findByTestId('mission-control-trust-sign-feedback')
    expect(feedback).toHaveTextContent(/interactive passphrase/i)
    expect(feedback).toHaveTextContent('ostk trust sign --humanfile')
  })

  it('fetches /secrets/roster, renders attestation summary pill + IMPOSTOR SEAT DETECTED badge, and matches verdicts onto session rows (->3115)', async () => {
    mockedApiGet.mockImplementation((urlPath: string) => {
      if (urlPath === '/sessions') {
        return Promise.resolve({
          sessions: [
            {
              id: 'claude-seat-1',
              name: 'claude-seat-1',
              runtime: 'claude',
              pid: 10421,
              status: 'active',
              activity: 'Editing secrets.py',
            },
            {
              id: 'sess-g2',
              name: 'gemini-auditor',
              runtime: 'gemini',
              status: 'active',
              activity: 'Auditing roster',
            },
            {
              id: 'rogue-worker',
              name: 'rogue-worker',
              runtime: 'claude',
              pid: 22001,
              status: 'active',
              activity: 'Suspicious write',
            },
          ],
          conflicts: [],
        })
      }
      if (urlPath === '/secrets/key-status') {
        return Promise.resolve({ ostk_available: true, humanfile_valid: true })
      }
      if (urlPath === '/secrets/roster') {
        return Promise.resolve({
          available: true,
          seats: [
            {
              seat: 'claude-worker-pid',
              pid: 10421,
              health: 'active',
              age: '45s',
              session: null,
              role: 'builder',
              serving: '->3115',
              lineage: 'root->claude',
              wake: 'live',
              ctx_pct: '18%',
              attested: '45s',
              verdict: 'VALID[attested]',
              verdict_category: 'valid',
            },
            {
              seat: 'gemini-seat-2',
              pid: 10899,
              health: 'active',
              age: '12s',
              session: 'sess-g2',
              role: 'auditor',
              serving: null,
              lineage: 'root->gemini',
              wake: 'poll',
              ctx_pct: '7%',
              attested: '12s',
              verdict: 'VALID',
              verdict_category: 'valid',
            },
            {
              seat: 'rogue-worker',
              pid: 22001,
              health: 'active',
              age: '3s',
              session: 'sess-fake',
              role: null,
              serving: null,
              lineage: 'lineage-unknown',
              wake: 'poll?',
              ctx_pct: '0%',
              attested: '-',
              verdict: 'IMPOSTOR[session-contradiction]',
              verdict_category: 'impostor',
            },
          ],
          counts: { total: 3, valid: 2, impostor: 1, unverifiable: 0 },
        })
      }
      return Promise.resolve(null)
    })

    renderStrip()

    expect(mockedApiGet).toHaveBeenCalledWith('/secrets/roster')

    const rosterPill = await screen.findByTestId('mission-control-roster-pill')
    expect(rosterPill).toHaveTextContent(/Seats:\s*2 attested/i)
    expect(rosterPill).toHaveTextContent(/3 active/i)

    const impostorBadge = await screen.findByTestId('mission-control-impostor-badge')
    expect(impostorBadge).toHaveTextContent('IMPOSTOR SEAT DETECTED')

    const seat1 = screen.getByTestId('mission-control-seat-claude-seat-1')
    expect(within(seat1).getByTestId('seat-verdict-badge')).toHaveTextContent('VALID[attested]')

    const seat2 = screen.getByTestId('mission-control-seat-sess-g2')
    expect(within(seat2).getByTestId('seat-verdict-badge')).toHaveTextContent('VALID')

    const rogueSeat = screen.getByTestId('mission-control-seat-rogue-worker')
    expect(within(rogueSeat).getByTestId('seat-verdict-badge')).toHaveTextContent(
      'IMPOSTOR[session-contradiction]',
    )
  })

  it('distinguishes standalone terminal (external: true) and claude --bg (kind: background) sessions with badges (->3121)', async () => {
    mockedApiGet.mockImplementation((urlPath: string) => {
      if (urlPath === '/sessions') {
        return Promise.resolve({
          sessions: [
            {
              id: 'claude-term-17',
              name: 'torios-17',
              label: 'torios-17',
              runtime: 'claude',
              kind: 'interactive',
              external: true,
              pid: 14165,
              cwd: '/Users/torimeyer/claude/torios',
              status: 'idle',
              activity: '',
              recent_files: [],
            },
            {
              id: 'claude-bg-99',
              name: 'claude-bg-99',
              label: 'claude-bg-99',
              runtime: 'claude',
              kind: 'background',
              external: true,
              pid: 24100,
              cwd: '/Users/torimeyer/claude/torios',
              status: 'active',
              activity: 'Running background build',
              recent_files: [],
            },
            {
              id: 'gemini-term-42',
              name: 'gemini-term-42',
              label: 'gemini-term-42',
              runtime: 'gemini',
              kind: 'interactive',
              external: true,
              pid: null,
              cwd: null,
              project_slug: 'youros-core',
              status: 'active',
              activity: 'Inspecting session files',
              recent_files: [],
            },
            {
              id: 'ostk-fleet-1',
              name: 'ostk-fleet-1',
              label: 'ostk-fleet-1',
              runtime: 'claude',
              kind: 'interactive',
              external: false,
              status: 'active',
              activity: 'Supervised fleet seat',
              recent_files: [],
            },
          ],
          conflicts: [],
        })
      }
      if (urlPath === '/secrets/key-status') {
        return Promise.resolve({ ostk_available: true, humanfile_valid: true })
      }
      return Promise.resolve(null)
    })

    renderStrip()

    const termClaude = await screen.findByTestId('mission-control-seat-claude-term-17')
    expect(within(termClaude).getByText('Claude')).toBeInTheDocument()
    expect(within(termClaude).getByTestId('seat-terminal-badge')).toHaveTextContent('terminal')
    expect(within(termClaude).queryByTestId('seat-bg-badge')).toBeNull()
    expect(within(termClaude).getByTestId('seat-repo-badge')).toHaveTextContent('torios')

    const bgClaude = screen.getByTestId('mission-control-seat-claude-bg-99')
    expect(within(bgClaude).getByText('Claude')).toBeInTheDocument()
    expect(within(bgClaude).getByTestId('seat-bg-badge')).toHaveTextContent('bg')
    expect(within(bgClaude).queryByTestId('seat-terminal-badge')).toBeNull()

    const termGemini = screen.getByTestId('mission-control-seat-gemini-term-42')
    expect(within(termGemini).getByText('Gemini')).toBeInTheDocument()
    expect(within(termGemini).getByTestId('seat-terminal-badge')).toHaveTextContent('terminal')
    expect(within(termGemini).getByTestId('seat-repo-badge')).toHaveTextContent('youros-core')

    const supervised = screen.getByTestId('mission-control-seat-ostk-fleet-1')
    expect(within(supervised).queryByTestId('seat-terminal-badge')).toBeNull()
    expect(within(supervised).queryByTestId('seat-bg-badge')).toBeNull()
  })
})
