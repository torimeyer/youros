import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import AdminSecurity from './Security'

vi.mock('../../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}))

vi.mock('../../stores/app', () => ({
  useAppStore: (selector: (s: { darkMode: boolean }) => unknown) =>
    selector({ darkMode: false }),
}))

vi.mock('../../components/Icon', () => ({
  default: ({ name }: { name: string }) => <span data-testid={`icon-${name}`}>{name}</span>,
}))

import { api } from '../../lib/api'

const mockApi = api as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  delete: ReturnType<typeof vi.fn>
}

const defaultApiResponses = () => {
  mockApi.get.mockImplementation((url: string) => {
    if (url === '/enterprise/sso') return Promise.resolve({ configured: false })
    if (url === '/enterprise/api-keys') return Promise.resolve({ providers: [] })
    if (url === '/org/signing-key') return Promise.resolve({ exists: false, fingerprint: null, generated_at: null })
    return Promise.resolve({})
  })
}

describe('AdminSecurity — signing key card', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders signing key card with Not set status when no key exists', async () => {
    defaultApiResponses()
    render(<AdminSecurity />)
    await waitFor(() => {
      expect(screen.getByTestId('signing-key-card')).toBeInTheDocument()
    })
    expect(screen.getByTestId('signing-key-status-badge')).toHaveTextContent('Not set')
  })

  it('shows Generate key button when no key exists', async () => {
    defaultApiResponses()
    render(<AdminSecurity />)
    await waitFor(() => {
      expect(screen.getByTestId('generate-signing-key-btn')).toBeInTheDocument()
    })
    expect(screen.getByTestId('generate-signing-key-btn')).toHaveTextContent('Generate key')
  })

  it('calls generate endpoint when Generate key is clicked', async () => {
    defaultApiResponses()
    mockApi.post.mockResolvedValue({
      fingerprint: 'abc123def456abc123def456abc123def456abc123def456abc123def456abcd',
      generated_at: '2026-04-27T00:00:00Z',
    })
    render(<AdminSecurity />)
    await waitFor(() => screen.getByTestId('generate-signing-key-btn'))
    fireEvent.click(screen.getByTestId('generate-signing-key-btn'))
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith('/org/signing-key/generate', {})
    })
  })

  it('shows Active badge and fingerprint after generate', async () => {
    const fingerprint = 'abc123def456abc123def456abc123def456abc123def456abc123def456abcd'
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/enterprise/sso') return Promise.resolve({ configured: false })
      if (url === '/enterprise/api-keys') return Promise.resolve({ providers: [] })
      if (url === '/org/signing-key') return Promise.resolve({
        exists: true,
        fingerprint,
        generated_at: '2026-04-27T00:00:00Z',
      })
      return Promise.resolve({})
    })
    render(<AdminSecurity />)
    await waitFor(() => {
      expect(screen.getByTestId('signing-key-status-badge')).toHaveTextContent('Active')
    })
    expect(screen.getByTestId('signing-key-fingerprint')).toHaveTextContent(fingerprint)
  })

  it('shows Revoke key button when key exists', async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/enterprise/sso') return Promise.resolve({ configured: false })
      if (url === '/enterprise/api-keys') return Promise.resolve({ providers: [] })
      if (url === '/org/signing-key') return Promise.resolve({
        exists: true,
        fingerprint: 'abc123def456abc123def456abc123def456abc123def456abc123def456abcd',
        generated_at: '2026-04-27T00:00:00Z',
      })
      return Promise.resolve({})
    })
    render(<AdminSecurity />)
    await waitFor(() => {
      expect(screen.getByTestId('revoke-signing-key-btn')).toBeInTheDocument()
    })
  })

  it('shows confirmation step when Revoke key is clicked', async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/enterprise/sso') return Promise.resolve({ configured: false })
      if (url === '/enterprise/api-keys') return Promise.resolve({ providers: [] })
      if (url === '/org/signing-key') return Promise.resolve({
        exists: true,
        fingerprint: 'abc123def456abc123def456abc123def456abc123def456abc123def456abcd',
        generated_at: '2026-04-27T00:00:00Z',
      })
      return Promise.resolve({})
    })
    render(<AdminSecurity />)
    await waitFor(() => screen.getByTestId('revoke-signing-key-btn'))
    fireEvent.click(screen.getByTestId('revoke-signing-key-btn'))
    expect(screen.getByTestId('revoke-confirm-btn')).toBeInTheDocument()
  })

  it('calls revoke endpoint when confirm is clicked', async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/enterprise/sso') return Promise.resolve({ configured: false })
      if (url === '/enterprise/api-keys') return Promise.resolve({ providers: [] })
      if (url === '/org/signing-key') return Promise.resolve({
        exists: true,
        fingerprint: 'abc123def456abc123def456abc123def456abc123def456abc123def456abcd',
        generated_at: '2026-04-27T00:00:00Z',
      })
      return Promise.resolve({})
    })
    mockApi.post.mockResolvedValue({ ok: true, warning: 'key removed' })
    render(<AdminSecurity />)
    await waitFor(() => screen.getByTestId('revoke-signing-key-btn'))
    fireEvent.click(screen.getByTestId('revoke-signing-key-btn'))
    fireEvent.click(screen.getByTestId('revoke-confirm-btn'))
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith('/org/signing-key/revoke', {})
    })
  })

  it('resets to Not set after revoke completes', async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/enterprise/sso') return Promise.resolve({ configured: false })
      if (url === '/enterprise/api-keys') return Promise.resolve({ providers: [] })
      if (url === '/org/signing-key') return Promise.resolve({
        exists: true,
        fingerprint: 'abc123def456abc123def456abc123def456abc123def456abc123def456abcd',
        generated_at: '2026-04-27T00:00:00Z',
      })
      return Promise.resolve({})
    })
    mockApi.post.mockResolvedValue({ ok: true, warning: 'key removed' })
    render(<AdminSecurity />)
    await waitFor(() => screen.getByTestId('revoke-signing-key-btn'))
    fireEvent.click(screen.getByTestId('revoke-signing-key-btn'))
    fireEvent.click(screen.getByTestId('revoke-confirm-btn'))
    await waitFor(() => {
      expect(screen.getByTestId('signing-key-status-badge')).toHaveTextContent('Not set')
    })
  })
})
