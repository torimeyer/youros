import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import AdminPolicies from './Policies'

const mockPolicies = {
  max_agent_budget: 10,
  require_approval_above: 5,
  allowed_models: ['sonnet', 'haiku'],
  audit_retention_days: 90,
  isolation_level: 'open',
}

const mockOrgSettings = {
  settings: {
    org_name: '',
    logo_url: '',
    marketplace_url: '',
    welcome_message: '',
    default_skill_pack_id: '',
    provider_policy: 'auto',
  },
}

vi.mock('../../lib/api', () => ({
  api: {
    get: vi.fn(),
    patch: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}))

vi.mock('../../stores/app', () => ({
  useAppStore: (selector: (s: { darkMode: boolean }) => unknown) =>
    selector({ darkMode: false }),
}))

import { api } from '../../lib/api'

const mockApi = api as {
  get: ReturnType<typeof vi.fn>
  patch: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  delete: ReturnType<typeof vi.fn>
}

beforeEach(() => {
  vi.clearAllMocks()
  mockApi.get.mockImplementation((url: string) => {
    if (url === '/enterprise/policies') return Promise.resolve({ policies: mockPolicies })
    if (url === '/enterprise/templates') return Promise.resolve({ templates: [] })
    if (url === '/enterprise/agentfile') return Promise.resolve({ content: '' })
    if (url === '/org/settings') return Promise.resolve(mockOrgSettings)
    return Promise.resolve({})
  })
  mockApi.patch.mockResolvedValue({})
})

describe('AdminPolicies — provider routing section', () => {
  it('renders the provider routing heading', async () => {
    render(<AdminPolicies />)
    await waitFor(() => {
      expect(screen.getByText('Provider routing')).toBeInTheDocument()
    })
  })

  it('shows the provider policy dropdown', async () => {
    render(<AdminPolicies />)
    await waitFor(() => {
      const select = screen.getByTestId('provider-policy-select')
      expect(select).toBeInTheDocument()
    })
  })

  it('loads the saved provider policy from org settings', async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/enterprise/policies') return Promise.resolve({ policies: mockPolicies })
      if (url === '/enterprise/templates') return Promise.resolve({ templates: [] })
      if (url === '/enterprise/agentfile') return Promise.resolve({ content: '' })
      if (url === '/org/settings')
        return Promise.resolve({ settings: { ...mockOrgSettings.settings, provider_policy: 'claude_only' } })
      return Promise.resolve({})
    })
    render(<AdminPolicies />)
    await waitFor(() => {
      const select = screen.getByTestId('provider-policy-select') as HTMLSelectElement
      expect(select.value).toBe('claude_only')
    })
  })

  it('defaults to auto when org settings has no provider_policy', async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/enterprise/policies') return Promise.resolve({ policies: mockPolicies })
      if (url === '/enterprise/templates') return Promise.resolve({ templates: [] })
      if (url === '/enterprise/agentfile') return Promise.resolve({ content: '' })
      if (url === '/org/settings') return Promise.resolve({ settings: {} })
      return Promise.resolve({})
    })
    render(<AdminPolicies />)
    await waitFor(() => {
      const select = screen.getByTestId('provider-policy-select') as HTMLSelectElement
      expect(select.value).toBe('auto')
    })
  })

  it('calls PATCH /org/settings when the dropdown changes', async () => {
    render(<AdminPolicies />)
    await waitFor(() => screen.getByTestId('provider-policy-select'))

    fireEvent.change(screen.getByTestId('provider-policy-select'), {
      target: { value: 'gemini_only' },
    })

    await waitFor(() => {
      expect(mockApi.patch).toHaveBeenCalledWith('/org/settings', { provider_policy: 'gemini_only' })
    })
  })

  it('shows all four policy options', async () => {
    render(<AdminPolicies />)
    await waitFor(() => screen.getByTestId('provider-policy-select'))

    const select = screen.getByTestId('provider-policy-select')
    const options = select.querySelectorAll('option')
    const values = Array.from(options).map((o) => (o as HTMLOptionElement).value)

    expect(values).toContain('auto')
    expect(values).toContain('claude_only')
    expect(values).toContain('gemini_only')
    expect(values).toContain('prefer_gemini')
  })
})
