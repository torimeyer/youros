import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Documents from './Documents'
import { api } from '../lib/api'

// Mock the api module
vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue([]),
    post: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  },
  ApiError: class extends Error {
    status: number
    response: { data: { detail: unknown } }
    constructor(msg: string, status: number) {
      super(msg)
      this.status = status
      this.response = { data: { detail: msg } }
    }
  },
}))

const mockApi = vi.mocked(api)

// Mock Icon component
vi.mock('../components/Icon', () => ({
  default: ({ name, ...props }: { name: string; [k: string]: unknown }) => (
    <span data-testid={`icon-${name}`} {...props}>{name}</span>
  ),
}))

// Mock TopBar
vi.mock('../components/TopBar', () => ({
  default: ({ title }: { title: string }) => <div data-testid="top-bar">{title}</div>,
}))

// Mock DrivePreview
vi.mock('../components/DrivePreview', () => ({
  default: ({ name }: { name: string }) => <div data-testid="drive-preview-inline">{name}</div>,
}))

// Mock FilePreviewPane
vi.mock('../components/FilePreviewPane', () => ({
  default: ({ entry }: { entry: { name: string } | null }) => (
    entry ? <div data-testid="file-preview-inline">{entry.name}</div> : null
  ),
}))

function renderWithRouter(tab?: string) {
  const initialEntries = tab ? [`/documents?tab=${tab}`] : ['/documents']
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <Documents />
    </MemoryRouter>
  )
}

// ---------------------------------------------------------------------------
// Test data factories
// ---------------------------------------------------------------------------

function makeDriveFile(overrides: Partial<{
  id: string; name: string; mimeType: string; modifiedTime: string
  iconLink: string; webViewLink: string; size: string | null
}> = {}) {
  return {
    id: overrides.id ?? 'drive-1',
    name: overrides.name ?? 'Budget.xlsx',
    mimeType: overrides.mimeType ?? 'application/vnd.google-apps.spreadsheet',
    modifiedTime: overrides.modifiedTime ?? '2026-04-15T10:00:00Z',
    iconLink: overrides.iconLink ?? 'https://example.com/icon.png',
    webViewLink: overrides.webViewLink ?? 'https://drive.google.com/file/d/drive-1',
    size: overrides.size ?? '1024',
  }
}

function makeDocument(overrides: Partial<{
  id: string; name: string; type: string; path: string; created: string; updated: string
}> = {}) {
  return {
    id: overrides.id ?? 'doc-1',
    name: overrides.name ?? 'Q1 Report',
    type: overrides.type ?? 'sheet',
    path: overrides.path ?? '/tmp/doc-1.xlsx',
    created: overrides.created ?? '2026-04-15T08:00:00Z',
    updated: overrides.updated ?? '2026-04-15T10:00:00Z',
  }
}

