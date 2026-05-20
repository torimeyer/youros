import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import MemoryPill from './MemoryPill'

vi.mock('../lib/api', () => ({
  api: { get: vi.fn() },
}))

import { api } from '../lib/api'
const mockApi = api as { get: ReturnType<typeof vi.fn> }

function Wrapper({ children }: { children: React.ReactNode }) {
  return <BrowserRouter>{children}</BrowserRouter>
}

describe('MemoryPill', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders with count when memory has bullets', async () => {
    mockApi.get.mockResolvedValue({ bullet_count: 3, file_exists: true })
    render(<MemoryPill />, { wrapper: Wrapper })
    const pill = await screen.findByTestId('memory-pill')
    expect(pill).toBeInTheDocument()
    expect(pill).toHaveTextContent('3')
  })

  it('does not render when bullet count is 0', async () => {
    mockApi.get.mockResolvedValue({ bullet_count: 0, file_exists: true })
    render(<MemoryPill />, { wrapper: Wrapper })
    await waitFor(() => expect(mockApi.get).toHaveBeenCalledWith('/memory/count'))
    expect(screen.queryByTestId('memory-pill')).not.toBeInTheDocument()
  })

  it('does not render when file is missing', async () => {
    mockApi.get.mockResolvedValue({ bullet_count: 0, file_exists: false })
    render(<MemoryPill />, { wrapper: Wrapper })
    await waitFor(() => expect(mockApi.get).toHaveBeenCalledWith('/memory/count'))
    expect(screen.queryByTestId('memory-pill')).not.toBeInTheDocument()
  })

  it('does not render when api call fails', async () => {
    mockApi.get.mockRejectedValue(new Error('network error'))
    render(<MemoryPill />, { wrapper: Wrapper })
    await waitFor(() => expect(mockApi.get).toHaveBeenCalledWith('/memory/count'))
    expect(screen.queryByTestId('memory-pill')).not.toBeInTheDocument()
  })

  it('has correct tooltip text', async () => {
    mockApi.get.mockResolvedValue({ bullet_count: 1, file_exists: true })
    render(<MemoryPill />, { wrapper: Wrapper })
    const pill = await screen.findByTestId('memory-pill')
    expect(pill).toHaveAttribute('title', 'Things I remember about you. Click to edit.')
  })

  it('calls /memory/count on mount', async () => {
    mockApi.get.mockResolvedValue({ bullet_count: 2, file_exists: true })
    render(<MemoryPill />, { wrapper: Wrapper })
    await waitFor(() => expect(mockApi.get).toHaveBeenCalledWith('/memory/count'))
  })
})
