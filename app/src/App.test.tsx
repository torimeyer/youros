import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { useAppStore } from './stores/app'

vi.mock('./lib/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue({}),
    post: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  },
}))

vi.mock('./pages/Files', () => ({ default: () => <div data-testid="page-files">Files</div> }))
vi.mock('./pages/Drive', () => ({ default: () => <div data-testid="page-drive">Drive</div> }))
vi.mock('./pages/Dashboard', () => ({ default: () => <div data-testid="page-dashboard" /> }))
vi.mock('./pages/Tasks', () => ({ default: () => <div /> }))
vi.mock('./pages/Timeline', () => ({ default: () => <div /> }))
vi.mock('./pages/Agents', () => ({ default: () => <div /> }))
vi.mock('./pages/Settings', () => ({ default: () => <div /> }))
vi.mock('./pages/Transcripts', () => ({ default: () => <div /> }))
vi.mock('./pages/Activity', () => ({ default: () => <div /> }))
vi.mock('./pages/CostTracking', () => ({ default: () => <div /> }))
vi.mock('./pages/Specs', () => ({ default: () => <div /> }))
vi.mock('./pages/DocsRedirect', () => ({ default: () => <div /> }))
vi.mock('./pages/Calendar', () => ({ default: () => <div /> }))
vi.mock('./pages/Gmail', () => ({ default: () => <div /> }))
vi.mock('./pages/IMessage', () => ({ default: () => <div /> }))
vi.mock('./pages/Slack', () => ({ default: () => <div /> }))
vi.mock('./pages/GitHub', () => ({ default: () => <div /> }))
vi.mock('./pages/Upgrade', () => ({ default: () => <div /> }))
vi.mock('./pages/Releases', () => ({ default: () => <div /> }))
vi.mock('./pages/Adoption', () => ({ default: () => <div /> }))
vi.mock('./pages/Workflows', () => ({ default: () => <div /> }))
vi.mock('./pages/WorkflowBuilder', () => ({ default: () => <div /> }))
vi.mock('./pages/ShareView', () => ({ default: () => <div data-testid="page-share" /> }))
vi.mock('./pages/InviteAccept', () => ({ default: () => <div data-testid="page-invite" /> }))
vi.mock('./pages/PrivacyPolicy', () => ({ default: () => <div /> }))
vi.mock('./pages/AboutMyOS', () => ({ default: () => <div /> }))
vi.mock('./pages/MyGems', () => ({ default: () => <div data-testid="page-gems" /> }))
vi.mock('./pages/admin/Overview', () => ({ default: () => <div /> }))
vi.mock('./pages/admin/Members', () => ({ default: () => <div /> }))
vi.mock('./pages/admin/Policies', () => ({ default: () => <div /> }))
vi.mock('./pages/admin/AuditTrail', () => ({ default: () => <div /> }))
vi.mock('./pages/admin/Security', () => ({ default: () => <div /> }))
vi.mock('./components/OnboardingWizard', () => ({ default: () => <div data-testid="onboarding" /> }))
vi.mock('./components/Layout', async () => {
  const { Outlet } = await import('react-router-dom')
  const { Footer } = await import('./components/Footer')
  return { Layout: () => <><Outlet /><Footer /></> }
})
vi.mock('./components/AdminLayout', async () => {
  const { Outlet } = await import('react-router-dom')
  return { default: () => <Outlet /> }
})

import App from './App'

describe('App routing', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ hydrated: true, onboarded: true })
  })

  afterEach(() => {
    window.history.pushState({}, '', '/')
  })

  it('renders the /files route', () => {
    window.history.pushState({}, '', '/files')
    render(<App />)
    expect(screen.getByTestId('page-files')).toBeInTheDocument()
  })

  it('renders the /drive route', () => {
    window.history.pushState({}, '', '/drive')
    render(<App />)
    expect(screen.getByTestId('page-drive')).toBeInTheDocument()
  })

  it('Footer is visible on an authenticated route', () => {
    window.history.pushState({}, '', '/')
    render(<App />)
    expect(screen.getByTestId('footer')).toBeInTheDocument()
  })

  it('Footer is not visible on /share/:token (pre-auth, outside Layout)', () => {
    window.history.pushState({}, '', '/share/abc123')
    render(<App />)
    expect(screen.queryByTestId('footer')).not.toBeInTheDocument()
  })

  it('Footer is not visible on /invite/:token (pre-auth, outside Layout)', () => {
    window.history.pushState({}, '', '/invite/abc123')
    render(<App />)
    expect(screen.queryByTestId('footer')).not.toBeInTheDocument()
  })

  it('Footer is not visible on onboarding (OnboardingWizard pre-auth screen)', () => {
    useAppStore.setState({ hydrated: true, onboarded: false })
    render(<App />)
    expect(screen.queryByTestId('footer')).not.toBeInTheDocument()
  })

  // OAuth callback cleanup: when Atlassian's callback redirects back to
  // the app with ?atlassian_connected=true, App must strip the param so
  // a refresh does not surface it again.
  it('strips ?atlassian_connected=true from the URL on mount', async () => {
    window.history.pushState({}, '', '/?atlassian_connected=true')
    render(<App />)
    await Promise.resolve()
    expect(window.location.search).toBe('')
  })

  it('preserves other query params when stripping ?atlassian_connected', async () => {
    window.history.pushState({}, '', '/?keep=this&atlassian_connected=true')
    render(<App />)
    await Promise.resolve()
    expect(window.location.search).toBe('?keep=this')
  })

  it('strips ?auth_error from the URL on mount', async () => {
    window.history.pushState({}, '', '/?auth_error=invalid_state')
    render(<App />)
    await Promise.resolve()
    expect(window.location.search).toBe('')
  })

  it('preserves other query params when stripping ?auth_error', async () => {
    window.history.pushState({}, '', '/?keep=this&auth_error=token_exchange_failed')
    render(<App />)
    await Promise.resolve()
    expect(window.location.search).toBe('?keep=this')
  })
})
