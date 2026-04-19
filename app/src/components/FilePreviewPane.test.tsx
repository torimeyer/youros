import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import FilePreviewPane, {
  categorizeFile,
  renderMarkdown,
  parseRoadmap,
  isRoadmapDoc,
  canonicalizeSpecStatus,
} from './FilePreviewPane'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

vi.mock('../lib/sidebarBus', () => ({
  bumpSpecs: vi.fn(),
}))

import { api } from '../lib/api'
import { bumpSpecs } from '../lib/sidebarBus'

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

describe('Roadmap parsing', () => {
  it('detects roadmap docs by kind: roadmap front matter', () => {
    const yes = '---\nkind: roadmap\n---\n\n# Roadmap\n\n[]'
    const no = '---\nkind: fleet-output\n---\n\n# Other\n\n'
    expect(isRoadmapDoc(yes)).toBe(true)
    expect(isRoadmapDoc(no)).toBe(false)
    expect(isRoadmapDoc('no front matter at all')).toBe(false)
  })

  it('parses a JSON quarter array into quarters with initiatives', () => {
    const json = [
      {
        quarter: 'Q1 2026',
        theme: 'Launch',
        initiatives: ['Ship onboarding', 'Publish templates', 'Add chat'],
      },
      {
        quarter: 'Q2 2026',
        theme: 'Polish',
        initiatives: ['Fix bugs', 'Improve perf'],
      },
    ]
    const content = `---\nkind: roadmap\n---\n\n# Roadmap\n\n${JSON.stringify(json)}`
    const parsed = parseRoadmap(content)
    expect(parsed.quarters).toHaveLength(2)
    expect(parsed.quarters[0].quarter).toBe('Q1 2026')
    expect(parsed.quarters[0].initiatives).toEqual([
      'Ship onboarding',
      'Publish templates',
      'Add chat',
    ])
  })

  it('falls back to bullet lines when JSON is absent', () => {
    const content =
      '---\nkind: roadmap\n---\n\n# Roadmap\n\n- Ship the onboarding flow\n- Polish the files page\n* tbd\n'
    const parsed = parseRoadmap(content)
    expect(parsed.quarters).toHaveLength(0)
    expect(parsed.bullets).toContain('Ship the onboarding flow')
    expect(parsed.bullets).toContain('Polish the files page')
    // "tbd" is a single word, excluded by the 3-word heuristic.
    expect(parsed.bullets).not.toContain('tbd')
  })
})

