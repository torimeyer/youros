import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import OrgSettings from './OrgSettings'

const mockSettings = {
  org_name: '',
  logo_url: '',
  marketplace_url: '',
  welcome_message: '',
  default_skill_pack_id: '',
}

const mockPolicies = {
  max_agent_budget: 10,
  require_approval_above: 5,
  allowed_models: ['sonnet', 'haiku'],
  audit_retention_days: 90,
  isolation_level: 'sandboxed',
}

vi.mock('../../lib/api', () => ({
  api: {
    get: vi.fn(),
    patch: vi.fn(),
  },
}))

vi.mock('../../stores/app', () => ({
  useAppStore: (selector: (s: { darkMode: boolean }) => unknown) =>
    selector({ darkMode: false }),
}))

vi.mock('../../components/Icon', () => ({
  default: ({ name }: { name: string }) => <span data-testid={`icon-${name}`} />,
}))

describe('OrgSettings', () => {
  beforeEach(async () => {
    const { api } = await import('../../lib/api')
    vi.mocked(api.get).mockResolvedValue({ settings: mockSettings, policies_summary: mockPolicies })
    vi.mocked(api.patch).mockResolvedValue({ settings: { ...mockSettings, org_name: 'Acme Corp' } })
  })

  it('renders all setting cards', async () => {
    render(<OrgSettings />)

    await waitFor(() => {
      expect(screen.getByText('Branding')).toBeInTheDocument()
      expect(screen.getByText('Marketplace')).toBeInTheDocument()
      expect(screen.getByText('Welcome message')).toBeInTheDocument()
      expect(screen.getByText('Default starter pack')).toBeInTheDocument()
      expect(screen.getByText('Active policies')).toBeInTheDocument()
    })
  })

  it('shows policies summary values', async () => {
    render(<OrgSettings />)

    await waitFor(() => {
      expect(screen.getByText('$10')).toBeInTheDocument()
      expect(screen.getByText('90 days')).toBeInTheDocument()
      expect(screen.getByText('sonnet, haiku')).toBeInTheDocument()
    })
  })

  it('links to policies page', async () => {
    render(<OrgSettings />)

    await waitFor(() => {
      const link = screen.getByRole('link', { name: /edit policies/i })
      expect(link).toHaveAttribute('href', '/admin/policies')
    })
  })

  it('calls PATCH on form submit', async () => {
    const { api } = await import('../../lib/api')
    render(<OrgSettings />)

    await waitFor(() => {
      expect(screen.getByLabelText('Display name')).toBeInTheDocument()
    })

    fireEvent.change(screen.getByLabelText('Display name'), {
      target: { value: 'Acme Corp' },
    })

    fireEvent.click(screen.getByRole('button', { name: /save changes/i }))

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith(
        '/org/settings',
        expect.objectContaining({ org_name: 'Acme Corp' }),
      )
    })
  })

  it('shows saved confirmation after successful submit', async () => {
    render(<OrgSettings />)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /save changes/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /save changes/i }))

    await waitFor(() => {
      expect(screen.getByText('Saved.')).toBeInTheDocument()
    })
  })

  it('shows error message when GET fails', async () => {
    const { api } = await import('../../lib/api')
    vi.mocked(api.get).mockRejectedValueOnce(new Error('Network error'))

    render(<OrgSettings />)

    await waitFor(() => {
      expect(screen.getByText('Network error')).toBeInTheDocument()
    })
  })
})
