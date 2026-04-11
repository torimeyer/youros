import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Drive from './Drive'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
    },
  }
})

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)

const NEEDS_REAUTH = {
  authenticated: true,
  needs_reauth: true,
  email: 'test@example.com',
  credentials_file_present: true,
}

function renderDrive() {
  return render(
    <MemoryRouter>
      <Drive />
    </MemoryRouter>
  )
}

describe('Drive reauth banner contrast', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/drive/auth/status')) return Promise.resolve(NEEDS_REAUTH)
      if (path.includes('/drive/files')) return Promise.resolve({ files: [], cached: false })
      return Promise.resolve({})
    })
  })

  it('uses readable amber classes on the banner wrapper', async () => {
    renderDrive()
    const message = await screen.findByText(
      'Reconnect your Google account to enable file uploads.'
    )
    // The banner wrapper is the ancestor div that carries the background
    // and border classes. Walk up until we find it.
    let node: HTMLElement | null = message
    while (node && !node.className.includes('bg-amber-500/15')) {
      node = node.parentElement
    }
    expect(node).not.toBeNull()
    const wrapperClasses = node!.className
    expect(wrapperClasses).toContain('bg-amber-500/15')
    expect(wrapperClasses).toContain('text-amber-100')
    expect(wrapperClasses).toContain('border-amber-400/60')
  })

  it('uses readable amber classes on the Reconnect button', async () => {
    renderDrive()
    const button = await waitFor(() =>
      screen.getByRole('button', { name: /^Reconnect$/ })
    )
    const buttonClasses = button.className
    expect(buttonClasses).toContain('text-amber-50')
    expect(buttonClasses).toContain('border-amber-400/60')
    expect(buttonClasses).toContain('bg-amber-500/30')
  })
})
