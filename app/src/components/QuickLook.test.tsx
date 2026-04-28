import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import QuickLook from './QuickLook'

const base = {
  filePath: '/test/file.txt',
  fileType: 'text/plain',
  onClose: vi.fn(),
  isOpen: true,
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('QuickLook', () => {
  it('renders nothing when isOpen=false', () => {
    render(<QuickLook {...base} isOpen={false} />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('renders modal with data-testid="quicklook-modal" when open', () => {
    render(<QuickLook {...base} />)
    expect(screen.getByTestId('quicklook-modal')).toBeInTheDocument()
  })

  it('renders an img when fileType is image/png', () => {
    render(<QuickLook {...base} filePath="/test/photo.png" fileType="image/png" />)
    expect(screen.getByTestId('quicklook-image')).toBeInTheDocument()
  })

  it('renders an iframe when fileType is application/pdf', () => {
    render(<QuickLook {...base} filePath="/test/doc.pdf" fileType="application/pdf" />)
    expect(screen.getByTestId('quicklook-pdf')).toBeInTheDocument()
  })

  it('renders rendered markdown when fileType is text/markdown', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, text: () => Promise.resolve('# Hello World') }),
    )
    render(<QuickLook {...base} filePath="/test/readme.md" fileType="text/markdown" />)
    await waitFor(() => {
      expect(screen.getByTestId('quicklook-markdown')).toBeInTheDocument()
      expect(screen.getByText('Hello World')).toBeInTheDocument()
    })
  })

  it('renders monospace pre when fileType is text/x-python', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, text: () => Promise.resolve('print("hello")') }),
    )
    render(<QuickLook {...base} filePath="/test/script.py" fileType="text/x-python" />)
    await waitFor(() => {
      const code = screen.getByText('print("hello")')
      expect(code).toBeInTheDocument()
      expect(code.closest('pre')).not.toBeNull()
    })
  })

  it('renders sheet table for csv files', async () => {
    const mockData = {
      kind: 'spreadsheet',
      sheets: [{
        name: 'Sheet1',
        rows: [['Name', 'Age'], ['Alice', '30'], ['Bob', '25']],
        total_rows: 3,
        truncated: false,
      }],
    }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(mockData) }),
    )
    render(<QuickLook {...base} filePath="/test/data.csv" fileType="text/csv" />)
    await waitFor(() => {
      expect(screen.getByTestId('quicklook-sheet')).toBeInTheDocument()
      expect(screen.getByText('Name')).toBeInTheDocument()
      expect(screen.getByText('Alice')).toBeInTheDocument()
    })
  })

  it('renders sheet table for xlsx files', async () => {
    const mockData = {
      kind: 'spreadsheet',
      sheets: [{
        name: 'Data',
        rows: [['Col1'], ['val1']],
        total_rows: 2,
        truncated: false,
      }],
    }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(mockData) }),
    )
    render(<QuickLook {...base} filePath="/test/report.xlsx" fileType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" />)
    await waitFor(() => {
      expect(screen.getByTestId('quicklook-sheet')).toBeInTheDocument()
      expect(screen.getByText('Col1')).toBeInTheDocument()
    })
  })

  it('renders slide list for pptx files', async () => {
    const mockData = {
      kind: 'slides',
      slides: [
        { index: 1, title: 'Welcome', body: 'Hello everyone' },
        { index: 2, title: 'Agenda', body: '1. Intro\n2. Demo' },
      ],
    }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(mockData) }),
    )
    render(<QuickLook {...base} filePath="/test/deck.pptx" fileType="application/vnd.openxmlformats-officedocument.presentationml.presentation" />)
    await waitFor(() => {
      expect(screen.getByTestId('quicklook-slides')).toBeInTheDocument()
      expect(screen.getByText('Welcome')).toBeInTheDocument()
      expect(screen.getByText('Agenda')).toBeInTheDocument()
    })
  })

  it('renders docx html preview', async () => {
    const mockData = { kind: 'docx', html: '<p>Hello <strong>World</strong></p>' }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(mockData) }),
    )
    render(<QuickLook {...base} filePath="/test/doc.docx" fileType="application/vnd.openxmlformats-officedocument.wordprocessingml.document" />)
    await waitFor(() => {
      expect(screen.getByTestId('quicklook-docx')).toBeInTheDocument()
      expect(screen.getByText('Hello')).toBeInTheDocument()
    })
  })

  it('shows "Preview not available" and open-native-fallback for unsupported types', () => {
    render(<QuickLook {...base} filePath="/test/file.bin" fileType="application/octet-stream" />)
    expect(screen.getByText(/Preview not available for this file type/i)).toBeInTheDocument()
    expect(screen.getByTestId('quicklook-open-native-fallback')).toBeInTheDocument()
  })

  it('open-in-app button posts to /api/files/open', async () => {
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ ok: true }) })
    vi.stubGlobal('fetch', mockFetch)
    render(<QuickLook {...base} filePath="/test/file.bin" fileType="application/octet-stream" />)
    fireEvent.click(screen.getByTestId('quicklook-open-native-fallback'))
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/files/open'),
        expect.objectContaining({ method: 'POST' }),
      )
    })
  })

  it('calls onClose on backdrop click', () => {
    const onClose = vi.fn()
    render(<QuickLook {...base} onClose={onClose} />)
    fireEvent.click(screen.getByTestId('quicklook-modal'))
    expect(onClose).toHaveBeenCalled()
  })

  it('calls onClose on Esc key', () => {
    const onClose = vi.fn()
    render(<QuickLook {...base} onClose={onClose} />)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })
})
