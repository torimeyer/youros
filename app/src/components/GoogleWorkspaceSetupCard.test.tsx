import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { GoogleWorkspaceSetupCard } from './GoogleWorkspaceSetupCard'
import { api } from '../lib/api'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
  },
}))

const DEFAULT_PROPS = {
  darkMode: false,
  subtextCls: 'text-slate-500',
  stepIndex: 8,
}

describe('GoogleWorkspaceSetupCard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.history.replaceState({}, '', '/')
  })

  it('fetches /drive/auth/status on mount', async () => {
    vi.mocked(api.get).mockResolvedValue({ authenticated: false, email: null })
    render(<GoogleWorkspaceSetupCard {...DEFAULT_PROPS} />)
    await waitFor(() => {
      expect(vi.mocked(api.get)).toHaveBeenCalledWith('/drive/auth/status')
    })
  })

  it('shows connect button when not authenticated', async () => {
    vi.mocked(api.get).mockResolvedValue({ authenticated: false, email: null })
    render(<GoogleWorkspaceSetupCard {...DEFAULT_PROPS} />)
    await waitFor(() => {
      expect(screen.getByTestId('connect-google-workspace')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('google-workspace-connected-pill')).not.toBeInTheDocument()
  })

  it('shows Connected pill when authenticated', async () => {
    vi.mocked(api.get).mockResolvedValue({ authenticated: true, email: 'user@example.com' })
    render(<GoogleWorkspaceSetupCard {...DEFAULT_PROPS} />)
    await waitFor(() => {
      expect(screen.getByTestId('google-workspace-connected-pill')).toBeInTheDocument()
    })
    expect(screen.getByTestId('google-workspace-connected-pill')).toHaveTextContent('Connected')
    expect(screen.queryByTestId('connect-google-workspace')).not.toBeInTheDocument()
  })

  it('shows email when authenticated and email is returned', async () => {
    vi.mocked(api.get).mockResolvedValue({ authenticated: true, email: 'user@corp.com' })
    render(<GoogleWorkspaceSetupCard {...DEFAULT_PROPS} />)
    await waitFor(() => {
      expect(screen.getByText('user@corp.com')).toBeInTheDocument()
    })
  })

  it('re-fetches status when ?connected=true redirect param is present', async () => {
    window.history.replaceState({}, '', '/?connected=true')
    vi.mocked(api.get).mockResolvedValue({ authenticated: true, email: 'user@corp.com' })
    render(<GoogleWorkspaceSetupCard {...DEFAULT_PROPS} />)
    await waitFor(() => {
      expect(vi.mocked(api.get)).toHaveBeenCalledWith('/drive/auth/status')
      expect(screen.getByTestId('google-workspace-connected-pill')).toBeInTheDocument()
    })
    // URL should be cleared after redirect
    expect(window.location.search).toBe('')
  })

  it('re-fetches status when ?auth_success=true redirect param is present', async () => {
    window.history.replaceState({}, '', '/?auth_success=true')
    vi.mocked(api.get).mockResolvedValue({ authenticated: true, email: null })
    render(<GoogleWorkspaceSetupCard {...DEFAULT_PROPS} />)
    await waitFor(() => {
      expect(screen.getByTestId('google-workspace-connected-pill')).toBeInTheDocument()
    })
  })

  it('falls back to not-connected when fetch fails', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('network error'))
    render(<GoogleWorkspaceSetupCard {...DEFAULT_PROPS} />)
    await waitFor(() => {
      expect(screen.getByTestId('connect-google-workspace')).toBeInTheDocument()
    })
  })
})
