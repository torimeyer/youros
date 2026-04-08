import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import FilePreviewPane, { categorizeFile, renderMarkdown } from './FilePreviewPane'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)

function fileEntry(name: string, path: string = name) {
  return { name, path, size_display: '1.0 KB' }
}

describe('categorizeFile', () => {
  it('recognizes markdown', () => {
    expect(categorizeFile('README.md')).toBe('markdown')
    expect(categorizeFile('notes.markdown')).toBe('markdown')
  })
  it('recognizes code', () => {
    expect(categorizeFile('index.ts')).toBe('code')
    expect(categorizeFile('app.py')).toBe('code')
    expect(categorizeFile('style.css')).toBe('code')
  })
  it('recognizes text', () => {
    expect(categorizeFile('notes.txt')).toBe('text')
    expect(categorizeFile('server.log')).toBe('text')
  })
  it('recognizes images', () => {
    expect(categorizeFile('photo.png')).toBe('image')
    expect(categorizeFile('icon.svg')).toBe('image')
  })
  it('recognizes spreadsheets', () => {
    expect(categorizeFile('data.xlsx')).toBe('spreadsheet')
    expect(categorizeFile('export.csv')).toBe('spreadsheet')
  })
  it('recognizes documents', () => {
    expect(categorizeFile('letter.docx')).toBe('document')
  })
  it('recognizes slides', () => {
    expect(categorizeFile('deck.pptx')).toBe('slides')
  })
  it('recognizes pdfs', () => {
    expect(categorizeFile('paper.pdf')).toBe('pdf')
  })
  it('falls back to unknown', () => {
    expect(categorizeFile('mystery.xyz')).toBe('unknown')
    expect(categorizeFile('noext')).toBe('unknown')
  })
})

describe('renderMarkdown', () => {
  it('renders headings, bold, and code fences', () => {
    const tree = renderMarkdown('# Hello\n\nThis is **bold** text.\n\n```js\nconsole.log(1)\n```')
    // Render into a dummy container
    render(<div>{tree}</div>)
    expect(screen.getByText('Hello')).toBeInTheDocument()
    expect(screen.getByText('bold')).toBeInTheDocument()
    expect(screen.getByText('console.log(1)')).toBeInTheDocument()
  })
})

