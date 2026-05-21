import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Specs, { displayStatus, SpecBody } from './Specs'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

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

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)
const mockedApiPost = vi.mocked(api.post)

const mockDocsResponse = {
  docs: [
    {
      path: 'docs/draft/onboarding-flow.md',
      filename: 'onboarding-flow.md',
      title: 'onboarding flow',
      status: 'draft',
      created_at: '2026-04-01T00:00:00Z',
      promoted_at: '',
      body: 'Plan the onboarding experience.\n\n- [ ] wizard shows on first launch\n- [ ] user can skip',
      acceptance_criteria: [
        { text: 'wizard shows on first launch', checked: false },
        { text: 'user can skip', checked: false },
      ],
    },
    {
      path: 'docs/spec/auth-system.md',
      filename: 'auth-system.md',
      title: 'auth system',
      status: 'spec',
      created_at: '2026-03-15T00:00:00Z',
      promoted_at: '2026-03-20T00:00:00Z',
      body: '## Overview\nAuth system design.\n\n- [ ] sign in flow\n- [ ] sign out flow',
      task_summary: { total: 2, open: 2, closed: 0 },
    },
    {
      path: 'docs/spec/dashboard-redesign.md',
      filename: 'dashboard-redesign.md',
      title: 'dashboard redesign',
      status: 'in-progress',
      created_at: '2026-02-10T00:00:00Z',
      promoted_at: '2026-02-15T00:00:00Z',
      body: '- [x] new layout\n- [ ] dark mode',
      task_summary: { total: 4, open: 2, closed: 2 },
      acceptance_criteria: [
        { text: 'new layout', checked: true },
        { text: 'dark mode', checked: false },
      ],
    },
    {
      path: 'docs/spec/settings-page.md',
      filename: 'settings-page.md',
      title: 'settings page',
      status: 'complete',
      created_at: '2026-01-05T00:00:00Z',
      promoted_at: '2026-01-10T00:00:00Z',
      body: '- [x] user preferences\n- [x] theme toggle',
      task_summary: { total: 2, open: 0, closed: 2 },
      acceptance_criteria: [
        { text: 'user preferences', checked: true },
        { text: 'theme toggle', checked: true },
      ],
    },
  ],
}

function renderSpecs() {
  return render(
    <MemoryRouter>
      <Specs />
    </MemoryRouter>
  )
}

