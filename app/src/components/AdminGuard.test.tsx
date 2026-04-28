import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import AdminGuard from './AdminGuard'
import { useAppStore } from '../stores/app'

vi.mock('../stores/app', () => ({
  useAppStore: vi.fn(),
}))

const mockedUseAppStore = vi.mocked(useAppStore)

function mockUser(user: { authenticated: boolean; email: string; role: string } | null) {
  mockedUseAppStore.mockImplementation((selector: (s: { enterpriseUser: typeof user }) => unknown) =>
    selector({ enterpriseUser: user })
  )
}

function renderGuard() {
  return render(
    <MemoryRouter>
      <AdminGuard>
        <div>admin content</div>
      </AdminGuard>
    </MemoryRouter>
  )
}

describe('AdminGuard', () => {
  it('renders children when user is admin', () => {
    mockUser({ authenticated: true, email: 'admin@example.com', role: 'admin' })
    renderGuard()
    expect(screen.getByText('admin content')).toBeInTheDocument()
    expect(screen.queryByText(/don't have access/i)).not.toBeInTheDocument()
  })

  it('renders no-access message when user is null', () => {
    mockUser(null)
    renderGuard()
    expect(screen.getByText(/don't have access/i)).toBeInTheDocument()
    expect(screen.getByText(/go back home/i)).toBeInTheDocument()
    expect(screen.queryByText('admin content')).not.toBeInTheDocument()
  })

  it('renders no-access message when user role is member', () => {
    mockUser({ authenticated: true, email: 'user@example.com', role: 'member' })
    renderGuard()
    expect(screen.getByText(/don't have access/i)).toBeInTheDocument()
    expect(screen.getByText(/go back home/i)).toBeInTheDocument()
    expect(screen.queryByText('admin content')).not.toBeInTheDocument()
  })
})
