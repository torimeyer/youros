import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import FileShareModal from './FileShareModal'

// Mock the api module
vi.mock('../lib/api', () => ({
  api: {
    post: vi.fn(),
    delete: vi.fn(),
  },
}))

import { api } from '../lib/api'

const mockApi = api as { post: ReturnType<typeof vi.fn>; delete: ReturnType<typeof vi.fn> }

describe('FileShareModal', () => {
  const defaultProps = {
    filePath: '/home/user/.myos/files/report.md',
    fileName: 'report.md',
    onClose: vi.fn(),
  }

  beforeEach(() => {
    vi.clearAllMocks()
    // Default clipboard mock
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    })
  })

  it('renders the modal with the file name', async () => {
    mockApi.post.mockResolvedValue({ token: 'abc123', url: '/share/abc123' })

    render(<FileShareModal {...defaultProps} />)

    expect(screen.getByTestId('file-share-modal')).toBeInTheDocument()
    expect(screen.getByText('report.md')).toBeInTheDocument()
  })

  it('shows loading state while creating the link', () => {
    // Never resolves during this test
    mockApi.post.mockReturnValue(new Promise(() => {}))

    render(<FileShareModal {...defaultProps} />)

    expect(screen.getByText('Creating link...')).toBeInTheDocument()
  })

  it('shows the share URL and copy button after successful creation', async () => {
    mockApi.post.mockResolvedValue({ token: 'tok-xyz', url: '/share/tok-xyz' })

    render(<FileShareModal {...defaultProps} />)

    await waitFor(() => {
      expect(screen.getByTestId('file-share-url')).toBeInTheDocument()
    })

    const urlEl = screen.getByTestId('file-share-url')
    expect(urlEl.textContent).toContain('/share/tok-xyz')
    expect(screen.getByTestId('file-share-copy')).toBeInTheDocument()
  })

  it('copy button writes the URL to clipboard', async () => {
    mockApi.post.mockResolvedValue({ token: 'tok-copy', url: '/share/tok-copy' })

    render(<FileShareModal {...defaultProps} />)

    await waitFor(() => screen.getByTestId('file-share-copy'))

    fireEvent.click(screen.getByTestId('file-share-copy'))

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      expect.stringContaining('/share/tok-copy')
    )
  })

  it('shows error when API call fails', async () => {
    mockApi.post.mockRejectedValue(new Error('Network error'))

    render(<FileShareModal {...defaultProps} />)

    await waitFor(() => {
      expect(screen.getByText(/could not create share link/i)).toBeInTheDocument()
    })
  })

  it('calls onClose when close button is clicked', async () => {
    mockApi.post.mockResolvedValue({ token: 'tok', url: '/share/tok' })
    const onClose = vi.fn()

    render(<FileShareModal {...defaultProps} onClose={onClose} />)

    fireEvent.click(screen.getByTestId('file-share-modal-close'))

    expect(onClose).toHaveBeenCalled()
  })

  it('calls onClose when Escape is pressed', async () => {
    mockApi.post.mockResolvedValue({ token: 'tok', url: '/share/tok' })
    const onClose = vi.fn()

    render(<FileShareModal {...defaultProps} onClose={onClose} />)

    fireEvent.keyDown(window, { key: 'Escape' })

    expect(onClose).toHaveBeenCalled()
  })

  it('shows revoked state after revoking the link', async () => {
    mockApi.post.mockResolvedValue({ token: 'tok-rev', url: '/share/tok-rev' })
    mockApi.delete.mockResolvedValue({})

    render(<FileShareModal {...defaultProps} />)

    await waitFor(() => screen.getByTestId('file-share-revoke'))

    fireEvent.click(screen.getByTestId('file-share-revoke'))

    await waitFor(() => {
      expect(screen.getByText('Link revoked.')).toBeInTheDocument()
    })

    expect(mockApi.delete).toHaveBeenCalledWith('/shares/tok-rev')
  })

  it('calls POST /files/share with the encoded file path', async () => {
    mockApi.post.mockResolvedValue({ token: 'tok', url: '/share/tok' })

    render(<FileShareModal {...defaultProps} />)

    await waitFor(() => expect(mockApi.post).toHaveBeenCalledOnce())

    const callArg: string = mockApi.post.mock.calls[0][0]
    expect(callArg).toContain('/files/share')
    expect(callArg).toContain(encodeURIComponent(defaultProps.filePath))
  })
})