describe('Specs page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/specs') return Promise.resolve(mockDocsResponse)
      // Default: no plan templates. The "Start from a template" tests
      // below override this per-test.
      if (path === '/specs/templates') return Promise.resolve({ templates: [] })
      // Return empty tasks for linked tasks endpoint
      if (path.includes('/tasks')) return Promise.resolve({ tasks: [] })
      return Promise.resolve({})
    })
    mockedApiPost.mockResolvedValue({ result: 'ok', task_ids: [] })
  })

  it('renders specs from API data', async () => {
    renderSpecs()
    await waitFor(() => expect(mockedApiGet).toHaveBeenCalledWith('/specs'))
    fireEvent.click(screen.getByTestId('stage-filter-all'))
    await waitFor(() => {
      expect(screen.getByText('onboarding flow')).toBeInTheDocument()
    })
    expect(screen.getByText('auth system')).toBeInTheDocument()
  })

  it('calls api.get with /specs on mount', async () => {
    renderSpecs()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/specs')
    })
  })

  it('shows the page title', async () => {
    renderSpecs()

    const heading = screen.getByRole('heading', { name: 'Specs' })
    expect(heading).toBeInTheDocument()
  })

  it('onboarding banner uses "Specs" vocabulary and light-mode-readable colors', async () => {
    localStorage.removeItem('specs-onboarding-dismissed')
    renderSpecs()

    const banner = await screen.findByRole('heading', { name: 'What are Specs?' })
    expect(banner).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'What are Plans?' })).toBeNull()
    expect(screen.getByText(/Specs let you define/i)).toBeInTheDocument()

    const bannerContainer = banner.closest('div')!
    expect(bannerContainer.className).toMatch(/bg-slate-100/)
    expect(banner.className).toMatch(/text-slate-900/)
  })

  it('count badge shows correct total', async () => {
    renderSpecs()

    await waitFor(() => {
      const badge = document.querySelector('.bg-blue-500.text-white.text-xs.rounded-full')
      expect(badge).not.toBeNull()
      expect(badge!.textContent).toBe('4')
    })
  })

  it('shows filter tabs with all status counts', async () => {
    renderSpecs()
    await waitFor(() => expect(mockedApiGet).toHaveBeenCalledWith('/specs'))
    // Stage filter pills: All, Draft, Ready, In Progress
    expect(screen.getByTestId('stage-filter-all')).toBeInTheDocument()
    expect(screen.getByTestId('stage-filter-draft')).toBeInTheDocument()
    expect(screen.getByTestId('stage-filter-ready')).toBeInTheDocument()
    expect(screen.getByTestId('stage-filter-in_progress')).toBeInTheDocument()
  })

  it('New Spec button is always enabled', async () => {
    renderSpecs()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })

    const button = screen.getByRole('button', { name: 'New Spec' })
    expect(button).not.toBeDisabled()
  })

  it('clicking New Spec opens the SpecWizard', async () => {
    renderSpecs()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })

    expect(screen.queryByTestId('spec-wizard')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'New Spec' }))

    expect(screen.getByTestId('spec-wizard')).toBeInTheDocument()
  })

  it('shows status badges on spec cards', async () => {
    renderSpecs()
    await waitFor(() => expect(mockedApiGet).toHaveBeenCalledWith('/specs'))
    fireEvent.click(screen.getByTestId('stage-filter-all'))
    await waitFor(() => {
      expect(screen.getByText('onboarding flow')).toBeInTheDocument()
    })

    const badges = screen.getAllByTestId('status-badge')
    expect(badges.length).toBe(4)
    expect(badges.map(b => b.textContent)).toContain('Draft')
    expect(badges.map(b => b.textContent)).toContain('Ready')
    expect(badges.map(b => b.textContent)).toContain('Building')
    expect(badges.map(b => b.textContent)).toContain('Done')
  })

  it('shows task progress bar for specs with task summary', async () => {
    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('dashboard redesign')).toBeInTheDocument()
    })

    const progressBars = screen.getAllByTestId('task-progress')
    expect(progressBars.length).toBeGreaterThanOrEqual(1)
    // dashboard redesign has 2/4 tasks
    expect(screen.getByText('2/4 tasks')).toBeInTheDocument()
  })

  it('expands detail view on card click', async () => {
    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    // Click on auth system card
    const cards = screen.getAllByTestId('spec-card')
    const authCard = cards.find(c => c.textContent?.includes('auth system'))!
    fireEvent.click(authCard)

    await waitFor(() => {
      expect(screen.getByTestId('spec-detail')).toBeInTheDocument()
    })
  })

  it('shows acceptance criteria in expanded detail', async () => {
    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    // Expand auth system
    const cards = screen.getAllByTestId('spec-card')
    const authCard = cards.find(c => c.textContent?.includes('auth system'))!
    fireEvent.click(authCard)

    await waitFor(() => {
      expect(screen.getByTestId('acceptance-criteria')).toBeInTheDocument()
    })

    // Acceptance criteria parsed from body
    expect(screen.getByText('sign in flow')).toBeInTheDocument()
    expect(screen.getByText('sign out flow')).toBeInTheDocument()
  })

  it('shows Build it button for ready specs and no Promote or Break into Tasks', async () => {
    // Verify button was removed in a later refactor.
    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    // Expand auth system (status: "spec" normalized to "ready")
    const cards = screen.getAllByTestId('spec-card')
    const authCard = cards.find(c => c.textContent?.includes('auth system'))!
    fireEvent.click(authCard)

    await waitFor(() => {
      expect(screen.getByTestId('spec-detail')).toBeInTheDocument()
    })

    expect(screen.getByText('Build it')).toBeInTheDocument()
    // Wave 2: Break into Tasks and Promote to Spec are gone from Ready.
    expect(screen.queryByText('Break into Tasks')).not.toBeInTheDocument()
    expect(screen.queryByText('Promote to Spec')).not.toBeInTheDocument()
    expect(screen.getByTestId('unlock-spec-button')).toBeInTheDocument()
  })

  it('shows Build it button for in-progress specs', async () => {
    // Verify button was removed in a later refactor.
    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('dashboard redesign')).toBeInTheDocument()
    })

    // Expand dashboard redesign (status: "in-progress")
    const cards = screen.getAllByTestId('spec-card')
    const dashCard = cards.find(c => c.textContent?.includes('dashboard redesign'))!
    fireEvent.click(dashCard)

    await waitFor(() => {
      expect(screen.getByTestId('spec-detail')).toBeInTheDocument()
    })

    expect(screen.getByText('Build it')).toBeInTheDocument()
  })

  it('complete specs do not show Build it button', async () => {
    // Verify button was removed in a later refactor. Complete specs
    // now render no action button at all.
    renderSpecs()
    await waitFor(() => expect(mockedApiGet).toHaveBeenCalledWith('/specs'))
    fireEvent.click(screen.getByTestId('stage-filter-all'))
    await waitFor(() => {
      expect(screen.getByText('settings page')).toBeInTheDocument()
    })

    // Expand settings page (status: "complete")
    const cards = screen.getAllByTestId('spec-card')
    const settingsCard = cards.find(c => c.textContent?.includes('settings page'))!
    fireEvent.click(settingsCard)

    await waitFor(() => {
      expect(screen.getByTestId('spec-detail')).toBeInTheDocument()
    })

    // Should NOT have Build it for complete specs
    expect(screen.queryByText('Build it')).not.toBeInTheDocument()
  })

  it('draft card has Promote to Spec button in expanded view', async () => {
    renderSpecs()
    await waitFor(() => expect(mockedApiGet).toHaveBeenCalledWith('/specs'))
    fireEvent.click(screen.getByTestId('stage-filter-all'))
    await waitFor(() => {
      expect(screen.getByText('onboarding flow')).toBeInTheDocument()
    })

    // Expand draft
    const cards = screen.getAllByTestId('spec-card')
    const draftCard = cards.find(c => c.textContent?.includes('onboarding flow'))!
    fireEvent.click(draftCard)

    await waitFor(() => {
      expect(screen.getByTestId('spec-detail')).toBeInTheDocument()
    })

    expect(screen.getByText('Promote to Spec')).toBeInTheDocument()
  })

  it('Promote to Spec calls POST /specs/promote', async () => {
    renderSpecs()
    await waitFor(() => expect(mockedApiGet).toHaveBeenCalledWith('/specs'))
    fireEvent.click(screen.getByTestId('stage-filter-all'))
    await waitFor(() => {
      expect(screen.getByText('onboarding flow')).toBeInTheDocument()
    })

    // Expand draft
    const cards = screen.getAllByTestId('spec-card')
    const draftCard = cards.find(c => c.textContent?.includes('onboarding flow'))!
    fireEvent.click(draftCard)

    await waitFor(() => {
      expect(screen.getByText('Promote to Spec')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Promote to Spec'))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/specs/promote', {
        path: 'docs/draft/onboarding-flow.md',
      })
    })
  })

  // Regression: a draft without any "- [ ]" checklist items must not be
  // promotable. Clicking would produce a predictable "Make sure it has
  // acceptance criteria" error, which is bad UX. The button is disabled
  // with a tooltip explaining what the user is waiting for.
  it('Promote to Spec is disabled when a draft has no acceptance criteria', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/specs') {
        return Promise.resolve({
          docs: [
            {
              path: 'docs/draft/no-ac.md',
              filename: 'no-ac.md',
              title: 'no ac draft',
              status: 'draft',
              created_at: '2026-04-01T00:00:00Z',
              promoted_at: '',
              body: 'Still generating acceptance criteria, so no checkboxes yet.',
            },
          ],
        })
      }
      if (path === '/specs/templates') return Promise.resolve({ templates: [] })
      if (path.includes('/tasks')) return Promise.resolve({ tasks: [] })
      return Promise.resolve({})
    })

    renderSpecs()
    await waitFor(() => expect(mockedApiGet).toHaveBeenCalledWith('/specs'))
    fireEvent.click(screen.getByTestId('stage-filter-all'))
    await waitFor(() => {
      expect(screen.getByText('no ac draft')).toBeInTheDocument()
    })

    const cards = screen.getAllByTestId('spec-card')
    const draftCard = cards.find(c => c.textContent?.includes('no ac draft'))!
    fireEvent.click(draftCard)

    const promoteBtn = await screen.findByTestId('promote-button')
    expect(promoteBtn).toBeDisabled()
    expect(promoteBtn).toHaveAttribute(
      'title',
      expect.stringMatching(/Waiting for acceptance criteria/i) as unknown as string
    )
    // Clicking the disabled button must not fire the promote request.
    fireEvent.click(promoteBtn)
    expect(mockedApiPost).not.toHaveBeenCalledWith(
      '/specs/promote',
      expect.anything()
    )
  })

  // Regression: once acceptance criteria appear in the draft body, the
  // Promote to Spec button must become clickable. This asserts the
  // fallback that parses "- [ ]" lines out of the body works before the
  // server has populated acceptance_criteria.
  it('Promote to Spec is enabled once at least one "- [ ]" line exists', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/specs') {
        return Promise.resolve({
          docs: [
            {
              path: 'docs/draft/has-ac.md',
              filename: 'has-ac.md',
              title: 'has ac draft',
              status: 'draft',
              created_at: '2026-04-01T00:00:00Z',
              promoted_at: '',
              body: 'Do the thing.\n\n- [ ] first acceptance criterion',
            },
          ],
        })
      }
      if (path === '/specs/templates') return Promise.resolve({ templates: [] })
      if (path.includes('/tasks')) return Promise.resolve({ tasks: [] })
      return Promise.resolve({})
    })

    renderSpecs()
    await waitFor(() => expect(mockedApiGet).toHaveBeenCalledWith('/specs'))
    fireEvent.click(screen.getByTestId('stage-filter-all'))
    await waitFor(() => {
      expect(screen.getByText('has ac draft')).toBeInTheDocument()
    })

    const cards = screen.getAllByTestId('spec-card')
    const draftCard = cards.find(c => c.textContent?.includes('has ac draft'))!
    fireEvent.click(draftCard)

    const promoteBtn = await screen.findByTestId('promote-button')
    expect(promoteBtn).not.toBeDisabled()
  })

  // Obsolete: the Verify button was removed. Previously this test checked
  // that Verify was disabled on a ready spec with zero linked tasks.

  // Wave 2: "Break into Tasks" button is removed from Ready. Decompose
  // happens automatically inside /specs/{path}/build, so the UI only
  // exposes the one-click "Build it" action. The decompose endpoint is
  // still exported by the backend for compat, but the frontend does not
  // call it directly anymore.

  it('Build This calls POST /specs/{encodedPath}/build', async () => {
    mockedApiPost.mockResolvedValue({ agents: ['agent-1'], message: 'Build started' })

    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    // Expand ready spec
    const cards = screen.getAllByTestId('spec-card')
    const authCard = cards.find(c => c.textContent?.includes('auth system'))!
    fireEvent.click(authCard)

    await waitFor(() => {
      expect(screen.getByText('Build it')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Build it'))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith(
        `/specs/${encodeURIComponent('docs/spec/auth-system.md')}/build`
      )
    })
  })

  it('Wave 2: Ready plan shows Build it but not Promote to Spec, and one click fires a single build request', async () => {
    // Build returns one agent so we can assert the status transition.
    mockedApiPost.mockResolvedValue({ agents: ['spec-auth-1'], message: 'Spawned 1 agent.' })
    // After the build call resolves, the second /specs fetch should
    // return the plan in in-progress state so the UI reflects the new
    // lifecycle stage. Each API instance in the test mocks differs by
    // call count.
    let specsCallCount = 0
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/specs') {
        specsCallCount += 1
        if (specsCallCount === 1) {
          return Promise.resolve(mockDocsResponse)
        }
        // After Build it, auth system is in-progress.
        return Promise.resolve({
          docs: mockDocsResponse.docs.map((d) =>
            d.path === 'docs/spec/auth-system.md'
              ? { ...d, status: 'in-progress', task_summary: { total: 1, open: 1, closed: 0 } }
              : d
          ),
        })
      }
      if (path.includes('/tasks')) return Promise.resolve({ tasks: [] })
      return Promise.resolve({})
    })

    renderSpecs()
    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    const cards = screen.getAllByTestId('spec-card')
    const authCard = cards.find((c) => c.textContent?.includes('auth system'))!
    fireEvent.click(authCard)

    await waitFor(() => {
      expect(screen.getByTestId('spec-detail')).toBeInTheDocument()
    })

    // Ready plan must show Build it and must NOT show Promote to Spec
    // or Break into Tasks. Unlock and edit must be visible for demote.
    expect(screen.getByText('Build it')).toBeInTheDocument()
    expect(screen.queryByText('Promote to Spec')).not.toBeInTheDocument()
    expect(screen.queryByText('Break into Tasks')).not.toBeInTheDocument()
    expect(screen.getByTestId('unlock-spec-button')).toBeInTheDocument()

    // One click must fire exactly one POST to /specs/.../build.
    const postCallsBefore = mockedApiPost.mock.calls.length
    fireEvent.click(screen.getByText('Build it'))

    await waitFor(() => {
      const buildCalls = mockedApiPost.mock.calls.filter(
        (c) => typeof c[0] === 'string' && c[0].endsWith('/build')
      )
      expect(buildCalls.length).toBe(1)
    })
    // No stray decompose or promote calls slipped in.
    const decomposeCalls = mockedApiPost.mock.calls.filter(
      (c) => c[0] === '/specs/decompose'
    )
    const promoteCalls = mockedApiPost.mock.calls.filter(
      (c) => c[0] === '/specs/promote'
    )
    expect(decomposeCalls.length).toBe(0)
    expect(promoteCalls.length).toBe(0)
    // Build fired exactly one new POST on top of the baseline.
    expect(mockedApiPost.mock.calls.length).toBe(postCallsBefore + 1)

    // After the Build resolves the UI should reflect the new state.
    await waitFor(() => {
      const badges = screen.getAllByTestId('status-badge')
      expect(badges.map((b) => b.textContent)).toContain('Building')
    })
  })

  it('Wave 2: Unlock and edit calls POST /specs/{encodedPath}/unlock', async () => {
    mockedApiPost.mockResolvedValue({ result: 'docs/draft/auth-system.md', path: 'docs/draft/auth-system.md' })

    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    const cards = screen.getAllByTestId('spec-card')
    const authCard = cards.find((c) => c.textContent?.includes('auth system'))!
    fireEvent.click(authCard)

    await waitFor(() => {
      expect(screen.getByTestId('unlock-spec-button')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('unlock-spec-button'))

    await waitFor(() => {
      const unlockCalls = mockedApiPost.mock.calls.filter(
        (c) => typeof c[0] === 'string' && c[0].endsWith('/unlock')
      )
      expect(unlockCalls.length).toBe(1)
      expect(unlockCalls[0][0]).toBe(
        `/specs/${'docs/spec/auth-system.md'.split('/').map(encodeURIComponent).join('/')}/unlock`
      )
    })
  })

  it('Build This renders returned agent names and links to Agents', async () => {
    mockedApiPost.mockResolvedValue({
      agents: ['spec-auth-01', 'spec-auth-02', 'spec-auth-03'],
      message: 'Spawned 3 agents to build this spec. Watch the Agents tab.',
    })
    // After a successful build, the real backend flips the spec's
    // status to "in-progress" and the UI auto-switches to the Building
    // tab. Mirror that here so the spec remains visible under the new
    // tab filter.
    let specsCallCount = 0
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/specs') {
        specsCallCount += 1
        if (specsCallCount === 1) return Promise.resolve(mockDocsResponse)
        return Promise.resolve({
          docs: mockDocsResponse.docs.map((d) =>
            d.path === 'docs/spec/auth-system.md'
              ? { ...d, status: 'in-progress' }
              : d
          ),
        })
      }
      if (path === '/specs/templates') return Promise.resolve({ templates: [] })
      if (path.includes('/tasks')) return Promise.resolve({ tasks: [] })
      return Promise.resolve({})
    })

    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    const cards = screen.getAllByTestId('spec-card')
    const authCard = cards.find(c => c.textContent?.includes('auth system'))!
    fireEvent.click(authCard)

    await waitFor(() => {
      expect(screen.getByText('Build it')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Build it'))

    await waitFor(() => {
      expect(screen.getByTestId('build-result')).toBeInTheDocument()
    })

    const names = screen.getAllByTestId('build-agent-name').map(el => el.textContent)
    expect(names).toEqual(['spec-auth-01', 'spec-auth-02', 'spec-auth-03'])
    expect(screen.getByTestId('build-agents-link')).toBeInTheDocument()
  })

  it('Build It shows no-ACs info card when spec has no unchecked acceptance criteria', async () => {
    mockedApiPost.mockResolvedValue({
      agents: [],
      message: 'This plan has no unchecked acceptance criteria. Add at least one, then click Build.',
      has_unchecked_acs: false,
    })

    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    const cards = screen.getAllByTestId('spec-card')
    const authCard = cards.find(c => c.textContent?.includes('auth system'))!
    fireEvent.click(authCard)

    await waitFor(() => {
      expect(screen.getByText('Build it')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Build it'))

    await waitFor(() => {
      expect(screen.getByTestId('build-no-acs-info')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('build-agent-name')).not.toBeInTheDocument()
    // The info card must be muted (slate), never red.
    const card = screen.getByTestId('build-no-acs-info')
    expect(card.className).not.toContain('red')
    expect(card.className).toContain('slate')
  })

  // Obsolete: the Verify button was removed. Previously this test
  // confirmed clicking Verify posted to /specs/{path}/verify.


  it('shows empty state when no specs exist', async () => {
    mockedApiGet.mockResolvedValue({ docs: [] })

    renderSpecs()

    await waitFor(() => {
      expect(screen.getByTestId('empty-state')).toBeInTheDocument()
      expect(screen.getByText('No specs yet')).toBeInTheDocument()
      expect(screen.getByText('Create a draft to start planning.')).toBeInTheDocument()
    })
  })

  it('tabs filter by status', async () => {
    renderSpecs()
    await waitFor(() => expect(mockedApiGet).toHaveBeenCalledWith('/specs'))

    // Default "all" filter shows every spec
    await waitFor(() => expect(screen.getByText('auth system')).toBeInTheDocument())
    expect(screen.getByText('dashboard redesign')).toBeInTheDocument()
    expect(screen.getByText('onboarding flow')).toBeInTheDocument()
    expect(screen.getByText('settings page')).toBeInTheDocument()

    // Click Draft stage pill
    fireEvent.click(screen.getByTestId('stage-filter-draft'))
    await waitFor(() => expect(screen.getByText('onboarding flow')).toBeInTheDocument())
    expect(screen.queryByText('auth system')).not.toBeInTheDocument()
    expect(screen.queryByText('dashboard redesign')).not.toBeInTheDocument()

    // Click Ready stage pill
    fireEvent.click(screen.getByTestId('stage-filter-ready'))
    await waitFor(() => expect(screen.getByText('auth system')).toBeInTheDocument())
    expect(screen.queryByText('onboarding flow')).not.toBeInTheDocument()

    // Click In Progress stage pill
    fireEvent.click(screen.getByTestId('stage-filter-in_progress'))
    await waitFor(() => expect(screen.getByText('dashboard redesign')).toBeInTheDocument())
    expect(screen.queryByText('auth system')).not.toBeInTheDocument()

    // Click All stage pill
    fireEvent.click(screen.getByTestId('stage-filter-all'))
    await waitFor(() => expect(screen.getByText('onboarding flow')).toBeInTheDocument())
    expect(screen.getByText('auth system')).toBeInTheDocument()
    expect(screen.getByText('dashboard redesign')).toBeInTheDocument()
    expect(screen.getByText('settings page')).toBeInTheDocument()
  })

  it('handles API error on initial load gracefully', async () => {
    mockedApiGet.mockRejectedValue(new Error('Network error'))

    renderSpecs()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/specs')
    })

    // Page title should still be visible
    expect(screen.getByRole('heading', { name: 'Specs' })).toBeInTheDocument()
  })

  it('shows promote error about acceptance criteria', async () => {
    mockedApiPost.mockRejectedValueOnce(new Error('Draft must contain at least one unchecked checkbox'))

    renderSpecs()
    await waitFor(() => expect(mockedApiGet).toHaveBeenCalledWith('/specs'))
    fireEvent.click(screen.getByTestId('stage-filter-all'))
    await waitFor(() => {
      expect(screen.getByText('onboarding flow')).toBeInTheDocument()
    })

    // Expand draft and click promote
    const cards = screen.getAllByTestId('spec-card')
    const draftCard = cards.find(c => c.textContent?.includes('onboarding flow'))!
    fireEvent.click(draftCard)

    await waitFor(() => {
      expect(screen.getByText('Promote to Spec')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Promote to Spec'))

    await waitFor(() => {
      expect(
        screen.getByText('This draft needs at least one checklist item (acceptance criteria) before it can be promoted.')
      ).toBeInTheDocument()
    })
  })

  it('collapses detail view on second click', async () => {
    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    const cards = screen.getAllByTestId('spec-card')
    const authCard = cards.find(c => c.textContent?.includes('auth system'))!

    // Expand
    fireEvent.click(authCard)
    await waitFor(() => {
      expect(screen.getByTestId('spec-detail')).toBeInTheDocument()
    })

    // Collapse
    fireEvent.click(authCard)
    await waitFor(() => {
      expect(screen.queryByTestId('spec-detail')).not.toBeInTheDocument()
    })
  })

  it('shows error when Build This fails', async () => {
    mockedApiPost.mockRejectedValueOnce(new Error('Not implemented'))

    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    // Expand and click build
    const cards = screen.getAllByTestId('spec-card')
    const authCard = cards.find(c => c.textContent?.includes('auth system'))!
    fireEvent.click(authCard)

    await waitFor(() => {
      expect(screen.getByText('Build it')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Build it'))

    await waitFor(() => {
      expect(screen.getByText('Could not start build. The backend may not support this yet.')).toBeInTheDocument()
    })
  })

  // Obsolete: the Verify button was removed. Previously this test
  // confirmed a failure message appeared when the verify POST failed.

  it('deletes a spec without a confirm dialog and offers an Undo toast', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm')
    const mockedApiDelete = vi.mocked(api.delete)

    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    // Open the overflow menu on the auth system card.
    const overflowButtons = screen.getAllByTestId('plan-overflow-button')
    const authCard = overflowButtons
      .map((b) => b.closest('[data-testid="spec-card"]'))
      .find((c) => c?.textContent?.includes('auth system'))!
    const authOverflow = authCard.querySelector(
      '[data-testid="plan-overflow-button"]'
    ) as HTMLElement
    fireEvent.click(authOverflow)

    await waitFor(() => {
      const btns = screen.getAllByTestId('delete-spec-button')
      expect(btns.length).toBeGreaterThan(0)
    })
    const deleteBtn = screen.getAllByTestId('delete-spec-button')[0]
    fireEvent.click(deleteBtn)

    expect(confirmSpy).not.toHaveBeenCalled()
    expect(mockedApiDelete).not.toHaveBeenCalled()
    expect(screen.getByTestId('undo-delete-spec-toast')).toBeInTheDocument()
    expect(screen.queryByText('auth system')).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('undo-delete-spec-button'))
    expect(mockedApiDelete).not.toHaveBeenCalled()
    expect(screen.queryByTestId('undo-delete-spec-toast')).not.toBeInTheDocument()

    confirmSpy.mockRestore()
  })

  it('encodes path segments individually so FastAPI :path converter still matches', async () => {
    const mockedApiDelete = vi.mocked(api.delete)
    mockedApiDelete.mockResolvedValue({})

    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })
    const overflowButtons = screen.getAllByTestId('plan-overflow-button')
    const authCard = overflowButtons
      .map((b) => b.closest('[data-testid="spec-card"]'))
      .find((c) => c?.textContent?.includes('auth system'))!
    const authOverflow = authCard.querySelector(
      '[data-testid="plan-overflow-button"]'
    ) as HTMLElement
    fireEvent.click(authOverflow)

    await waitFor(() => {
      expect(screen.getAllByTestId('delete-spec-button').length).toBeGreaterThan(0)
    })

    vi.useFakeTimers()
    try {
      fireEvent.click(screen.getAllByTestId('delete-spec-button')[0])
      await vi.advanceTimersByTimeAsync(5100)
      expect(mockedApiDelete).toHaveBeenCalledWith('/specs/docs/spec/auth-system.md')
    } finally {
      vi.useRealTimers()
    }
  })

  it('normalizes legacy "spec" status to "ready"', async () => {
    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    // The badge should say "Ready" not "Spec"
    const badges = screen.getAllByTestId('status-badge')
    const authBadge = badges.find(b => {
      // Find the badge in the same card as "auth system"
      const card = b.closest('[data-testid="spec-card"]') || b.closest('.bg-slate-900\\/40')
      return card?.textContent?.includes('auth system')
    })
    expect(authBadge?.textContent).toBe('Ready')
  })

  it('empty state uses EmptyState primitive when no specs exist', async () => {
    mockedApiGet.mockResolvedValue({ docs: [] })

    renderSpecs()

    await waitFor(() => {
      const emptyState = screen.getByTestId('empty-state')
      expect(emptyState).toBeInTheDocument()
    })
  })

  it('New Draft button uses Button primitive', async () => {
    renderSpecs()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/specs')
    })

    const btn = screen.getByRole('button', { name: 'New Spec' })
    expect(btn).toBeInTheDocument()
    // Button primitive includes font-medium class
    expect(btn).toHaveClass('font-medium')
  })


  // Wave 2: decompose no longer has its own UI. The "decompose success
  // toast" path still exists internally (handleDecompose) but is never
  // reached from user action now that the Ready state hides Break into
  // Tasks. The backend one-click build handles decompose implicitly.

  // Obsolete: the Verify button was removed. Previously this test
  // confirmed the button carried a descriptive tooltip.

  it('Build shows spinner per open task with the assigned agent name', async () => {
    // Build returns two agents.
    mockedApiPost.mockResolvedValueOnce({
      agents: ['spec-auth-001', 'spec-auth-002'],
      message: 'Spawned 2 agents to build this spec.',
    })
    // The tasks endpoint returns two open tasks, each assigned to an agent.
    // Second /specs fetch flips auth-system to in-progress so it stays
    // visible once the UI auto-switches to the Building tab.
    let specsCallCount = 0
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/specs') {
        specsCallCount += 1
        if (specsCallCount === 1) return Promise.resolve(mockDocsResponse)
        return Promise.resolve({
          docs: mockDocsResponse.docs.map((d) =>
            d.path === 'docs/spec/auth-system.md'
              ? { ...d, status: 'in-progress' }
              : d
          ),
        })
      }
      if (path.endsWith('/tasks')) {
        return Promise.resolve({
          tasks: [
            { id: '001', title: 'sign in flow', status: 'open', assigned_agent: 'spec-auth-001' },
            { id: '002', title: 'sign out flow', status: 'open', assigned_agent: 'spec-auth-002' },
          ],
        })
      }
      return Promise.resolve({})
    })

    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    const cards = screen.getAllByTestId('spec-card')
    const authCard = cards.find((c) => c.textContent?.includes('auth system'))!
    fireEvent.click(authCard)

    await waitFor(() => {
      expect(screen.getByText('Build it')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Build it'))

    await waitFor(() => {
      expect(screen.getAllByTestId('task-building-spinner').length).toBe(2)
    })
    expect(screen.getAllByTestId('task-building-label').length).toBe(2)
    const agentLinks = screen.getAllByTestId('task-agent-link')
    expect(agentLinks.map((a) => a.textContent)).toEqual(['spec-auth-001', 'spec-auth-002'])
    expect(screen.getByTestId('build-progress-count').textContent).toContain('0 of 2 tasks built')
  })

  it('Build polls task status every 2 seconds and flips to checkmark on close', async () => {
    mockedApiPost.mockResolvedValueOnce({
      agents: ['spec-auth-001'],
      message: 'Spawned 1 agent.',
    })

    let taskStatus: 'open' | 'closed' = 'open'
    // Second /specs fetch flips auth-system to in-progress so it
    // stays visible once the UI auto-switches to the Building tab.
    let specsCallCount = 0
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/specs') {
        specsCallCount += 1
        if (specsCallCount === 1) return Promise.resolve(mockDocsResponse)
        return Promise.resolve({
          docs: mockDocsResponse.docs.map((d) =>
            d.path === 'docs/spec/auth-system.md'
              ? { ...d, status: 'in-progress' }
              : d
          ),
        })
      }
      if (path.endsWith('/tasks')) {
        return Promise.resolve({
          tasks: [
            {
              id: '001',
              title: 'sign in flow',
              status: taskStatus,
              assigned_agent: 'spec-auth-001',
            },
          ],
        })
      }
      return Promise.resolve({})
    })

    renderSpecs()
    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    const cards = screen.getAllByTestId('spec-card')
    const authCard = cards.find((c) => c.textContent?.includes('auth system'))!
    fireEvent.click(authCard)

    await waitFor(() => {
      expect(screen.getByText('Build it')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Build it'))

    // First render after Build: spinner is up for the open task.
    await waitFor(() => {
      expect(screen.getByTestId('task-building-spinner')).toBeInTheDocument()
    })

    // Flip the backing state and wait for a poll cycle. Polling runs
    // every 2 seconds; real timers give us a clean end-to-end check.
    taskStatus = 'closed'
    await waitFor(
      () => {
        expect(screen.getByTestId('task-done-check')).toBeInTheDocument()
      },
      { timeout: 4000, interval: 200 }
    )
    expect(screen.queryByTestId('task-building-spinner')).not.toBeInTheDocument()
    // Agent name is rendered with "by: ..." prefix once done.
    expect(screen.getByTestId('task-agent-link').textContent).toContain(
      'by: spec-auth-001'
    )
  })

  it('agent chip in a task row links to the Agents tab with the agent name', async () => {
    mockedApiPost.mockResolvedValueOnce({
      agents: ['spec-auth-42'],
      message: 'Spawned 1 agent.',
    })
    // Second /specs fetch flips auth-system to in-progress so it
    // stays visible once the UI auto-switches to the Building tab.
    let specsCallCount = 0
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/specs') {
        specsCallCount += 1
        if (specsCallCount === 1) return Promise.resolve(mockDocsResponse)
        return Promise.resolve({
          docs: mockDocsResponse.docs.map((d) =>
            d.path === 'docs/spec/auth-system.md'
              ? { ...d, status: 'in-progress' }
              : d
          ),
        })
      }
      if (path.endsWith('/tasks')) {
        return Promise.resolve({
          tasks: [
            {
              id: '042',
              title: 'sign in flow',
              status: 'open',
              assigned_agent: 'spec-auth-42',
            },
          ],
        })
      }
      return Promise.resolve({})
    })

    renderSpecs()
    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })
    const cards = screen.getAllByTestId('spec-card')
    const authCard = cards.find((c) => c.textContent?.includes('auth system'))!
    fireEvent.click(authCard)

    await waitFor(() => {
      expect(screen.getByText('Build it')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Build it'))

    await waitFor(() => {
      expect(screen.getByTestId('task-agent-link')).toBeInTheDocument()
    })
    // The link is a button; clicking it should route to /agents with
    // the agent name as a query param so the Agents tab can highlight
    // the matching transcript.
    const link = screen.getByTestId('task-agent-link')
    expect(link.tagName).toBe('BUTTON')
    expect(link.getAttribute('title')).toContain('spec-auth-42')
  })

  it('Plans page shows the Start from a template grid and clicking a template creates a ready plan', async () => {
    const templates = [
      {
        id: 'build-a-website',
        name: 'Build a Website',
        description: 'Plan, design, build, and review a new website end to end.',
        icon: 'web',
        goal_markdown: 'body',
        acceptance_criteria: ['ac1', 'ac2'],
        tasks: ['Write the PRD', 'Build the frontend'],
      },
      {
        id: 'launch-announcement',
        name: 'Launch announcement',
        description: 'Write an announcement that explains what shipped and why it matters.',
        icon: 'campaign',
        goal_markdown: 'body',
        acceptance_criteria: ['ac1'],
        tasks: ['Draft the announcement'],
      },
    ]

    // Track fetch order: first /specs returns the seed list, second
    // returns the list with the new ready plan appended.
    let specsCallCount = 0
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/specs/templates') {
        return Promise.resolve({ templates })
      }
      if (path === '/specs') {
        specsCallCount += 1
        if (specsCallCount === 1) return Promise.resolve(mockDocsResponse)
        return Promise.resolve({
          docs: [
            ...mockDocsResponse.docs,
            {
              path: 'docs/spec/build-a-website.md',
              filename: 'build-a-website.md',
              title: 'Build a Website',
              status: 'spec',
              created_at: '2026-04-17T00:00:00Z',
              promoted_at: '2026-04-17T00:00:00Z',
              body: 'body',
            },
          ],
        })
      }
      if (path.includes('/tasks')) return Promise.resolve({ tasks: [] })
      return Promise.resolve({})
    })

    mockedApiPost.mockResolvedValue({
      result: 'docs/spec/build-a-website.md',
      status: 'ready',
      promoted_path: 'docs/spec/build-a-website.md',
      template_id: 'build-a-website',
    })

    renderSpecs()

    // Both template cards render in the grid.
    await waitFor(() => {
      expect(screen.getByTestId('plan-templates')).toBeInTheDocument()
    })
    expect(screen.getByTestId('plan-template-build-a-website')).toBeInTheDocument()
    expect(screen.getByTestId('plan-template-launch-announcement')).toBeInTheDocument()

    // Clicking a template card opens the details modal instead of
    // applying the template directly. The user can adjust the plan
    // name, add an optional note, and then click Create plan.
    fireEvent.click(screen.getByTestId('plan-template-build-a-website'))

    await waitFor(() => {
      expect(screen.getByTestId('spec-template-details-modal')).toBeInTheDocument()
    })

    // Submit with the pre-filled plan name unchanged.
    fireEvent.click(screen.getByTestId('spec-template-apply'))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/specs/from-template', {
        template_id: 'build-a-website',
        title: 'Build a Website',
        note: '',
      })
    })

    // New plan appears in the list. Use getAllByText because the
    // template card also renders the text "Build a Website".
    await waitFor(() => {
      const matches = screen.getAllByText('Build a Website')
      // Expect at least two matches: the template card and the new plan row.
      expect(matches.length).toBeGreaterThanOrEqual(2)
    })
    const badges = screen.getAllByTestId('status-badge')
    expect(badges.map((b) => b.textContent)).toContain('Ready')
  })

  // Wave 4 W6: acceptance criteria are status pills, not checkboxes.
  it('Ready plan renders AC items as status pills, not clickable checkboxes', async () => {
    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    const cards = screen.getAllByTestId('spec-card')
    const authCard = cards.find((c) => c.textContent?.includes('auth system'))!
    fireEvent.click(authCard)

    await waitFor(() => {
      expect(screen.getByTestId('acceptance-criteria')).toBeInTheDocument()
    })

    // No interactive checkboxes inside the AC list.
    const acList = screen.getByTestId('acceptance-criteria')
    expect(
      acList.querySelector('input[type="checkbox"]')
    ).toBeNull()

    // The list uses role="list" semantics.
    expect(acList.querySelector('ul[role="list"]')).not.toBeNull()

    // Each AC renders a status pill with a done or not-done state and
    // a tooltip explaining when it flips to green.
    const pills = screen.getAllByTestId('ac-status-pill')
    expect(pills.length).toBeGreaterThan(0)
    for (const pill of pills) {
      const state = pill.getAttribute('data-state')
      expect(state === 'done' || state === 'not-done').toBe(true)
      expect(pill.getAttribute('title')).toContain(
        'Acceptance criteria flip to green when Verify passes'
      )
    }

    // The pills are not buttons or inputs, so they are not clickable
    // in a way that would fire a PATCH.
    for (const pill of pills) {
      expect(pill.tagName).not.toBe('BUTTON')
      expect(pill.tagName).not.toBe('INPUT')
    }
  })

  // Wave 4 F5: one Verify button regardless of plan status.
  // Obsolete: the Verify button was removed. Previously this test
  // confirmed exactly one Verify button rendered per plan card.

  // Wave 4 F6: Delete is reachable by its accessible name, no window.confirm.
  it('Delete action is reachable by its accessible name and triggers the confirm dialog (not window.confirm)', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm')
    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    // The overflow button is reachable by its accessible name.
    const overflowBtns = screen.getAllByRole('button', { name: 'More actions' })
    expect(overflowBtns.length).toBeGreaterThan(0)

    // Find the one on the auth system card.
    const authOverflow = overflowBtns.find((b) =>
      b.closest('[data-testid="spec-card"]')?.textContent?.includes('auth system')
    )!
    fireEvent.click(authOverflow)

    // The Delete menu item is reachable by its accessible name.
    const deleteItem = screen.getByRole('menuitem', { name: /Delete spec/i })
    expect(deleteItem).toBeInTheDocument()

    fireEvent.click(deleteItem)

    // window.confirm was never called. The in-app Undo toast appears.
    expect(confirmSpy).not.toHaveBeenCalled()
    expect(screen.getByTestId('undo-delete-spec-toast')).toBeInTheDocument()
    confirmSpy.mockRestore()
  })

  // Wave 4 F7: displayStatus maps every backend status to a pretty name.
  it('displayStatus maps every backend status to a user-facing pretty name', () => {
    expect(displayStatus('draft')).toBe('Draft')
    expect(displayStatus('spec')).toBe('Ready')
    expect(displayStatus('ready')).toBe('Ready')
    expect(displayStatus('in-progress')).toBe('Building')
    expect(displayStatus('complete')).toBe('Done')
    // Unknown values fall back to Draft so the UI never renders raw
    // backend identifiers.
    expect(displayStatus('something-new')).toBe('Draft')
    expect(displayStatus('')).toBe('Draft')
  })
})


