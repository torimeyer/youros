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

  it('uses theme-mapped trigger classes and accent-text folder icon without unmapped dark-opacity or blue classes (→3106)', () => {
    document.documentElement.setAttribute('data-theme', 'light')
    document.documentElement.style.setProperty('--color-accent', '#ec4899')

    try {
      renderSwitcher()
      const trigger = screen.getByTestId('repo-switcher')

      expect(trigger.className).not.toContain('bg-slate-900/70')
      expect(trigger.className).not.toContain('hover:bg-slate-800/80')
      expect(trigger.className).toContain('bg-slate-800')
      expect(trigger.className).toContain('border-slate-700')
      expect(trigger.className).toContain('hover:bg-slate-700')
      expect(trigger.className).toContain('text-slate-200')

      const folderIcon = Array.from(trigger.querySelectorAll('.material-symbols-outlined')).find(
        (el) => el.textContent === 'folder',
      )
      expect(folderIcon).toBeDefined()
      expect(folderIcon?.className).not.toContain('text-blue-400')
      expect(folderIcon?.className).toContain('accent-text')
    } finally {
      document.documentElement.removeAttribute('data-theme')
      document.documentElement.style.removeProperty('--color-accent')
    }
  })

  it('styles dropdown listbox, active repo option, and Manage repos footer with theme-mapped surfaces and accent classes (→3106)', async () => {
    document.documentElement.setAttribute('data-theme', 'light')
    document.documentElement.style.setProperty('--color-accent', '#ec4899')

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

    try {
      renderSwitcher()
      fireEvent.click(screen.getByTestId('repo-switcher'))

      const listbox = await screen.findByRole('listbox')
      expect(listbox.className).not.toContain('border-slate-700/80')
      expect(listbox.className).toContain('bg-slate-800')
      expect(listbox.className).toContain('border-slate-700')

      const options = await screen.findAllByRole('option')
      const activeOption = options.find((opt) => opt.getAttribute('aria-selected') === 'true')
      const inactiveOption = options.find((opt) => opt.getAttribute('aria-selected') === 'false')

      expect(activeOption).toBeDefined()
      expect(activeOption?.className).not.toContain('bg-blue-500/15')
      expect(activeOption?.className).not.toContain('text-blue-300')
      expect(activeOption?.className).toContain('accent-highlight')

      const checkIcon = activeOption?.querySelector('.material-symbols-outlined')
      expect(checkIcon?.className).not.toContain('text-blue-400')
      expect(checkIcon?.className).toContain('accent-text')

      expect(inactiveOption).toBeDefined()
      expect(inactiveOption?.className).not.toContain('hover:bg-slate-800')
      expect(inactiveOption?.className).toContain('hover:bg-slate-700')
      expect(inactiveOption?.className).toContain('hover:text-white')

      const manageBtn = screen.getByText('Manage repos').closest('button')
      expect(manageBtn).not.toBeNull()
      expect(manageBtn?.className).not.toContain('hover:bg-slate-800/70')
      expect(manageBtn?.className).not.toContain('hover:text-slate-200')
      expect(manageBtn?.className).toContain('hover:bg-slate-700')
      expect(manageBtn?.className).toContain('hover:text-white')
    } finally {
      document.documentElement.removeAttribute('data-theme')
      document.documentElement.style.removeProperty('--color-accent')
    }
  })
})
