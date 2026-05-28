import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import QuickLook, { DrivePreviewData } from './QuickLook'

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

  it('renders an img when fileType is image/png', async () => {
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock-image-url')
    vi.spyOn(URL, 'revokeObjectURL').mockReturnValue(undefined)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      blob: () => Promise.resolve(new Blob(['img'], { type: 'image/png' })),
    }))
    render(<QuickLook {...base} filePath="/test/photo.png" fileType="image/png" />)
    await waitFor(() => {
      expect(screen.getByTestId('quicklook-image')).toBeInTheDocument()
    })
  })

  it('renders an iframe when fileType is application/pdf', async () => {
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock-pdf-url')
    vi.spyOn(URL, 'revokeObjectURL').mockReturnValue(undefined)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      blob: () => Promise.resolve(new Blob(['%PDF'], { type: 'application/pdf' })),
    }))
    render(<QuickLook {...base} filePath="/test/doc.pdf" fileType="application/pdf" />)
    await waitFor(() => {
      expect(screen.getByTestId('quicklook-pdf')).toBeInTheDocument()
    })
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

  it('renders slide carousel for pptx files with prev/next navigation', async () => {
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
      // First slide shown by default.
      expect(screen.getByText('Welcome')).toBeInTheDocument()
      expect(screen.getByText('Slide 1 of 2')).toBeInTheDocument()
      // Second slide not yet visible.
      expect(screen.queryByText('Agenda')).not.toBeInTheDocument()
    })
    // Navigate forward — Agenda becomes visible.
    fireEvent.click(screen.getByTestId('slide-next'))
    await waitFor(() => {
      expect(screen.getByText('Agenda')).toBeInTheDocument()
      expect(screen.getByText('Slide 2 of 2')).toBeInTheDocument()
      expect(screen.queryByText('Welcome')).not.toBeInTheDocument()
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

// ---------------------------------------------------------------------------
// Drive mode tests
// ---------------------------------------------------------------------------

const driveBase = {
  filePath: '',
  fileType: '',
  onClose: vi.fn(),
  isOpen: true,
  driveFileId: 'test-file-id',
}

function makeDriveData(overrides: Partial<DrivePreviewData> = {}): DrivePreviewData {
  return {
    kind: 'other',
    name: 'file.bin',
    mime_type: 'application/octet-stream',
    thumbnail_url: null,
    export_url: '/api/drive/files/test-file-id/preview',
    web_view_link: 'https://drive.google.com/file/d/test-file-id',
    sample: null,
    ...overrides,
  }
}

describe('QuickLook - Drive mode', () => {
  it('fetches /api/drive/preview/{id} when driveData is not provided', async () => {
    const mockData = makeDriveData({ kind: 'other' })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockData),
    }))
    render(<QuickLook {...driveBase} />)
    await waitFor(() => {
      expect(screen.getByTestId('quicklook-modal')).toBeInTheDocument()
      // fetch was called with the preview endpoint
    })
    const fetchMock = vi.mocked(global.fetch)
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/drive/preview/test-file-id'),
    )
  })

  it('renders Drive PDF via blob URL fetched from export_url', async () => {
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock-pdf')
    vi.spyOn(URL, 'revokeObjectURL').mockReturnValue(undefined)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      blob: () => Promise.resolve(new Blob(['%PDF'], { type: 'application/pdf' })),
    }))
    render(<QuickLook {...driveBase} driveData={makeDriveData({
      kind: 'pdf',
      name: 'report.pdf',
      mime_type: 'application/pdf',
    })} />)
    await waitFor(() => {
      expect(screen.getByTestId('quicklook-pdf')).toBeInTheDocument()
    })
    const iframe = screen.getByTestId('quicklook-pdf') as HTMLIFrameElement
    expect(iframe.src).toContain('blob:mock-pdf')
  })

  it('renders Drive image via blob URL fetched from export_url', async () => {
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock-image')
    vi.spyOn(URL, 'revokeObjectURL').mockReturnValue(undefined)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      blob: () => Promise.resolve(new Blob(['img'], { type: 'image/png' })),
    }))
    render(<QuickLook {...driveBase} driveData={makeDriveData({
      kind: 'image',
      name: 'photo.png',
      mime_type: 'image/png',
      thumbnail_url: 'https://img/thumb',
    })} />)
    await waitFor(() => {
      expect(screen.getByTestId('quicklook-image')).toBeInTheDocument()
    })
    const img = screen.getByTestId('quicklook-image') as HTMLImageElement
    expect(img.src).toContain('blob:mock-image')
  })

  it('renders Drive sheets with multi-tab and sheet-tab-{name} testids', () => {
    render(<QuickLook {...driveBase} driveData={makeDriveData({
      kind: 'sheet',
      name: 'Budget',
      mime_type: 'application/vnd.google-apps.spreadsheet',
      sample: {
        sheets: [
          { name: 'Revenue', headers: ['Month', 'Amount'], rows: [['Jan', '1000']], truncated: false },
          { name: 'Costs', headers: ['Item', 'Price'], rows: [['Rent', '500']], truncated: false },
        ],
      },
    })} />)
    expect(screen.getByTestId('quicklook-sheet')).toBeInTheDocument()
    expect(screen.getByTestId('sheet-tab-Revenue')).toBeInTheDocument()
    expect(screen.getByTestId('sheet-tab-Costs')).toBeInTheDocument()
    expect(screen.getByText('Month')).toBeInTheDocument()
    // Switch to Costs tab
    fireEvent.click(screen.getByTestId('sheet-tab-Costs'))
    expect(screen.getByText('Item')).toBeInTheDocument()
    expect(screen.getByText('Price')).toBeInTheDocument()
  })

  it('renders Drive slides carousel with prev/next navigation', () => {
    render(<QuickLook {...driveBase} driveData={makeDriveData({
      kind: 'slides',
      name: 'Deck',
      mime_type: 'application/vnd.google-apps.presentation',
      sample: {
        slides: [
          { slide_id: 'p1', thumbnail_url: 'https://img/slide1' },
          { slide_id: 'p2', thumbnail_url: 'https://img/slide2' },
        ],
        truncated: false,
      },
    })} />)
    expect(screen.getByTestId('quicklook-slides')).toBeInTheDocument()
    expect(screen.getByText('Slide 1 of 2')).toBeInTheDocument()
    const container = screen.getByTestId('quicklook-slides')
    const imgs = container.querySelectorAll('img')
    expect(imgs.length).toBe(1)
    expect((imgs[0] as HTMLImageElement).src).toContain('slide1')
    // Navigate to slide 2
    fireEvent.click(screen.getByTestId('slide-next'))
    expect(screen.getByText('Slide 2 of 2')).toBeInTheDocument()
    expect((screen.getByTestId('quicklook-slides').querySelectorAll('img')[0] as HTMLImageElement).src).toContain('slide2')
  })

  it('renders Drive doc HTML content', () => {
    render(<QuickLook {...driveBase} driveData={makeDriveData({
      kind: 'doc',
      name: 'Plan',
      mime_type: 'application/vnd.google-apps.document',
      sample: {
        html: '<h1>Project Plan</h1><p>First paragraph.</p>',
        truncated: false,
      },
    })} />)
    expect(screen.getByTestId('quicklook-doc')).toBeInTheDocument()
    expect(screen.getByText('First paragraph.')).toBeInTheDocument()
  })

  it('renders full Drive doc HTML without a truncation notice', () => {
    const lastParagraph = 'This is the last paragraph of a very long document.'
    render(<QuickLook {...driveBase} driveData={makeDriveData({
      kind: 'doc',
      name: 'Long Doc',
      mime_type: 'application/vnd.google-apps.document',
      sample: {
        html: `<p>Opening sentence.</p><p>${lastParagraph}</p>`,
        truncated: false,
      },
    })} />)
    expect(screen.getByText('Opening sentence.')).toBeInTheDocument()
    expect(screen.getByText(lastParagraph)).toBeInTheDocument()
    expect(screen.queryByText(/showing the first part/i)).not.toBeInTheDocument()
  })

  it('renders full Drive doc blocks without a truncation notice', () => {
    render(<QuickLook {...driveBase} driveData={makeDriveData({
      kind: 'doc',
      name: 'Long Doc',
      mime_type: 'application/vnd.google-apps.document',
      sample: {
        blocks: [
          { type: 'heading', text: 'Introduction' },
          { type: 'paragraph', text: 'This is the very last paragraph.' },
        ],
        truncated: false,
      },
    })} />)
    expect(screen.getByText('Introduction')).toBeInTheDocument()
    expect(screen.getByText('This is the very last paragraph.')).toBeInTheDocument()
    expect(screen.queryByText(/showing the first part/i)).not.toBeInTheDocument()
  })

  it('renders Drive doc blocks when html is absent', () => {
    render(<QuickLook {...driveBase} driveData={makeDriveData({
      kind: 'doc',
      name: 'Plan',
      mime_type: 'application/vnd.google-apps.document',
      sample: {
        blocks: [
          { type: 'heading', text: 'Section Title' },
          { type: 'paragraph', text: 'Body text here.' },
        ],
        truncated: false,
      },
    })} />)
    expect(screen.getByTestId('quicklook-doc')).toBeInTheDocument()
    expect(screen.getByText('Section Title')).toBeInTheDocument()
    expect(screen.getByText('Body text here.')).toBeInTheDocument()
  })

  it('shows Save to yourOS button in drive mode', () => {
    render(<QuickLook {...driveBase} driveData={makeDriveData()} />)
    expect(screen.getByTestId('drive-preview-save-to-myos')).toBeInTheDocument()
    expect(screen.getByText(/save to myos/i)).toBeInTheDocument()
  })

  it('shows Saved after successful import', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ ok: true, path: '/path/to/file' }),
    }))
    render(<QuickLook {...driveBase} driveData={makeDriveData()} />)
    fireEvent.click(screen.getByTestId('drive-preview-save-to-myos'))
    await waitFor(() => {
      const fetchMock = vi.mocked(global.fetch)
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/files/import-from-drive',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ drive_file_id: 'test-file-id' }),
        }),
      )
    })
    await waitFor(() => {
      expect(screen.getByText(/saved/i)).toBeInTheDocument()
    })
  })

  it('shows error message after failed import', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({ detail: 'Drive download failed.' }),
    }))
    render(<QuickLook {...driveBase} driveData={makeDriveData()} />)
    fireEvent.click(screen.getByTestId('drive-preview-save-to-myos'))
    await waitFor(() => {
      expect(screen.getByText('Drive download failed.')).toBeInTheDocument()
    })
  })

  it('shows Open in Drive link with web_view_link', () => {
    render(<QuickLook {...driveBase} driveData={makeDriveData({
      web_view_link: 'https://drive.google.com/file/d/test-file-id',
    })} />)
    const link = screen.getByRole('link', { name: /open in drive/i })
    expect((link as HTMLAnchorElement).href).toBe('https://drive.google.com/file/d/test-file-id')
  })

  it('does not show Open in app button in drive mode', () => {
    render(<QuickLook {...driveBase} driveData={makeDriveData()} />)
    expect(screen.queryByTestId('quicklook-open-native')).not.toBeInTheDocument()
  })
})
