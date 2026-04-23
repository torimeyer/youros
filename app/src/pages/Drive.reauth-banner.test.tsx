import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Documents from './Documents'

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

// jsdom does not provide window.matchMedia. Provide a minimal stub
// so components that use responsive breakpoints do not crash.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)

const NOT_AUTHENTICATED = {
  authenticated: false,
  needs_reauth: true,
  email: null,
  credentials_file_present: true,
}

function renderDocumentsDriveTab() {
  return render(
    <MemoryRouter initialEntries={['/documents?tab=drive']}>
      <Documents />
    </MemoryRouter>
  )
}

describe('Drive connect state in Documents', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/drive/auth/status')) return Promise.resolve(NOT_AUTHENTICATED)
      if (path.includes('/drive/files')) return Promise.resolve({ files: [], cached: false })
      if (path === '/documents') return Promise.resolve([])
      if (path.includes('/docs/recent')) return Promise.resolve({ files: [] })
      if (path.includes('/specs/counts')) return Promise.resolve({ unfinished: 0 })
      return Promise.resolve({})
    })
  })

  it('shows Connect Google Drive button when not authenticated', async () => {
    renderDocumentsDriveTab()
    const button = await waitFor(() =>
      screen.getByTestId('connect-drive-button')
    )
    expect(button).toBeInTheDocument()
  })

  it('shows cloud_off state message when not authenticated', async () => {
    renderDocumentsDriveTab()
    const message = await waitFor(() =>
      screen.getByText('Connect Google Drive to see your files here')
    )
    expect(message).toBeInTheDocument()
  })
})