describe('Specs page real-time bus', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/specs') return Promise.resolve(mockDocsResponse)
      if (path === '/specs/templates') return Promise.resolve({ templates: [] })
      if (path.includes('/tasks')) return Promise.resolve({ tasks: [] })
      return Promise.resolve({})
    })
  })

  it('refetches /specs when bumpSpecs fires', async () => {
    const { bumpSpecs, _resetSidebarBus } = await import('../lib/sidebarBus')
    _resetSidebarBus()
    renderSpecs()

    // Wait for the initial mount fetch.
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/specs')
    })
    const initialCalls = mockedApiGet.mock.calls.filter(
      (c) => c[0] === '/specs'
    ).length

    // Fire the bus. The Specs page should refetch.
    bumpSpecs()

    await waitFor(() => {
      const after = mockedApiGet.mock.calls.filter(
        (c) => c[0] === '/specs'
      ).length
      expect(after).toBeGreaterThan(initialCalls)
    })
  })

  it('does not flash a just-deleted spec back into the list during the 5s undo window', async () => {
    // Regression: useUserActivity bumps the specs bus on every click /
    // keystroke (throttled to 1/s), which triggers an immediate
    // fetchDocs. Because the real DELETE is queued behind the 5s undo
    // timer, the server still has the spec on disk. Before the fix,
    // setDocs with the refetched list restored the just-removed spec
    // and it sat visible for ~5s, looking like "delete doesn't work".
    // The pendingDeleteSpecPathsRef guard filters it out until DELETE
    // resolves.
    const { bumpSpecs, _resetSidebarBus } = await import('../lib/sidebarBus')
    _resetSidebarBus()
    const mockedApiDelete = vi.mocked(api.delete)
    mockedApiDelete.mockResolvedValue({})

    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    const overflowButtons = screen.getAllByTestId('plan-overflow-button')
    const authCard = overflowButtons
      .map((b) => b.closest('[data-testid="spec-card"]'))
      .find((c) => c?.textContent?.includes('auth system'))!
    const authOverflow = authCard.querySelector(
      '[data-testid="plan-overflow-button"]'
    ) as HTMLElement
    fireEvent.click(authOverflow)

    await waitFor(() => {
      expect(screen.getAllByTestId('delete-spec-button').length).toBeGreaterThan(0)
    })

    fireEvent.click(screen.getAllByTestId('delete-spec-button')[0])

    // Optimistic removal: spec is gone from the list.
    expect(screen.queryByText('auth system')).not.toBeInTheDocument()

    // Simulate the user-activity hook firing bumpSpecs() mid-undo
    // window (e.g. Tori moves her mouse or clicks anywhere else).
    bumpSpecs()

    // Before the fix, the refetch returns the spec (file still on
    // disk) and setDocs restores it. After the fix, the pending-delete
    // path is filtered out.
    await waitFor(() => {
      const calls = mockedApiGet.mock.calls.filter((c) => c[0] === '/specs')
      expect(calls.length).toBeGreaterThan(1)
    })
    expect(screen.queryByText('auth system')).not.toBeInTheDocument()
    // DELETE has not fired yet: we're still inside the 5s undo window.
    expect(mockedApiDelete).not.toHaveBeenCalled()
  })

  // Layout regression: the spec list must render above the "Start from a
  // template" grid so returning users see their own plans first and only
  // scroll past them to start a new plan from a template.
  it('specs list renders above template grid', async () => {
    const templates = [
      {
        id: 'build-a-website',
        name: 'Build a Website',
        description: 'Plan, design, build, and review a new website end to end.',
        icon: 'web',
        goal_markdown: 'body',
        acceptance_criteria: ['ac1'],
        tasks: ['Write the PRD'],
      },
    ]

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/specs') return Promise.resolve(mockDocsResponse)
      if (path === '/specs/templates') return Promise.resolve({ templates })
      if (path.includes('/tasks')) return Promise.resolve({ tasks: [] })
      return Promise.resolve({})
    })

    renderSpecs()

    // Wait for both the ready spec and the template grid to mount.
    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })
    await waitFor(() => {
      expect(screen.getByTestId('plan-templates')).toBeInTheDocument()
    })

    const cards = screen.getAllByTestId('spec-card')
    const authCard = cards.find((c) => c.textContent?.includes('auth system'))!
    const templatesGrid = screen.getByTestId('plan-templates')

    // compareDocumentPosition returns a bitmask. FOLLOWING (0x04) means
    // the arg node comes after the reference node in document order.
    const relation = authCard.compareDocumentPosition(templatesGrid)
    expect(relation & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  // Auto-switch: a successful Build it on a spec should move the user
  // to the In Progress tab so they land on the filtered list that contains
  // the plan they just built instead of staring at their old tab.
  it('Build it switches to In Progress tab on success', async () => {
    mockedApiPost.mockResolvedValue({
      agents: ['agent-a', 'agent-b'],
      message: 'Spawned 2 agents.',
    })

    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    // Default stage filter is "all". Confirm the In Progress pill does NOT yet
    // have selected styling before Build fires.
    const inProgressTabBefore = screen.getByTestId('stage-filter-in_progress')
    expect(inProgressTabBefore.className).not.toContain('bg-blue-500')

    // Expand the ready spec and click Build it.
    const cards = screen.getAllByTestId('spec-card')
    const authCard = cards.find((c) => c.textContent?.includes('auth system'))!
    fireEvent.click(authCard)

    await waitFor(() => {
      expect(screen.getByText('Build it')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Build it'))

    // After the build POST resolves with agents, the In Progress stage pill
    // should carry the selected styling.
    await waitFor(() => {
      const inProgressTab = screen.getByTestId('stage-filter-in_progress')
      expect(inProgressTab.className).toContain('bg-blue-500')
      expect(inProgressTab.className).toContain('text-white')
    })
  })

  it('build button for in-progress spec with all tasks closed shows updated tooltip without em-dash', async () => {
    const allClosedDocs = {
      docs: mockDocsResponse.docs.map((d) =>
        d.path === 'docs/spec/dashboard-redesign.md'
          ? { ...d, task_summary: { total: 4, open: 0, closed: 4 } }
          : d
      ),
    }
    mockedApiGet.mockResolvedValue(allClosedDocs)

    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('dashboard redesign')).toBeInTheDocument()
    })

    const cards = screen.getAllByTestId('spec-card')
    const designCard = cards.find((c) => c.textContent?.includes('dashboard redesign'))!
    fireEvent.click(designCard)

    await waitFor(() => {
      expect(screen.getByTestId('build-button')).toBeInTheDocument()
    })

    expect(screen.getByTestId('build-button')).toHaveAttribute(
      'title',
      'Every task is already closed. The spec is done.'
    )
  })
})

