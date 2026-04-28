import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import AttachmentPicker, { AttachmentFile } from './AttachmentPicker'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
  },
}))

import { api } from '../lib/api'
const mockApi = api as { get: ReturnType<typeof vi.fn> }

const AGENT_FILES = {
  files: [
    {
      id: 'f1',
      name: 'report.pdf',
      path: '/home/.myos/files/report.pdf',
      size: 1024,
      modified_at: '2026-04-27T10:00:00Z',
      mime_type: 'application/pdf',
      provenance: { source: 'agent' },
    },
    {
      id: 'f2',
      name: 'notes.txt',
      path: '/home/.myos/files/notes.txt',
      size: 512,
      modified_at: '2026-04-26T08:00:00Z',
      mime_type: 'text/plain',
      provenance: null,
    },
  ],
}

const UPLOAD_FILES = {
  files: [
    {
      id: 'u1',
      name: 'upload.png',
      path: '/home/.myos/files/uploads/upload.png',
      size: 2048,
      modified_at: '2026-04-25T12:00:00Z',
      mime_type: 'image/png',
      provenance: { source: 'user' },
    },
  ],
}

const DRIVE_FILES = {
  files: [
    {
      id: 'drive1',
      name: 'Slide Deck.pptx',
      mimeType: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      modifiedTime: '2026-04-20T09:00:00Z',
      webViewLink: 'https://drive.google.com/file/d/drive1',
      size: 4096,
    },
  ],
}

function setup(onSelect = vi.fn(), onClose = vi.fn()) {
  return render(
    <AttachmentPicker open={true} onSelect={onSelect} onClose={onClose} />
  )
}

describe('AttachmentPicker', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    // Default: agents tab loads first
    mockApi.get.mockImplementation((url: string) => {
      if (url.includes('/files/timeline')) return Promise.resolve(AGENT_FILES)
      if (url.includes('/files/list')) return Promise.resolve(UPLOAD_FILES)
      if (url.includes('/drive/files')) return Promise.resolve(DRIVE_FILES)
      return Promise.reject(new Error('unexpected url'))
    })
  })

  it('renders the modal with all 3 tabs', async () => {
    setup()

    expect(screen.getByTestId('attachment-picker-modal')).toBeInTheDocument()
    expect(screen.getByTestId('tab-agents')).toBeInTheDocument()
    expect(screen.getByTestId('tab-uploads')).toBeInTheDocument()
    expect(screen.getByTestId('tab-drive')).toBeInTheDocument()

    expect(screen.getByTestId('tab-agents')).toHaveTextContent('Recent from Agents')
    expect(screen.getByTestId('tab-uploads')).toHaveTextContent('My Uploads')
    expect(screen.getByTestId('tab-drive')).toHaveTextContent('Google Drive')
  })

  it('loads and shows agent files in the default tab', async () => {
    setup()

    await waitFor(() => {
      expect(screen.getByTestId('tab-content-agents')).toBeInTheDocument()
    })

    expect(screen.getByText('report.pdf')).toBeInTheDocument()
    expect(screen.getByText('notes.txt')).toBeInTheDocument()
  })

  it('switches to the Uploads tab and loads upload files', async () => {
    setup()

    // Wait for agents tab to finish loading first
    await waitFor(() => {
      expect(screen.getByTestId('tab-content-agents')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('tab-uploads'))

    await waitFor(() => {
      expect(screen.getByTestId('tab-content-uploads')).toBeInTheDocument()
    })

    expect(screen.getByText('upload.png')).toBeInTheDocument()
  })

  it('switches to the Drive tab and loads Drive files', async () => {
    setup()

    await waitFor(() => {
      expect(screen.getByTestId('tab-content-agents')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('tab-drive'))

    await waitFor(() => {
      expect(screen.getByTestId('tab-content-drive')).toBeInTheDocument()
    })

    expect(screen.getByText('Slide Deck.pptx')).toBeInTheDocument()
  })

  it('calls onSelect with the file when a row is clicked', async () => {
    const onSelect = vi.fn()
    setup(onSelect)

    await waitFor(() => {
      expect(screen.getByTestId('file-row-agents-0')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('file-row-agents-0'))

    expect(onSelect).toHaveBeenCalledOnce()
    const selected: AttachmentFile = onSelect.mock.calls[0][0]
    expect(selected.name).toBe('report.pdf')
    expect(selected.source).toBe('agent')
  })

  it('calls onClose when clicking a file row', async () => {
    const onClose = vi.fn()
    setup(vi.fn(), onClose)

    await waitFor(() => {
      expect(screen.getByTestId('file-row-agents-0')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('file-row-agents-0'))
    expect(onClose).toHaveBeenCalled()
  })

  it('calls onClose when the X button is clicked', () => {
    const onClose = vi.fn()
    setup(vi.fn(), onClose)
    fireEvent.click(screen.getByTestId('attachment-picker-close'))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('calls onClose when the backdrop is clicked', () => {
    const onClose = vi.fn()
    setup(vi.fn(), onClose)
    fireEvent.click(screen.getByTestId('attachment-picker-backdrop'))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('calls onClose on Escape key', () => {
    const onClose = vi.fn()
    setup(vi.fn(), onClose)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('does not render when open is false', () => {
    render(<AttachmentPicker open={false} onSelect={vi.fn()} onClose={vi.fn()} />)
    expect(screen.queryByTestId('attachment-picker-modal')).not.toBeInTheDocument()
  })

  it('shows an error message when the API fails', async () => {
    mockApi.get.mockRejectedValue(new Error('network error'))
    setup()

    await waitFor(() => {
      expect(screen.getByText(/Could not load files/)).toBeInTheDocument()
    })
  })

  it('shows empty state when no files are returned', async () => {
    mockApi.get.mockResolvedValue({ files: [] })
    setup()

    await waitFor(() => {
      expect(screen.getByText(/No recent files from agents yet/)).toBeInTheDocument()
    })
  })
})