describe('FilePreviewPane', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('returns null when entry is null', () => {
    const { container } = render(
      <FilePreviewPane entry={null} onClose={() => {}} />
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders markdown content', async () => {
    mockedApiGet.mockResolvedValue({
      content: '# Title\n\nSome **bold** text.',
      type: 'text',
      size: 30,
    })

    render(
      <FilePreviewPane
        entry={fileEntry('README.md')}
        onClose={() => {}}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('Title')).toBeInTheDocument()
    })
    expect(screen.getByText('bold')).toBeInTheDocument()
    expect(mockedApiGet).toHaveBeenCalledWith('/files/read?path=README.md')
  })

  it('renders a code file with the code endpoint', async () => {
    mockedApiGet.mockResolvedValue({
      content: 'const x = 1',
      type: 'text',
      size: 11,
    })

    render(
      <FilePreviewPane
        entry={fileEntry('index.ts')}
        onClose={() => {}}
      />
    )

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/files/read?path=index.ts')
    })
    // The keyword "const" should appear (code highlighter wraps it in a span)
    expect(screen.getByText('const')).toBeInTheDocument()
  })

  it('renders a CSV as a sortable table', async () => {
    mockedApiGet.mockResolvedValue({
      kind: 'spreadsheet',
      name: 'data.csv',
      size: 100,
      sheets: [
        {
          name: 'data',
          rows: [
            ['name', 'age'],
            ['Alice', 30],
            ['Bob', 25],
          ],
          total_rows: 3,
          truncated: false,
        },
      ],
    })

    render(
      <FilePreviewPane
        entry={fileEntry('data.csv')}
        onClose={() => {}}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeInTheDocument()
    })
    expect(screen.getByText('Bob')).toBeInTheDocument()
    expect(mockedApiGet).toHaveBeenCalledWith('/files/preview?path=data.csv')
  })

  it('renders an xlsx with multiple sheet tabs', async () => {
    mockedApiGet.mockResolvedValue({
      kind: 'spreadsheet',
      name: 'workbook.xlsx',
      size: 500,
      sheets: [
        {
          name: 'Sheet1',
          rows: [['header'], ['a'], ['b']],
          total_rows: 3,
          truncated: false,
        },
        {
          name: 'Sheet2',
          rows: [['other'], ['c']],
          total_rows: 2,
          truncated: false,
        },
      ],
    })

    render(
      <FilePreviewPane
        entry={fileEntry('workbook.xlsx')}
        onClose={() => {}}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('Sheet1')).toBeInTheDocument()
    })
    expect(screen.getByText('Sheet2')).toBeInTheDocument()
    // Clicking Sheet2 should show its rows
    fireEvent.click(screen.getByText('Sheet2'))
    await waitFor(() => {
      expect(screen.getByText('c')).toBeInTheDocument()
    })
  })

  it('renders an image file', async () => {
    mockedApiGet.mockResolvedValue({
      content: 'data:image/png;base64,iVBORw0KGgo',
      type: 'image',
      size: 100,
      mime: 'image/png',
    })

    render(
      <FilePreviewPane
        entry={fileEntry('pic.png')}
        onClose={() => {}}
      />
    )

    await waitFor(() => {
      const img = screen.getByAltText('pic.png') as HTMLImageElement
      expect(img).toBeInTheDocument()
      expect(img.src).toContain('data:image/png;base64')
    })
  })

  it('renders a docx as HTML', async () => {
    mockedApiGet.mockResolvedValue({
      kind: 'docx',
      name: 'letter.docx',
      size: 500,
      html: '<h1>Hello</h1><p>World</p>',
    })

    render(
      <FilePreviewPane
        entry={fileEntry('letter.docx')}
        onClose={() => {}}
      />
    )

    await waitFor(() => {
      expect(screen.getByTestId('docx-html')).toBeInTheDocument()
    })
    expect(screen.getByTestId('docx-html').innerHTML).toContain('Hello')
  })

  it('renders pptx slides as cards', async () => {
    mockedApiGet.mockResolvedValue({
      kind: 'slides',
      name: 'deck.pptx',
      size: 500,
      slides: [
        { index: 1, title: 'Welcome', body: 'Intro body text' },
        { index: 2, title: 'Agenda', body: 'Item one' },
      ],
    })

    render(
      <FilePreviewPane
        entry={fileEntry('deck.pptx')}
        onClose={() => {}}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('Welcome')).toBeInTheDocument()
    })
    expect(screen.getByText('Agenda')).toBeInTheDocument()
    expect(screen.getByText('Slide 1')).toBeInTheDocument()
    expect(screen.getByText('Slide 2')).toBeInTheDocument()
  })

  it('renders a pdf with page text', async () => {
    mockedApiGet.mockResolvedValue({
      kind: 'pdf',
      name: 'doc.pdf',
      size: 1000,
      total_pages: 2,
      truncated: false,
      pages: [
        { index: 1, text: 'First page content' },
        { index: 2, text: 'Second page content' },
      ],
    })

    render(
      <FilePreviewPane
        entry={fileEntry('doc.pdf')}
        onClose={() => {}}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('First page content')).toBeInTheDocument()
    })
    expect(screen.getByText('Second page content')).toBeInTheDocument()
    expect(screen.getByText('Page 1')).toBeInTheDocument()
  })

  it('shows the unsupported fallback for unknown file types', async () => {
    mockedApiGet.mockResolvedValue({
      content: null,
      type: 'binary',
      size: 100,
    })

    render(
      <FilePreviewPane
        entry={fileEntry('mystery.xyz')}
        onClose={() => {}}
        onOpenExternally={() => {}}
      />
    )

    await waitFor(() => {
      expect(
        screen.getByText('Preview not available for this file type.')
      ).toBeInTheDocument()
    })
    expect(screen.getByText('Open in system app')).toBeInTheDocument()
  })

  it('closes via the X button', async () => {
    mockedApiGet.mockResolvedValue({
      content: 'hi',
      type: 'text',
      size: 2,
    })
    const onClose = vi.fn()
    render(
      <FilePreviewPane
        entry={fileEntry('readme.md')}
        onClose={onClose}
      />
    )

    await waitFor(() => {
      expect(screen.getByTitle('Close preview')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTitle('Close preview'))
    expect(onClose).toHaveBeenCalled()
  })

  it('closes via the Escape key', async () => {
    mockedApiGet.mockResolvedValue({
      content: 'hi',
      type: 'text',
      size: 2,
    })
    const onClose = vi.fn()
    render(
      <FilePreviewPane
        entry={fileEntry('readme.md')}
        onClose={onClose}
      />
    )

    await waitFor(() => {
      expect(screen.getByTitle('Close preview')).toBeInTheDocument()
    })
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })

  it('shows error state when the fetch fails and allows retry', async () => {
    mockedApiGet.mockRejectedValueOnce(new Error('boom'))
    mockedApiGet.mockResolvedValueOnce({
      content: 'hello',
      type: 'text',
      size: 5,
    })

    render(
      <FilePreviewPane
        entry={fileEntry('readme.md')}
        onClose={() => {}}
      />
    )

    await waitFor(() => {
      expect(screen.getByText("Couldn't open this file.")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Try again'))

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledTimes(2)
    })
  })

  it('closes when clicking the backdrop overlay', async () => {
    mockedApiGet.mockResolvedValue({
      content: 'hi',
      type: 'text',
      size: 2,
    })
    const onClose = vi.fn()
    render(
      <FilePreviewPane
        entry={fileEntry('readme.md')}
        onClose={onClose}
      />
    )

    await waitFor(() => {
      expect(screen.getByTestId('file-preview-overlay')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('file-preview-overlay'))
    expect(onClose).toHaveBeenCalled()
  })
})
