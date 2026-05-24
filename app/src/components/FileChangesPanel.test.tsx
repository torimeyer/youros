import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import FileChangesPanel, { type FileChange } from './FileChangesPanel'

const mockFetch = vi.fn()
global.fetch = mockFetch as unknown as typeof fetch

function makeFile(overrides: Partial<FileChange> = {}): FileChange {
  return {
    path: 'src/foo.ts',
    diff: '--- a/src/foo.ts\n+++ b/src/foo.ts\n@@ -1 +1 @@\n-old\n+new',
    is_binary: false,
    size_bytes: 100,
    mime_type: 'text/plain',
    ...overrides,
  }
}

describe('FileChangesPanel', () => {
  beforeEach(() => {
    mockFetch.mockReset()
  })

  it('renders collapsed by default with file count', () => {
    render(<FileChangesPanel turnId="t1" files={[makeFile()]} />)
    expect(screen.getByTestId('file-changes-panel')).toBeInTheDocument()
    expect(screen.getByTestId('file-changes-toggle')).toHaveTextContent('Files changed (1)')
    expect(screen.queryByTestId('file-changes-expanded')).not.toBeInTheDocument()
  })

  it('expands on toggle click', () => {
    render(<FileChangesPanel turnId="t1" files={[makeFile()]} />)
    fireEvent.click(screen.getByTestId('file-changes-toggle'))
    expect(screen.getByTestId('file-changes-expanded')).toBeInTheDocument()
    expect(screen.getByTestId('file-change-src/foo.ts')).toBeInTheDocument()
  })

  it('collapses again on second toggle click', () => {
    render(<FileChangesPanel turnId="t1" files={[makeFile()]} />)
    fireEvent.click(screen.getByTestId('file-changes-toggle'))
    fireEvent.click(screen.getByTestId('file-changes-toggle'))
    expect(screen.queryByTestId('file-changes-expanded')).not.toBeInTheDocument()
  })

  it('shows inline diff for text files when expanded', () => {
    render(<FileChangesPanel turnId="t1" files={[makeFile()]} />)
    fireEvent.click(screen.getByTestId('file-changes-toggle'))
    expect(screen.getByTestId('inline-diff')).toBeInTheDocument()
  })

  it('does not show diff for binary files', () => {
    render(<FileChangesPanel turnId="t1" files={[makeFile({ is_binary: true, diff: '' })]} />)
    fireEvent.click(screen.getByTestId('file-changes-toggle'))
    expect(screen.queryByTestId('inline-diff')).not.toBeInTheDocument()
    expect(screen.getByText(/text\/plain/)).toBeInTheDocument()
  })

  it('calls undo endpoint and shows Undone on success', async () => {
    mockFetch.mockResolvedValue({ ok: true, status: 200 })
    render(<FileChangesPanel turnId="t1" files={[makeFile()]} />)
    fireEvent.click(screen.getByTestId('file-changes-toggle'))
    fireEvent.click(screen.getByTestId('undo-btn-src/foo.ts'))
    await waitFor(() => expect(screen.getByTestId('undone-src/foo.ts')).toBeInTheDocument())
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/turn_audit/undo',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('shows conflict error on 409', async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 409 })
    render(<FileChangesPanel turnId="t1" files={[makeFile()]} />)
    fireEvent.click(screen.getByTestId('file-changes-toggle'))
    fireEvent.click(screen.getByTestId('undo-btn-src/foo.ts'))
    await waitFor(() => expect(screen.getByTestId('undo-error-src/foo.ts')).toBeInTheDocument())
    expect(screen.getByTestId('undo-error-src/foo.ts')).toHaveTextContent('externally')
  })

  it('shows generic error on non-409 failure', async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 500 })
    render(<FileChangesPanel turnId="t1" files={[makeFile()]} />)
    fireEvent.click(screen.getByTestId('file-changes-toggle'))
    fireEvent.click(screen.getByTestId('undo-btn-src/foo.ts'))
    await waitFor(() => expect(screen.getByTestId('undo-error-src/foo.ts')).toBeInTheDocument())
    expect(screen.getByTestId('undo-error-src/foo.ts')).toHaveTextContent('Undo failed')
  })

  it('shows undo-all button when multiple files', () => {
    const files = [makeFile({ path: 'a.ts' }), makeFile({ path: 'b.ts' })]
    render(<FileChangesPanel turnId="t1" files={files} />)
    fireEvent.click(screen.getByTestId('file-changes-toggle'))
    expect(screen.getByTestId('undo-all-btn')).toBeInTheDocument()
  })

  it('does not show undo-all button with single file', () => {
    render(<FileChangesPanel turnId="t1" files={[makeFile()]} />)
    fireEvent.click(screen.getByTestId('file-changes-toggle'))
    expect(screen.queryByTestId('undo-all-btn')).not.toBeInTheDocument()
  })
})
