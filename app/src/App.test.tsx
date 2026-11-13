import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { api } from './lib/api'
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

vi.mock('./pages/Dashboard', () => ({ default: () => <div data-testid="page-dashboard" /> }))
vi.mock('./pages/Tasks', () => ({ default: () => <div data-testid="page-tasks" /> }))
vi.mock('./pages/Timeline', () => ({ default: () => <div /> }))
vi.mock('./pages/Agents', () => ({ default: () => <div data-testid="page-agents" /> }))
vi.mock('./pages/Settings', () => ({ default: () => <div /> }))
vi.mock('./pages/Transcripts', () => ({ default: () => <div data-testid="page-transcripts" /> }))
vi.mock('./pages/Activity', () => ({ default: () => <div /> }))
vi.mock('./pages/CostTracking', () => ({ default: () => <div /> }))
vi.mock('./pages/Specs', () => ({ default: () => <div data-testid="page-specs" /> }))
vi.mock('./pages/BreakRoom', () => ({ default: () => <div data-testid="page-break" /> }))
vi.mock('./pages/Projects', () => ({ default: () => <div data-testid="page-projects" /> }))
vi.mock('./pages/DocsRedirect', () => ({ default: () => <div /> }))
vi.mock('./pages/Slack', () => ({ default: () => <div data-testid="page-slack" /> }))
vi.mock('./pages/Jira', () => ({ default: () => <div data-testid="page-jira" /> }))
vi.mock('./pages/Confluence', () => ({ default: () => <div data-testid="page-confluence" /> }))
vi.mock('./pages/PortfolioPage', () => ({ default: () => <div data-testid="page-portfolio" /> }))
vi.mock('./pages/ExecutiveSummary', () => ({ default: () => <div data-testid="page-exec-summary" /> }))
vi.mock('./pages/Releases', () => ({ default: () => <div /> }))
vi.mock('./pages/ShareView', () => ({ default: () => <div data-testid="page-share" /> }))
vi.mock('./pages/InviteAccept', () => ({ default: () => <div data-testid="page-invite" /> }))
vi.mock('./pages/PrivacyPolicy', () => ({ default: () => <div /> }))
vi.mock('./pages/AboutYourOS', () => ({ default: () => <div /> }))
vi.mock('./pages/admin/Overview', () => ({ default: () => <div data-testid="page-admin-overview" /> }))
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
    useAppStore.setState({ hydrated: true, onboarded: true, enterpriseEnabled: false } as never)
  })

  afterEach(() => {
    window.history.pushState({}, '', '/')
  })

  it('does not poll /calendar/events unconditionally on boot (root useCalendarFeed removed)', async () => {
    window.history.pushState({}, '', '/')
    render(<App />)
    expect(await screen.findByTestId('page-dashboard')).toBeInTheDocument()
    const getCalls = vi.mocked(api.get).mock.calls.map((c) => c[0])
    expect(getCalls.some((u) => String(u).includes('/calendar/events'))).toBe(false)
  })

  it('renders Core routes (/specs, /tasks, /agents, /transcripts, /break, /projects)', async () => {
    for (const [path, testId] of [
      ['/specs', 'page-specs'],
      ['/tasks', 'page-tasks'],
      ['/agents', 'page-agents'],
      ['/transcripts', 'page-transcripts'],
      ['/break', 'page-break'],
      ['/projects', 'page-projects'],
    ] as const) {
      window.history.pushState({}, '', path)
      const { unmount } = render(<App />)
      expect(await screen.findByTestId(testId)).toBeInTheDocument()
      unmount()
    }
  })

  it('renders 404 route-not-found for enterprise and purged routes when enterpriseEnabled is false', async () => {
    for (const blockedPath of [
      '/admin',
      '/portfolio',
      '/executive-summary',
      '/jira',
      '/confluence',
      '/slack',
      '/gmail',
      '/calendar',
      '/drive',
      '/files',
      '/gems',
      '/workflows',
      '/github',
    ]) {
      window.history.pushState({}, '', blockedPath)
      const { unmount } = render(<App />)
      expect(await screen.findByTestId('route-not-found')).toBeInTheDocument()
      expect(screen.queryByTestId('page-admin-overview')).not.toBeInTheDocument()
      unmount()
    }
  })

  it('mounts enterprise routes (/admin, /portfolio, /jira, /slack, /share/:token) when enterpriseEnabled is true', async () => {
    useAppStore.setState({ enterpriseEnabled: true } as never)

    window.history.pushState({}, '', '/admin')
    const r1 = render(<App />)
    expect(await screen.findByTestId('page-admin-overview')).toBeInTheDocument()
    r1.unmount()

    window.history.pushState({}, '', '/portfolio')
    const r2 = render(<App />)
    expect(await screen.findByTestId('page-portfolio')).toBeInTheDocument()
    r2.unmount()

    window.history.pushState({}, '', '/share/abc123')
    const r3 = render(<App />)
    expect(await screen.findByTestId('page-share')).toBeInTheDocument()
    expect(screen.queryByTestId('footer')).not.toBeInTheDocument()
    r3.unmount()
  })

  it('Footer is visible on an authenticated route', async () => {
    window.history.pushState({}, '', '/')
    render(<App />)
    expect(await screen.findByTestId('footer')).toBeInTheDocument()
  })

  it('Footer is not visible on /share/:token or /invite/:token when enterpriseEnabled is true', () => {
    useAppStore.setState({ enterpriseEnabled: true } as never)
    window.history.pushState({}, '', '/share/abc123')
    const r1 = render(<App />)
    expect(screen.queryByTestId('footer')).not.toBeInTheDocument()
    r1.unmount()

    window.history.pushState({}, '', '/invite/abc123')
    const r2 = render(<App />)
    expect(screen.queryByTestId('footer')).not.toBeInTheDocument()
    r2.unmount()
  })

  it('Footer is not visible on onboarding (OnboardingWizard pre-auth screen)', () => {
    useAppStore.setState({ hydrated: true, onboarded: false })
    render(<App />)
    expect(screen.queryByTestId('footer')).not.toBeInTheDocument()
  })

  it('swaps the wizard for the app the moment onboarded flips to true', () => {
    useAppStore.setState({ hydrated: true, onboarded: false })
    render(<App />)
    expect(screen.getByTestId('onboarding')).toBeInTheDocument()

    act(() => { useAppStore.setState({ onboarded: true }) })

    expect(screen.queryByTestId('onboarding')).not.toBeInTheDocument()
    expect(screen.getByTestId('page-dashboard')).toBeInTheDocument()
  })

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
