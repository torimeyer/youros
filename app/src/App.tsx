import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useNotificationStore } from './stores/notifications'
import { Layout } from './components/Layout'
import OnboardingWizard from './components/OnboardingWizard'
import { ErrorBoundary } from './components/ErrorBoundary'
import Dashboard from './pages/Dashboard'
import Timeline from './pages/Timeline'
import Agents from './pages/Agents'
import Settings from './pages/Settings'
import SettingsRules from './pages/SettingsRules'
import Transcripts from './pages/Transcripts'
import Activity from './pages/Activity'
import CostTracking from './pages/CostTracking'
import Backlog from './pages/Backlog'
import Tasks from './pages/Tasks'
import Specs from './pages/Specs'
import ExecutiveSummary from './pages/ExecutiveSummary'
import PortfolioPage from './pages/PortfolioPage'
import SpecImport from './pages/SpecImport'
import SpecFaq from './pages/SpecFaq'
import DocsRedirect from './pages/DocsRedirect'
import Calendar from './pages/Calendar'
import Gmail from './pages/Gmail'
import IMessage from './pages/IMessage'
import Slack from './pages/Slack'
import GitHub from './pages/GitHub'
import Jira from './pages/Jira'
import Confluence from './pages/Confluence'
import Upgrade from './pages/Upgrade'
import Releases from './pages/Releases'

import Workflows from './pages/Workflows'
import WorkflowBuilder from './pages/WorkflowBuilder'
import Drive from './pages/Drive'
import Files from './pages/Files'
import OstkFiles from './pages/OstkFiles'
import Sessions from './pages/Sessions'
import { useAppStore } from './stores/app'
import { useRunningAgentsFeed } from './hooks/useRunningAgentsFeed'
import { useSessionsFeed } from './hooks/useSessionsFeed'
import { useDashboardFeed } from './hooks/useDashboardFeed'
import { useNotificationsFeed } from './hooks/useNotificationsFeed'
import { useCalendarFeed } from './hooks/useCalendarFeed'
import { useTaskFinishedSound } from './hooks/useTaskFinishedSound'
import ShareView from './pages/ShareView'
import AdminLayout from './components/AdminLayout'
import AdminOverview from './pages/admin/Overview'
import AdminMembers from './pages/admin/Members'
import AdminPolicies from './pages/admin/Policies'
import AdminAuditTrail from './pages/admin/AuditTrail'
import AdminSecurity from './pages/admin/Security'
import InviteAccept from './pages/InviteAccept'
import PrivacyPolicy from './pages/PrivacyPolicy'
import AboutYourOS from './pages/AboutYourOS'
import AgentfileEditor from './pages/AgentfileEditor'
import MySetup from './pages/MySetup'

import MyGems from './pages/MyGems'
import BreakRoom from './pages/BreakRoom'
import Library from './pages/Library'
import Projects from './pages/Projects'

