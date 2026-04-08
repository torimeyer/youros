import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Files from './Files'
import { useAppStore } from '../stores/app'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)
const mockedApiPost = vi.mocked(api.post)

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

const mockFileReadResponse = {
  content: 'console.log("hello world")',
  type: 'text' as const,
  size: 26,
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
    useAppStore.setState({ chatOpen: false, osName: 'myOS', darkMode: true })
    mockedApiGet.mockResolvedValue(mockProjectsResponse)
    mockedApiPost.mockResolvedValue({})
  })

  // --- Root view: project list ---

  it('renders the page title', async () => {
    renderFiles()
    expect(screen.getByRole('heading', { name: 'Files' })).toBeInTheDocument()
  })

  it('fetches and renders project list on mount', async () => {
    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('my-app')).toBeInTheDocument()
    })
    expect(screen.getByText('api-service')).toBeInTheDocument()
  })

  it('calls api.get with /projects on mount', async () => {
    renderFiles()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/projects')
    })
  })

  it('shows loading text before projects arrive', () => {
    mockedApiGet.mockReturnValue(new Promise(() => {}))
    renderFiles()

    expect(screen.getByText('Loading projects...')).toBeInTheDocument()
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

  it('shows error state on API failure', async () => {
    mockedApiGet.mockRejectedValue(new Error('Network error'))
    renderFiles()

    await waitFor(() => {
      expect(
        screen.getByText('Could not load projects. Make sure the API is running.')
      ).toBeInTheDocument()
    })
  })

  it('shows footer text when projects are loaded', async () => {
    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('Click a project to browse its files')).toBeInTheDocument()
    })
  })

  it('has a Refresh button', async () => {
    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('Refresh')).toBeInTheDocument()
    })
  })

  it('Refresh button refetches projects', async () => {
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

    // Click the project button
    const projectButton = screen.getByText('my-app').closest('button')!
    fireEvent.click(projectButton)

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith(
        '/projects/browse?path=my-app'
      )
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
    expect(screen.getByText('Projects')).toBeInTheDocument()
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

    // Should go back to root project view
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
      expect(screen.getByText('Projects')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Projects'))

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

  // --- File preview modal ---

  it('clicking a file opens the preview pane', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/projects/browse')) return mockBrowseResponse
      if (path.startsWith('/files/read')) return mockFileReadResponse
      return {}
    })

    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('my-app')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('my-app').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('README.md')).toBeInTheDocument()
    })

    // Click on the file
    fireEvent.click(screen.getByText('README.md').closest('button')!)

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith(
        '/files/read?path=my-app%2FREADME.md'
      )
    })
  })

  it('preview pane shows file content for text files', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/projects/browse')) return mockBrowseResponse
      if (path.startsWith('/files/read')) return mockFileReadResponse
      return {}
    })

    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('my-app')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('my-app').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('index.ts')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('index.ts').closest('button')!)

    // The code highlighter splits content into many token spans, so we
    // assert on a recognizable piece of the source instead of the whole line.
    await waitFor(() => {
      expect(screen.getByText('console')).toBeInTheDocument()
    })
    expect(screen.getByText('log')).toBeInTheDocument()
  })

  it('preview pane has Close preview button', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/projects/browse')) return mockBrowseResponse
      if (path.startsWith('/files/read')) return mockFileReadResponse
      return {}
    })

    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('my-app')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('my-app').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('README.md')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('README.md').closest('button')!)

    await waitFor(() => {
      expect(screen.getByTitle('Close preview')).toBeInTheDocument()
    })
  })

  it('Close preview button dismisses the pane', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/projects/browse')) return mockBrowseResponse
      if (path.startsWith('/files/read')) return mockFileReadResponse
      return {}
    })

    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('my-app')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('my-app').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('README.md')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('README.md').closest('button')!)

    await waitFor(() => {
      expect(screen.getByTitle('Close preview')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTitle('Close preview'))

    // Pane should be dismissed. File content should no longer be visible.
    await waitFor(() => {
      expect(screen.queryByTitle('Close preview')).not.toBeInTheDocument()
    })
  })

  it('preview pane has Open externally button', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/projects/browse')) return mockBrowseResponse
      if (path.startsWith('/files/read')) return mockFileReadResponse
      return {}
    })

    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('my-app')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('my-app').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('README.md')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('README.md').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('Open externally')).toBeInTheDocument()
    })
  })

  it('Open externally button calls the open-file endpoint', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/projects/browse')) return mockBrowseResponse
      if (path.startsWith('/files/read')) return mockFileReadResponse
      return {}
    })

    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('my-app')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('my-app').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('README.md')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('README.md').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('Open externally')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Open externally'))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/projects/open-file', {
        path: 'my-app/README.md',
      })
    })
  })

  it('shows unsupported message when the file type cannot be previewed', async () => {
    // Use an unknown-extension file so the preview pane lands in the fallback branch.
    const unknownBrowse = {
      ...mockBrowseResponse,
      entries: [
        {
          name: 'mystery.xyz',
          kind: 'file' as const,
          path: 'my-app/mystery.xyz',
          item_count: null,
          size: 4096,
          size_display: '4.0 KB',
          last_modified: new Date().toISOString(),
        },
      ],
    }

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/projects/browse')) return unknownBrowse
      if (path.startsWith('/files/read'))
        return { content: null, type: 'binary', size: 4096 }
      return {}
    })

    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('my-app')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('my-app').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('mystery.xyz')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('mystery.xyz').closest('button')!)

    await waitFor(() => {
      expect(
        screen.getByText('Preview not available for this file type.')
      ).toBeInTheDocument()
    })
  })

  it('shows preview error message on API failure', async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === '/projects') return mockProjectsResponse
      if (path.startsWith('/projects/browse')) return mockBrowseResponse
      if (path.startsWith('/files/read')) throw new Error('fail')
      return {}
    })

    renderFiles()

    await waitFor(() => {
      expect(screen.getByText('my-app')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('my-app').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('README.md')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('README.md').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText("Couldn't open this file.")).toBeInTheDocument()
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

    // Click the folder
    fireEvent.click(screen.getByText('src').closest('button')!)

    await waitFor(() => {
      expect(screen.getByText('main.ts')).toBeInTheDocument()
    })
  })
})
