import { Navigate } from 'react-router-dom'

// The Docs page was renamed to Specs. Old links pointing at /docs
// must redirect to /specs so they never land on a blank route.
// Keep this named component (not an inline <Navigate>) so App.tsx has
// a visible reference for the retro prevention test.
export default function DocsRedirect() {
  return <Navigate to="/specs" replace />
}