export default function App() {
  useRunningAgentsFeed()
  useSessionsFeed()
  useDashboardFeed()
  useNotificationsFeed()
  useCalendarFeed()
  useTaskFinishedSound()
  const hydrated = useAppStore((s) => s.hydrated)
  const onboarded = useAppStore((s) => s.onboarded)
  const hydrateFromServer = useAppStore((s) => s.hydrateFromServer)
  const instanceName = useAppStore((s) => s.instanceName)

  // Pull the latest settings from the server once at app boot so the
  // server is the source of truth. localStorage is only used for the
  // very first paint to avoid a flash of wrong state.
  useEffect(() => {
    hydrateFromServer()
  }, [hydrateFromServer])

  // OAuth callback cleanup: strip ?atlassian_connected=true and surface
  // any ?auth_error so the user knows when Atlassian OAuth failed.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const authError = params.get('auth_error')
    if (authError) {
      params.delete('auth_error')
      const next = params.toString()
      window.history.replaceState({}, '', window.location.pathname + (next ? `?${next}` : ''))
      useNotificationStore.getState().addPersistentToast({
        id: `atlassian-auth-error-${authError}`,
        type: 'error',
        title: 'Atlassian connection failed',
        body: `${authError.replace(/_/g, ' ')}. Try connecting again in Settings.`,
      })
    }
    if (params.get('atlassian_connected') === 'true') {
      params.delete('atlassian_connected')
      const next = params.toString()
      const url = window.location.pathname + (next ? `?${next}` : '')
      window.history.replaceState({}, '', url)
    }
  }, [])

  // Wait for hydration BEFORE deciding whether to show the wizard. The
  // server is the source of truth for onboarded; localStorage is just a
  // cache that can drift (especially after an admin reset). If we read
  // onboarded from localStorage here, a user whose server was reset
  // would still see the main UI because her LocalStorage still says
  // true. Blocking on hydration guarantees the wizard appears after
  // a reset without requiring a DevTools LocalStorage purge.
  //
  // Hydration always resolves quickly — hydrateFromServer sets
  // hydrated=true both on success AND on fetch error (see
  // stores/app.ts), so an unreachable backend falls through to
  // localStorage within the same tick instead of hanging here.
  //
  // →2687: a failed first fetch is not final. The store keeps retrying
  // in the background with growing delays and applies the server's
  // answer whenever it lands. Because this component subscribes to
  // onboarded, a late "onboarded=true" dismisses the wizard below and
  // mounts the real app without a reload.
  if (!hydrated) {
    // Render a minimal loading screen instead of a blank page so the user
    // sees something during the brief server hydration window after hard
    // reload. Uses inline styles to avoid any CSS load dependency.
    return (
      <div
        role="status"
        aria-live="polite"
        style={{
          position: 'fixed',
          inset: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#0b0d10',
          color: '#9ca3af',
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
          fontSize: 14,
          letterSpacing: 0.3,
        }}
      >
        Loading {instanceName}...
      </div>
    )
  }

  // Post-hydration: server truth now in the store. Show the wizard if
  // onboarded is still false after the fetch landed.
  if (!onboarded) {
    // Wrapped so a wizard render crash shows a recoverable error card
    // instead of blanking the whole app (the wizard renders above the
    // route-level boundary, so without this a throw = white screen).
    return (
      <ErrorBoundary>
        <OnboardingWizard />
      </ErrorBoundary>
    )
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route path="share/:token" element={<ShareView />} />
        <Route path="invite/:token" element={<InviteAccept />} />
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="tasks" element={<Tasks />} />
          <Route path="timeline" element={<Timeline />} />
          <Route path="activity" element={<Activity />} />
          <Route path="agents" element={<Agents />} />
          <Route path="agentfiles/:name/edit" element={<AgentfileEditor />} />
          <Route path="transcripts" element={<Transcripts />} />
          <Route path="backlog" element={<Backlog />} />
          <Route path="break" element={<BreakRoom />} />
          <Route path="specs" element={<Specs />} />
          <Route path="executive-summary" element={<ExecutiveSummary />} />
          <Route path="portfolio" element={<PortfolioPage />} />
          <Route path="specs/import" element={<SpecImport />} />
          <Route path="specs/faq" element={<SpecFaq />} />
          <Route path="files" element={<Files />} />
          <Route path="library" element={<Library />} />
          <Route path="projects" element={<Projects />} />
          <Route path="ostk" element={<OstkFiles />} />
          <Route path="sessions" element={<Sessions />} />
          <Route path="drive" element={<Drive />} />
          {/* Backward compat: /docs redirects to /specs after the rename. */}
          <Route path="docs" element={<DocsRedirect />} />
          {/* /onboarding is not a real route: the wizard renders conditionally
              at the app root when onboarded=false. Redirect here so deep-links
              used in QA, support, and demos always land somewhere valid. */}
          <Route path="onboarding" element={<Navigate to="/" replace />} />
          <Route path="calendar" element={<Calendar />} />
          <Route path="gmail" element={<Gmail />} />
          <Route path="imessage" element={<IMessage />} />
          <Route path="contacts" element={<Navigate to="/imessage" replace />} />
          <Route path="slack" element={<Slack />} />
          <Route path="github" element={<GitHub />} />
          <Route path="jira" element={<Jira />} />
          <Route path="jira/:key" element={<Jira />} />
          <Route path="confluence" element={<Confluence />} />
          <Route path="confluence/:pageId" element={<Confluence />} />
          <Route path="costs" element={<CostTracking />} />
          <Route path="releases" element={<Releases />} />
          <Route path="workflows" element={<Workflows />} />
          <Route path="workflows/builder" element={<WorkflowBuilder />} />
          <Route path="workflows/builder/:id" element={<WorkflowBuilder />} />
          {/* Admin routes */}
          <Route path="admin" element={<AdminLayout />}>
            <Route index element={<AdminOverview />} />
            <Route path="members" element={<AdminMembers />} />
            <Route path="policies" element={<AdminPolicies />} />
            <Route path="audit" element={<AdminAuditTrail />} />
            <Route path="security" element={<AdminSecurity />} />
          </Route>
          <Route path="adoption" element={<Navigate to="/settings" replace />} />
          <Route path="settings" element={<Settings />} />
          <Route path="settings/rules" element={<SettingsRules />} />
          <Route path="settings/upgrade" element={<Upgrade />} />
          <Route path="privacy" element={<PrivacyPolicy />} />
          <Route path="about" element={<AboutYourOS />} />
          <Route path="my-setup" element={<MySetup />} />
          <Route path="gems" element={<MyGems />} />

        </Route>
      </Routes>
    </BrowserRouter>
  )
}
