import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Files from './Files'
import { useAppStore } from '../stores/app'

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
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

import { api, ApiError } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)
const mockedApiPost = vi.mocked(api.post)
const mockedApiDelete = vi.mocked(api.delete)

const mockProjectsResponse = {
  projects: [
    {
      name: 'my-app',
      path: '/home/user/my-app',
      has_git: true,
      file_count: 42,
      last_modified: new Date().toISOString(),
      description: 'A sample application',
      project_type: 'node',
    },
    {
      name: 'api-service',
      path: '/home/user/api-service',
      has_git: false,
      file_count: 15,
      last_modified: null,
      description: null,
      project_type: 'python',
    },
  ],
}

const mockBrowseResponse = {
  current_path: 'my-app',
  parent_path: '',
  breadcrumbs: [{ name: 'my-app', path: 'my-app' }],
  entries: [
    {
      name: 'src',
      kind: 'folder' as const,
      path: 'my-app/src',
      item_count: 10,
      size: null,
      size_display: '10 items',
      last_modified: new Date().toISOString(),
    },
    {
      name: 'README.md',
      kind: 'file' as const,
      path: 'my-app/README.md',
      item_count: null,
      size: 1024,
      size_display: '1.0 KB',
      last_modified: new Date().toISOString(),
    },
    {
      name: 'index.ts',
      kind: 'file' as const,
      path: 'my-app/index.ts',
      item_count: null,
      size: 256,
      size_display: '256 B',
      last_modified: new Date().toISOString(),
    },
  ],
}

function renderFiles() {
  return render(
    <MemoryRouter>
      <Files />
    </MemoryRouter>
  )
}