describe('claims: Building badge + inline note', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedApiPost.mockResolvedValue({ result: 'ok', task_ids: [] })
  })

  it('shows Building badge when tasks payload has terminal-source claims (overrides Ready)', async () => {
    const startedAt = new Date(Date.now() - 5 * 60 * 1000).toISOString()

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/specs') return Promise.resolve(mockDocsResponse)
      if (path === '/specs/templates') return Promise.resolve({ templates: [] })
      if (path.includes('auth-system') && path.includes('/tasks')) {
        return Promise.resolve({
          tasks: [],
          claims: [{ agent: 'myOS', source: 'wrapper', started_at: startedAt, task_ids: [] }],
        })
      }
      if (path.includes('/tasks')) return Promise.resolve({ tasks: [], claims: [] })
      return Promise.resolve({})
    })

    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    // Expand the auth system card to trigger fetchLinkedTasks (which fetches claims)
    const cards = screen.getAllByTestId('spec-card')
    const authCard = cards.find(c => c.textContent?.includes('auth system'))!
    fireEvent.click(authCard)

    // Badge should flip to Building (overriding Ready) once claims are loaded
    await waitFor(() => {
      const badges = screen.getAllByTestId('status-badge')
      const authBadge = badges.find(b => {
        const card = b.closest('[data-testid="spec-card"]')?.closest('.bg-slate-900\\/40') ??
          b.closest('.bg-slate-900\\/40')
        return card?.textContent?.includes('auth system')
      })
      expect(authBadge?.textContent).toBe('Building')
    })
  })

  it('shows inline note with "in terminal" next to the badge for terminal-source claims', async () => {
    const startedAt = new Date(Date.now() - 5 * 60 * 1000).toISOString()

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/specs') return Promise.resolve(mockDocsResponse)
      if (path === '/specs/templates') return Promise.resolve({ templates: [] })
      if (path.includes('auth-system') && path.includes('/tasks')) {
        return Promise.resolve({
          tasks: [],
          claims: [{ agent: 'myOS', source: 'wrapper', started_at: startedAt, task_ids: [] }],
        })
      }
      if (path.includes('/tasks')) return Promise.resolve({ tasks: [], claims: [] })
      return Promise.resolve({})
    })

    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    const cards = screen.getAllByTestId('spec-card')
    const authCard = cards.find(c => c.textContent?.includes('auth system'))!
    fireEvent.click(authCard)

    await waitFor(() => {
      expect(screen.getByTestId('claims-note')).toBeInTheDocument()
    })

    expect(screen.getByTestId('claims-note').textContent).toContain('in terminal')
    expect(screen.getByTestId('claims-note').textContent).toContain('myOS')
  })

  it('badge tooltip lists active claims for debugging', async () => {
    const startedAt = new Date(Date.now() - 5 * 60 * 1000).toISOString()

    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/specs') return Promise.resolve(mockDocsResponse)
      if (path === '/specs/templates') return Promise.resolve({ templates: [] })
      if (path.includes('auth-system') && path.includes('/tasks')) {
        return Promise.resolve({
          tasks: [],
          claims: [{ agent: 'myOS', source: 'wrapper', started_at: startedAt, task_ids: [] }],
        })
      }
      if (path.includes('/tasks')) return Promise.resolve({ tasks: [], claims: [] })
      return Promise.resolve({})
    })

    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    const cards = screen.getAllByTestId('spec-card')
    const authCard = cards.find(c => c.textContent?.includes('auth system'))!
    fireEvent.click(authCard)

    await waitFor(() => {
      const badges = screen.getAllByTestId('status-badge')
      const authBadge = badges.find(b => {
        const card = b.closest('.bg-slate-900\\/40')
        return card?.textContent?.includes('auth system')
      })
      expect(authBadge?.getAttribute('title')).toContain('myOS')
    })
  })

  it('does not show claims-note when claims is empty', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/specs') return Promise.resolve(mockDocsResponse)
      if (path === '/specs/templates') return Promise.resolve({ templates: [] })
      if (path.includes('/tasks')) return Promise.resolve({ tasks: [], claims: [] })
      return Promise.resolve({})
    })

    renderSpecs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    const cards = screen.getAllByTestId('spec-card')
    const authCard = cards.find(c => c.textContent?.includes('auth system'))!
    fireEvent.click(authCard)

    await waitFor(() => {
      expect(screen.getByTestId('spec-detail')).toBeInTheDocument()
    })

    expect(screen.queryByTestId('claims-note')).not.toBeInTheDocument()
  })
})

