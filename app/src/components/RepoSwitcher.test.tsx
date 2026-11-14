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

  it('renders ostk readiness badges, live socket indicator, and uninitialized active-repo prompt (→3113)', async () => {
    useAppStore.setState({ activeRepoPath: '/Users/dev/claude/raw-service' })
    mockedGet.mockResolvedValueOnce({
      active_repo_path: '/Users/dev/claude/raw-service',
      repos: [
        {
          name: 'torios',
          path: '/Users/dev/claude/torios',
          is_active: false,
          branch: 'main',
          ostk_initialized: true,
          ostk_socket_live: true,
        },
        {
          name: 'alpha-lib',
          path: '/Users/dev/claude/alpha-lib',
          is_active: false,
          branch: 'develop',
          ostk_initialized: true,
          ostk_socket_live: false,
        },
        {
          name: 'raw-service',
          path: '/Users/dev/claude/raw-service',
          is_active: true,
          branch: 'feat/init',
          ostk_initialized: false,
          ostk_socket_live: false,
        },
      ],
    })

    renderSwitcher()
    fireEvent.click(screen.getByTestId('repo-switcher'))

    const liveBadge = await screen.findByTestId('ostk-badge-torios')
    expect(liveBadge.textContent).toContain('ostk')
    expect(liveBadge.getAttribute('title')).toBe('ostk live')
    expect(screen.getByTestId('ostk-live-dot-torios')).toBeInTheDocument()

    const initBadge = screen.getByTestId('ostk-badge-alpha-lib')
    expect(initBadge.textContent).toContain('ostk')
    expect(initBadge.getAttribute('title')).toBe('ostk initialized')
    expect(screen.queryByTestId('ostk-live-dot-alpha-lib')).not.toBeInTheDocument()

    const uninitBadge = screen.getByTestId('ostk-badge-raw-service')
    expect(uninitBadge.textContent).toContain('no .ostk')

    expect(screen.getByTestId('ostk-uninitialized-banner')).toBeInTheDocument()
    expect(screen.getByTestId('init-ostk-raw-service')).toBeInTheDocument()
    expect(screen.queryByTestId('init-ostk-torios')).not.toBeInTheDocument()
  })

  it('initializes an uninitialized active repo via POST /projects/ostk-init, shows loading state, flips badge, and dispatches repo-change event (→3113)', async () => {
    useAppStore.setState({ activeRepoPath: '/Users/dev/claude/beta-engine' })

    mockedGet
      .mockResolvedValueOnce({
        active_repo_path: '/Users/dev/claude/beta-engine',
        repos: [
          {
            name: 'beta-engine',
            path: '/Users/dev/claude/beta-engine',
            is_active: true,
            branch: 'main',
            ostk_initialized: false,
            ostk_socket_live: false,
          },
        ],
      })
      .mockResolvedValueOnce({
        active_repo_path: '/Users/dev/claude/beta-engine',
        repos: [
          {
            name: 'beta-engine',
            path: '/Users/dev/claude/beta-engine',
            is_active: true,
            branch: 'main',
            ostk_initialized: true,
            ostk_socket_live: false,
          },
        ],
      })
      .mockResolvedValueOnce({
        active_repo_path: '/Users/dev/claude/beta-engine',
        name: 'beta-engine',
        ostk_initialized: true,
        ostk_socket_live: false,
      })

    let resolveInit!: (val: unknown) => void
    mockedPost.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveInit = resolve
        }),
    )

    const eventEvents: CustomEvent[] = []
    const listener = (ev: Event) => eventEvents.push(ev as CustomEvent)
    window.addEventListener('myos:active-repo-changed', listener)

    try {
      renderSwitcher()
      fireEvent.click(screen.getByTestId('repo-switcher'))

      const initBtn = await screen.findByTestId('init-ostk-beta-engine')
      expect(initBtn.textContent).toContain('Initialize ostk')

      fireEvent.click(initBtn)

      await waitFor(() => {
        expect(screen.getByTestId('init-ostk-beta-engine').textContent).toContain('Initializing...')
        expect(screen.getByTestId('init-ostk-beta-engine')).toBeDisabled()
      })

      expect(mockedPost).toHaveBeenCalledWith('/projects/ostk-init', {
        path: '/Users/dev/claude/beta-engine',
      })

      resolveInit({
        initialized: true,
        path: '/Users/dev/claude/beta-engine',
        ostk_initialized: true,
        ostk_socket_live: false,
        output: 'initialized ostk project',
      })

      await waitFor(() => {
        expect(screen.getByTestId('ostk-badge-beta-engine').textContent).toContain('ostk')
        expect(screen.getByTestId('ostk-badge-beta-engine').textContent).not.toContain('no .ostk')
        expect(screen.queryByTestId('init-ostk-beta-engine')).not.toBeInTheDocument()
        expect(screen.queryByTestId('ostk-uninitialized-banner')).not.toBeInTheDocument()
      })

      expect(eventEvents.length).toBeGreaterThanOrEqual(1)
      expect(eventEvents[0].detail).toMatchObject({
        path: '/Users/dev/claude/beta-engine',
        ostk_initialized: true,
      })
    } finally {
      window.removeEventListener('myos:active-repo-changed', listener)
    }
  })

  it('surfaces inline error message and allows retry when POST /projects/ostk-init fails (→3113)', async () => {
    mockedGet.mockResolvedValueOnce({
      active_repo_path: '/Users/dev/claude/torios',
      repos: [
        {
          name: 'torios',
          path: '/Users/dev/claude/torios',
          is_active: true,
          branch: 'main',
          ostk_initialized: true,
          ostk_socket_live: true,
        },
        {
          name: 'broken-repo',
          path: '/Users/dev/claude/broken-repo',
          is_active: false,
          branch: 'main',
          ostk_initialized: false,
          ostk_socket_live: false,
        },
      ],
    })
    mockedPost.mockRejectedValueOnce(new Error('ostk binary is not installed'))

    renderSwitcher()
    fireEvent.click(screen.getByTestId('repo-switcher'))

    const initBtn = await screen.findByTestId('init-ostk-broken-repo')
    fireEvent.click(initBtn)

    const alertEl = await screen.findByTestId('ostk-init-error')
    expect(alertEl.textContent).toContain('ostk binary is not installed')
    expect(screen.getByTestId('init-ostk-broken-repo')).not.toBeDisabled()
    expect(screen.getByTestId('ostk-badge-broken-repo').textContent).toContain('no .ostk')
  })

  it('initializes an inactive sibling repo without firing active-repo-changed and preserves light/dark theme-mapped classes (→3113)', async () => {
    document.documentElement.setAttribute('data-theme', 'light')
    document.documentElement.style.setProperty('--color-accent', '#06b6d4')

    mockedGet
      .mockResolvedValueOnce({
        active_repo_path: '/Users/dev/claude/torios',
        repos: [
          {
            name: 'torios',
            path: '/Users/dev/claude/torios',
            is_active: true,
            branch: 'main',
            ostk_initialized: true,
            ostk_socket_live: true,
          },
          {
            name: 'sibling-app',
            path: '/Users/dev/claude/sibling-app',
            is_active: false,
            branch: 'main',
            ostk_initialized: false,
            ostk_socket_live: false,
          },
        ],
      })
      .mockResolvedValueOnce({
        active_repo_path: '/Users/dev/claude/torios',
        repos: [
          {
            name: 'torios',
            path: '/Users/dev/claude/torios',
            is_active: true,
            branch: 'main',
            ostk_initialized: true,
            ostk_socket_live: true,
          },
          {
            name: 'sibling-app',
            path: '/Users/dev/claude/sibling-app',
            is_active: false,
            branch: 'main',
            ostk_initialized: true,
            ostk_socket_live: false,
          },
        ],
      })
    mockedPost.mockResolvedValueOnce({
      initialized: true,
      path: '/Users/dev/claude/sibling-app',
      ostk_initialized: true,
      ostk_socket_live: false,
      output: 'initialized ostk project',
    })

    const eventSpy = vi.fn()
    window.addEventListener('myos:active-repo-changed', eventSpy)

    try {
      renderSwitcher()
      fireEvent.click(screen.getByTestId('repo-switcher'))

      const initBtn = await screen.findByTestId('init-ostk-sibling-app')
      const liveBadge = screen.getByTestId('ostk-badge-torios')
      const uninitBadge = screen.getByTestId('ostk-badge-sibling-app')

      expect(liveBadge.className).toContain('accent-highlight')
      expect(liveBadge.className).toContain('accent-border')
      expect(uninitBadge.className).toContain('bg-slate-900')
      expect(uninitBadge.className).toContain('border-slate-700')
      expect(initBtn.className).toContain('bg-slate-900')
      expect(initBtn.className).toContain('border-slate-700')
      expect(initBtn.className).toContain('hover:bg-slate-700')

      fireEvent.click(initBtn)

      await waitFor(() => {
        expect(mockedPost).toHaveBeenCalledWith('/projects/ostk-init', {
          path: '/Users/dev/claude/sibling-app',
        })
        expect(screen.getByTestId('ostk-badge-sibling-app').textContent).toContain('ostk')
        expect(screen.queryByTestId('init-ostk-sibling-app')).not.toBeInTheDocument()
      })

      expect(eventSpy).not.toHaveBeenCalled()
    } finally {
      window.removeEventListener('myos:active-repo-changed', eventSpy)
      document.documentElement.removeAttribute('data-theme')
      document.documentElement.style.removeProperty('--color-accent')
    }
  })
})
