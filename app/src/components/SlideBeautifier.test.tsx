import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import SlideBeautifier from './SlideBeautifier'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

import { api } from '../lib/api'

const mockedPost = vi.mocked(api.post)

const fakeResponse = {
  name: 'deck.pptx',
  slide_count: 2,
  cached: false,
  elapsed_seconds: 1.2,
  originals: [
    {
      index: 1,
      title: 'Raw title one',
      bullets: ['raw one', 'raw two'],
      body: '',
      image_count: 0,
      notes: '',
    },
    {
      index: 2,
      title: 'Raw title two',
      bullets: ['alpha', 'beta'],
      body: '',
      image_count: 0,
      notes: '',
    },
  ],
  beautified: [
    {
      index: 1,
      layout: 'title_and_content' as const,
      title: 'Polished title one',
      body: ['Clear first point', 'Clear second point'],
      accent_color: '#22c55e',
      font_pairing: { heading: 'Inter', body: 'Source Sans' },
      notes: '',
      rationale: 'Tighter wording and a friendlier accent.',
    },
    {
      index: 2,
      layout: 'quote' as const,
      title: 'Quote slide',
      body: ['An inspiring line.', 'Someone wise'],
      accent_color: '#3b82f6',
      font_pairing: { heading: 'Playfair', body: 'Source Sans' },
      notes: '',
      rationale: 'Turned the bullets into a single quote.',
    },
  ],
}