// --- FR-008 through FR-011: stage filter pills, chips, archive, husk ---

const mockStageDocsResponse = {
  docs: [
    {
      path: 'docs/draft/empty-idea.md',
      filename: 'empty-idea.md',
      title: 'empty idea',
      status: 'draft',
      stage: 'draft',
      created_at: '2026-01-01T00:00:00Z',
      promoted_at: '',
      body: 'rough idea',
    },
    {
      path: 'docs/spec/payment-flow.md',
      filename: 'payment-flow.md',
      title: 'payment flow',
      status: 'ready',
      stage: 'ready',
      created_at: '2026-02-01T00:00:00Z',
      promoted_at: '2026-02-05T00:00:00Z',
      body: 'Payment spec.',
      acceptance_criteria: [{ text: 'checkout works', checked: false }],
    },
    {
      path: 'docs/spec/dark-mode.md',
      filename: 'dark-mode.md',
      title: 'dark mode',
      status: 'in-progress',
      stage: 'in_progress',
      created_at: '2026-03-01T00:00:00Z',
      promoted_at: '2026-03-05T00:00:00Z',
      body: 'Dark mode spec.',
      task_summary: { total: 3, open: 1, closed: 2 },
    },
  ],
}

const mockHusk1 = {
  path: 'docs/draft/stale-a.md',
  filename: 'stale-a.md',
  title: 'stale draft a',
  status: 'draft',
  stage: 'draft',
  husk: true,
  husk_reason: 'no acceptance criteria found',
  created_at: '2025-12-01T00:00:00Z',
  promoted_at: '',
  body: '',
}

