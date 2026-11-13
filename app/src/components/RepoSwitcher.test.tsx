import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import RepoSwitcher from './RepoSwitcher'
import { useAppStore } from '../stores/app'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
  },
}))

import { api } from '../lib/api'

const mockedGet = vi.mocked(api.get)
const mockedPost = vi.mocked(api.post)

function renderSwitcher(initialPath = '/') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="*" element={<RepoSwitcher />} />
        <Route path="/projects" element={<div data-testid="projects-page">Projects View</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('RepoSwitcher', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    useAppStore.setState({ activeRepoPath: null })
  })

  it('renders repo-switcher trigger with default torios basename when activeRepoPath is null', () => {
    renderSwitcher()
    const trigger = screen.getByTestId('repo-switcher')
    expect(trigger).toBeInTheDocument()
    expect(trigger.textContent).toContain('torios')
    expect(mockedGet).not.toHaveBeenCalled()
  })

  it('displays the basename of activeRepoPath from useAppStore', () => {
    useAppStore.setState({ activeRepoPath: '/Users/dev/claude/alpha-service' })
    renderSwitcher()
    expect(screen.getByTestId('repo-switcher').textContent).toContain('alpha-service')
  })

  it('fetches /projects/repos lazily when opened and switches active repo on selection', async () => {
    mockedGet.mockResolvedValueOnce({
      active_repo_path: '/Users/dev/claude/torios',
      repos: [
        {
          name: 'torios',
          path: '/Users/dev/claude/torios',
          is_active: true,
          branch: 'main',
        },
        {
          name: 'beta-engine',
          path: '/Users/dev/claude/beta-engine',
          is_active: false,
          branch: 'feat/v2',
        },
      ],
    })
    mockedPost.mockResolvedValueOnce({
      active_repo_path: '/Users/dev/claude/beta-engine',
      name: 'beta-engine',
    })

    renderSwitcher()
    fireEvent.click(screen.getByTestId('repo-switcher'))

    await waitFor(() => {
      expect(mockedGet).toHaveBeenCalledWith('/projects/repos')
      expect(screen.getByText('beta-engine')).toBeInTheDocument()
    })
    expect(screen.getByText('feat/v2')).toBeInTheDocument()

    fireEvent.click(screen.getByText('beta-engine'))

    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith('/projects/active-repo', {
        path: '/Users/dev/claude/beta-engine',
      })
      expect(useAppStore.getState().activeRepoPath).toBe('/Users/dev/claude/beta-engine')
    })
  })

  it('navigates to /projects when clicking Manage repos in the dropdown footer', async () => {
    mockedGet.mockResolvedValueOnce({
      active_repo_path: '/Users/dev/claude/torios',
      repos: [
        {
          name: 'torios',
          path: '/Users/dev/claude/torios',
          is_active: true,
          branch: 'main',
        },
      ],
    })

    renderSwitcher()
    fireEvent.click(screen.getByTestId('repo-switcher'))

    const manageBtn = await screen.findByText('Manage repos')
    fireEvent.click(manageBtn)

    await waitFor(() => {
      expect(screen.getByTestId('projects-page')).toBeInTheDocument()
    })
  })
})
