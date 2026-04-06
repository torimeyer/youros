import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Docs from './Docs'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)
const mockedApiPost = vi.mocked(api.post)

const mockDocsResponse = {
  docs: [
    {
      path: 'docs/draft/onboarding-flow.md',
      filename: 'onboarding-flow.md',
      title: 'onboarding flow',
      status: 'draft',
      created_at: '2026-04-01T00:00:00Z',
      promoted_at: '',
      body: 'Plan the onboarding experience.',
    },
    {
      path: 'docs/spec/auth-system.md',
      filename: 'auth-system.md',
      title: 'auth system',
      status: 'spec',
      created_at: '2026-03-15T00:00:00Z',
      promoted_at: '2026-03-20T00:00:00Z',
      body: '- [ ] sign in flow\n- [ ] sign out flow',
    },
  ],
}

function renderDocs() {
  return render(
    <MemoryRouter>
      <Docs />
    </MemoryRouter>
  )
}

describe('Docs page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedApiGet.mockResolvedValue(mockDocsResponse)
    mockedApiPost.mockResolvedValue({ result: 'ok' })
  })

  it('renders docs from API data', async () => {
    renderDocs()

    await waitFor(() => {
      expect(screen.getByText('onboarding flow')).toBeInTheDocument()
    })

    expect(screen.getByText('auth system')).toBeInTheDocument()
  })

  it('calls api.get with /docs on mount', async () => {
    renderDocs()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/docs')
    })
  })

  it('shows the page title', async () => {
    renderDocs()

    const heading = screen.getByRole('heading', { name: 'Docs' })
    expect(heading).toBeInTheDocument()
  })

  it('count badge shows correct total', async () => {
    renderDocs()

    await waitFor(() => {
      const badge = document.querySelector('.bg-blue-500.text-white.text-xs.rounded-full')
      expect(badge).not.toBeNull()
      expect(badge!.textContent).toBe('2')
    })
  })

  it('shows draft and spec workflow indicators', async () => {
    renderDocs()

    await waitFor(() => {
      expect(screen.getByText('onboarding flow')).toBeInTheDocument()
    })

    // Workflow summary shows counts for drafts and specs as bold numbers
    const boldCounts = document.querySelectorAll('strong.text-white')
    expect(boldCounts.length).toBeGreaterThanOrEqual(2)
  })

  it('new draft button calls POST /docs/draft', async () => {
    renderDocs()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })

    const input = screen.getByPlaceholderText('Name your plan...')
    fireEvent.change(input, { target: { value: 'My new plan' } })

    const button = screen.getByRole('button', { name: 'New Draft' })
    fireEvent.click(button)

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/docs/draft', { title: 'My new plan' })
    })
  })

  it('clears input after creating a draft', async () => {
    renderDocs()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })

    const input = screen.getByPlaceholderText('Name your plan...') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'Temp' } })
    expect(input.value).toBe('Temp')

    fireEvent.click(screen.getByRole('button', { name: 'New Draft' }))

    await waitFor(() => {
      expect(input.value).toBe('')
    })
  })

  it('Enter key creates a new draft', async () => {
    renderDocs()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })

    const input = screen.getByPlaceholderText('Name your plan...')
    fireEvent.change(input, { target: { value: 'Enter plan' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/docs/draft', { title: 'Enter plan' })
    })
  })

  it('does not create draft with empty input', async () => {
    renderDocs()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })

    fireEvent.click(screen.getByRole('button', { name: 'New Draft' }))
    expect(mockedApiPost).not.toHaveBeenCalled()
  })

  it('draft card has Promote to Spec button', async () => {
    renderDocs()

    await waitFor(() => {
      expect(screen.getByText('onboarding flow')).toBeInTheDocument()
    })

    expect(screen.getByRole('button', { name: 'Promote to Spec' })).toBeInTheDocument()
  })

  it('spec card has Break into Tasks button', async () => {
    renderDocs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    expect(screen.getByRole('button', { name: 'Break into Tasks' })).toBeInTheDocument()
  })

  it('Promote to Spec calls POST /docs/promote', async () => {
    renderDocs()

    await waitFor(() => {
      expect(screen.getByText('onboarding flow')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Promote to Spec' }))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/docs/promote', {
        path: 'docs/draft/onboarding-flow.md',
      })
    })
  })

  it('Break into Tasks calls POST /docs/decompose', async () => {
    renderDocs()

    await waitFor(() => {
      expect(screen.getByText('auth system')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Break into Tasks' }))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/docs/decompose', {
        path: 'docs/spec/auth-system.md',
      })
    })
  })

  it('shows success message after creating draft', async () => {
    renderDocs()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })

    const input = screen.getByPlaceholderText('Name your plan...')
    fireEvent.change(input, { target: { value: 'Test plan' } })
    fireEvent.click(screen.getByRole('button', { name: 'New Draft' }))

    await waitFor(() => {
      expect(screen.getByText('Draft created.')).toBeInTheDocument()
    })
  })

  it('shows error message on draft creation failure', async () => {
    mockedApiPost.mockRejectedValueOnce(new Error('Server error'))

    renderDocs()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })

    const input = screen.getByPlaceholderText('Name your plan...')
    fireEvent.change(input, { target: { value: 'Bad plan' } })
    fireEvent.click(screen.getByRole('button', { name: 'New Draft' }))

    await waitFor(() => {
      expect(screen.getByText('Could not create draft. Try again.')).toBeInTheDocument()
    })
  })

  it('shows empty state when no docs exist', async () => {
    mockedApiGet.mockResolvedValue({ docs: [] })

    renderDocs()

    await waitFor(() => {
      expect(screen.getByText('No documents yet. Create a draft to start planning.')).toBeInTheDocument()
    })
  })

  it('tabs filter by status', async () => {
    renderDocs()

    await waitFor(() => {
      expect(screen.getByText('onboarding flow')).toBeInTheDocument()
    })

    // Click Drafts tab
    fireEvent.click(screen.getByRole('button', { name: /Drafts/ }))

    // Draft should be visible, spec should not
    expect(screen.getByText('onboarding flow')).toBeInTheDocument()
    expect(screen.queryByText('auth system')).not.toBeInTheDocument()

    // Click Specs tab
    fireEvent.click(screen.getByRole('button', { name: /Specs/ }))

    expect(screen.queryByText('onboarding flow')).not.toBeInTheDocument()
    expect(screen.getByText('auth system')).toBeInTheDocument()

    // Click All tab
    fireEvent.click(screen.getByRole('button', { name: 'All' }))

    expect(screen.getByText('onboarding flow')).toBeInTheDocument()
    expect(screen.getByText('auth system')).toBeInTheDocument()
  })

  it('handles API error on initial load gracefully', async () => {
    mockedApiGet.mockRejectedValue(new Error('Network error'))

    renderDocs()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/docs')
    })

    // Page title should still be visible
    expect(screen.getByRole('heading', { name: 'Docs' })).toBeInTheDocument()
  })

  it('shows promote error about acceptance criteria', async () => {
    mockedApiPost.mockRejectedValueOnce(new Error('Draft must contain at least one unchecked checkbox'))

    renderDocs()

    await waitFor(() => {
      expect(screen.getByText('onboarding flow')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Promote to Spec' }))

    await waitFor(() => {
      expect(
        screen.getByText('This draft needs at least one checklist item (acceptance criteria) before it can be promoted.')
      ).toBeInTheDocument()
    })
  })
})