const mockHusk2 = {
  path: 'docs/draft/stale-b.md',
  filename: 'stale-b.md',
  title: 'stale draft b',
  status: 'draft',
  stage: 'draft',
  husk: true,
  husk_reason: 'placeholder-only acceptance criteria',
  created_at: '2025-12-02T00:00:00Z',
  promoted_at: '',
  body: '',
}

function renderSpecsWithStageData(extraDocs: object[] = []) {
  mockedApiGet.mockImplementation((path: string) => {
    if (path === '/specs') return Promise.resolve({
      docs: [...mockStageDocsResponse.docs, ...extraDocs],
    })
    if (path === '/specs/templates') return Promise.resolve({ templates: [] })
    if (path.includes('/tasks')) return Promise.resolve({ tasks: [], claims: [] })
    return Promise.resolve({})
  })
  return render(<MemoryRouter><Specs /></MemoryRouter>)
}

describe('FR-008: stage filter pills', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('renders stage filter pills for all four stages', async () => {
    renderSpecsWithStageData()
    await waitFor(() => expect(screen.getByText('payment flow')).toBeInTheDocument())
    expect(screen.getByTestId('stage-filter-all')).toBeInTheDocument()
    expect(screen.getByTestId('stage-filter-draft')).toBeInTheDocument()
    expect(screen.getByTestId('stage-filter-ready')).toBeInTheDocument()
    expect(screen.getByTestId('stage-filter-in_progress')).toBeInTheDocument()
  })

  it('default filter shows all specs', async () => {
    renderSpecsWithStageData()
    await waitFor(() => expect(screen.getByText('payment flow')).toBeInTheDocument())
    expect(screen.getByText('payment flow')).toBeInTheDocument()
    expect(screen.getByText('dark mode')).toBeInTheDocument()
    expect(screen.getByText('empty idea')).toBeInTheDocument()
  })

  it('clicking All shows every spec', async () => {
    renderSpecsWithStageData()
    await waitFor(() => expect(screen.getByText('payment flow')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('stage-filter-all'))
    await waitFor(() => {
      expect(screen.getByText('empty idea')).toBeInTheDocument()
      expect(screen.getByText('dark mode')).toBeInTheDocument()
    })
  })

  it('clicking Draft shows only draft-stage specs', async () => {
    renderSpecsWithStageData()
    await waitFor(() => expect(screen.getByText('payment flow')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('stage-filter-draft'))
    await waitFor(() => expect(screen.getByText('empty idea')).toBeInTheDocument())
    expect(screen.queryByText('payment flow')).not.toBeInTheDocument()
    expect(screen.queryByText('old feature')).not.toBeInTheDocument()
  })

  it('each spec card renders a stage chip with the doc stage', async () => {
    renderSpecsWithStageData()
    fireEvent.click(screen.getByTestId('stage-filter-all'))
    await waitFor(() => expect(screen.getAllByTestId('stage-chip').length).toBeGreaterThan(0))
    const chips = screen.getAllByTestId('stage-chip').map(c => c.textContent)
    expect(chips).toContain('Ready')
    expect(chips).toContain('In Progress')
  })
})



describe('FR-010: empty draft tag', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('shows Empty draft tag on an empty spec', async () => {
    renderSpecsWithStageData([mockHusk1])
    fireEvent.click(screen.getByTestId('stage-filter-all'))
    await waitFor(() => expect(screen.getByText('stale draft a')).toBeInTheDocument())
    expect(screen.getByTestId('husk-tag')).toBeInTheDocument()
  })

  it('Empty draft tag title shows husk_reason', async () => {
    renderSpecsWithStageData([mockHusk1])
    fireEvent.click(screen.getByTestId('stage-filter-all'))
    await waitFor(() => expect(screen.getByTestId('husk-tag')).toBeInTheDocument())
    expect(screen.getByTestId('husk-tag').getAttribute('title')).toBe('no acceptance criteria found')
  })
})

