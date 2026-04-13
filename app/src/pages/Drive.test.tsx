import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Drive from './Drive'
import { ApiError } from '../lib/api'

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
const mockedApiPost = vi.mocked(api.post)

const NOT_AUTHENTICATED = {
  authenticated: false,
  email: null,
  credentials_file_present: false,
}

const NOT_AUTHENTICATED_WITH_CREDS = {
  authenticated: false,
  email: null,
  credentials_file_present: true,
}

const AUTHENTICATED = {
  authenticated: true,
  email: 'tori@example.com',
  credentials_file_present: true,
}

const SAMPLE_FILES = [
  {
    id: 'file-1',
    name: 'Q1 Report',
    mimeType: 'application/vnd.google-apps.document',
    modifiedTime: new Date().toISOString(),
    iconLink: '',
    webViewLink: 'https://docs.google.com/document/d/file-1',
    size: null,
  },
  {
    id: 'file-2',
    name: 'Budget 2026',
    mimeType: 'application/vnd.google-apps.spreadsheet',
    modifiedTime: new Date().toISOString(),
    iconLink: '',
    webViewLink: 'https://docs.google.com/spreadsheets/d/file-2',
    size: null,
  },
]

function renderDrive() {
  return render(
    <MemoryRouter>
      <Drive />
    </MemoryRouter>
  )
}

describe('Drive page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows a loading spinner while checking auth', async () => {
    // Never resolves so the spinner stays.
    mockedApiGet.mockReturnValue(new Promise(() => {}))
    renderDrive()
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('shows the connect screen when not authenticated without credentials', async () => {
    mockedApiGet.mockResolvedValue(NOT_AUTHENTICATED)
    renderDrive()
    await waitFor(() => {
      expect(screen.getByText('Connect Google Drive')).toBeInTheDocument()
    })
    // When no credentials file is present, the CredentialsPicker is shown
    // instead of an enabled connect button. The user cannot connect until
    // they save a credentials file first.
    expect(screen.queryByRole('button', { name: /connect your google account/i })).not.toBeInTheDocument()
  })

  it('shows enabled connect button when credentials file is present', async () => {
    mockedApiGet.mockResolvedValue(NOT_AUTHENTICATED_WITH_CREDS)
    renderDrive()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /connect your google account/i })).not.toBeDisabled()
    })
  })

  it('shows the file list when authenticated', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/drive/auth/status')) {
        return Promise.resolve(AUTHENTICATED)
      }
      if (path.includes('/drive/files')) {
        return Promise.resolve({ files: SAMPLE_FILES, cached: true })
      }
      return Promise.resolve({})
    })
    renderDrive()
    await waitFor(() => {
      expect(screen.getByText('Q1 Report')).toBeInTheDocument()
      expect(screen.getByText('Budget 2026')).toBeInTheDocument()
    })
  })

  it('shows connected email when authenticated', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/drive/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/drive/files')) return Promise.resolve({ files: [], cached: false })
      return Promise.resolve({})
    })
    renderDrive()
    await waitFor(() => {
      expect(screen.getByText('tori@example.com')).toBeInTheDocument()
    })
  })

  it('shows file type labels', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/drive/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/drive/files')) return Promise.resolve({ files: SAMPLE_FILES, cached: false })
      return Promise.resolve({})
    })
    renderDrive()
    await waitFor(() => {
      expect(screen.getByText('Google Doc')).toBeInTheDocument()
      expect(screen.getByText('Google Sheets')).toBeInTheDocument()
    })
  })

  it('opens the preview overlay when a file is clicked', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/drive/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/drive/files')) return Promise.resolve({ files: SAMPLE_FILES, cached: false })
      return Promise.resolve({})
    })

    // Mock fetch for the preview endpoint.
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      headers: { get: () => 'application/json' },
      json: async () => ({ previewable: false, webViewLink: 'https://drive.google.com', mimeType: 'application/vnd.google-apps.document' }),
    } as unknown as Response)

    renderDrive()
    await waitFor(() => {
      expect(screen.getByText('Q1 Report')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Q1 Report'))
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })
  })

  it('closes the preview overlay with the close button', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/drive/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/drive/files')) return Promise.resolve({ files: SAMPLE_FILES, cached: false })
      return Promise.resolve({})
    })

    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      headers: { get: () => 'application/json' },
      json: async () => ({ previewable: false, webViewLink: '', mimeType: 'application/zip' }),
    } as unknown as Response)

    renderDrive()
    await waitFor(() => screen.getByText('Q1 Report'))

    fireEvent.click(screen.getByText('Q1 Report'))
    await waitFor(() => screen.getByRole('dialog'))

    const closeBtn = screen.getByRole('button', { name: /close preview/i })
    fireEvent.click(closeBtn)
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
  })

  it('calls sync endpoint when Sync now is clicked', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/drive/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/drive/files')) return Promise.resolve({ files: SAMPLE_FILES, cached: true })
      return Promise.resolve({})
    })
    mockedApiPost.mockResolvedValue({ ok: true, file_count: 2, synced_at: Date.now() / 1000 })

    renderDrive()
    await waitFor(() => screen.getByText('Sync now'))

    fireEvent.click(screen.getByText('Sync now'))
    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/drive/sync')
    })
  })

  it('calls revoke endpoint when Disconnect is clicked', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/drive/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/drive/files')) return Promise.resolve({ files: [], cached: false })
      return Promise.resolve({})
    })
    mockedApiPost.mockResolvedValue({ ok: true })

    renderDrive()
    await waitFor(() => screen.getByText('Disconnect'))

    fireEvent.click(screen.getByText('Disconnect'))
    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/drive/auth/revoke')
    })
  })

  it('shows the Enable Drive API screen when api_not_enabled is returned', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/drive/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/drive/files')) {
        return Promise.reject(
          new ApiError(
            403,
            JSON.stringify({
              detail: {
                api_not_enabled: true,
                message: 'Google Drive API is not enabled in your Google Cloud project.',
              },
            })
          )
        )
      }
      return Promise.resolve({})
    })

    renderDrive()

    await waitFor(() => {
      expect(screen.getByText('Drive API not enabled')).toBeInTheDocument()
    })

    const link = screen.getByRole('link', { name: /Enable Drive API in Google Cloud/i })
    expect(link).toBeInTheDocument()
    expect(link.getAttribute('href')).toBe(
      'https://console.cloud.google.com/apis/library/drive.googleapis.com'
    )
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel')).toBe('noreferrer')
  })

  it('Retry clears the api_not_enabled screen on success', async () => {
    let filesCalls = 0
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/drive/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/drive/files')) {
        filesCalls += 1
        if (filesCalls === 1) {
          return Promise.reject(
            new ApiError(
              403,
              JSON.stringify({ detail: { api_not_enabled: true, message: 'not enabled' } })
            )
          )
        }
        return Promise.resolve({ files: SAMPLE_FILES, cached: false })
      }
      return Promise.resolve({})
    })

    renderDrive()

    const retryButton = await screen.findByRole('button', { name: /Retry/i })
    fireEvent.click(retryButton)

    await waitFor(() => {
      expect(screen.queryByText('Drive API not enabled')).not.toBeInTheDocument()
    })
  })

  it('shows empty state with sync prompt when no files', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/drive/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/drive/files')) return Promise.resolve({ files: [], cached: false })
      return Promise.resolve({})
    })
    renderDrive()
    await waitFor(() => {
      expect(screen.getByText(/No files found/i)).toBeInTheDocument()
    })
  })
})
