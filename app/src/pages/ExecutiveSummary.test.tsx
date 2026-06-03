import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ExecutiveSummary from './ExecutiveSummary'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

// jsdom does not provide window.matchMedia.
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

const configuredResponse = {
  configured: true,
  krs: [
    {
      key: 'KR-1',
      title: 'Reduce sign-in failures',
      health: 'at_risk',
      reasons: ['One initiative is behind'],
      initiatives: [
        {
          key: 'INIT-10',
          title: 'Passwordless rollout',
          health: 'off_track',
          reasons: ['Blocked ticket', 'Due date passed'],
          audit: {
            missing_initiative_parent: false,
            missing_kr_link: false,
            missing_description: true,
            missing_ref_docs: false,
          },
        },
      ],
    },
  ],
  audit_findings: [
    { key: 'TASK-7', finding: 'Missing initiative parent', detail: 'No parent set on this ticket' },
    { key: 'TASK-8', finding: 'Missing description', detail: 'Description field is empty' },
  ],
  pending_approvals: [
    {
      key: 'INIT-10',
      title: 'Passwordless rollout',
      draft_value: 'off_track',
      draft_note: 'Mitigation: adding two engineers. Help needed: security review.',
      why: 'Vendor delay pushed the timeline two weeks.',
    },
  ],
}

const unconfiguredResponse = {
  configured: false,
  krs: [],
  audit_findings: [],
  pending_approvals: [],
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ExecutiveSummary />
    </MemoryRouter>,
  )
}

describe('ExecutiveSummary', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the three sections from a configured response', async () => {
    mockedApiGet.mockResolvedValueOnce(configuredResponse)
    renderPage()

    // Section 1: rollup hygiene checklist from audit findings.
    expect(await screen.findByTestId('section-rollup-hygiene')).toBeTruthy()
    expect(screen.getByText('Missing initiative parent')).toBeTruthy()
    // Each audit row deep-links to the Jira issue (in-app route).
    const auditLink = screen.getByTestId('audit-link-TASK-7') as HTMLAnchorElement
    expect(auditLink.getAttribute('href')).toContain('/jira/TASK-7')

    // Section 2: KR -> initiative rollup tree.
    expect(screen.getByTestId('section-rollup-tree')).toBeTruthy()
    expect(screen.getByText('Reduce sign-in failures')).toBeTruthy()
    expect(screen.getByText('Passwordless rollout')).toBeTruthy()
    // Health chips render with plain labels.
    expect(screen.getAllByText('Off track').length).toBeGreaterThan(0)
    expect(screen.getAllByText('At risk').length).toBeGreaterThan(0)

    // Section 3: confidence updates awaiting approval.
    expect(screen.getByTestId('section-pending-approvals')).toBeTruthy()
    expect(screen.getByText(/Mitigation: adding two engineers/)).toBeTruthy()
    expect(screen.getByTestId('approve-INIT-10')).toBeTruthy()
  })

  it('shows the unconfigured empty state when configured is false', async () => {
    mockedApiGet.mockResolvedValueOnce(unconfiguredResponse)
    renderPage()

    expect(await screen.findByTestId('empty-state')).toBeTruthy()
    // No section content and no write affordances render in the empty state.
    expect(screen.queryByTestId('section-rollup-hygiene')).toBeNull()
    expect(screen.queryByTestId('section-rollup-tree')).toBeNull()
    expect(screen.queryByTestId('section-pending-approvals')).toBeNull()
  })

  it('Approve & write to Jira posts to the approve endpoint with the right body', async () => {
    mockedApiGet.mockResolvedValueOnce(configuredResponse)
    mockedApiPost.mockResolvedValueOnce({
      key: 'INIT-10',
      written_value: 'off_track',
      comment_id: 'c1',
      ok: true,
    })
    renderPage()

    const approveBtn = await screen.findByTestId('approve-INIT-10')
    fireEvent.click(approveBtn)

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith(
        '/api/portfolio/confidence/INIT-10/approve',
        {
          value: 'off_track',
          note: 'Mitigation: adding two engineers. Help needed: security review.',
          why: 'Vendor delay pushed the timeline two weeks.',
        },
      )
    })
  })
})