describe('FR-011: bulk delete empty drafts', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('shows delete empty drafts button when 2 or more empty drafts are visible', async () => {
    renderSpecsWithStageData([mockHusk1, mockHusk2])
    fireEvent.click(screen.getByTestId('stage-filter-all'))
    await waitFor(() => expect(screen.getByTestId('delete-all-husks-button')).toBeInTheDocument())
  })

  it('does NOT show delete empty drafts button when fewer than 2 empty drafts are visible', async () => {
    renderSpecsWithStageData([mockHusk1])
    fireEvent.click(screen.getByTestId('stage-filter-all'))
    await waitFor(() => expect(screen.getByText('stale draft a')).toBeInTheDocument())
    expect(screen.queryByTestId('delete-all-husks-button')).not.toBeInTheDocument()
  })

  it('delete empty drafts button calls DELETE for each old empty draft', async () => {
    const mockedApiDelete = vi.mocked(api.delete)
    mockedApiDelete.mockResolvedValue(undefined)
    renderSpecsWithStageData([mockHusk1, mockHusk2])
    fireEvent.click(screen.getByTestId('stage-filter-all'))
    await waitFor(() => expect(screen.getByTestId('delete-all-husks-button')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('delete-all-husks-button'))
    await waitFor(() => {
      expect(mockedApiDelete).toHaveBeenCalledWith(expect.stringMatching(/stale-a/))
      expect(mockedApiDelete).toHaveBeenCalledWith(expect.stringMatching(/stale-b/))
    })
  })
})

