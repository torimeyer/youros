import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import AgentTemplateFileUpload from './AgentTemplateFileUpload'

// Mock fetch globally
const mockFetch = vi.fn()
global.fetch = mockFetch as unknown as typeof fetch

beforeEach(() => {
  mockFetch.mockReset()
})

describe('AgentTemplateFileUpload', () => {
  it('renders the attach files section', () => {
    render(
      <AgentTemplateFileUpload
        templateId="custom-abc123"
        attachedFiles={[]}
        onFilesChange={vi.fn()}
      />
    )
    expect(screen.getByText('Attach files')).toBeInTheDocument()
    expect(screen.getByText(/Click to browse or drag a file here/)).toBeInTheDocument()
  })

  it('renders existing attached files', () => {
    render(
      <AgentTemplateFileUpload
        templateId="custom-abc123"
        attachedFiles={['report.pdf', 'notes.txt']}
        onFilesChange={vi.fn()}
      />
    )
    expect(screen.getByText('report.pdf')).toBeInTheDocument()
    expect(screen.getByText('notes.txt')).toBeInTheDocument()
  })

  it('calls onFilesChange with updated list after successful upload', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ filename: 'document.txt', size: 100, template_id: 'custom-abc123' }),
    } as Response)

    const onFilesChange = vi.fn()
    render(
      <AgentTemplateFileUpload
        templateId="custom-abc123"
        attachedFiles={[]}
        onFilesChange={onFilesChange}
      />
    )

    // Simulate file selection via the hidden input
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    expect(input).toBeTruthy()
    const file = new File(['hello'], 'document.txt', { type: 'text/plain' })
    fireEvent.change(input, { target: { files: [file] } })

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/agents/templates/custom-abc123/upload',
        expect.objectContaining({ method: 'POST' })
      )
      expect(onFilesChange).toHaveBeenCalledWith(['document.txt'])
    })
  })

  it('shows an error when upload fails', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      json: async () => ({ detail: 'File too large.' }),
    } as Response)

    render(
      <AgentTemplateFileUpload
        templateId="custom-abc123"
        attachedFiles={[]}
        onFilesChange={vi.fn()}
      />
    )

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['x'.repeat(100)], 'big.pdf', { type: 'application/pdf' })
    fireEvent.change(input, { target: { files: [file] } })

    await waitFor(() => {
      expect(screen.getByText('File too large.')).toBeInTheDocument()
    })
  })

  it('rejects unsupported file types without calling fetch', async () => {
    render(
      <AgentTemplateFileUpload
        templateId="custom-abc123"
        attachedFiles={[]}
        onFilesChange={vi.fn()}
      />
    )

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['data'], 'image.png', { type: 'image/png' })
    fireEvent.change(input, { target: { files: [file] } })

    await waitFor(() => {
      expect(mockFetch).not.toHaveBeenCalled()
      expect(screen.getByText(/Only .txt, .md, .pdf, .docx/)).toBeInTheDocument()
    })
  })

  it('calls onFilesChange with file removed after clicking remove', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ deleted: 'notes.txt', template_id: 'custom-abc123' }),
    } as Response)

    const onFilesChange = vi.fn()
    render(
      <AgentTemplateFileUpload
        templateId="custom-abc123"
        attachedFiles={['notes.txt']}
        onFilesChange={onFilesChange}
      />
    )

    expect(screen.getByText('notes.txt')).toBeInTheDocument()

    // Click the remove button (title="Remove file")
    const removeBtn = screen.getByTitle('Remove file')
    fireEvent.click(removeBtn)

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/agents/templates/custom-abc123/upload/notes.txt',
        expect.objectContaining({ method: 'DELETE' })
      )
      expect(onFilesChange).toHaveBeenCalledWith([])
    })
  })
})
