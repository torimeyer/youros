import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import InviteAccept from './InviteAccept'

// Mock the api module so nothing reached through the app store can hit
// the network.
vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue({}),
    post: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  },
}))

const originalLocation = window.location

// Replace window.location with a stub whose href records assignments
// instead of navigating (jsdom cannot navigate). Returns a reader for
// the recorded value.
function stubLocation() {
  let assignedHref = ''
  Object.defineProperty(window, 'location', {
    configurable: true,
    writable: true,
    value: {
      ...originalLocation,
      get href() {
        return assignedHref
      },
      set href(v: string) {
        assignedHref = v
      },
    },
  })
  return () => assignedHref
}

afterEach(() => {
  Object.defineProperty(window, 'location', {
    configurable: true,
    writable: true,
    value: originalLocation,
  })
})

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/invite/:token" element={<InviteAccept />} />
        <Route path="/invite" element={<InviteAccept />} />
        <Route path="/" element={<div data-testid="home-page" />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('InviteAccept page', () => {
  it('sends the browser to the backend accept endpoint for the token in the URL', () => {
    const href = stubLocation()
    renderAt('/invite/tok-abc123')
    // The backend validates the token, sets the session cookie, and
    // redirects back; the page's only job is landing on this exact URL.
    expect(href()).toBe('/api/enterprise/invite/tok-abc123')
    // While the redirect happens the user sees the waiting state, not an error.
    expect(screen.getByText('Accepting your invite...')).toBeInTheDocument()
    expect(screen.queryByText('No invite token found.')).not.toBeInTheDocument()
  })

  it('shows the error card and never redirects when the URL has no token', () => {
    const href = stubLocation()
    renderAt('/invite')
    expect(screen.getByText('No invite token found.')).toBeInTheDocument()
    // No redirect happened
    expect(href()).toBe('')
    expect(screen.queryByText('Accepting your invite...')).not.toBeInTheDocument()
  })

  it('the "Go to home" button on the error card navigates back to the app', () => {
    stubLocation()
    renderAt('/invite')
    fireEvent.click(screen.getByText('Go to home'))
    expect(screen.getByTestId('home-page')).toBeInTheDocument()
  })
})