describe('Roadmap renderer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders a plus button per initiative and posts to from-roadmap-line on click', async () => {
    const content =
      '---\nkind: roadmap\n---\n\n# Roadmap\n\n' +
      JSON.stringify([
        {
          quarter: 'Q1 2026',
          theme: 'Launch',
          initiatives: ['Ship guided onboarding', 'Publish five templates'],
        },
      ])

    mockedApiGet.mockResolvedValue({
      content,
      type: 'text',
      size: content.length,
    })

    const mockedApiPost = vi.mocked(api.post)
    mockedApiPost.mockResolvedValue({
      title: 'Ship guided onboarding',
      promoted_path: 'docs/spec/ship-guided-onboarding.md',
      status: 'ready',
    })

    render(
      <FilePreviewPane
        entry={{ name: 'roadmap.md', path: '/tmp/roadmap.md', size_display: '1 KB' }}
        onClose={() => {}}
      />
    )

    await waitFor(() => {
      expect(screen.getByTestId('roadmap-preview')).toBeInTheDocument()
    })

    const buttons = screen.getAllByTestId('roadmap-make-spec-button')
    expect(buttons.length).toBe(2)
    expect(buttons[0].textContent).toContain('Make spec')

    fireEvent.click(buttons[0])

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith(
        '/specs/from-roadmap-line',
        {
          roadmap_path: '/tmp/roadmap.md',
          initiative_text: 'Ship guided onboarding',
        }
      )
    })

    await waitFor(() => {
      expect(screen.getByTestId('roadmap-spec-toast')).toBeInTheDocument()
    })
    expect(screen.getByTestId('roadmap-spec-toast').textContent).toContain(
      'Ship guided onboarding'
    )
  })

  it('renders an inline spec-status pill next to the bullet after + Make spec (draft text + View spec link)', async () => {
    // The new UX answers "what is the spec doing now?" inline, without
    // forcing the user to navigate away. The pill shows the returned
    // status and a View spec link while the old toast still fires as a
    // transient confirmation.
    const content =
      '---\nkind: roadmap\n---\n\n# Roadmap\n\n' +
      JSON.stringify([
        {
          quarter: 'Q1 2026',
          theme: 'Launch',
          initiatives: ['Ship guided onboarding'],
        },
      ])

    mockedApiGet.mockImplementation(async (path: string) => {
      if (typeof path === 'string' && path.startsWith('/specs')) {
        return { docs: [] }
      }
      return {
        content,
        type: 'text',
        size: content.length,
      }
    })

    const mockedApiPost = vi.mocked(api.post)
    mockedApiPost.mockResolvedValue({
      title: 'Ship guided onboarding',
      promoted_path: 'docs/spec/ship-guided-onboarding.md',
      status: 'draft',
    })

    render(
      <FilePreviewPane
        entry={{ name: 'roadmap.md', path: '/tmp/roadmap.md', size_display: '1 KB' }}
        onClose={() => {}}
      />
    )

    await waitFor(() => {
      expect(screen.getByTestId('roadmap-preview')).toBeInTheDocument()
    })

    const button = screen.getByTestId('roadmap-make-spec-button')
    fireEvent.click(button)

    // Inline pill replaces the button with the status string.
    await waitFor(() => {
      expect(screen.getByTestId('roadmap-spec-status-pill')).toBeInTheDocument()
    })
    const pill = screen.getByTestId('roadmap-spec-status-pill')
    expect(pill.getAttribute('data-status')).toBe('draft')
    expect(pill.textContent).toContain('Spec: draft')

    // Deep-link to the spec detail remains reachable.
    expect(screen.getByTestId('roadmap-spec-view-link')).toBeInTheDocument()

    // The + Make spec button for that bullet is now gone (replaced by
    // the pill). No duplicate promote paths.
    expect(screen.queryByTestId('roadmap-make-spec-button')).not.toBeInTheDocument()
  })

  it('handleMakeSpec fires bumpSpecs on successful POST', async () => {
    // Regression: the Specs page listens on the specs bus so it can
    // refetch without waiting on a page refresh. If handleMakeSpec ever
    // stops bumping the bus, the new plan will silently not appear on
    // that page.
    const content =
      '---\nkind: roadmap\n---\n\n# Roadmap\n\n' +
      JSON.stringify([
        {
          quarter: 'Q1 2026',
          theme: 'Launch',
          initiatives: ['Ship guided onboarding'],
        },
      ])

    mockedApiGet.mockImplementation(async (path: string) => {
      if (typeof path === 'string' && path.startsWith('/specs')) {
        return { docs: [] }
      }
      return { content, type: 'text', size: content.length }
    })

    const mockedApiPost = vi.mocked(api.post)
    mockedApiPost.mockResolvedValue({
      title: 'Ship guided onboarding',
      promoted_path: 'docs/spec/ship-guided-onboarding.md',
      status: 'ready',
    })

    const bumpSpy = vi.mocked(bumpSpecs)
    bumpSpy.mockClear()

    render(
      <FilePreviewPane
        entry={{ name: 'roadmap.md', path: '/tmp/roadmap.md', size_display: '1 KB' }}
        onClose={() => {}}
      />
    )

    await waitFor(() => {
      expect(screen.getByTestId('roadmap-preview')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('roadmap-make-spec-button'))

    await waitFor(() => {
      expect(bumpSpy).toHaveBeenCalledTimes(1)
    })
  })
})

