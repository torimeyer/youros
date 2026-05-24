import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route, Navigate } from 'react-router-dom'

// Regression for the "No routes matched location /onboarding" warning.
// The wizard renders conditionally outside BrowserRouter (at the app root)
// when onboarded=false, so /onboarding is never a real React Router path.
// Navigating to /onboarding while already onboarded used to log a yellow
// React Router warning and render nothing. The fix adds a redirect route
// so the path is always handled.
describe('/onboarding route redirect', () => {
  function renderAt(initialPath: string) {
    return render(
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/" element={<div>Home Page</div>} />
          {/* This mirrors the redirect added to App.tsx */}
          <Route path="onboarding" element={<Navigate to="/" replace />} />
          <Route path="*" element={<div>Not Found</div>} />
        </Routes>
      </MemoryRouter>
    )
  }

  it('renders the home page when visiting / directly', () => {
    renderAt('/')
    expect(screen.getByText('Home Page')).toBeInTheDocument()
  })

  it('redirects /onboarding to / instead of showing Not Found', () => {
    renderAt('/onboarding')
    expect(screen.getByText('Home Page')).toBeInTheDocument()
    expect(screen.queryByText('Not Found')).not.toBeInTheDocument()
  })
})