function makeRecentDoc(overrides: Partial<{
  name: string; path: string; size: number; size_display: string
  last_modified: string | null; snippet: string
}> = {}) {
  return {
    name: overrides.name ?? 'notes.txt',
    path: overrides.path ?? '/uploads/notes.txt',
    size: overrides.size ?? 512,
    size_display: overrides.size_display ?? '512 B',
    last_modified: overrides.last_modified ?? '2026-04-15T09:00:00Z',
    snippet: overrides.snippet ?? 'Some content...',
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Default mock that returns empty data for all endpoints */
function defaultMockGet(path: string) {
  if (path === '/drive/auth/status') {
    return Promise.resolve({
      authenticated: false,
      email: null,
      credentials_file_present: false,
      needs_reauth: false,
    } as never)
  }
  if (path === '/documents') return Promise.resolve([] as never)
  if (path.startsWith('/docs/recent')) return Promise.resolve({ files: [] } as never)
  if (path.startsWith('/drive/files')) return Promise.resolve({ files: [], cached: false } as never)
  return Promise.resolve({} as never)
}

describe('Documents page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    mockApi.get.mockImplementation(defaultMockGet)
  })

  // =========================================================================
  // Tab behavior
  // =========================================================================

  describe('Tab behavior', () => {
    it('renders with Generated tab by default when no URL param', () => {
      renderWithRouter()
      expect(screen.getByTestId('tab-generated')).toBeDefined()
      expect(screen.getByTestId('documents-page')).toBeDefined()
    })

    it('URL param ?tab=drive sets initial tab to My Drive', () => {
      renderWithRouter('drive')
      expect(screen.getByTestId('tab-drive')).toBeDefined()
    })

    it('URL param ?tab=uploads sets initial tab to Uploads', () => {
      renderWithRouter('uploads')
      expect(screen.getByTestId('tab-uploads')).toBeDefined()
    })

    it('shows all three tabs in the tab bar', () => {
      renderWithRouter()
      expect(screen.getByTestId('tab-drive')).toBeDefined()
      expect(screen.getByTestId('tab-uploads')).toBeDefined()
      expect(screen.getByTestId('tab-generated')).toBeDefined()
    })

    it('tab switching works', async () => {
      renderWithRouter()
      const driveTab = screen.getByTestId('tab-drive')
      fireEvent.click(driveTab)
      await waitFor(() => {
        expect(screen.getByTestId('connect-drive-button')).toBeDefined()
      })
    })

    it('switching to Uploads tab shows the drop zone', async () => {
      renderWithRouter()
      fireEvent.click(screen.getByTestId('tab-uploads'))
      await waitFor(() => {
        expect(screen.getByTestId('upload-drop-zone')).toBeDefined()
      })
    })
  })

  // =========================================================================
  // My Drive tab
  // =========================================================================

  describe('My Drive tab', () => {
    it('shows connect button when not authenticated', async () => {
      renderWithRouter('drive')
      await waitFor(() => {
        expect(screen.getByTestId('connect-drive-button')).toBeDefined()
      })
    })

    it('drive auth check fires on mount', async () => {
      renderWithRouter('drive')
      await waitFor(() => {
        expect(mockApi.get).toHaveBeenCalledWith('/drive/auth/status')
      })
    })

    it('shows file list when authenticated', async () => {
      const driveFiles = [
        makeDriveFile({ id: 'df-1', name: 'Presentation.pptx' }),
        makeDriveFile({ id: 'df-2', name: 'Spreadsheet.xlsx' }),
      ]
      mockApi.get.mockImplementation((path: string) => {
        if (path === '/drive/auth/status') {
          return Promise.resolve({
            authenticated: true,
            email: 'test@example.com',
            credentials_file_present: true,
            needs_reauth: false,
          } as never)
        }
        if (path.startsWith('/drive/files')) {
          return Promise.resolve({ files: driveFiles, cached: false } as never)
        }
        return defaultMockGet(path)
      })
      renderWithRouter('drive')
      await waitFor(() => {
        expect(screen.getByTestId('drive-file-df-1')).toBeDefined()
        expect(screen.getByTestId('drive-file-df-2')).toBeDefined()
      })
    })

    it('search filters Drive files client-side', async () => {
      const driveFiles = [
        makeDriveFile({ id: 'df-a', name: 'Alpha Report' }),
        makeDriveFile({ id: 'df-b', name: 'Beta Plan' }),
      ]
      mockApi.get.mockImplementation((path: string) => {
        if (path === '/drive/auth/status') {
          return Promise.resolve({
            authenticated: true, email: 'x@x.com',
            credentials_file_present: true, needs_reauth: false,
          } as never)
        }
        if (path.startsWith('/drive/files')) {
          return Promise.resolve({ files: driveFiles, cached: false } as never)
        }
        return defaultMockGet(path)
      })
      renderWithRouter('drive')
      await waitFor(() => {
        expect(screen.getByTestId('drive-file-df-a')).toBeDefined()
        expect(screen.getByTestId('drive-file-df-b')).toBeDefined()
      })
      const searchInput = screen.getByTestId('drive-search')
      fireEvent.change(searchInput, { target: { value: 'Alpha' } })
      await waitFor(() => {
        expect(screen.getByTestId('drive-file-df-a')).toBeDefined()
        expect(screen.queryByTestId('drive-file-df-b')).toBeNull()
      })
    })

    it('clicking Drive file shows DrivePreview in center pane', async () => {
      const driveFiles = [makeDriveFile({ id: 'df-click', name: 'ClickMe.docx' })]
      mockApi.get.mockImplementation((path: string) => {
        if (path === '/drive/auth/status') {
          return Promise.resolve({
            authenticated: true, email: 'x@x.com',
            credentials_file_present: true, needs_reauth: false,
          } as never)
        }
        if (path.startsWith('/drive/files')) {
          return Promise.resolve({ files: driveFiles, cached: false } as never)
        }
        return defaultMockGet(path)
      })
      renderWithRouter('drive')
      await waitFor(() => {
        expect(screen.getByTestId('drive-file-df-click')).toBeDefined()
      })
      fireEvent.click(screen.getByTestId('drive-file-df-click'))
      await waitFor(() => {
        expect(screen.getByTestId('drive-preview-inline')).toBeDefined()
      })
    })

    it('sync button calls POST /drive/sync', async () => {
      mockApi.get.mockImplementation((path: string) => {
        if (path === '/drive/auth/status') {
          return Promise.resolve({
            authenticated: true, email: 'x@x.com',
            credentials_file_present: true, needs_reauth: false,
          } as never)
        }
        if (path.startsWith('/drive/files')) {
          return Promise.resolve({ files: [], cached: false } as never)
        }
        return defaultMockGet(path)
      })
      mockApi.post.mockResolvedValue({ ok: true, file_count: 5, synced_at: Date.now() / 1000 } as never)
      renderWithRouter('drive')
      await waitFor(() => {
        const syncBtn = screen.queryByTestId('icon-sync')
        expect(syncBtn).not.toBeNull()
      })
      const syncIcon = screen.getByTestId('icon-sync')
      const syncButton = syncIcon.closest('button')
      if (syncButton) fireEvent.click(syncButton)
      await waitFor(() => {
        expect(mockApi.post).toHaveBeenCalledWith('/drive/sync')
      })
    })
  })

  // =========================================================================
  // Uploads tab
  // =========================================================================

  describe('Uploads tab', () => {
    it('drop zone renders with expected text', async () => {
      renderWithRouter('uploads')
      await waitFor(() => {
        expect(screen.getByTestId('upload-drop-zone')).toBeDefined()
      })
      const dropZone = screen.getByTestId('upload-drop-zone')
      expect(dropZone.textContent).toBeTruthy()
    })

    it('recent docs load from GET /docs/recent', async () => {
      const recentFiles = [
        makeRecentDoc({ name: 'file-a.txt', path: '/uploads/file-a.txt' }),
        makeRecentDoc({ name: 'file-b.md', path: '/uploads/file-b.md' }),
      ]
      mockApi.get.mockImplementation((path: string) => {
        if (path.startsWith('/docs/recent')) {
          return Promise.resolve({ files: recentFiles } as never)
        }
        return defaultMockGet(path)
      })
      renderWithRouter('uploads')
      await waitFor(() => {
        expect(mockApi.get).toHaveBeenCalledWith('/docs/recent?limit=20')
      })
    })

    it('clicking local file shows FilePreviewPane', async () => {
      const recentFiles = [
        makeRecentDoc({ name: 'preview-me.txt', path: '/uploads/preview-me.txt' }),
      ]
      mockApi.get.mockImplementation((path: string) => {
        if (path.startsWith('/docs/recent')) {
          return Promise.resolve({ files: recentFiles } as never)
        }
        return defaultMockGet(path)
      })
      renderWithRouter('uploads')
      await waitFor(() => {
        const fileItem = screen.getByTestId('upload-file-/uploads/preview-me.txt')
        expect(fileItem).toBeDefined()
      })
      fireEvent.click(screen.getByTestId('upload-file-/uploads/preview-me.txt'))
      await waitFor(() => {
        expect(screen.getByTestId('file-preview-inline')).toBeDefined()
      })
    })
  })

  // =========================================================================
  // Generated tab
  // =========================================================================

  describe('Generated tab', () => {
    it('shows New button only on Generated tab', () => {
      renderWithRouter()
      expect(screen.getByTestId('new-button')).toBeDefined()
    })

    it('does not show New button on Drive tab', () => {
      renderWithRouter('drive')
      expect(screen.queryByTestId('new-button')).toBeNull()
    })

    it('create new document flow: click New, pick type, enter name, submit', async () => {
      const createdDoc = makeDocument({ id: 'new-doc-1', name: 'My Sheet', type: 'sheet' })
      mockApi.post.mockResolvedValueOnce(createdDoc as never)
      mockApi.get.mockImplementation((path: string) => {
        if (path === '/documents/new-doc-1') return Promise.resolve(createdDoc as never)
        if (path === '/documents/new-doc-1/preview') return Promise.resolve({ type: 'sheet', sheets: [] } as never)
        return defaultMockGet(path)
      })
      renderWithRouter()
      fireEvent.click(screen.getByTestId('new-button'))
      await waitFor(() => {
        expect(screen.getByTestId('new-doc-modal')).toBeDefined()
      })
      fireEvent.click(screen.getByTestId('type-sheet'))
      const nameInput = screen.getByTestId('name-input')
      fireEvent.change(nameInput, { target: { value: 'My Sheet' } })
      fireEvent.click(screen.getByTestId('create-button'))
      await waitFor(() => {
        expect(mockApi.post).toHaveBeenCalledWith('/documents', { type: 'sheet', name: 'My Sheet' })
      })
    })

    it('creating a slide deck adds it to the list and selects it', async () => {
      const createdSlides = makeDocument({ id: 'slides-new', name: 'My Deck', type: 'slides' })
      mockApi.post.mockResolvedValueOnce(createdSlides as never)
      mockApi.get.mockImplementation((path: string) => {
        if (path === '/documents/slides-new') return Promise.resolve(createdSlides as never)
        if (path === '/documents/slides-new/preview') {
          return Promise.resolve({ type: 'slides', slides: [] } as never)
        }
        return defaultMockGet(path)
      })
      renderWithRouter()
      fireEvent.click(screen.getByTestId('new-button'))
      await waitFor(() => { expect(screen.getByTestId('new-doc-modal')).toBeDefined() })
      fireEvent.click(screen.getByTestId('type-slides'))
      const nameInput = screen.getByTestId('name-input')
      fireEvent.change(nameInput, { target: { value: 'My Deck' } })
      fireEvent.click(screen.getByTestId('create-button'))
      await waitFor(() => {
        expect(mockApi.post).toHaveBeenCalledWith('/documents', { type: 'slides', name: 'My Deck' })
        expect(screen.getByTestId('doc-item-slides-new')).toBeDefined()
      })
    })

    it('edit instruction sends to POST /documents/{id}/edit', async () => {
      const doc = makeDocument({ id: 'edit-doc' })
      mockApi.get.mockImplementation((path: string) => {
        if (path === '/documents') return Promise.resolve([doc] as never)
        if (path === '/documents/edit-doc') return Promise.resolve(doc as never)
        if (path === '/documents/edit-doc/preview') return Promise.resolve({ type: 'sheet', sheets: [{ name: 'Sheet1', headers: ['A'], rows: [['1']] }] } as never)
        return defaultMockGet(path)
      })
      mockApi.post.mockResolvedValue({ digest: 'abc', mutations_applied: 1 } as never)
      renderWithRouter()
      await waitFor(() => {
        expect(screen.getByTestId('doc-item-edit-doc')).toBeDefined()
      })
      fireEvent.click(screen.getByTestId('doc-item-edit-doc'))
      await waitFor(() => {
        expect(screen.getByTestId('edit-input')).toBeDefined()
      })
      fireEvent.change(screen.getByTestId('edit-input'), { target: { value: 'Add a row' } })
      fireEvent.click(screen.getByTestId('send-edit-button'))
      await waitFor(() => {
        expect(mockApi.post).toHaveBeenCalledWith('/documents/edit-doc/edit', { instruction: 'Add a row' })
      })
    })

    it('edit error shows actionable message in edit log', async () => {
      const doc = makeDocument({ id: 'err-doc' })
      mockApi.get.mockImplementation((path: string) => {
        if (path === '/documents') return Promise.resolve([doc] as never)
        if (path === '/documents/err-doc') return Promise.resolve(doc as never)
        if (path === '/documents/err-doc/preview') return Promise.resolve({ type: 'sheet', sheets: [] } as never)
        return defaultMockGet(path)
      })
      mockApi.post.mockRejectedValueOnce(new Error('Server error. Try again or restart the backend.'))
      renderWithRouter()
      await waitFor(() => { expect(screen.getByTestId('doc-item-err-doc')).toBeDefined() })
      fireEvent.click(screen.getByTestId('doc-item-err-doc'))
      await waitFor(() => { expect(screen.getByTestId('edit-input')).toBeDefined() })
      fireEvent.change(screen.getByTestId('edit-input'), { target: { value: 'Break it' } })
      fireEvent.click(screen.getByTestId('send-edit-button'))
      await waitFor(() => {
        const entries = screen.getAllByTestId('edit-entry')
        const lastEntry = entries[entries.length - 1]
        expect(lastEntry.textContent).toContain('Server error')
      })
    })

    it('save button calls POST /documents/{id}/save', async () => {
      const doc = makeDocument({ id: 'save-doc' })
      mockApi.get.mockImplementation((path: string) => {
        if (path === '/documents') return Promise.resolve([doc] as never)
        if (path === '/documents/save-doc') return Promise.resolve(doc as never)
        if (path === '/documents/save-doc/preview') return Promise.resolve({ type: 'sheet', sheets: [] } as never)
        return defaultMockGet(path)
      })
      mockApi.post.mockResolvedValue({ path: '/tmp/saved.xlsx', size_bytes: 1024, version: 1 } as never)
      renderWithRouter()
      await waitFor(() => { expect(screen.getByTestId('doc-item-save-doc')).toBeDefined() })
      fireEvent.click(screen.getByTestId('doc-item-save-doc'))
      await waitFor(() => { expect(screen.getByTestId('save-button')).toBeDefined() })
      fireEvent.click(screen.getByTestId('save-button'))
      await waitFor(() => {
        expect(mockApi.post).toHaveBeenCalledWith('/documents/save-doc/save')
      })
    })

    it('download button is present when a doc is selected', async () => {
      const doc = makeDocument({ id: 'dl-doc' })
      mockApi.get.mockImplementation((path: string) => {
        if (path === '/documents') return Promise.resolve([doc] as never)
        if (path === '/documents/dl-doc') return Promise.resolve(doc as never)
        if (path === '/documents/dl-doc/preview') return Promise.resolve({ type: 'sheet', sheets: [] } as never)
        return defaultMockGet(path)
      })
      renderWithRouter()
      await waitFor(() => { expect(screen.getByTestId('doc-item-dl-doc')).toBeDefined() })
      fireEvent.click(screen.getByTestId('doc-item-dl-doc'))
      await waitFor(() => { expect(screen.getByTestId('download-button')).toBeDefined() })
      const dlBtn = screen.getByTestId('download-button')
      expect(dlBtn).toBeDefined()
    })

    it('versions button loads from GET /documents/{id}/versions', async () => {
      const doc = makeDocument({ id: 'ver-doc' })
      const versions = [
        { version: 1, timestamp: '2026-04-15T08:00:00Z', path: '/tmp/v1.xlsx' },
        { version: 2, timestamp: '2026-04-15T09:00:00Z', path: '/tmp/v2.xlsx' },
      ]
      mockApi.get.mockImplementation((path: string) => {
        if (path === '/documents') return Promise.resolve([doc] as never)
        if (path === '/documents/ver-doc') return Promise.resolve(doc as never)
        if (path === '/documents/ver-doc/preview') return Promise.resolve({ type: 'sheet', sheets: [] } as never)
        if (path === '/documents/ver-doc/versions') return Promise.resolve(versions as never)
        return defaultMockGet(path)
      })
      renderWithRouter()
      await waitFor(() => { expect(screen.getByTestId('doc-item-ver-doc')).toBeDefined() })
      fireEvent.click(screen.getByTestId('doc-item-ver-doc'))
      await waitFor(() => { expect(screen.getByTestId('versions-button')).toBeDefined() })
      fireEvent.click(screen.getByTestId('versions-button'))
      await waitFor(() => {
        expect(mockApi.get).toHaveBeenCalledWith('/documents/ver-doc/versions')
      })
    })

    it('delete removes doc from list', async () => {
      const doc = makeDocument({ id: 'del-doc', name: 'Delete Me' })
      mockApi.get.mockImplementation((path: string) => {
        if (path === '/documents') return Promise.resolve([doc] as never)
        if (path === '/documents/del-doc') return Promise.resolve(doc as never)
        if (path === '/documents/del-doc/preview') return Promise.resolve({ type: 'sheet', sheets: [] } as never)
        return defaultMockGet(path)
      })
      mockApi.delete.mockResolvedValue({} as never)
      renderWithRouter()
      await waitFor(() => { expect(screen.getByTestId('doc-item-del-doc')).toBeDefined() })
      fireEvent.click(screen.getByTestId('kebab-del-doc'))
      await waitFor(() => { expect(screen.getByTestId('delete-del-doc')).toBeDefined() })
      fireEvent.click(screen.getByTestId('delete-del-doc'))
      await waitFor(() => {
        expect(mockApi.delete).toHaveBeenCalledWith('/documents/del-doc')
        expect(screen.queryByTestId('doc-item-del-doc')).toBeNull()
      })
    })

    it('empty list shows the empty state message', async () => {
      mockApi.get.mockImplementation(defaultMockGet)
      renderWithRouter()
      await waitFor(() => {
        expect(screen.getByTestId('empty-list')).toBeDefined()
      })
    })
  })

  // =========================================================================
  // Preview pane
  // =========================================================================

  describe('Preview pane', () => {
    it('sheet preview renders table headers', async () => {
      const doc = makeDocument({ id: 'sheet-doc', type: 'sheet' })
      mockApi.get.mockImplementation((path: string) => {
        if (path === '/documents') return Promise.resolve([doc] as never)
        if (path === '/documents/sheet-doc') return Promise.resolve(doc as never)
        if (path === '/documents/sheet-doc/preview') {
          return Promise.resolve({
            type: 'sheet',
            sheets: [{
              name: 'Sheet1',
              headers: ['Name', 'Value', 'Status'],
              rows: [['Widget', '100', 'Active']],
            }],
          } as never)
        }
        return defaultMockGet(path)
      })
      renderWithRouter()
      await waitFor(() => { expect(screen.getByTestId('doc-item-sheet-doc')).toBeDefined() })
      fireEvent.click(screen.getByTestId('doc-item-sheet-doc'))
      await waitFor(() => {
        expect(screen.getByTestId('sheet-table')).toBeDefined()
      })
      const table = screen.getByTestId('sheet-table')
      expect(table.textContent).toContain('Name')
      expect(table.textContent).toContain('Value')
      expect(table.textContent).toContain('Status')
    })

    it('PDF preview shows page count', async () => {
      const doc = makeDocument({ id: 'pdf-doc', type: 'pdf' })
      mockApi.get.mockImplementation((path: string) => {
        if (path === '/documents') return Promise.resolve([doc] as never)
        if (path === '/documents/pdf-doc') return Promise.resolve(doc as never)
        if (path === '/documents/pdf-doc/preview') {
          return Promise.resolve({
            type: 'pdf',
            page_count: 5,
            pages: [{ page: 1, text: 'Page 1 content' }],
          } as never)
        }
        return defaultMockGet(path)
      })
      renderWithRouter()
      await waitFor(() => { expect(screen.getByTestId('doc-item-pdf-doc')).toBeDefined() })
      fireEvent.click(screen.getByTestId('doc-item-pdf-doc'))
      await waitFor(() => {
        expect(screen.getByTestId('pdf-viewer')).toBeDefined()
      })
      const viewer = screen.getByTestId('pdf-viewer')
      expect(viewer.textContent).toContain('5')
    })

    it('drawio preview renders container', async () => {
      const doc = makeDocument({ id: 'drawio-doc', type: 'diagram' })
      mockApi.get.mockImplementation((path: string) => {
        if (path === '/documents') return Promise.resolve([doc] as never)
        if (path === '/documents/drawio-doc') return Promise.resolve(doc as never)
        if (path === '/documents/drawio-doc/preview') {
          return Promise.resolve({
            type: 'drawio',
            xml: '<mxfile>...</mxfile>',
            svg: '<svg></svg>',
          } as never)
        }
        return defaultMockGet(path)
      })
      renderWithRouter()
      await waitFor(() => { expect(screen.getByTestId('doc-item-drawio-doc')).toBeDefined() })
      fireEvent.click(screen.getByTestId('doc-item-drawio-doc'))
      await waitFor(() => {
        expect(screen.getByTestId('drawio-viewer')).toBeDefined()
      })
    })

    it('slides preview renders slide cards', async () => {
      const doc = makeDocument({ id: 'slides-doc', type: 'slides' })
      mockApi.get.mockImplementation((path: string) => {
        if (path === '/documents') return Promise.resolve([doc] as never)
        if (path === '/documents/slides-doc') return Promise.resolve(doc as never)
        if (path === '/documents/slides-doc/preview') {
          return Promise.resolve({
            type: 'slides',
            slides: [
              { number: 1, title: 'Intro', body: 'Welcome' },
              { number: 2, title: 'Summary', body: 'Done' },
            ],
          } as never)
        }
        return defaultMockGet(path)
      })
      renderWithRouter()
      await waitFor(() => { expect(screen.getByTestId('doc-item-slides-doc')).toBeDefined() })
      fireEvent.click(screen.getByTestId('doc-item-slides-doc'))
      await waitFor(() => {
        expect(screen.getByTestId('slides-viewer')).toBeDefined()
        expect(screen.getByTestId('slide-card-1')).toBeDefined()
        expect(screen.getByTestId('slide-card-2')).toBeDefined()
      })
    })

    it('loading state shows spinner', async () => {
      const doc = makeDocument({ id: 'loading-doc' })
      let resolvePreview: (v: unknown) => void = () => {}
      mockApi.get.mockImplementation((path: string) => {
        if (path === '/documents') return Promise.resolve([doc] as never)
        if (path === '/documents/loading-doc') return Promise.resolve(doc as never)
        if (path === '/documents/loading-doc/preview') {
          return new Promise((resolve) => { resolvePreview = resolve })
        }
        return defaultMockGet(path)
      })
      renderWithRouter()
      await waitFor(() => { expect(screen.getByTestId('doc-item-loading-doc')).toBeDefined() })
      fireEvent.click(screen.getByTestId('doc-item-loading-doc'))
      await waitFor(() => {
        expect(screen.getByTestId('preview-loading')).toBeDefined()
      })
      resolvePreview({ type: 'sheet', sheets: [] })
    })

    it('error state shows retry button', async () => {
      const doc = makeDocument({ id: 'error-doc' })
      mockApi.get.mockImplementation((path: string) => {
        if (path === '/documents') return Promise.resolve([doc] as never)
        if (path === '/documents/error-doc') return Promise.resolve(doc as never)
        if (path === '/documents/error-doc/preview') return Promise.reject(new Error('preview failed'))
        return defaultMockGet(path)
      })
      renderWithRouter()
      await waitFor(() => { expect(screen.getByTestId('doc-item-error-doc')).toBeDefined() })
      fireEvent.click(screen.getByTestId('doc-item-error-doc'))
      await waitFor(() => {
        expect(screen.getByTestId('preview-error')).toBeDefined()
        expect(screen.getByTestId('retry-preview')).toBeDefined()
      })
    })
  })

  // =========================================================================
  // Responsive behavior / layout
  // =========================================================================

  describe('Responsive behavior', () => {
    it('right rail only appears on Generated tab when a doc is selected', async () => {
      const doc = makeDocument({ id: 'rail-doc' })
      mockApi.get.mockImplementation((path: string) => {
        if (path === '/documents') return Promise.resolve([doc] as never)
        if (path === '/documents/rail-doc') return Promise.resolve(doc as never)
        if (path === '/documents/rail-doc/preview') return Promise.resolve({ type: 'sheet', sheets: [] } as never)
        return defaultMockGet(path)
      })
      renderWithRouter()
      // Right rail not visible until a doc is selected
      expect(screen.queryByTestId('right-rail')).toBeNull()
      await waitFor(() => { expect(screen.getByTestId('doc-item-rail-doc')).toBeDefined() })
      fireEvent.click(screen.getByTestId('doc-item-rail-doc'))
      await waitFor(() => {
        expect(screen.getByTestId('right-rail')).toBeDefined()
      })
    })

    it('right rail does not show for Drive tab', () => {
      renderWithRouter('drive')
      expect(screen.queryByTestId('right-rail')).toBeNull()
    })

    it('right rail does not show for Uploads tab', () => {
      renderWithRouter('uploads')
      expect(screen.queryByTestId('right-rail')).toBeNull()
    })

    it('center panel is always present', () => {
      renderWithRouter()
      expect(screen.getByTestId('center-panel')).toBeDefined()
    })

    it('center panel is present for Drive tab', () => {
      renderWithRouter('drive')
      expect(screen.getByTestId('center-panel')).toBeDefined()
    })

    it('center panel is present for Uploads tab', () => {
      renderWithRouter('uploads')
      expect(screen.getByTestId('center-panel')).toBeDefined()
    })

    it('left rail stays visible when a Drive file is open', async () => {
      const driveFiles = [makeDriveFile({ id: 'df-rail', name: 'RailTest.xlsx' })]
      mockApi.get.mockImplementation((path: string) => {
        if (path === '/drive/auth/status') {
          return Promise.resolve({
            authenticated: true, email: 'x@x.com',
            credentials_file_present: true, needs_reauth: false,
          } as never)
        }
        if (path.startsWith('/drive/files')) {
          return Promise.resolve({ files: driveFiles, cached: false } as never)
        }
        return defaultMockGet(path)
      })
      renderWithRouter('drive')
      await waitFor(() => {
        expect(screen.getByTestId('drive-file-df-rail')).toBeDefined()
      })
      fireEvent.click(screen.getByTestId('drive-file-df-rail'))
      await waitFor(() => {
        expect(screen.getByTestId('drive-preview-inline')).toBeDefined()
      })
      // Left rail must still be present
      expect(screen.getByTestId('left-rail')).toBeDefined()
    })

  })
})