describe('Spec status dedupe', () => {
  // Historical note: the backend has carried two aliases for the same
  // state ("complete" vs "done", "in-progress" vs "building"). When two
  // chips sit next to each other on a roadmap preview, they must read
  // as the same single state, never as visually identical but internally
  // distinct "statuses". canonicalizeSpecStatus is the single collapse
  // point that everything downstream relies on, so it gets a direct unit
  // test plus a render-level assertion covering the chip pipeline end-to-end.

  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('canonicalizeSpecStatus collapses done to complete and building to in-progress', () => {
    expect(canonicalizeSpecStatus('done')).toBe('complete')
    expect(canonicalizeSpecStatus('complete')).toBe('complete')
    expect(canonicalizeSpecStatus('building')).toBe('in-progress')
    expect(canonicalizeSpecStatus('in-progress')).toBe('in-progress')
    // Unknown and passthrough states are left alone so debugging still works.
    expect(canonicalizeSpecStatus('draft')).toBe('draft')
    expect(canonicalizeSpecStatus('ready')).toBe('ready')
    expect(canonicalizeSpecStatus('failed')).toBe('failed')
    expect(canonicalizeSpecStatus('mystery')).toBe('mystery')
  })

  it('renders one Spec: done label for a chip whose backend status is "done"', async () => {
    // The poll returns the raw backend status. The chip must render the
    // canonical label "Spec: done" exactly once and expose the canonical
    // data-status "complete" so upstream code that filters or counts by
    // data-status sees one bucket, not two.
    const content =
      '---\nkind: roadmap\n---\n\n# Roadmap\n\n' +
      JSON.stringify([
        {
          quarter: 'Q1 2026',
          theme: 'Launch',
          initiatives: ['Ship guided onboarding'],
        },
      ])

    // Pre-load the poll response with the legacy "done" alias. The poll
    // effect fires an immediate tick after the promote click, so if the
    // data is already in place the chip transitions from "ready" to the
    // canonical "complete" rendering on the first tick.
    const specsResponse = {
      docs: [
        { path: 'docs/spec/ship-guided-onboarding.md', status: 'done' },
      ] as Array<{ path: string; status: string }>,
    }
    mockedApiGet.mockImplementation(async (path: string) => {
      if (typeof path === 'string' && path.startsWith('/specs')) {
        return specsResponse
      }
      return { content, type: 'text', size: content.length }
    })

    const mockedApiPost = vi.mocked(api.post)
    mockedApiPost.mockResolvedValue({
      title: 'Ship guided onboarding',
      promoted_path: 'docs/spec/ship-guided-onboarding.md',
      status: 'ready',
    })

    render(
      <FilePreviewPane
        entry={{ name: 'roadmap.md', path: '/tmp/roadmap.md', size_display: '1 KB' }}
        onClose={() => {}}
      />
    )

    await waitFor(() => {
      expect(screen.getByTestId('roadmap-preview')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('roadmap-make-spec-button'))

    await waitFor(() => {
      const pill = screen.getByTestId('roadmap-spec-status-pill')
      expect(pill.textContent).toContain('Spec: done')
      expect(pill.getAttribute('data-status')).toBe('complete')
    })

    // Only one pill, only one label. If a stale raw status leaked through
    // we would see two pill nodes (one for complete, one for done) with
    // the same visible text.
    const allPills = screen.getAllByTestId('roadmap-spec-status-pill')
    expect(allPills).toHaveLength(1)
    const doneLabels = allPills.filter((p) =>
      p.textContent?.includes('Spec: done')
    )
    expect(doneLabels).toHaveLength(1)
  })

  it('collapses complete and done to one bucket when two chips render together', async () => {
    // Two bullets, two promoted specs. Backend returns one as "complete"
    // and the other as "done". Both chips must render the same label
    // "Spec: done" and share the same canonical data-status so callers
    // cannot double-count the terminal state.
    const content =
      '---\nkind: roadmap\n---\n\n# Roadmap\n\n' +
      JSON.stringify([
        {
          quarter: 'Q1 2026',
          theme: 'Launch',
          initiatives: [
            'Ship guided onboarding alpha',
            'Ship guided onboarding beta',
          ],
        },
      ])

    // Pre-load the poll response with one "complete" row and one "done"
    // row so each chip's first post-promote tick transitions to its
    // terminal raw status. The dedupe layer then collapses both to the
    // same canonical rendering.
    const specsResponse = {
      docs: [
        { path: 'docs/spec/ship-guided-onboarding-alpha.md', status: 'complete' },
        { path: 'docs/spec/ship-guided-onboarding-beta.md', status: 'done' },
      ] as Array<{ path: string; status: string }>,
    }
    mockedApiGet.mockImplementation(async (path: string) => {
      if (typeof path === 'string' && path.startsWith('/specs')) {
        return specsResponse
      }
      return { content, type: 'text', size: content.length }
    })

    const mockedApiPost = vi.mocked(api.post)
    mockedApiPost.mockImplementation(async (_path, body: unknown) => {
      const payload = body as { initiative_text: string }
      const slug = payload.initiative_text.toLowerCase().replace(/\s+/g, '-')
      return {
        title: payload.initiative_text,
        promoted_path: `docs/spec/${slug}.md`,
        status: 'ready',
      }
    })

    render(
      <FilePreviewPane
        entry={{ name: 'roadmap.md', path: '/tmp/roadmap.md', size_display: '1 KB' }}
        onClose={() => {}}
      />
    )

    await waitFor(() => {
      expect(screen.getByTestId('roadmap-preview')).toBeInTheDocument()
    })

    const buttons = screen.getAllByTestId('roadmap-make-spec-button')
    expect(buttons).toHaveLength(2)
    fireEvent.click(buttons[0])
    await waitFor(() => {
      expect(screen.getAllByTestId('roadmap-spec-status-pill')).toHaveLength(1)
    })
    fireEvent.click(screen.getByTestId('roadmap-make-spec-button'))
    await waitFor(() => {
      expect(screen.getAllByTestId('roadmap-spec-status-pill')).toHaveLength(2)
    })

    // The poll response (pre-loaded above) marks one spec "complete" and
    // the other "done". Both chips must converge on the same canonical
    // rendering.
    await waitFor(() => {
      const pills = screen.getAllByTestId('roadmap-spec-status-pill')
      expect(pills).toHaveLength(2)
      for (const pill of pills) {
        expect(pill.textContent).toContain('Spec: done')
        expect(pill.getAttribute('data-status')).toBe('complete')
      }
    })

    // Dedupe check: after canonicalization, every terminal pill shares a
    // single data-status value. If the UI were leaking raw statuses, we
    // would see a "done" bucket next to a "complete" bucket.
    const pills = screen.getAllByTestId('roadmap-spec-status-pill')
    const uniqueStatuses = new Set(
      pills.map((p) => p.getAttribute('data-status'))
    )
    expect(uniqueStatuses.size).toBe(1)
    expect(uniqueStatuses.has('complete')).toBe(true)
  })
})
