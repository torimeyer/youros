import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import GitHub from './GitHub'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
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

function renderGitHub() {
  return render(
    <MemoryRouter>
      <GitHub />
    </MemoryRouter>
  )
}

describe('GitHub ConnectCard (chunk-d migration)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.githubIssues.v1')
  })

  it('renders ConnectCard with slate accent when not connected', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/github/status')) {
        return Promise.resolve({ connected: false, repo: '' })
      }
      return Promise.resolve({})
    })

    renderGitHub()

    await waitFor(() => {
      expect(screen.getByTestId('connect-card')).toBeInTheDocument()
    })

    // Slate accent color: #94a3b8 -> jsdom converts to rgb(148, 163, 184)
    const card = screen.getByTestId('connect-card')
    expect(card.innerHTML).toMatch(/148, 163, 184/)
  })

  it('shows Connect GitHub title in ConnectCard', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/github/status')) {
        return Promise.resolve({ connected: false, repo: '' })
      }
      return Promise.resolve({})
    })

    renderGitHub()

    await waitFor(() => {
      expect(screen.getByText('Connect GitHub')).toBeInTheDocument()
    })
  })

  it('shows token and repo inputs inside the ConnectCard', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/github/status')) {
        return Promise.resolve({ connected: false, repo: '' })
      }
      return Promise.resolve({})
    })

    renderGitHub()

    await waitFor(() => {
      expect(screen.getByPlaceholderText('ghp_...')).toBeInTheDocument()
    })
    expect(screen.getByPlaceholderText(/owner\/repo/i)).toBeInTheDocument()
  })
})

describe('GitHub localStorage seed', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.githubIssues.v1')
  })

  it('paints cached issues from localStorage before the network resolves', async () => {
    const seeded = [
      {
        number: 42,
        title: 'Seeded issue from cache',
        state: 'open',
        body: '',
        labels: [],
        assignee: '',
        created_at: '2026-04-14T00:00:00Z',
        updated_at: '2026-04-14T00:00:00Z',
        html_url: 'https://example.com/42',
      },
    ]
    window.localStorage.setItem('myos.githubIssues.v1', JSON.stringify(seeded))

    // Hold the network calls open so the seeded row is what paints first.
    const pending = new Promise(() => {})
    mockedApiGet.mockImplementation(() => pending as Promise<never>)

    renderGitHub()

    // The seeded title appears before the status or issues network call resolves.
    expect(await screen.findByText('Seeded issue from cache')).toBeInTheDocument()
  })

  it('shows a Refreshing hint while the network replaces the seeded rows', async () => {
    const seeded = [
      {
        number: 7,
        title: 'Old cached title',
        state: 'open',
        body: '',
        labels: [],
        assignee: '',
        created_at: '2026-04-13T00:00:00Z',
        updated_at: '2026-04-13T00:00:00Z',
        html_url: 'https://example.com/7',
      },
    ]
    window.localStorage.setItem('myos.githubIssues.v1', JSON.stringify(seeded))

    let resolveIssues: ((value: { issues: typeof seeded }) => void) | null = null
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/github/status')) {
        return Promise.resolve({ connected: true, repo: 'owner/repo' })
      }
      if (path.includes('/github/issues')) {
        return new Promise((resolve) => {
          resolveIssues = resolve as typeof resolveIssues
        })
      }
      return Promise.resolve({})
    })

    renderGitHub()

    // Refreshing indicator shows while we have seeded rows and are fetching.
    await waitFor(() => {
      expect(screen.getByTestId('github-refreshing')).toBeInTheDocument()
    })

    // Resolve the network; fresh data replaces the seed and the hint disappears.
    const fresh = [
      {
        number: 99,
        title: 'Fresh title from server',
        state: 'open',
        body: '',
        labels: [],
        assignee: '',
        created_at: '2026-04-15T00:00:00Z',
        updated_at: '2026-04-15T00:00:00Z',
        html_url: 'https://example.com/99',
      },
    ]
    resolveIssues!({ issues: fresh })

    await waitFor(() => {
      expect(screen.getByText('Fresh title from server')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('github-refreshing')).not.toBeInTheDocument()
  })
})

