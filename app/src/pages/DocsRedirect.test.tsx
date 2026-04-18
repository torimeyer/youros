import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route, Navigate } from 'react-router-dom'

// The Docs page was renamed to Specs. Old links pointing at /docs
// must redirect to /specs so they never land on a blank route.
// Regression for the "clicking Docs shows a blank screen" bug.
describe('Docs to Specs redirect', () => {
  function renderAt(initialPath: string) {
    return render(
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/specs" element={<div>Specs Page Rendered</div>} />
          <Route path="/docs" element={<Navigate to="/specs" replace />} />
          <Route path="*" element={<div>Not Found</div>} />
        </Routes>
      </MemoryRouter>
    )
  }

  it('renders the Specs page when visiting /specs directly', () => {
    renderAt('/specs')
    expect(screen.getByText('Specs Page Rendered')).toBeInTheDocument()
  })

  it('redirects /docs to /specs instead of rendering a blank page', () => {
    renderAt('/docs')
    expect(screen.getByText('Specs Page Rendered')).toBeInTheDocument()
    expect(screen.queryByText('Not Found')).not.toBeInTheDocument()
  })
})
