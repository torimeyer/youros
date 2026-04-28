import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import AdminCatalog from './Catalog'

const mockEntries = [
  {
    id: 'entry-1',
    kind: 'agentfile' as const,
    name: 'code-reviewer',
    version: 2,
    content: 'FROM opus\nPROMPT "Review code."\n',
    signed_by_org: 'admin',
    published_at: '2026-04-01T00:00:00Z',
    status: 'published' as const,
  },
]

const mockVersions = [
  { ...mockEntries[0], version: 2, status: 'published' as const, id: 'entry-1' },
  {
    id: 'entry-0',
    kind: 'agentfile' as const,
    name: 'code-reviewer',
    version: 1,
    content: 'FROM auto\n',
    signed_by_org: 'admin',
    published_at: '2026-03-01T00:00:00Z',
    status: 'rolled_back' as const,
  },
]

vi.mock('../../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

vi.mock('../../stores/app', () => ({
  useAppStore: (selector: (s: { darkMode: boolean }) => unknown) =>
    selector({ darkMode: false }),
}))

vi.mock('../../components/Icon', () => ({
  default: ({ name }: { name: string }) => <span data-testid={`icon-${name}`}>{name}</span>,
}))

import { api } from '../../lib/api'

const mockApi = api as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
}

describe('AdminCatalog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the catalog page with heading', async () => {
    mockApi.get.mockResolvedValue({ entries: [] })
    render(<AdminCatalog />)
    expect(screen.getByTestId('catalog-page')).toBeInTheDocument()
    expect(screen.getByText('Team Catalog')).toBeInTheDocument()
  })

  it('shows empty state when no entries', async () => {
    mockApi.get.mockResolvedValue({ entries: [] })
    render(<AdminCatalog />)
    await waitFor(() => {
      expect(screen.getByTestId('catalog-empty')).toBeInTheDocument()
    })
  })

  it('renders catalog list with entries', async () => {
    mockApi.get.mockResolvedValue({ entries: mockEntries })
    render(<AdminCatalog />)
    await waitFor(() => {
      expect(screen.getByTestId('catalog-list')).toBeInTheDocument()
    })
    expect(screen.getByText('code-reviewer')).toBeInTheDocument()
    expect(screen.getByText('v2')).toBeInTheDocument()
  })

  it('publish button disabled when fields empty', async () => {
    mockApi.get.mockResolvedValue({ entries: [] })
    render(<AdminCatalog />)
    const btn = screen.getByTestId('publish-button')
    expect(btn).toBeDisabled()
  })

  it('publish flow calls api.post and refreshes list', async () => {
    mockApi.get
      .mockResolvedValueOnce({ entries: [] })
      .mockResolvedValueOnce({ entries: mockEntries })
    mockApi.post.mockResolvedValue({ entry: mockEntries[0] })

    render(<AdminCatalog />)

    fireEvent.change(screen.getByTestId('publish-name-input'), {
      target: { value: 'code-reviewer' },
    })
    fireEvent.change(screen.getByTestId('publish-content-input'), {
      target: { value: 'FROM opus\n' },
    })

    const btn = screen.getByTestId('publish-button')
    expect(btn).not.toBeDisabled()
    fireEvent.click(btn)

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith('/team/catalog/agentfiles', {
        name: 'code-reviewer',
        content: 'FROM opus\n',
      })
    })

    await waitFor(() => {
      expect(screen.getByTestId('publish-success')).toBeInTheDocument()
    })
  })

  it('shows history panel when History button clicked', async () => {
    mockApi.get
      .mockResolvedValueOnce({ entries: mockEntries })
      .mockResolvedValueOnce({ versions: mockVersions })

    render(<AdminCatalog />)

    await waitFor(() => {
      expect(screen.getByTestId(`history-btn-entry-1`)).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('history-btn-entry-1'))

    await waitFor(() => {
      expect(screen.getByTestId('version-history')).toBeInTheDocument()
    })

    expect(screen.getByTestId('version-row-entry-1')).toBeInTheDocument()
    expect(screen.getByTestId('version-row-entry-0')).toBeInTheDocument()
  })

  it('rollback button appears only for non-published versions', async () => {
    mockApi.get
      .mockResolvedValueOnce({ entries: mockEntries })
      .mockResolvedValueOnce({ versions: mockVersions })

    render(<AdminCatalog />)

    await waitFor(() => screen.getByTestId('history-btn-entry-1'))
    fireEvent.click(screen.getByTestId('history-btn-entry-1'))

    await waitFor(() => screen.getByTestId('version-history'))

    // v2 is current (published) — no rollback button
    expect(screen.queryByTestId('rollback-btn-entry-1')).toBeNull()
    // v1 is rolled_back — rollback button present
    expect(screen.getByTestId('rollback-btn-entry-0')).toBeInTheDocument()
  })

  it('rollback calls api.post and refreshes', async () => {
    const rolledEntry = { ...mockEntries[0], version: 3, id: 'entry-2' }
    mockApi.get
      .mockResolvedValueOnce({ entries: mockEntries })
      .mockResolvedValueOnce({ versions: mockVersions })
      .mockResolvedValueOnce({ entries: [rolledEntry] })
      .mockResolvedValueOnce({ versions: [...mockVersions, { ...rolledEntry }] })
    mockApi.post.mockResolvedValue({ entry: rolledEntry })

    render(<AdminCatalog />)
    await waitFor(() => screen.getByTestId('history-btn-entry-1'))
    fireEvent.click(screen.getByTestId('history-btn-entry-1'))
    await waitFor(() => screen.getByTestId('rollback-btn-entry-0'))
    fireEvent.click(screen.getByTestId('rollback-btn-entry-0'))

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith('/team/catalog/entry-0/rollback', {})
    })
  })

  it('install calls api.post and shows feedback', async () => {
    mockApi.get.mockResolvedValue({ entries: mockEntries })
    mockApi.post.mockResolvedValue({ ok: true, installed_path: '/home/.myos/agentfiles/code-reviewer.agent' })

    render(<AdminCatalog />)
    await waitFor(() => screen.getByTestId('install-btn-entry-1'))
    fireEvent.click(screen.getByTestId('install-btn-entry-1'))

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith('/team/catalog/entry-1/install', {})
    })
    await waitFor(() => {
      expect(screen.getByTestId('install-feedback-entry-1')).toBeInTheDocument()
    })
  })

  it('close history button hides the panel', async () => {
    mockApi.get
      .mockResolvedValueOnce({ entries: mockEntries })
      .mockResolvedValueOnce({ versions: mockVersions })

    render(<AdminCatalog />)
    await waitFor(() => screen.getByTestId('history-btn-entry-1'))
    fireEvent.click(screen.getByTestId('history-btn-entry-1'))
    await waitFor(() => screen.getByTestId('version-history'))

    fireEvent.click(screen.getByTestId('close-history'))
    expect(screen.queryByTestId('version-history')).toBeNull()
  })
})
