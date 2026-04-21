import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from './components/Layout'
import OnboardingWizard from './components/OnboardingWizard'
import Dashboard from './pages/Dashboard'
import Tasks from './pages/Tasks'
import Timeline from './pages/Timeline'
import Agents from './pages/Agents'
import Files from './pages/Files'
import Settings from './pages/Settings'
import Transcripts from './pages/Transcripts'
import Activity from './pages/Activity'
import CostTracking from './pages/CostTracking'
import Specs from './pages/Specs'
import DocsRedirect from './pages/DocsRedirect'
import Drive from './pages/Drive'
import Calendar from './pages/Calendar'
import Gmail from './pages/Gmail'
import IMessage from './pages/IMessage'
import Slack from './pages/Slack'
import GitHub from './pages/GitHub'
import Upgrade from './pages/Upgrade'
import Releases from './pages/Releases'
import Workflows from './pages/Workflows'
import WorkflowBuilder from './pages/WorkflowBuilder'
import { useAppStore } from './stores/app'
import ShareView from './pages/ShareView'
import AdminLayout from './components/AdminLayout'
import AdminOverview from './pages/admin/Overview'
import AdminMembers from './pages/admin/Members'
import AdminPolicies from './pages/admin/Policies'
import AdminAuditTrail from './pages/admin/AuditTrail'
import AdminSecurity from './pages/admin/Security'
import InviteAccept from './pages/InviteAccept'

export default function App() {
  const hydrated = useAppStore((s) => s.hydrated)
  const onboarded = useAppStore((s) => s.onboarded)
  const hydrateFromServer = useAppStore((s) => s.hydrateFromServer)

  // Pull the latest settings from the server once at app boot so the
  // server is the source of truth. localStorage is only used for the
  // very first paint to avoid a flash of wrong state.
  useEffect(() => {
    hydrateFromServer()
  }, [hydrateFromServer])

  // Show the wizard immediately when the initial onboarded flag is false.
  // If we waited for hydration here, a slow or unreachable backend would
  // leave a blank screen on hard reload. For a not-yet-onboarded user there
  // is nothing to hydrate into anyway, so render the wizard right away.
  if (!onboarded) {
    return <OnboardingWizard />
  }

  // Wait for server settings before deciding which post-onboarding view to
  // mount. Without this, localStorage can say "onboarded=true" while the
  // server says false, causing the wizard to flash then disappear.
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
        Loading torios...
      </div>
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
          <Route path="files" element={<Files />} />
          <Route path="transcripts" element={<Transcripts />} />
          <Route path="specs" element={<Specs />} />
          {/* Backward compat: /docs redirects to /specs after the rename. */}
          <Route path="docs" element={<DocsRedirect />} />
          {/* /onboarding is not a real route: the wizard renders conditionally
              at the app root when onboarded=false. Redirect here so deep-links
              used in QA, support, and demos always land somewhere valid. */}
          <Route path="onboarding" element={<Navigate to="/" replace />} />
          <Route path="drive" element={<Drive />} />
          <Route path="calendar" element={<Calendar />} />
          <Route path="gmail" element={<Gmail />} />
          <Route path="imessage" element={<IMessage />} />
          <Route path="slack" element={<Slack />} />
          <Route path="github" element={<GitHub />} />
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
          {/* Backward compat: /team redirects to /admin */}
          <Route path="team" element={<Navigate to="/admin" replace />} />
          <Route path="settings" element={<Settings />} />
          <Route path="settings/upgrade" element={<Upgrade />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
