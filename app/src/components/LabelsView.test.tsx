import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import LabelsView from './LabelsView'

// Mock the api module
vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
    patch: vi.fn(),
    put: vi.fn(),
  },
}))

import { api } from '../lib/api'

const mockApi = api as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  delete: ReturnType<typeof vi.fn>
  patch: ReturnType<typeof vi.fn>
}

const MOCK_LABELS = [
  { id: 'l1', name: 'Bug', color: '#ef4444', task_count: 3, open_count: 1, closed_count: 2 },
  { id: 'l2', name: 'Feature', color: '#22c55e', task_count: 1, open_count: 0, closed_count: 1 },
]

const MOCK_COLORS = [
  '#ef4444', '#f97316', '#eab308', '#22c55e', '#06b6d4',
  '#3b82f6', '#8b5cf6', '#ec4899', '#6366f1', '#14b8a6',
]

beforeEach(() => {
  vi.clearAllMocks()
  mockApi.get.mockImplementation((path: string) => {
    if (path === '/labels') return Promise.resolve({ labels: MOCK_LABELS })
    if (path === '/labels/colors') return Promise.resolve({ colors: MOCK_COLORS })
    return Promise.resolve({})
  })
})

describe('LabelsView', () => {
  it('shows loading state initially', () => {
    // Make the API hang so we can see loading
    mockApi.get.mockImplementation(() => new Promise(() => {}))
    render(<LabelsView />)
    expect(screen.getByText('Loading labels...')).toBeTruthy()
  })

  it('renders label names after loading', async () => {
    render(<LabelsView />)
    await waitFor(() => {
      expect(screen.getByText('Bug')).toBeTruthy()
      expect(screen.getByText('Feature')).toBeTruthy()
    })
  })

  it('shows task count for each label', async () => {
    render(<LabelsView />)
    await waitFor(() => {
      expect(screen.getByText('2/3')).toBeTruthy()
      expect(screen.getByText('1/1')).toBeTruthy()
    })
  })

  it('shows empty state when no labels exist', async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === '/labels') return Promise.resolve({ labels: [] })
      if (path === '/labels/colors') return Promise.resolve({ colors: MOCK_COLORS })
      return Promise.resolve({})
    })
    render(<LabelsView />)
    await waitFor(() => {
      expect(screen.getByText('No labels yet.')).toBeTruthy()
    })
  })

  it('opens create form when "New label" is clicked', async () => {
    const user = userEvent.setup()
    render(<LabelsView />)
    await waitFor(() => screen.getByText('Bug'))

    await user.click(screen.getByText('New label'))
    expect(screen.getByPlaceholderText('Label name')).toBeTruthy()
    expect(screen.getByText('Create')).toBeTruthy()
  })

  it('creates a label through the form', async () => {
    const user = userEvent.setup()
    const onLabelsChanged = vi.fn()
    mockApi.post.mockResolvedValue({
      label: { id: 'l3', name: 'Docs', color: '#3b82f6', task_count: 0 },
    })

    render(<LabelsView onLabelsChanged={onLabelsChanged} />)
    await waitFor(() => screen.getByText('Bug'))

    await user.click(screen.getByText('New label'))
    const input = screen.getByPlaceholderText('Label name')
    await user.type(input, 'Docs')

    // Click a color swatch (the blue one, 6th color)
    const colorButtons = screen.getAllByTitle(/#/)
    await user.click(colorButtons[5]) // #3b82f6

    await user.click(screen.getByText('Create'))

    expect(mockApi.post).toHaveBeenCalledWith('/labels', {
      name: 'Docs',
      color: '#3b82f6',
    })
    expect(onLabelsChanged).toHaveBeenCalled()
  })

  it('calls onFilterByLabel when a label is clicked', async () => {
    const user = userEvent.setup()
    const onFilterByLabel = vi.fn()
    render(<LabelsView onFilterByLabel={onFilterByLabel} />)
    await waitFor(() => screen.getByText('Bug'))

    await user.click(screen.getByText('Bug'))
    expect(onFilterByLabel).toHaveBeenCalledWith('l1')
  })

  it('clears filter when active label is clicked again', async () => {
    const user = userEvent.setup()
    const onFilterByLabel = vi.fn()
    render(<LabelsView onFilterByLabel={onFilterByLabel} activeLabelId="l1" />)
    await waitFor(() => screen.getByText('Bug'))

    await user.click(screen.getByText('Bug'))
    expect(onFilterByLabel).toHaveBeenCalledWith(null)
  })

  it('disables Create button when no color is selected', async () => {
    const user = userEvent.setup()
    render(<LabelsView />)
    await waitFor(() => screen.getByText('Bug'))

    await user.click(screen.getByText('New label'))
    const input = screen.getByPlaceholderText('Label name')
    await user.type(input, 'Test')

    const createBtn = screen.getByText('Create')
    expect(createBtn).toBeDisabled()
  })

  it('shows preview pill while creating', async () => {
    const user = userEvent.setup()
    render(<LabelsView />)
    await waitFor(() => screen.getByText('Bug'))

    await user.click(screen.getByText('New label'))
    const input = screen.getByPlaceholderText('Label name')
    await user.type(input, 'Preview')

    // Select a color
    const colorButtons = screen.getAllByTitle(/#/)
    await user.click(colorButtons[0])

    expect(screen.getByText('Preview:')).toBeTruthy()
  })
})
