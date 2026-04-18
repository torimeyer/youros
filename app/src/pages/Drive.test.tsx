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
      delete: vi.fn(),
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
const mockedApiDelete = vi.mocked(api.delete)

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
    // Clear localStorage so each test starts with no cached file list.
    // The page seeds from localStorage for a faster return visit, which
    // would otherwise leak rows between tests.
    window.localStorage.removeItem('myos.driveCache.v1')
  })

  it('shows a loading spinner while checking auth', async () => {
    // Never resolves so the spinner stays.
    mockedApiGet.mockReturnValue(new Promise(() => {}))
    renderDrive()
    expect(screen.getByTestId('loading-state')).toBeInTheDocument()
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

    // Mock fetch for the structured preview endpoint.
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: () => 'application/json' },
      json: async () => ({
        kind: 'doc',
        name: 'Q1 Report',
        mime_type: 'application/vnd.google-apps.document',
        thumbnail_url: null,
        export_url: '/api/drive/files/file-1/preview',
        web_view_link: 'https://drive.google.com',
        sample: { blocks: [{ type: 'heading', text: 'Q1 Report' }], truncated: false },
      }),
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
      ok: true,
      status: 200,
      headers: { get: () => 'application/json' },
      json: async () => ({
        kind: 'other',
        name: 'Q1 Report',
        mime_type: 'application/vnd.google-apps.document',
        thumbnail_url: null,
        export_url: '/api/drive/files/file-1/preview',
        web_view_link: '',
        sample: null,
      }),
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

  // --- Delete / undo toast ---

  it('shows a delete button on each file row', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/drive/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/drive/files')) return Promise.resolve({ files: SAMPLE_FILES, cached: false })
      return Promise.resolve({})
    })
    renderDrive()
    await waitFor(() => screen.getByText('Q1 Report'))

    const deleteBtn = screen.getByTitle('Delete Q1 Report')
    expect(deleteBtn).toBeInTheDocument()
  })

  it('clicking delete removes the file from the list and shows the undo toast', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/drive/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/drive/files')) return Promise.resolve({ files: SAMPLE_FILES, cached: false })
      return Promise.resolve({})
    })
    mockedApiDelete.mockResolvedValue({ ok: true })

    renderDrive()
    await waitFor(() => screen.getByText('Q1 Report'))

    fireEvent.click(screen.getByTitle('Delete Q1 Report'))

    // File disappears optimistically.
    await waitFor(() => {
      expect(screen.queryByText('Q1 Report')).not.toBeInTheDocument()
    })
    // Undo toast appears.
    expect(screen.getByTestId('undo-delete-drive-toast')).toBeInTheDocument()
  })

  it(
    'shows an in-app error toast when trash fails (not window.alert)',
    async () => {
      const alertSpy = vi.spyOn(window, 'alert')
      mockedApiGet.mockImplementation((path: string) => {
        if (path.includes('/drive/auth/status')) return Promise.resolve(AUTHENTICATED)
        if (path.includes('/drive/files'))
          return Promise.resolve({ files: SAMPLE_FILES, cached: false })
        return Promise.resolve({})
      })
      // The DELETE call fails so the app must surface an error toast.
      mockedApiDelete.mockRejectedValue(
        new ApiError(500, JSON.stringify({ detail: 'The file is protected.' }))
      )

      renderDrive()
      await waitFor(() => screen.getByText('Q1 Report'))

      fireEvent.click(screen.getByTitle('Delete Q1 Report'))

      // The app has a 5-second undo grace window before it calls DELETE.
      // Wait for the error toast with a generous real-time timeout.
      await waitFor(
        () => {
          expect(screen.getByTestId('drive-trash-error-toast')).toBeInTheDocument()
        },
        { timeout: 8000 }
      )
      // Error text should include the file name.
      expect(screen.getByTestId('drive-trash-error-toast').textContent).toContain('Q1 Report')
      // The browser-native alert must NOT have been called.
      expect(alertSpy).not.toHaveBeenCalled()

      alertSpy.mockRestore()
    },
    15000
  )

  it('clicking Undo restores the file list and does not call DELETE', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/drive/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/drive/files')) return Promise.resolve({ files: SAMPLE_FILES, cached: false })
      return Promise.resolve({})
    })
    mockedApiDelete.mockResolvedValue({ ok: true })

    renderDrive()
    await waitFor(() => screen.getByText('Q1 Report'))

    fireEvent.click(screen.getByTitle('Delete Q1 Report'))
    await waitFor(() => screen.getByTestId('undo-delete-drive-toast'))

    // Undo before the timer fires.
    fireEvent.click(screen.getByTestId('undo-delete-drive-button'))

    // Toast disappears.
    await waitFor(() => {
      expect(screen.queryByTestId('undo-delete-drive-toast')).not.toBeInTheDocument()
    })
    // No DELETE call should have been made (undo cancelled the timer).
    expect(mockedApiDelete).not.toHaveBeenCalled()
  })

  // --- webViewLink fallback for non-previewable file types ---

  it('clicking a non-previewable file row opens webViewLink in a new tab instead of the preview panel', async () => {
    const NON_PREVIEWABLE_FILES = [
      {
        id: 'pptx-1',
        name: 'Pitch Deck.pptx',
        mimeType: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        modifiedTime: new Date().toISOString(),
        iconLink: '',
        webViewLink: 'https://drive.google.com/file/d/pptx-1/view',
        size: null,
      },
    ]

    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/drive/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/drive/files')) return Promise.resolve({ files: NON_PREVIEWABLE_FILES, cached: false })
      return Promise.resolve({})
    })

    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null)

    renderDrive()
    await waitFor(() => screen.getByText('Pitch Deck.pptx'))

    fireEvent.click(screen.getByText('Pitch Deck.pptx'))

    // Preview panel must NOT open.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    // webViewLink must open in a new tab.
    expect(openSpy).toHaveBeenCalledWith(
      'https://drive.google.com/file/d/pptx-1/view',
      '_blank',
      'noopener,noreferrer'
    )

    openSpy.mockRestore()
  })

  it('clicking a .xlsx file row opens webViewLink in a new tab', async () => {
    const XLSX_FILE = [
      {
        id: 'xlsx-1',
        name: 'Budget.xlsx',
        mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        modifiedTime: new Date().toISOString(),
        iconLink: '',
        webViewLink: 'https://drive.google.com/file/d/xlsx-1/view',
        size: null,
      },
    ]

    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/drive/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/drive/files')) return Promise.resolve({ files: XLSX_FILE, cached: false })
      return Promise.resolve({})
    })

    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null)

    renderDrive()
    await waitFor(() => screen.getByText('Budget.xlsx'))

    fireEvent.click(screen.getByText('Budget.xlsx'))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(openSpy).toHaveBeenCalledWith(
      'https://drive.google.com/file/d/xlsx-1/view',
      '_blank',
      'noopener,noreferrer'
    )

    openSpy.mockRestore()
  })

  it('clicking a .zip file row opens webViewLink in a new tab', async () => {
    const ZIP_FILE = [
      {
        id: 'zip-1',
        name: 'Archive.zip',
        mimeType: 'application/zip',
        modifiedTime: new Date().toISOString(),
        iconLink: '',
        webViewLink: 'https://drive.google.com/file/d/zip-1/view',
        size: null,
      },
    ]

    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/drive/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/drive/files')) return Promise.resolve({ files: ZIP_FILE, cached: false })
      return Promise.resolve({})
    })

    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null)

    renderDrive()
    await waitFor(() => screen.getByText('Archive.zip'))

    fireEvent.click(screen.getByText('Archive.zip'))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(openSpy).toHaveBeenCalledWith(
      'https://drive.google.com/file/d/zip-1/view',
      '_blank',
      'noopener,noreferrer'
    )

    openSpy.mockRestore()
  })

  it('clicking a Google Doc row still opens the preview panel (inline renderer intact)', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/drive/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/drive/files')) return Promise.resolve({ files: SAMPLE_FILES, cached: false })
      return Promise.resolve({})
    })

    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      headers: { get: () => 'application/json' },
      json: async () => ({ previewable: false, webViewLink: 'https://drive.google.com', mimeType: 'application/vnd.google-apps.document' }),
    } as unknown as Response)

    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null)

    renderDrive()
    await waitFor(() => screen.getByText('Q1 Report'))

    fireEvent.click(screen.getByText('Q1 Report'))

    // Preview panel must open (not a new tab).
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })
    expect(openSpy).not.toHaveBeenCalled()

    openSpy.mockRestore()
  })

  // --- redirect_uri_mismatch banner ---

  it('shows the redirect_uri_mismatch banner with the exact URL and a GCP link when error=redirect_uri_mismatch is in the URL', async () => {
    // Simulate returning to /drive?error=redirect_uri_mismatch after Google
    // rejects the auth request because the redirect URI is not registered.
    Object.defineProperty(window, 'location', {
      writable: true,
      value: { ...window.location, search: '?error=redirect_uri_mismatch', pathname: '/drive' },
    })

    mockedApiGet.mockResolvedValue({
      authenticated: false,
      email: null,
      credentials_file_present: true,
      needs_reauth: false,
    })

    renderDrive()

    await waitFor(() => {
      expect(screen.getByText(/Google refused the connection because this URL is not registered/i)).toBeInTheDocument()
    })

    // The exact URI the app sends must be shown in the banner.
    expect(screen.getByText('https://localhost:8000/api/drive/auth/callback')).toBeInTheDocument()

    // "Add it to Authorized redirect URIs" instruction must appear.
    expect(screen.getByText(/Authorized redirect URIs/i)).toBeInTheDocument()

    // A link to GCP credentials must be present.
    const gcpLink = screen.getByRole('link', { name: /Open Google Cloud Console credentials/i })
    expect(gcpLink).toBeInTheDocument()
    expect(gcpLink.getAttribute('href')).toBe('https://console.cloud.google.com/apis/credentials')
    expect(gcpLink.getAttribute('target')).toBe('_blank')

    // Reset location for subsequent tests.
    Object.defineProperty(window, 'location', {
      writable: true,
      value: { ...window.location, search: '', pathname: '/drive' },
    })
  })
})

