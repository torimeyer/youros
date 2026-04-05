import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Layout } from './components/Layout'
import OnboardingWizard from './components/OnboardingWizard'
import Dashboard from './pages/Dashboard'
import Tasks from './pages/Tasks'
import Timeline from './pages/Timeline'
import Ideas from './pages/Ideas'
import Agents from './pages/Agents'
import Files from './pages/Files'
import Settings from './pages/Settings'
import Transcripts from './pages/Transcripts'
import CostTracking from './pages/CostTracking'
import { useAppStore } from './stores/app'

export default function App() {
  const onboarded = useAppStore((s) => s.onboarded)

  if (!onboarded) {
    return <OnboardingWizard />
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="tasks" element={<Tasks />} />
          <Route path="timeline" element={<Timeline />} />
          <Route path="ideas" element={<Ideas />} />
          <Route path="agents" element={<Agents />} />
          <Route path="files" element={<Files />} />
          <Route path="transcripts" element={<Transcripts />} />
          <Route path="costs" element={<CostTracking />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
