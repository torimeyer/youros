import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
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
})
