import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Ideas from './Ideas'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

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

type IdeaItem = {
  straw: string
  staleness: 'stale' | 'very_stale' | null
  source: string | null
}

const item = (straw: string, overrides: Partial<IdeaItem> = {}): IdeaItem => ({
  straw,
  staleness: null,
  source: null,
  ...overrides,
})

const mockIdeasResponse = {
  clusters: [
    {
      name: 'Design',
      count: 2,
      items: [item('Redesign sidebar'), item('New color palette')],
    },
    {
      name: 'Performance',
      count: 1,
      items: [item('Cache API responses')],
    },
  ],
  unclustered: [item('Random thought about cats'), item('Learn Rust')],
}

const mockConvertedResponse = {
  converted: [],
}

const mockTemplatesResponse = {
  templates: [],
}

function setupDefaultMocks() {
  mockedApiGet.mockImplementation((path: string) => {
    if (path === '/ideas') return Promise.resolve(mockIdeasResponse)
    if (path === '/ideas?status=converted') return Promise.resolve(mockConvertedResponse)
    if (path === '/ideas/templates') return Promise.resolve(mockTemplatesResponse)
    return Promise.resolve({})
  })
}

function renderIdeas() {
  return render(
    <MemoryRouter>
      <Ideas />
    </MemoryRouter>
  )
}

describe('Ideas page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setupDefaultMocks()
    mockedApiPost.mockResolvedValue({})
    mockedApiDelete.mockResolvedValue({})
  })

  it('renders ideas from API data', async () => {
    renderIdeas()

    await waitFor(() => {
      // Cluster items appear in both the compilations summary and the ideas list.
      expect(screen.getAllByText('Redesign sidebar').length).toBeGreaterThanOrEqual(1)
    })

    expect(screen.getAllByText('New color palette').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Cache API responses').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('Random thought about cats')).toBeInTheDocument()
    expect(screen.getByText('Learn Rust')).toBeInTheDocument()
  })

  it('calls api.get with /ideas on mount', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/ideas')
    })
  })

  it('shows the page title', async () => {
    renderIdeas()

    const heading = screen.getByRole('heading', { name: 'Ideas' })
    expect(heading).toBeInTheDocument()
  })

  it('Save button calls POST /ideas with thought and template_id', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })

    const input = screen.getByPlaceholderText('Quick dump an idea (or just mention it in chat)')
    fireEvent.change(input, { target: { value: 'Build a rocket ship' } })

    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/ideas', {
        thought: 'Build a rocket ship',
        template_id: null,
      })
    })
  })

  it('Save clears input after successful submission', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })

    const input = screen.getByPlaceholderText(
      'Quick dump an idea (or just mention it in chat)'
    ) as HTMLInputElement
    fireEvent.change(input, { target: { value: 'My idea' } })
    expect(input.value).toBe('My idea')

    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(input.value).toBe('')
    })
  })

  it('Save does not call API with empty input', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(mockedApiPost).not.toHaveBeenCalled()
  })

  it('Enter key submits the input with template_id', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalled()
    })

    const input = screen.getByPlaceholderText('Quick dump an idea (or just mention it in chat)')
    fireEvent.change(input, { target: { value: 'Enter idea' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/ideas', {
        thought: 'Enter idea',
        template_id: null,
      })
    })
  })

  it('compile button calls POST /ideas/compile', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(screen.getAllByText('Redesign sidebar').length).toBeGreaterThanOrEqual(1)
    })

    fireEvent.click(screen.getByRole('button', { name: 'Create Tasks' }))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/ideas/compile')
    })
  })

  it('per-idea "Break into tasks" button calls convert endpoint', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(screen.getAllByText('Redesign sidebar').length).toBeGreaterThanOrEqual(1)
    })

    const breakButtons = screen.getAllByRole('button', { name: 'Break into tasks' })
    expect(breakButtons.length).toBeGreaterThan(0)

    fireEvent.click(breakButtons[0])

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/ideas/convert', { straw: 'Redesign sidebar' })
    })
  })

  it('renders suggested compilations section when clusters exist', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(screen.getByText('Suggested Compilations')).toBeInTheDocument()
    })
  })

  it('sort toggle cycles between "Newest first", "Oldest first", and "Stalest first"', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(screen.getByText('Newest first')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Newest first'))
    expect(screen.getByText('Oldest first')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Oldest first'))
    expect(screen.getByText('Stalest first')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Stalest first'))
    expect(screen.getByText('Newest first')).toBeInTheDocument()
  })

  it('remove button calls DELETE /ideas/{straw}', async () => {
    renderIdeas()

    await waitFor(() => {
      expect(screen.getByText('Learn Rust')).toBeInTheDocument()
    })

    const removeButtons = screen.getAllByTitle('Remove idea')
    // Last remove button corresponds to "Learn Rust".
    fireEvent.click(removeButtons[removeButtons.length - 1])

    await waitFor(() => {
      expect(mockedApiDelete).toHaveBeenCalledWith('/ideas/Learn%20Rust')
    })
  })

  it('handles API error on initial load gracefully', async () => {
    mockedApiGet.mockRejectedValue(new Error('Network error'))

    renderIdeas()

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/ideas')
    })

    // Page title should still render.
    expect(screen.getByRole('heading', { name: 'Ideas' })).toBeInTheDocument()
  })

  it('renders staleness badges when items are stale', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/ideas') {
        return Promise.resolve({
          clusters: [],
          unclustered: [
            item('Very old thing', { staleness: 'very_stale' }),
            item('Kinda old thing', { staleness: 'stale' }),
          ],
        })
      }
      if (path === '/ideas?status=converted') return Promise.resolve(mockConvertedResponse)
      if (path === '/ideas/templates') return Promise.resolve(mockTemplatesResponse)
      return Promise.resolve({})
    })

    renderIdeas()

    await waitFor(() => {
      expect(screen.getByText('Very stale')).toBeInTheDocument()
    })
    expect(screen.getByText('Getting stale')).toBeInTheDocument()
  })

  it('renders "From chat" badge when source is chat', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path === '/ideas') {
        return Promise.resolve({
          clusters: [],
          unclustered: [item('Idea from chat', { source: 'chat' })],
        })
      }
      if (path === '/ideas?status=converted') return Promise.resolve(mockConvertedResponse)
      if (path === '/ideas/templates') return Promise.resolve(mockTemplatesResponse)
      return Promise.resolve({})
    })

    renderIdeas()

    await waitFor(() => {
      expect(screen.getByText('From chat')).toBeInTheDocument()
    })
  })
})
