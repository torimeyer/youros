import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import PlanWavesPanel from './PlanWavesPanel'
import { api } from '../lib/api'
import * as spawnLib from '../lib/spawn'

vi.mock('../lib/api', () => ({
  api: { get: vi.fn() },
}))

vi.mock('../lib/spawn', async () => {
  const actual = await vi.importActual<typeof spawnLib>('../lib/spawn')
  return {
    ...actual,
    buildSpec: vi.fn(),
  }
})

const mockedApiGet = vi.mocked(api.get)
const mockedBuildSpec = vi.mocked(spawnLib.buildSpec)

const makeWavesResponse = (scopeHint: string) => ({
  waves: [
    {
      wave: 1,
      blocked_by_prior: false,
      needles: [
        {
          id: 'needle-1',
          title: 'Fix the thing',
          priority: 'P1',
          scope_hint: scopeHint,
        },
      ],
    },
  ],
  total_needles: 1,
})

const sampleSpecsResponse = {
  docs: [
    {
      path: 'docs/spec/ready-feature.md',
      filename: 'ready-feature.md',
      title: 'Ready feature',
      status: 'ready',
      created_at: '2026-05-01T00:00:00Z',
      promoted_at: '2026-05-02T00:00:00Z',
      body: '',
    },
    {
      path: 'docs/spec/draft-feature.md',
      filename: 'draft-feature.md',
      title: 'Draft feature',
      status: 'draft',
      created_at: '2026-05-01T00:00:00Z',
      promoted_at: '',
      body: '',
    },
    {
      path: 'docs/spec/building-feature.md',
      filename: 'building-feature.md',
      title: 'Building feature',
      status: 'in-progress',
      created_at: '2026-05-01T00:00:00Z',
      promoted_at: '2026-05-02T00:00:00Z',
      body: '',
    },
  ],
}

beforeEach(() => {
  vi.clearAllMocks()
  // Default: any /api call resolves to a sensible empty payload so unrelated
  // tests don't blow up on undefined responses. Individual tests can
  // override via mockImplementation when they care about a specific path.
  mockedApiGet.mockImplementation((path: string) => {
    if (path.includes('/tasks/waves')) return Promise.resolve(makeWavesResponse('general'))
    if (path === '/specs') return Promise.resolve(sampleSpecsResponse)
    if (path === '/tasks') return Promise.resolve({ tasks: [] })
    return Promise.resolve({})
  })
})

describe('PlanWavesPanel — needle title rendering', () => {
  it('strips ⊕-appended body from needle title', async () => {
    mockedApiGet.mockResolvedValue({
      waves: [
        {
          wave: 1,
          blocked_by_prior: false,
          needles: [
            {
              id: 'needle-title-clamp',
              title: 'Onboarding: three options ⊕ Open design question: should we do X or Y?',
              priority: 'P2',
              scope_hint: 'general',
            },
          ],
        },
      ],
      total_needles: 1,
    })
    render(<PlanWavesPanel open={true} onClose={() => {}} />)

    await waitFor(() => screen.getByTestId('wave-needle-needle-title-clamp'))

    const needle = screen.getByTestId('wave-needle-needle-title-clamp')
    const titleEl = needle.querySelector('p.text-slate-800')
    expect(titleEl).not.toBeNull()
    expect(titleEl!.textContent).toBe('Onboarding: three options')
    expect(titleEl!.textContent).not.toContain('⊕')
    expect(titleEl!.textContent).not.toContain('Open design question')
    expect(titleEl!.className).toContain('line-clamp-2')
  })
})

