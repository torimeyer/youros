import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import QuickLook from './QuickLook'

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

  it('renders an img when fileType is image/png', () => {
    render(<QuickLook {...base} filePath="/test/photo.png" fileType="image/png" />)
    expect(screen.getByTestId('quicklook-image')).toBeInTheDocument()
  })

  it('renders an iframe when fileType is application/pdf', () => {
    render(<QuickLook {...base} filePath="/test/doc.pdf" fileType="application/pdf" />)
    expect(screen.getByTestId('quicklook-pdf')).toBeInTheDocument()
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

  it('shows "Preview not available" and download link when fileType is application/octet-stream', () => {
    render(<QuickLook {...base} filePath="/test/file.bin" fileType="application/octet-stream" />)
    expect(screen.getByText(/Preview not available for this file type/i)).toBeInTheDocument()
    expect(screen.getByTestId('quicklook-download-link')).toBeInTheDocument()
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