describe('SpecBody', () => {
  it('renders ### headings as h4 elements, not literal ### text', () => {
    render(<SpecBody body={'### Block 1: Foo\nsome paragraph'} />)
    expect(screen.getByRole('heading', { level: 4, name: 'Block 1: Foo' })).toBeInTheDocument()
    expect(screen.queryByText('### Block 1: Foo')).toBeNull()
  })

  it('renders ## headings as h3 elements', () => {
    render(<SpecBody body={'## Overview\nsome text'} />)
    expect(screen.getByRole('heading', { level: 3, name: 'Overview' })).toBeInTheDocument()
  })

  it('renders # headings as h2 elements', () => {
    render(<SpecBody body={'# Title\nsome text'} />)
    expect(screen.getByRole('heading', { level: 2, name: 'Title' })).toBeInTheDocument()
  })
})

describe('Specs focus from kanban link (→1501)', () => {
  it('scrolls matching spec into view when ?focus=<path>', async () => {
    const scrollIntoViewMock = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoViewMock

    mockedApiGet.mockImplementation((url: string) => {
      if (url === '/specs') return Promise.resolve({ docs: [mockDocsResponse.docs[0]] })
      if (url.startsWith('/specs/') && url.endsWith('/tasks')) return Promise.resolve({ tasks: [] })
      return Promise.resolve({})
    })

    render(
      <MemoryRouter initialEntries={['/specs?focus=docs%2Fdraft%2Fonboarding-flow.md']}>
        <Specs />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('onboarding flow')).toBeInTheDocument()
    })

    await waitFor(() => {
      expect(scrollIntoViewMock).toHaveBeenCalled()
    })
  })
})
