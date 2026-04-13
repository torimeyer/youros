import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Projects from './Projects'
import { useAppStore } from '../stores/app'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
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

const mockProjectsResponse = {
  projects: [
    {
      name: 'torios',
      path: '/home/user/torios',
      has_git: true,
      file_count: 120,
      last_modified: new Date().toISOString(),
      description: 'Personal operating system',
      project_type: 'node',
    },
    {
      name: 'api-server',
      path: '/home/user/api-server',
      has_git: true,
      file_count: 45,
      last_modified: null,
      description: null,
      project_type: 'python',
    },
    {
      name: 'my-scripts',
      path: '/home/user/my-scripts',
      has_git: false,
      file_count: 3,
      last_modified: new Date(Date.now() - 86400000).toISOString(),
      description: 'Random utility scripts',
      project_type: 'folder',
    },
  ],
}

function renderProjects() {
  return render(
    <MemoryRouter>
      <Projects />
    </MemoryRouter>
  )
}

describe('Projects page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ chatOpen: false, osName: 'myOS', darkMode: true })
    mockedApiGet.mockResolvedValue(mockProjectsResponse)
  })

  it('renders the page title', async () => {
    renderProjects()
    expect(screen.getByRole('heading', { name: 'Projects' })).toBeInTheDocument()
  })

  it('renders subtitle text', async () => {
    renderProjects()
    expect(
      screen.getByText('All directories in the myOS workspace.')
    ).toBeInTheDocument()
  })

  it('fetches and renders project cards on mount', async () => {
    renderProjects()

    await waitFor(() => {
      expect(screen.getByText('torios')).toBeInTheDocument()
    })
    expect(screen.getByText('api-server')).toBeInTheDocument()
    expect(screen.getByText('my-scripts')).toBeInTheDocument()
  })

  it('calls api.get with /projects on mount', async () => {
    renderProjects()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/projects')
    })
  })

  it('shows loading state before data arrives', () => {
    mockedApiGet.mockReturnValue(new Promise(() => {}))
    renderProjects()

    expect(screen.getByText('Loading projects...')).toBeInTheDocument()
  })

  it('shows project descriptions when available', async () => {
    renderProjects()

    await waitFor(() => {
      expect(screen.getByText('Personal operating system')).toBeInTheDocument()
    })
    expect(screen.getByText('Random utility scripts')).toBeInTheDocument()
  })

  it('shows project type badges', async () => {
    renderProjects()

    await waitFor(() => {
      expect(screen.getByText('Node.js')).toBeInTheDocument()
    })
    expect(screen.getByText('Python')).toBeInTheDocument()
    expect(screen.getByText('Folder')).toBeInTheDocument()
  })

  it('shows git badges for projects with git', async () => {
    renderProjects()

    await waitFor(() => {
      expect(screen.getByText('torios')).toBeInTheDocument()
    })

    const gitBadges = screen.getAllByText('git')
    // torios and api-server have git, my-scripts does not
    expect(gitBadges.length).toBe(2)
  })

  it('shows file count for each project', async () => {
    renderProjects()

    await waitFor(() => {
      expect(screen.getByText('120 items')).toBeInTheDocument()
    })
    expect(screen.getByText('45 items')).toBeInTheDocument()
    expect(screen.getByText('3 items')).toBeInTheDocument()
  })

  it('shows empty state when no projects', async () => {
    mockedApiGet.mockResolvedValue({ projects: [] })
    renderProjects()

    await waitFor(() => {
      expect(screen.getByText('No projects found.')).toBeInTheDocument()
    })
  })

  it('shows error state on API failure', async () => {
    mockedApiGet.mockRejectedValue(new Error('Network error'))
    renderProjects()

    await waitFor(() => {
      expect(
        screen.getByText('Could not load projects. Make sure the API is running.')
      ).toBeInTheDocument()
    })
  })

  it('shows project count in footer', async () => {
    renderProjects()

    await waitFor(() => {
      expect(screen.getByText('3 projects in workspace')).toBeInTheDocument()
    })
  })

  it('shows singular "project" for single project', async () => {
    mockedApiGet.mockResolvedValue({
      projects: [mockProjectsResponse.projects[0]],
    })
    renderProjects()

    await waitFor(() => {
      expect(screen.getByText('1 project in workspace')).toBeInTheDocument()
    })
  })

  it('has a Refresh button', async () => {
    renderProjects()

    await waitFor(() => {
      expect(screen.getByText('Refresh')).toBeInTheDocument()
    })
  })

  it('Refresh button refetches data', async () => {
    renderProjects()

    await waitFor(() => {
      expect(mockedApiGet.mock.calls.length).toBeGreaterThanOrEqual(1)
    })
    const initialCalls = mockedApiGet.mock.calls.length

    fireEvent.click(screen.getByText('Refresh'))

    await waitFor(() => {
      expect(mockedApiGet.mock.calls.length).toBeGreaterThan(initialCalls)
    })
  })

  it('shows project paths', async () => {
    renderProjects()

    await waitFor(() => {
      expect(screen.getByText('/home/user/torios')).toBeInTheDocument()
    })
    expect(screen.getByText('/home/user/api-server')).toBeInTheDocument()
    expect(screen.getByText('/home/user/my-scripts')).toBeInTheDocument()
  })
})
