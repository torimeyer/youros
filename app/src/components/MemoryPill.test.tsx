import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import MemoryPill from './MemoryPill'

vi.mock('../lib/api', () => ({
  api: { get: vi.fn() },
}))

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

import { api } from '../lib/api'
const mockApi = api as { get: ReturnType<typeof vi.fn> }

function Wrapper({ children }: { children: React.ReactNode }) {
  return <BrowserRouter>{children}</BrowserRouter>
}

describe('MemoryPill', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders brain icon and "N remembered" label', async () => {
    mockApi.get.mockResolvedValue({ bullet_count: 3, file_exists: true })
    render(<MemoryPill />, { wrapper: Wrapper })
    const pill = await screen.findByTestId('memory-pill')
    expect(pill).toBeInTheDocument()
    expect(pill).toHaveTextContent('🧠')
    expect(pill).toHaveTextContent('3 remembered')
  })

  it('navigates to /settings#memory on click', async () => {
    mockApi.get.mockResolvedValue({ bullet_count: 5, file_exists: true })
    render(<MemoryPill />, { wrapper: Wrapper })
    const pill = await screen.findByTestId('memory-pill')
    fireEvent.click(pill)
    expect(mockNavigate).toHaveBeenCalledWith('/settings#memory')
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