describe('PlanWavesPanel — scope_hint rendering', () => {
  it('strips ⊕ separator characters before display', async () => {
    mockedApiGet.mockResolvedValue(
      makeWavesResponse('frontend/src ⊕ backend/api ⊕ tests/unit'),
    )
    render(<PlanWavesPanel open={true} onClose={() => {}} />)

    await waitFor(() => screen.getByTestId('wave-needle-needle-1'))

    const hint = screen.getByTestId('wave-needle-needle-1').querySelector('p.text-slate-500')
    expect(hint).not.toBeNull()
    expect(hint!.textContent).not.toContain('⊕')
    expect(hint!.textContent).toContain('frontend/src')
    expect(hint!.textContent).toContain('backend/api')
  })

  it('applies line-clamp-2 class to scope_hint element', async () => {
    mockedApiGet.mockResolvedValue(
      makeWavesResponse('some long description text that would wrap over multiple lines'),
    )
    render(<PlanWavesPanel open={true} onClose={() => {}} />)

    await waitFor(() => screen.getByTestId('wave-needle-needle-1'))

    const needle = screen.getByTestId('wave-needle-needle-1')
    const hint = needle.querySelector('p.text-slate-500')
    expect(hint).not.toBeNull()
    expect(hint!.className).toContain('line-clamp-2')
  })
})

describe('PlanWavesPanel — Tasks subtab', () => {
  it('renders without crashing when a task has a null title (→1504)', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/tasks/waves')) return Promise.resolve(makeWavesResponse('general'))
      if (path === '/specs') return Promise.resolve(sampleSpecsResponse)
      if (path === '/tasks')
        return Promise.resolve({
          tasks: [
            { id: '1', title: 'Normal task', status: 'open', priority: 'P1' },
            // closed task with no title — this is what was crashing the panel
            { id: '2', title: null, status: 'closed', priority: 'P2' },
            { id: '3', title: undefined, status: 'closed' },
          ],
        })
      return Promise.resolve({})
    })

    render(<PlanWavesPanel open={true} onClose={() => {}} />)
    await waitFor(() => screen.getByTestId('plan-waves-subtab-tasks'))
    fireEvent.click(screen.getByTestId('plan-waves-subtab-tasks'))

    // Panel must render without throwing — task row 1 is visible.
    await waitFor(() => screen.getByTestId('task-row-1'))
    expect(screen.getByTestId('task-row-1')).toBeInTheDocument()
    // Rows for null-title tasks render without error.
    expect(screen.getByTestId('task-row-2')).toBeInTheDocument()
  })

  it('strips ⊕-suffix from task title in Tasks subtab', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/tasks/waves')) return Promise.resolve(makeWavesResponse('general'))
      if (path === '/specs') return Promise.resolve(sampleSpecsResponse)
      if (path === '/tasks')
        return Promise.resolve({
          tasks: [{ id: 't1', title: 'Ship it ⊕ extra body text', status: 'open', priority: 'P1' }],
        })
      return Promise.resolve({})
    })

    render(<PlanWavesPanel open={true} onClose={() => {}} />)
    fireEvent.click(screen.getByTestId('plan-waves-subtab-tasks'))

    await waitFor(() => screen.getByTestId('task-row-t1'))
    const row = screen.getByTestId('task-row-t1')
    expect(row.querySelector('p.text-slate-800')?.textContent).toBe('Ship it')
  })
})

describe('PlanWavesPanel — subtabs', () => {
  it('renders Specs, Tasks, and Waves subtab buttons', async () => {
    render(<PlanWavesPanel open={true} onClose={() => {}} />)
    await waitFor(() => screen.getByTestId('plan-waves-subtab-waves'))
    expect(screen.getByTestId('plan-waves-subtab-specs')).toBeInTheDocument()
    expect(screen.getByTestId('plan-waves-subtab-tasks')).toBeInTheDocument()
    expect(screen.getByTestId('plan-waves-subtab-waves')).toBeInTheDocument()
  })

  it('defaults to Waves subtab so wave content shows on open', async () => {
    render(<PlanWavesPanel open={true} onClose={() => {}} />)
    // The waves subtab should be the active one and wave content should render.
    await waitFor(() => screen.getByTestId('wave-needle-needle-1'))
    const wavesBtn = screen.getByTestId('plan-waves-subtab-waves')
    expect(wavesBtn.className).toContain('bg-purple-600')
  })
})

