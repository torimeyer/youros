import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { GoogleAccountSetupCard } from './GoogleWorkspaceSetupCard'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
  },
}))

// jsdom does not provide window.matchMedia.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
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

const NOT_CONFIGURED = { authenticated: false, email: null, credentials_file_present: false }
const CONFIGURED_NOT_AUTHED = { authenticated: false, email: null, credentials_file_present: true }
const AUTHED = { authenticated: true, email: 'tori@example.com', credentials_file_present: true }

function renderCard() {
  return render(
    <GoogleAccountSetupCard darkMode={false} subtextCls="text-slate-500" />
  )
}

describe('GoogleAccountSetupCard (S015)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.history.replaceState({}, '', '/')
  })

  it('shows upload area when credentials file is not present', async () => {
    mockedApiGet.mockResolvedValue(NOT_CONFIGURED)
    renderCard()
    await waitFor(() => {
      expect(screen.getByTestId('google-credentials-upload')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('connect-google-workspace')).not.toBeInTheDocument()
  })

  it('shows connect button when credentials present but not authenticated', async () => {
    mockedApiGet.mockResolvedValue(CONFIGURED_NOT_AUTHED)
    renderCard()
    await waitFor(() => {
      expect(screen.getByTestId('connect-google-workspace')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('google-credentials-upload')).not.toBeInTheDocument()
  })

  it('shows connected state when authenticated', async () => {
    mockedApiGet.mockResolvedValue(AUTHED)
    renderCard()
    await waitFor(() => {
      expect(screen.getByTestId('google-workspace-connected-pill')).toBeInTheDocument()
    })
  })

  it('auto-continues to OAuth after successful upload', async () => {
    // First call: not configured. Second call (after upload): credentials present.
    mockedApiGet
      .mockResolvedValueOnce(NOT_CONFIGURED)
      .mockResolvedValueOnce(CONFIGURED_NOT_AUTHED)
      .mockResolvedValueOnce({ url: 'https://accounts.google.com/o/oauth2/auth?test=1' })

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true }),
    })

    const assignSpy = vi.fn()
    Object.defineProperty(window, 'location', {
      writable: true,
      value: { href: '', assign: assignSpy },
    })

    renderCard()
    await waitFor(() => {
      expect(screen.getByTestId('google-credentials-upload')).toBeInTheDocument()
    })

    // Simulate file drop
    const uploadArea = screen.getByTestId('google-credentials-upload')
    const file = new File(['{"installed":{"client_id":"x","client_secret":"y","redirect_uris":["http://localhost"]}}'], 'creds.json', { type: 'application/json' })
    fireEvent.drop(uploadArea, { dataTransfer: { files: [file] } })

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/drive/credentials',
        expect.objectContaining({ method: 'POST' })
      )
    })

    // After upload, status is re-fetched and auth url is fetched, then redirect happens
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/drive/auth/status')
      expect(mockedApiGet).toHaveBeenCalledWith(expect.stringContaining('/drive/auth/url'))
    })
  })

  it('shows plain-language error on bad file upload', async () => {
    mockedApiGet.mockResolvedValue(NOT_CONFIGURED)

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ok: false, error: 'That does not look like a Google credentials file.' }),
    })

    renderCard()
    await waitFor(() => {
      expect(screen.getByTestId('google-credentials-upload')).toBeInTheDocument()
    })

    const uploadArea = screen.getByTestId('google-credentials-upload')
    const badFile = new File(['not-json'], 'bad.json', { type: 'application/json' })
    fireEvent.drop(uploadArea, { dataTransfer: { files: [badFile] } })

    await waitFor(() => {
      expect(screen.getByTestId('credentials-upload-error')).toBeInTheDocument()
    })
  })
})