// Post-OAuth pick-repo flow: when GitHub's OAuth callback redirects back
// the token is already saved (status.connected=true) but no repo is
// chosen yet (status.repo=""). The page must show a small "pick a repo"
// form instead of either the full PAT connect card or the issues list.
describe('GitHub OAuth pick-repo flow', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.githubIssues.v1')
  })

  it('shows the pick-repo card when connected with empty repo', async () => {
    const mockedApiGet = vi.mocked(api.get)
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/github/status')) {
        return Promise.resolve({ connected: true, repo: '' })
      }
      return Promise.resolve({})
    })

    renderGitHub()

    await waitFor(() => {
      expect(screen.getByTestId('github-oauth-pick-repo')).toBeInTheDocument()
    })
    expect(screen.getByTestId('github-oauth-pick-repo-submit')).toBeInTheDocument()
    // The PAT-form fields from the full connect card MUST NOT appear here.
    expect(screen.queryByPlaceholderText(/ghp_/i)).toBeNull()
  })

  it('submitting the picked repo posts {repo} only', async () => {
    const { fireEvent } = await import('@testing-library/react')
    const mockedApiGet = vi.mocked(api.get)
    const mockedApiPost = vi.mocked(api.post)
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/github/status')) {
        return Promise.resolve({ connected: true, repo: '' })
      }
      if (path.includes('/github/issues')) {
        return Promise.resolve({ issues: [] })
      }
      return Promise.resolve({})
    })
    mockedApiPost.mockResolvedValue({ ok: true })

    renderGitHub()

    const input = (await screen.findByTestId('github-oauth-pick-repo')) as HTMLInputElement
    fireEvent.change(input, { target: { value: 'owner/picked' } })

    const submit = await screen.findByTestId('github-oauth-pick-repo-submit')
    fireEvent.click(submit)

    await waitFor(() => {
      const matched = mockedApiPost.mock.calls.find((c) => c[0] === '/github/connect')
      expect(matched).toBeTruthy()
      expect(matched?.[1]).toEqual({ repo: 'owner/picked' })
    })
  })

  it('rejects empty repo input with an error message', async () => {
    const mockedApiGet = vi.mocked(api.get)
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/github/status')) {
        return Promise.resolve({ connected: true, repo: '' })
      }
      return Promise.resolve({})
    })

    renderGitHub()

    const submit = await screen.findByTestId('github-oauth-pick-repo-submit')
    const { fireEvent } = await import('@testing-library/react')
    fireEvent.click(submit)

    await waitFor(() => {
      expect(screen.getByText(/Repository is required/i)).toBeInTheDocument()
    })
  })
})

// Test A: Make needle button + preview card
describe('GitHub make-needle flow', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.githubIssues.v1')
  })

  it('shows make-needle button on a connected issue row, opens preview card, and confirm calls api.post with issue-to-needle path', async () => {
    const { fireEvent } = await import('@testing-library/react')
    const mockedApiPost = vi.mocked(api.post)
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/github/status')) {
        return Promise.resolve({ connected: true, repo: 'owner/repo' })
      }
      if (path.includes('/github/issues')) {
        return Promise.resolve({
          issues: [
            {
              number: 7,
              title: 'Test issue title',
              state: 'open',
              body: 'Some description\n- [ ] First criteria\n- [ ] Second criteria',
              labels: [],
              assignee: '',
              created_at: '2026-04-14T00:00:00Z',
              updated_at: '2026-04-14T00:00:00Z',
              html_url: 'https://example.com/7',
            },
          ],
        })
      }
      return Promise.resolve({})
    })
    mockedApiPost.mockResolvedValue({ ok: true })

    renderGitHub()

    // Wait for the make-needle button to appear on the issue row
    const makeNeedleBtn = await screen.findByTestId('make-needle-7')
    expect(makeNeedleBtn).toBeInTheDocument()

    // Preview card is not visible yet
    expect(screen.queryByTestId('needle-preview-7')).not.toBeInTheDocument()

    // Click it — preview card should appear
    fireEvent.click(makeNeedleBtn)

    await waitFor(() => {
      expect(screen.getByTestId('needle-preview-7')).toBeInTheDocument()
    })

    // The preview card contains editable title, description, and priority
    const previewCard = screen.getByTestId('needle-preview-7')
    expect(previewCard.querySelector('input[type="text"], input:not([type])') ?? previewCard.querySelector('[data-field="title"]')).toBeTruthy()
    expect(previewCard.querySelector('textarea') ?? previewCard.querySelector('[data-field="description"]')).toBeTruthy()
    expect(previewCard.querySelector('select') ?? previewCard.querySelector('[data-field="priority"]')).toBeTruthy()

    // Confirm button calls api.post with the correct path
    const confirmBtn = screen.getByTestId('needle-confirm-7')
    fireEvent.click(confirmBtn)

    await waitFor(() => {
      const calls = mockedApiPost.mock.calls
      const matched = calls.find((c) => String(c[0]).includes('/github/issue-to-needle/7'))
      expect(matched).toBeTruthy()
    })
  })
})

// Test B: Import as needles option
describe('GitHub import-as-needles flow', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.githubIssues.v1')
  })

  it('exposes import-as-needles control that calls api.post with /github/sync and mode=needle', async () => {
    const { fireEvent } = await import('@testing-library/react')
    const mockedApiPost = vi.mocked(api.post)
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/github/status')) {
        return Promise.resolve({ connected: true, repo: 'owner/repo' })
      }
      if (path.includes('/github/issues')) {
        return Promise.resolve({ issues: [] })
      }
      return Promise.resolve({})
    })
    mockedApiPost.mockResolvedValue({ ok: true, created: 0, skipped: 0, total_issues: 0, errors: [] })

    renderGitHub()

    // The import-as-needles control must be present in the connected view
    const importAsNeedles = await screen.findByTestId('import-as-needles')
    expect(importAsNeedles).toBeInTheDocument()

    fireEvent.click(importAsNeedles)

    await waitFor(() => {
      const calls = mockedApiPost.mock.calls
      const matched = calls.find(
        (c) =>
          String(c[0]).includes('/github/sync') &&
          (String(c[0]).includes('mode=needle') || (c[1] as Record<string, unknown>)?.mode === 'needle'),
      )
      expect(matched).toBeTruthy()
    })
  })
})