describe('Files page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ chatOpen: false, osName: 'yourOS', darkMode: true })
    mockedApiGet.mockResolvedValue(mockProjectsResponse)
    mockedApiPost.mockResolvedValue({})
    mockedApiDelete.mockResolvedValue({ ok: true, path: '' })
  })

  // --- Page structure ---

  it('renders the page title', async () => {
    renderFiles()
    expect(screen.getAllByRole('heading', { name: 'Projects' }).length).toBeGreaterThan(0)
  })

  it('has a Refresh button', async () => {
    renderFiles()
    expect(screen.getByText('Refresh')).toBeInTheDocument()
  })

  it('Refresh button triggers a refetch', async () => {
    renderFiles()

    await waitFor(() => {
      expect(mockedApiGet.mock.calls.length).toBeGreaterThanOrEqual(1)
    })
    const initialCalls = mockedApiGet.mock.calls.length

    fireEvent.click(screen.getByText('Refresh'))

    await waitFor(() => {
      expect(mockedApiGet.mock.calls.length).toBeGreaterThan(initialCalls)
    })
  })

  // --- Project list ---

  it('fetches and renders project list', async () => {
    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('my-app')).toBeInTheDocument()
    })
    expect(screen.getByText('api-service')).toBeInTheDocument()
  })

  it('shows project descriptions when available', async () => {
    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('A sample application')).toBeInTheDocument()
    })
  })

  it('shows git badge for projects with git', async () => {
    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('my-app')).toBeInTheDocument()
    })

    const gitBadges = screen.getAllByText('git')
    expect(gitBadges.length).toBeGreaterThanOrEqual(1)
  })

  it('shows project type label', async () => {
    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('Node.js')).toBeInTheDocument()
    })
    expect(screen.getByText('Python')).toBeInTheDocument()
  })

  it('shows file count for each project', async () => {
    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('42')).toBeInTheDocument()
    })
    expect(screen.getByText('15')).toBeInTheDocument()
  })

  it('shows empty state when no projects', async () => {
    mockedApiGet.mockResolvedValue({ projects: [] })

    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('No projects found.')).toBeInTheDocument()
    })
  })

  it('shows footer text when projects are loaded', async () => {
    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('Click a project to browse its files')).toBeInTheDocument()
    })
  })

  it('recent docs renders rows in newest-first order', async () => {
    const newerDoc = {
      name: 'newer-doc.md',
      path: '/home/user/.myos/files/newer-doc.md',
      size: 512,
      size_display: '512 B',
      last_modified: new Date('2026-04-25T12:00:00Z').toISOString(),
      snippet: 'newer content',
    }
    const olderDoc = {
      name: 'older-doc.md',
      path: '/home/user/.myos/files/older-doc.md',
      size: 256,
      size_display: '256 B',
      last_modified: new Date('2026-04-25T10:00:00Z').toISOString(),
      snippet: 'older content',
    }
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/docs/recent')) return { files: [olderDoc, newerDoc] }
      return {}
    })

    renderFiles()

    await waitFor(() => {
      expect(screen.getByTestId(`recent-doc-row-${newerDoc.path}`)).toBeInTheDocument()
    })

    const rows = screen.getAllByTestId(/^recent-doc-row-/)
    expect(rows[0]).toHaveAttribute('data-testid', `recent-doc-row-${newerDoc.path}`)
    expect(rows[1]).toHaveAttribute('data-testid', `recent-doc-row-${olderDoc.path}`)
  })

  // --- Directory browser view ---

  it('clicking a project navigates to the directory browser', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/projects/browse')) return mockBrowseResponse
      return {}
    })

    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('my-app')).toBeInTheDocument()
    })

    const projectButton = screen.getByText('my-app').closest('button')!
    fireEvent.click(projectButton)

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/projects/browse?path=my-app')
    })
  })

  it('shows directory entries after navigating into a project', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/projects/browse')) return mockBrowseResponse
      return {}
    })

    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('my-app')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('my-app').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('src')).toBeInTheDocument()
    })
    expect(screen.getByText('README.md')).toBeInTheDocument()
    expect(screen.getByText('index.ts')).toBeInTheDocument()
  })

  it('renders a forbidden empty state (not a spinner) when browse returns 403', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/projects/browse')) {
        throw new ApiError(403, '{"detail":"Path is outside the workspace."}')
      }
      return {}
    })

    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('my-app')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('my-app').closest('button')!)

    await waitFor(() => {
      expect(screen.getByTestId('files-forbidden-empty-state')).toBeInTheDocument()
    })
    expect(
      screen.queryByText('Could not load this folder. It may not exist or the API may be down.')
    ).not.toBeInTheDocument()
    expect(screen.queryByText('Loading folder contents...')).not.toBeInTheDocument()
  })

  it('shows breadcrumb navigation when browsing a directory', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/projects/browse')) return mockBrowseResponse
      return {}
    })

    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('my-app')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('my-app').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('Back')).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: 'Projects' })).toBeInTheDocument()
  })

  it('Back button returns to parent level', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/projects/browse')) return mockBrowseResponse
      return {}
    })

    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('my-app')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('my-app').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('Back')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Back'))

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/projects')
    })
  })

  it('Projects breadcrumb returns to root', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/projects/browse')) return mockBrowseResponse
      return {}
    })

    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('my-app')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('my-app').closest('button')!)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Projects' })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Projects' }))

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/projects')
    })
  })

  it('shows item count for directory entries', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/projects/browse')) return mockBrowseResponse
      return {}
    })

    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('my-app')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('my-app').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('3 items in this folder')).toBeInTheDocument()
    })
  })

  it('shows empty folder message when directory has no entries', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/projects/browse'))
        return { current_path: 'empty', parent_path: '', breadcrumbs: [], entries: [] }
      return {}
    })

    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('my-app')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('my-app').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('This folder is empty.')).toBeInTheDocument()
    })
  })

  it('clicking a folder navigates into it', async () => {
    const nestedBrowse = {
      current_path: 'my-app/src',
      parent_path: 'my-app',
      breadcrumbs: [
        { name: 'my-app', path: 'my-app' },
        { name: 'src', path: 'my-app/src' },
      ],
      entries: [
        {
          name: 'main.ts',
          kind: 'file' as const,
          path: 'my-app/src/main.ts',
          item_count: null,
          size: 512,
          size_display: '512 B',
          last_modified: new Date().toISOString(),
        },
      ],
    }

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path === '/projects/browse?path=my-app') return mockBrowseResponse
      if (path === '/projects/browse?path=my-app%2Fsrc') return nestedBrowse
      return {}
    })

    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('my-app')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('my-app').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('src')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('src').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('main.ts')).toBeInTheDocument()
    })
  })

  // --- Recent Documents delete button ---

  const mockRecentDocsResponse = {
    files: [
      {
        name: 'roadmap-2026-04-16T10-00.md',
        path: '/home/user/.myos/files/roadmap-2026-04-16T10-00.md',
        size: 512,
        size_display: '512 B',
        last_modified: new Date().toISOString(),
        snippet: 'Plan for Q2',
      },
    ],
  }

  it('shows a delete button on each Recent Documents row', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/docs/recent')) return mockRecentDocsResponse
      return {}
    })

    renderFiles()

    await waitFor(() => {
      expect(
        screen.getByTestId(`recent-doc-delete-${mockRecentDocsResponse.files[0].path}`)
      ).toBeInTheDocument()
    })
  })

  it('clicking the delete button opens the in-app confirm modal (not window.confirm)', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm')
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/docs/recent')) return mockRecentDocsResponse
      return {}
    })

    renderFiles()

    const doc = mockRecentDocsResponse.files[0]
    await waitFor(() => {
      expect(screen.getByTestId(`recent-doc-delete-${doc.path}`)).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId(`recent-doc-delete-${doc.path}`))

    const dialog = await screen.findByRole('dialog')
    expect(dialog).toBeInTheDocument()
    expect(screen.getByText(`Delete ${doc.name}?`)).toBeInTheDocument()

    expect(confirmSpy).not.toHaveBeenCalled()
    expect(mockedApiDelete).not.toHaveBeenCalled()

    confirmSpy.mockRestore()
  })

  it('clicking Confirm in the modal performs the delete', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/docs/recent')) return mockRecentDocsResponse
      return {}
    })

    renderFiles()

    const doc = mockRecentDocsResponse.files[0]
    await waitFor(() => {
      expect(screen.getByTestId(`recent-doc-delete-${doc.path}`)).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId(`recent-doc-delete-${doc.path}`))

    const confirmBtn = await screen.findByTestId('confirm-modal-confirm')
    fireEvent.click(confirmBtn)

    await waitFor(() => {
      expect(mockedApiDelete).toHaveBeenCalledWith(
        `/docs/recent?path=${encodeURIComponent(doc.path)}`
      )
    })

    await waitFor(() => {
      expect(screen.getByTestId('files-delete-success-toast')).toBeInTheDocument()
    })
  })

  it('clicking Cancel in the modal does not perform the delete', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/docs/recent')) return mockRecentDocsResponse
      return {}
    })

    renderFiles()

    const doc = mockRecentDocsResponse.files[0]
    await waitFor(() => {
      expect(screen.getByTestId(`recent-doc-delete-${doc.path}`)).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId(`recent-doc-delete-${doc.path}`))

    const cancelBtn = await screen.findByTestId('confirm-modal-cancel')
    fireEvent.click(cancelBtn)

    expect(mockedApiDelete).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('surfaces an in-app error toast when delete fails', async () => {
    const alertSpy = vi.spyOn(window, 'alert')
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/docs/recent')) return mockRecentDocsResponse
      return {}
    })
    mockedApiDelete.mockRejectedValueOnce(new Error('boom'))

    renderFiles()

    const doc = mockRecentDocsResponse.files[0]
    await waitFor(() => {
      expect(screen.getByTestId(`recent-doc-delete-${doc.path}`)).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId(`recent-doc-delete-${doc.path}`))
    fireEvent.click(await screen.findByTestId('confirm-modal-confirm'))

    await waitFor(() => {
      expect(screen.getByTestId('files-delete-error-toast')).toBeInTheDocument()
    })
    expect(alertSpy).not.toHaveBeenCalled()

    alertSpy.mockRestore()
  })
})