describe('Drive localStorage cache (faster return visits)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.removeItem('myos.driveCache.v1')
  })

  it('paints files from localStorage immediately before the network responds', async () => {
    const cachedFiles = [
      {
        id: 'cached-1',
        name: 'Cached doc',
        mimeType: 'application/vnd.google-apps.document',
        modifiedTime: new Date().toISOString(),
        iconLink: '',
        webViewLink: 'https://docs.google.com/document/d/cached-1',
        size: null,
      },
    ]
    window.localStorage.setItem('myos.driveCache.v1', JSON.stringify(cachedFiles))

    // Network hangs so we prove the first paint came from cache.
    mockedApiGet.mockReturnValue(new Promise(() => {}))

    renderDrive()

    await waitFor(() => {
      expect(screen.getByText('Cached doc')).toBeInTheDocument()
    })
  })

  it('writes fresh files to localStorage so the next visit paints instantly', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/drive/auth/status')) return Promise.resolve(AUTHENTICATED)
      if (path.includes('/drive/files')) return Promise.resolve({ files: SAMPLE_FILES, cached: false })
      return Promise.resolve({})
    })

    renderDrive()

    await waitFor(() => {
      expect(screen.getByText('Q1 Report')).toBeInTheDocument()
    })

    // The freshly fetched files should now be in localStorage.
    const saved = window.localStorage.getItem('myos.driveCache.v1')
    expect(saved).not.toBeNull()
    const parsed = saved ? JSON.parse(saved) : []
    expect(parsed).toHaveLength(2)
    expect(parsed[0].id).toBe('file-1')
  })
})
