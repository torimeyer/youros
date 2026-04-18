import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import DrivePreview, { DrivePreviewData } from './DrivePreview';

// jsdom does not provide window.matchMedia. Provide a minimal stub so
// responsive-aware components do not crash.
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
});

function imageData(overrides: Partial<DrivePreviewData> = {}): DrivePreviewData {
  return {
    kind: 'image',
    name: 'photo.png',
    mime_type: 'image/png',
    thumbnail_url: 'https://img.example.com/photo=s800',
    export_url: '/api/drive/files/img-1/preview',
    web_view_link: 'https://drive.google.com/file/d/img-1',
    sample: null,
    ...overrides,
  };
}

describe('DrivePreview', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders an <img> for image files', () => {
    render(
      <DrivePreview
        fileId="img-1"
        name="photo.png"
        mimeType="image/png"
        webViewLink="https://drive.google.com/file/d/img-1"
        onClose={() => {}}
        initialData={imageData()}
      />
    );
    const img = screen.getByTestId('drive-preview-image') as HTMLImageElement;
    expect(img).toBeInTheDocument();
    expect(img.getAttribute('loading')).toBe('lazy');
    expect(img.src).toContain('=s800');
  });

  it('opens a full-size image modal when the thumbnail is clicked', async () => {
    render(
      <DrivePreview
        fileId="img-1"
        name="photo.png"
        mimeType="image/png"
        onClose={() => {}}
        initialData={imageData()}
      />
    );
    fireEvent.click(screen.getByTestId('drive-preview-image'));
    await waitFor(() => {
      expect(screen.getByTestId('drive-preview-full-image')).toBeInTheDocument();
    });
  });

  it('renders an <iframe> for PDF files', () => {
    render(
      <DrivePreview
        fileId="pdf-1"
        name="report.pdf"
        mimeType="application/pdf"
        onClose={() => {}}
        initialData={{
          kind: 'pdf',
          name: 'report.pdf',
          mime_type: 'application/pdf',
          thumbnail_url: null,
          export_url: '/api/drive/files/pdf-1/preview',
          web_view_link: '',
          sample: null,
        }}
      />
    );
    const iframe = screen.getByTestId('drive-preview-pdf') as HTMLIFrameElement;
    expect(iframe).toBeInTheDocument();
    expect(iframe.src).toContain('/api/drive/files/pdf-1/preview');
  });

  it('renders a table with headers and rows for sheets', () => {
    render(
      <DrivePreview
        fileId="sheet-1"
        name="Budget"
        mimeType="application/vnd.google-apps.spreadsheet"
        onClose={() => {}}
        initialData={{
          kind: 'sheet',
          name: 'Budget',
          mime_type: 'application/vnd.google-apps.spreadsheet',
          thumbnail_url: null,
          export_url: '/api/drive/files/sheet-1/preview',
          web_view_link: '',
          sample: {
            headers: ['Month', 'Revenue', 'Cost'],
            rows: [
              ['Jan', '100', '50'],
              ['Feb', '200', '80'],
            ],
            truncated: false,
          },
        }}
      />
    );
    const table = screen.getByTestId('drive-preview-sheet');
    expect(table).toBeInTheDocument();
    expect(screen.getByText('Month')).toBeInTheDocument();
    expect(screen.getByText('Revenue')).toBeInTheDocument();
    expect(screen.getByText('Jan')).toBeInTheDocument();
    expect(screen.getByText('Feb')).toBeInTheDocument();
  });

  it('renders a horizontal strip of slide thumbnails for slides', () => {
    render(
      <DrivePreview
        fileId="slides-1"
        name="Deck"
        mimeType="application/vnd.google-apps.presentation"
        onClose={() => {}}
        initialData={{
          kind: 'slides',
          name: 'Deck',
          mime_type: 'application/vnd.google-apps.presentation',
          thumbnail_url: null,
          export_url: '/api/drive/files/slides-1/preview',
          web_view_link: '',
          sample: {
            slides: [
              { slide_id: 'p1', thumbnail_url: 'https://img/p1' },
              { slide_id: 'p2', thumbnail_url: 'https://img/p2' },
            ],
            truncated: false,
          },
        }}
      />
    );
    const strip = screen.getByTestId('drive-preview-slides');
    expect(strip).toBeInTheDocument();
    const imgs = strip.querySelectorAll('img');
    expect(imgs.length).toBe(2);
    expect((imgs[0] as HTMLImageElement).src).toContain('https://img/p1');
    expect(screen.getByText('Slide 1')).toBeInTheDocument();
    expect(screen.getByText('Slide 2')).toBeInTheDocument();
  });

  it('renders formatted headings and paragraphs for docs', () => {
    render(
      <DrivePreview
        fileId="doc-1"
        name="Plan"
        mimeType="application/vnd.google-apps.document"
        onClose={() => {}}
        initialData={{
          kind: 'doc',
          name: 'Plan',
          mime_type: 'application/vnd.google-apps.document',
          thumbnail_url: null,
          export_url: '/api/drive/files/doc-1/preview',
          web_view_link: '',
          sample: {
            blocks: [
              { type: 'heading', text: 'Project Plan' },
              { type: 'paragraph', text: 'This is the intro paragraph.' },
              { type: 'heading', text: 'Next Steps' },
            ],
            truncated: false,
          },
        }}
      />
    );
    const doc = screen.getByTestId('drive-preview-doc');
    expect(doc).toBeInTheDocument();
    // Headings use <h2> so they render with higher visual weight.
    const h2s = doc.querySelectorAll('h2');
    expect(h2s.length).toBe(2);
    expect(h2s[0].textContent).toBe('Project Plan');
    expect(h2s[1].textContent).toBe('Next Steps');
    expect(screen.getByText('This is the intro paragraph.')).toBeInTheDocument();
  });

  it('handles missing thumbnail_url gracefully for images by falling back to export_url', () => {
    render(
      <DrivePreview
        fileId="img-2"
        name="photo.png"
        mimeType="image/png"
        onClose={() => {}}
        initialData={imageData({
          thumbnail_url: null,
          export_url: '/api/drive/files/img-2/preview',
        })}
      />
    );
    const img = screen.getByTestId('drive-preview-image') as HTMLImageElement;
    expect(img.src).toContain('/api/drive/files/img-2/preview');
  });

  it('shows an Open in Google Drive link for unknown file types', () => {
    render(
      <DrivePreview
        fileId="zip-1"
        name="archive.zip"
        mimeType="application/zip"
        webViewLink="https://drive.google.com/file/d/zip-1"
        onClose={() => {}}
        initialData={{
          kind: 'other',
          name: 'archive.zip',
          mime_type: 'application/zip',
          thumbnail_url: null,
          export_url: '/api/drive/files/zip-1/preview',
          web_view_link: 'https://drive.google.com/file/d/zip-1',
          sample: null,
        }}
      />
    );
    expect(screen.getByText(/cannot be previewed/i)).toBeInTheDocument();
    const link = screen.getByRole('link', { name: /open in google drive/i });
    expect((link as HTMLAnchorElement).href).toBe(
      'https://drive.google.com/file/d/zip-1'
    );
  });

  it('calls onClose when the close button is clicked', () => {
    const onClose = vi.fn();
    render(
      <DrivePreview
        fileId="img-1"
        name="photo.png"
        mimeType="image/png"
        onClose={onClose}
        initialData={imageData()}
      />
    );
    fireEvent.click(screen.getByRole('button', { name: /close preview/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it('fetches structured preview data when initialData is not provided', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => imageData(),
    } as unknown as Response);
    global.fetch = fetchMock;

    render(
      <DrivePreview
        fileId="img-1"
        name="photo.png"
        mimeType="image/png"
        onClose={() => {}}
      />
    );
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/api/drive/preview/img-1');
      expect(screen.getByTestId('drive-preview-image')).toBeInTheDocument();
    });
  });
});