describe('PlanWavesPanel — Specs subtab', () => {
  it('fetches /specs and renders spec titles when Specs subtab is clicked', async () => {
    render(<PlanWavesPanel open={true} onClose={() => {}} />)
    await waitFor(() => screen.getByTestId('plan-waves-subtab-specs'))
    fireEvent.click(screen.getByTestId('plan-waves-subtab-specs'))

    await waitFor(() => screen.getByText('Ready feature'))
    expect(screen.getByText('Draft feature')).toBeInTheDocument()
    expect(screen.getByText('Building feature')).toBeInTheDocument()
  })

  it('renders Build button only on Ready specs', async () => {
    render(<PlanWavesPanel open={true} onClose={() => {}} />)
    fireEvent.click(screen.getByTestId('plan-waves-subtab-specs'))
    await waitFor(() => screen.getByText('Ready feature'))

    // Ready spec has a Build button.
    expect(screen.getByTestId('spec-build-button-docs/spec/ready-feature.md')).toBeInTheDocument()
    // Draft and Building specs do NOT.
    expect(screen.queryByTestId('spec-build-button-docs/spec/draft-feature.md')).toBeNull()
    expect(screen.queryByTestId('spec-build-button-docs/spec/building-feature.md')).toBeNull()
  })

  it('clicking Build calls buildSpec with the spec path', async () => {
    mockedBuildSpec.mockResolvedValue({
      status: 'ok',
      agents: ['agent-a'],
      message: 'Started 1 agent.',
    })
    render(<PlanWavesPanel open={true} onClose={() => {}} />)
    fireEvent.click(screen.getByTestId('plan-waves-subtab-specs'))
    await waitFor(() => screen.getByText('Ready feature'))

    fireEvent.click(screen.getByTestId('spec-build-button-docs/spec/ready-feature.md'))

    await waitFor(() => {
      expect(mockedBuildSpec).toHaveBeenCalled()
    })
    expect(mockedBuildSpec.mock.calls[0][0]).toBe('docs/spec/ready-feature.md')
  })

  it('shows ConflictDialog when buildSpec returns a conflict', async () => {
    mockedBuildSpec.mockResolvedValue({
      status: 'conflict',
      conflicts: [
        {
          requested: 'api/foo.py',
          held_by_spawn: 'agent-other',
          held_path: 'api/foo.py',
        },
      ],
    })
    render(<PlanWavesPanel open={true} onClose={() => {}} />)
    fireEvent.click(screen.getByTestId('plan-waves-subtab-specs'))
    await waitFor(() => screen.getByText('Ready feature'))

    fireEvent.click(screen.getByTestId('spec-build-button-docs/spec/ready-feature.md'))

    await waitFor(() => screen.getByTestId('conflict-dialog'))
    expect(screen.getByTestId('conflict-dialog-header')).toHaveTextContent('1')
    expect(screen.getAllByTestId('conflict-row')).toHaveLength(1)
  })

  it('Wait button dismisses the ConflictDialog without re-spawning', async () => {
    mockedBuildSpec.mockResolvedValue({
      status: 'conflict',
      conflicts: [
        { requested: 'api/foo.py', held_by_spawn: 'agent-other', held_path: 'api/foo.py' },
      ],
    })
    render(<PlanWavesPanel open={true} onClose={() => {}} />)
    fireEvent.click(screen.getByTestId('plan-waves-subtab-specs'))
    await waitFor(() => screen.getByText('Ready feature'))

    fireEvent.click(screen.getByTestId('spec-build-button-docs/spec/ready-feature.md'))
    await waitFor(() => screen.getByTestId('conflict-dialog'))
    expect(mockedBuildSpec).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByTestId('conflict-dialog-wait'))
    await waitFor(() => expect(screen.queryByTestId('conflict-dialog')).toBeNull())
    // No second buildSpec call.
    expect(mockedBuildSpec).toHaveBeenCalledTimes(1)
  })
})