describe('SlideBeautifier', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the Make it pretty button', () => {
    render(<SlideBeautifier filePath="deck.pptx" fileName="deck.pptx" />)
    expect(screen.getByRole('button', { name: /make it pretty/i })).toBeInTheDocument()
  })

  it('shows a loading message while polishing', async () => {
    mockedPost.mockReturnValue(new Promise(() => {}))
    render(<SlideBeautifier filePath="deck.pptx" />)

    fireEvent.click(screen.getByRole('button', { name: /make it pretty/i }))

    expect(
      screen.getByText(/polishing your slides\. this takes a few seconds/i),
    ).toBeInTheDocument()
  })

  it('renders before and after slides after a successful call', async () => {
    mockedPost.mockResolvedValue(fakeResponse)
    render(<SlideBeautifier filePath="deck.pptx" />)

    fireEvent.click(screen.getByRole('button', { name: /make it pretty/i }))

    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith('/files/beautify-deck', {
        path: 'deck.pptx',
      })
    })

    await waitFor(() => {
      expect(screen.getByText('Polished title one')).toBeInTheDocument()
    })

    expect(screen.getByText('Raw title one')).toBeInTheDocument()
    expect(screen.getByText('Polished title one')).toBeInTheDocument()
    expect(screen.getByText('Clear first point')).toBeInTheDocument()
    expect(screen.getByText('Clear second point')).toBeInTheDocument()
    expect(
      screen.getByText(/tighter wording and a friendlier accent/i),
    ).toBeInTheDocument()

    // Headers confirm the side by side layout.
    expect(screen.getAllByText(/^before$/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/^after$/i).length).toBeGreaterThan(0)
  })

  it('shows a plain language error when the call fails', async () => {
    mockedPost.mockRejectedValue(
      new Error(
        JSON.stringify({
          detail: 'This only works on PowerPoint files that end in .pptx.',
        }),
      ),
    )
    render(<SlideBeautifier filePath="notes.md" />)

    fireEvent.click(screen.getByRole('button', { name: /make it pretty/i }))

    await waitFor(() => {
      expect(
        screen.getByText(/only works on powerpoint files/i),
      ).toBeInTheDocument()
    })
    expect(screen.getByRole('alert')).toBeInTheDocument()
  })

  it('the "Use these for the whole deck" button switches every slide to polished at once', async () => {
    mockedPost.mockResolvedValue(fakeResponse)
    render(<SlideBeautifier filePath="deck.pptx" />)

    fireEvent.click(screen.getByRole('button', { name: /make it pretty/i }))

    await waitFor(() => {
      expect(screen.getByText('Polished title one')).toBeInTheDocument()
    })

    // First, flip both slides back to original using the per slide controls.
    const keepOriginalButtons = screen.getAllByRole('button', {
      name: /keep original/i,
    })
    keepOriginalButtons.forEach((b) => fireEvent.click(b))

    // The "Use polished" buttons should now be in the inactive state. Click
    // the master button and confirm the polished titles are still rendered.
    const master = screen.getByRole('button', {
      name: /use these for the whole deck/i,
    })
    fireEvent.click(master)

    // Polished content is present regardless of toggle state because both
    // versions render in the grid. We verify the toggle state flipped
    // by checking the polished buttons now carry the active blue class.
    const polishedButtons = screen.getAllByRole('button', { name: /use polished/i })
    polishedButtons.forEach((btn) => {
      expect(btn.className).toContain('bg-blue-600')
    })
  })

  it('the "Keep my originals" button switches every slide back to original', async () => {
    mockedPost.mockResolvedValue(fakeResponse)
    render(<SlideBeautifier filePath="deck.pptx" />)
    fireEvent.click(screen.getByRole('button', { name: /make it pretty/i }))
    await waitFor(() => {
      expect(screen.getByText('Polished title one')).toBeInTheDocument()
    })

    const keepAll = screen.getByRole('button', {
      name: /keep my originals/i,
    })
    fireEvent.click(keepAll)

    // After clicking, every per slide "Keep original" toggle should carry
    // the active slate-700 class. This is the only way to observe the
    // state flip without duplicating the button wiring.
    const keepOriginalButtons = screen.getAllByRole('button', {
      name: /keep original/i,
    })
    keepOriginalButtons.forEach((btn) => {
      expect(btn.className).toContain('bg-slate-700')
    })
  })

  it('shows a friendly cache hint when the response is cached', async () => {
    mockedPost.mockResolvedValue({ ...fakeResponse, cached: true })
    render(<SlideBeautifier filePath="deck.pptx" />)
    fireEvent.click(screen.getByRole('button', { name: /make it pretty/i }))
    await waitFor(() => {
      expect(screen.getByText(/shown from cache/i)).toBeInTheDocument()
    })
  })

  it('renders every supported layout without crashing', async () => {
    // One slide per layout, mixed in a single response. This gives the
    // layout switch coverage for title, section_header, image_left,
    // image_right, two_column, comparison, and the default branch.
    const mixedResponse = {
      name: 'mixed.pptx',
      slide_count: 6,
      cached: false,
      elapsed_seconds: 2.1,
      originals: Array.from({ length: 6 }, (_, i) => ({
        index: i + 1,
        title: `Original ${i + 1}`,
        bullets: [],
        body: '',
        image_count: 0,
        notes: '',
      })),
      beautified: [
        {
          index: 1,
          layout: 'title' as const,
          title: 'A big title',
          body: [],
          accent_color: '#123456',
          font_pairing: { heading: 'Inter', body: 'Source Sans' },
          notes: '',
          rationale: 'Big and bold.',
        },
        {
          index: 2,
          layout: 'section_header' as const,
          title: 'Act Two',
          body: [],
          accent_color: '#123456',
          font_pairing: { heading: 'Inter', body: 'Source Sans' },
          notes: '',
          rationale: 'Section break.',
        },
        {
          index: 3,
          layout: 'image_left' as const,
          title: 'Left image',
          body: ['point one'],
          accent_color: '#123456',
          font_pairing: { heading: 'Inter', body: 'Source Sans' },
          notes: '',
          rationale: 'Image on the left.',
        },
        {
          index: 4,
          layout: 'image_right' as const,
          title: 'Right image',
          body: ['point two'],
          accent_color: '#123456',
          font_pairing: { heading: 'Inter', body: 'Source Sans' },
          notes: '',
          rationale: 'Image on the right.',
        },
        {
          index: 5,
          layout: 'two_column' as const,
          title: 'Columns',
          body: { left: ['L1', 'L2'], right: ['R1', 'R2'] },
          accent_color: '#123456',
          font_pairing: { heading: 'Inter', body: 'Source Sans' },
          notes: '',
          rationale: 'Side by side.',
        },
        {
          index: 6,
          layout: 'comparison' as const,
          title: 'Compare',
          body: ['A1', 'A2', 'B1', 'B2'],
          accent_color: '#123456',
          font_pairing: { heading: 'Inter', body: 'Source Sans' },
          notes: '',
          rationale: 'Compared.',
        },
      ],
    }

    mockedPost.mockResolvedValue(mixedResponse)
    render(<SlideBeautifier filePath="mixed.pptx" />)
    fireEvent.click(screen.getByRole('button', { name: /make it pretty/i }))

    await waitFor(() => {
      expect(screen.getByText('A big title')).toBeInTheDocument()
    })

    // Every layout renders its test id so we can prove the branches ran.
    expect(screen.getByTestId('beautified-title')).toBeInTheDocument()
    expect(screen.getByTestId('beautified-section')).toBeInTheDocument()
    // image_left and image_right share the same test id so assert two.
    expect(screen.getAllByTestId('beautified-image').length).toBe(2)
    expect(screen.getAllByTestId('beautified-twocol').length).toBe(2)
    expect(screen.getByText('L1')).toBeInTheDocument()
    expect(screen.getByText('R1')).toBeInTheDocument()
  })

  it('shows a fallback error when the failure message is not JSON', async () => {
    mockedPost.mockRejectedValue(new Error('plain text failure'))
    render(<SlideBeautifier filePath="deck.pptx" />)
    fireEvent.click(screen.getByRole('button', { name: /make it pretty/i }))

    await waitFor(() => {
      expect(screen.getByText('plain text failure')).toBeInTheDocument()
    })
  })
})
