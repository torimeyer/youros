import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { SpecReview } from './SpecReview'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

import { api } from '../lib/api'

const mockedGet = vi.mocked(api.get)
const mockedPost = vi.mocked(api.post)

const readyResponse = {
  spec_path: 'docs/spec/foo.md',
  readiness: {
    ready: true,
    file_path: 'docs/spec/foo.md',
    checks: [
      { name: 'has_ac_checkboxes', passed: true, detail: 'OK', required: true },
    ],
  },
  drift: {
    drift: false,
    items: [],
    summary: '',
  },
  constitution: {
    principles: [
      { source: 'CLAUDE.md', section: 'Quality', text: 'Keep it simple' },
      { source: 'CLAUDE.md', section: 'Security', text: 'No secrets in code' },
    ],
    violations: [],
  },
}

const notReadyResponse = {
  spec_path: 'docs/spec/bar.md',
  readiness: {
    ready: false,
    file_path: 'docs/spec/bar.md',
    checks: [
      { name: 'has_ac_checkboxes', passed: false, detail: 'Missing steps', required: true },
      { name: 'has_file_paths', passed: false, detail: 'No file paths', required: true },
      { name: 'outcome_concrete', passed: false, detail: 'Too vague', required: false },
    ],
  },
  drift: {
    drift: true,
    items: [
      { kind: 'added', detail: 'New endpoint added to api/routers/specs.py' },
      { kind: 'removed', detail: 'Old helper removed from api/services/spec_audit.py' },
    ],
    summary: '2 drift items',
  },
  constitution: {
    principles: [
      { source: 'CLAUDE.md', section: 'Quality', text: 'Keep it simple' },
    ],
    violations: [
      { principle: 'Quality', detail: 'Spec references deprecated pattern' },
    ],
  },
}

describe('SpecReview', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders "Ready to build" when readiness.ready is true', async () => {
    mockedGet.mockResolvedValueOnce(readyResponse)

    render(<SpecReview specPath="docs/spec/foo.md" />)

    await waitFor(() => {
      expect(screen.getByTestId('review-readiness')).toHaveTextContent('Ready to build')
    })
  })

  it('renders count of failing required checks when not ready', async () => {
    mockedGet.mockResolvedValueOnce(notReadyResponse)

    render(<SpecReview specPath="docs/spec/bar.md" />)

    await waitFor(() => {
      expect(screen.getByTestId('review-readiness')).toHaveTextContent('2 required items left')
    })
  })

  it('renders drift items when drift is true', async () => {
    mockedGet.mockResolvedValueOnce(notReadyResponse)

    render(<SpecReview specPath="docs/spec/bar.md" />)

    await waitFor(() => {
      const driftSection = screen.getByTestId('review-drift')
      expect(driftSection).toHaveTextContent('New endpoint added to api/routers/specs.py')
      expect(driftSection).toHaveTextContent('Old helper removed from api/services/spec_audit.py')
    })
  })

  it('renders "No drift detected" when drift is false', async () => {
    mockedGet.mockResolvedValueOnce(readyResponse)

    render(<SpecReview specPath="docs/spec/foo.md" />)

    await waitFor(() => {
      expect(screen.getByTestId('review-drift')).toHaveTextContent('No drift detected')
    })
  })

  it('renders inherited principle count', async () => {
    mockedGet.mockResolvedValueOnce(readyResponse)

    render(<SpecReview specPath="docs/spec/foo.md" />)

    await waitFor(() => {
      expect(screen.getByTestId('review-constitution')).toHaveTextContent('Inherits 2 project principles')
    })
  })

  it('renders violations when present', async () => {
    mockedGet.mockResolvedValueOnce(notReadyResponse)

    render(<SpecReview specPath="docs/spec/bar.md" />)

    await waitFor(() => {
      expect(screen.getByTestId('review-constitution')).toHaveTextContent('Spec references deprecated pattern')
    })
  })

  it('renders an error state when the api rejects', async () => {
    mockedGet.mockRejectedValueOnce(new Error('Network failure'))

    render(<SpecReview specPath="docs/spec/broken.md" />)

    await waitFor(() => {
      expect(screen.getByTestId('review-error')).toHaveTextContent('Network failure')
    })
  })

  // ─── E4: Fresh-eyes verify ──────────────────────────────────────────────────

  it('renders the fresh verify button after loading (E4)', async () => {
    mockedGet.mockResolvedValueOnce(readyResponse)

    render(<SpecReview specPath="docs/spec/foo.md" />)

    await waitFor(() => {
      expect(screen.getByTestId('fresh-verify-btn')).toBeTruthy()
    })
  })

  it('calls /verify?fresh=true and shows result on success (E4)', async () => {
    mockedGet.mockResolvedValueOnce(readyResponse)
    mockedPost.mockResolvedValueOnce({ fresh: true, ok: true, summary: 'All requirements are tested.' })

    render(<SpecReview specPath="docs/spec/foo.md" />)

    await waitFor(() => expect(screen.getByTestId('fresh-verify-btn')).toBeTruthy())
    fireEvent.click(screen.getByTestId('fresh-verify-btn'))

    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith('/specs/docs/spec/foo.md/verify?fresh=true', {})
      expect(screen.getByTestId('fresh-verify-result')).toHaveTextContent('All requirements are tested.')
    })
  })

  it('shows fallback message when fresh verify call throws (E4)', async () => {
    mockedGet.mockResolvedValueOnce(readyResponse)
    mockedPost.mockRejectedValueOnce(new Error('no key'))

    render(<SpecReview specPath="docs/spec/foo.md" />)

    await waitFor(() => expect(screen.getByTestId('fresh-verify-btn')).toBeTruthy())
    fireEvent.click(screen.getByTestId('fresh-verify-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('fresh-verify-result')).toHaveTextContent('Fresh review could not run.')
    })
  })
})

// ─── Task 4: PR badge + link input ────────────────────────────────────────────

const reviewBase = {
  spec_path: 'docs/spec/demo.md',
  readiness: { ready: true, file_path: null, checks: [] },
  drift: { drift: false, acked: false, items: [], summary: '' },
  constitution: { principles: [], violations: [] },
}

describe('SpecReview github_pr badge', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows a merged badge when the linked PR is merged', async () => {
    mockedGet.mockImplementation((path: string) => {
      if (path.includes('/review')) return Promise.resolve({ ...reviewBase, github_pr: 'acme/web#123' })
      if (path.includes('/github/pr/')) return Promise.resolve({ number: 123, title: 'x', state: 'merged', merged_at: '2026-06-02T10:00:00Z', created_at: '2026-06-01T00:00:00Z', html_url: '' })
      return Promise.resolve({})
    })
    render(<SpecReview specPath="docs/spec/demo.md" />)
    await waitFor(() => expect(screen.getByTestId('spec-pr-badge')).toHaveTextContent(/Merged on/i))
  })

  it('shows the link input and no badge when no PR is linked', async () => {
    mockedGet.mockResolvedValue({ ...reviewBase, github_pr: '' })
    render(<SpecReview specPath="docs/spec/demo.md" />)
    await waitFor(() => expect(screen.getByTestId('spec-pr-input')).toBeInTheDocument())
    expect(screen.queryByTestId('spec-pr-badge')).not.toBeInTheDocument()
  })
})
