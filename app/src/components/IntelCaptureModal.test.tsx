import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import IntelCaptureModal from './IntelCaptureModal'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

import { api } from '../lib/api'
const mockApi = api as { get: ReturnType<typeof vi.fn>; post: ReturnType<typeof vi.fn> }

describe('IntelCaptureModal', () => {
  const onClose = vi.fn()
  const onCaptured = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.get.mockResolvedValue({ competitors: ['Acme Corp', 'Rival Co'] })
    mockApi.post.mockResolvedValue({ id: 'new-id', captured_at: '2026-05-17T12:00:00Z' })
  })

  it('renders nothing when closed', () => {
    render(<IntelCaptureModal open={false} onClose={onClose} onCaptured={onCaptured} />)
    expect(screen.queryByTestId('intel-capture-modal')).toBeNull()
  })

  it('renders the modal when open', () => {
    render(<IntelCaptureModal open={true} onClose={onClose} onCaptured={onCaptured} />)
    expect(screen.getByTestId('intel-capture-modal')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('defaults to URL mode', () => {
    render(<IntelCaptureModal open={true} onClose={onClose} onCaptured={onCaptured} />)
    const urlRadio = screen.getByTestId('intel-mode-url') as HTMLInputElement
    expect(urlRadio.checked).toBe(true)
    expect(screen.getByTestId('intel-url-input')).toBeInTheDocument()
  })

  it('switches to text mode', () => {
    render(<IntelCaptureModal open={true} onClose={onClose} onCaptured={onCaptured} />)
    fireEvent.click(screen.getByTestId('intel-mode-text'))
    expect(screen.getByTestId('intel-text-input')).toBeInTheDocument()
    expect(screen.queryByTestId('intel-url-input')).toBeNull()
  })

  it('submits URL capture and calls onCaptured', async () => {
    render(<IntelCaptureModal open={true} onClose={onClose} onCaptured={onCaptured} />)

    fireEvent.change(screen.getByTestId('intel-competitor-input'), {
      target: { value: 'Acme Corp' },
    })
    fireEvent.change(screen.getByTestId('intel-url-input'), {
      target: { value: 'https://acme.com/pricing' },
    })
    fireEvent.change(screen.getByTestId('intel-tags-input'), {
      target: { value: 'pricing, feature' },
    })
    fireEvent.click(screen.getByTestId('intel-submit-btn'))

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith('/intel/capture', {
        competitor: 'Acme Corp',
        url: 'https://acme.com/pricing',
        text: null,
        tags: ['pricing', 'feature'],
      })
      expect(onCaptured).toHaveBeenCalled()
    })
  })

  it('submits text capture', async () => {
    render(<IntelCaptureModal open={true} onClose={onClose} onCaptured={onCaptured} />)

    fireEvent.change(screen.getByTestId('intel-competitor-input'), {
      target: { value: 'Rival Co' },
    })
    fireEvent.click(screen.getByTestId('intel-mode-text'))
    fireEvent.change(screen.getByTestId('intel-text-input'), {
      target: { value: 'They launched a free tier' },
    })
    fireEvent.click(screen.getByTestId('intel-submit-btn'))

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith('/intel/capture', {
        competitor: 'Rival Co',
        url: null,
        text: 'They launched a free tier',
        tags: [],
      })
    })
  })

  it('calls onClose when Cancel is clicked', () => {
    render(<IntelCaptureModal open={true} onClose={onClose} onCaptured={onCaptured} />)
    fireEvent.click(screen.getByText('Cancel'))
    expect(onClose).toHaveBeenCalled()
  })
})
