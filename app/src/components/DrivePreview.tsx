import { useEffect, useState } from 'react';
import Icon from './Icon';
import { LoadingState } from './ui';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type DrivePreviewKind = 'image' | 'pdf' | 'sheet' | 'slides' | 'doc' | 'other';

export interface DrivePreviewData {
  kind: DrivePreviewKind;
  name: string;
  mime_type: string;
  thumbnail_url: string | null;
  export_url: string;
  web_view_link: string;
  sample: SheetSample | DocSample | SlidesSample | null;
}

export interface SheetSample {
  headers: string[];
  rows: string[][];
  truncated: boolean;
}

export interface DocBlock {
  type: 'heading' | 'paragraph';
  text: string;
}

export interface DocSample {
  blocks: DocBlock[];
  truncated: boolean;
}

export interface SlidesSample {
  slides: { slide_id: string; thumbnail_url: string }[];
  truncated: boolean;
}

export interface DrivePreviewProps {
  fileId: string;
  name: string;
  mimeType: string;
  webViewLink?: string;
  onClose: () => void;
  initialData?: DrivePreviewData;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function DrivePreview({
  fileId,
  name,
  mimeType,
  webViewLink,
  onClose,
  initialData,
}: DrivePreviewProps) {
  const [data, setData] = useState<DrivePreviewData | null>(initialData ?? null);
  const [loading, setLoading] = useState(!initialData);
  const [error, setError] = useState<string | null>(null);
  const [fullImage, setFullImage] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [importStatus, setImportStatus] = useState<'idle' | 'success' | 'error'>('idle');
  const [importMessage, setImportMessage] = useState<string | null>(null);

  // Close on Escape.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (fullImage) {
          setFullImage(null);
        } else {
          onClose();
        }
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose, fullImage]);

  // Fetch structured preview data.
  useEffect(() => {
    if (initialData) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const resp = await fetch(`/api/drive/preview/${encodeURIComponent(fileId)}`);
        if (!resp.ok) {
          throw new Error(`Could not load preview (${resp.status}).`);
        }
        const json = (await resp.json()) as DrivePreviewData;
        if (!cancelled) setData(json);
      } catch (err: unknown) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Could not load preview.');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [fileId, initialData]);

  const handleSaveToMyOS = async () => {
    setImporting(true);
    setImportStatus('idle');
    setImportMessage(null);
    try {
      const resp = await fetch('/api/files/import-from-drive', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ drive_file_id: fileId }),
      });
      const json = await resp.json();
      if (!resp.ok) {
        throw new Error(json.detail ?? 'Import failed.');
      }
      setImportStatus('success');
      setImportMessage(`Saved to myOS.`);
    } catch (err: unknown) {
      setImportStatus('error');
      setImportMessage(err instanceof Error ? err.message : 'Could not save.');
    } finally {
      setImporting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/50 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      data-testid="drive-preview-overlay"
    >
      <aside
        className="bg-slate-900 border-l border-slate-700 shadow-2xl w-full max-w-3xl h-full flex flex-col"
        role="dialog"
        aria-label={`Preview of ${name}`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700 flex-shrink-0">
          <div className="min-w-0">
            <p className="text-sm font-medium text-slate-100 truncate">{name}</p>
            <p className="text-[11px] text-slate-500">{prettyMime(mimeType)}</p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0 ml-3">
            {/* Save to myOS button */}
            <button
              onClick={handleSaveToMyOS}
              disabled={importing || importStatus === 'success'}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors border ${
                importStatus === 'success'
                  ? 'bg-green-500/20 border-green-500/40 text-green-300 cursor-default'
                  : importStatus === 'error'
                  ? 'bg-red-500/20 border-red-500/40 text-red-300 hover:bg-red-500/30'
                  : 'bg-blue-600/80 hover:bg-blue-600 border-blue-500/50 text-white disabled:opacity-50 disabled:cursor-not-allowed'
              }`}
              data-testid="drive-preview-save-to-myos"
              title={importMessage ?? 'Download this file to myOS'}
            >
              {importing ? (
                <>
                  <span className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Saving...
                </>
              ) : importStatus === 'success' ? (
                <>
                  <Icon name="check_circle" size={14} />
                  Saved
                </>
              ) : (
                <>
                  <Icon name="download" size={14} />
                  Save to myOS
                </>
              )}
            </button>

            {webViewLink && (
              <a
                href={webViewLink}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg text-xs text-slate-300 transition-colors border border-slate-600"
              >
                <Icon name="open_in_new" size={14} />
                Open in Drive
              </a>
            )}
            <button
              onClick={onClose}
              className="flex items-center justify-center w-8 h-8 hover:bg-slate-800 rounded-lg text-slate-400 hover:text-white transition-colors"
              title="Close preview"
              aria-label="Close preview"
            >
              <Icon name="close" size={18} />
            </button>
          </div>
        </div>

        {/* Import error banner */}
        {importStatus === 'error' && importMessage && (
          <div className="px-5 py-2 bg-red-500/10 border-b border-red-500/20 text-xs text-red-300 flex items-center gap-2">
            <Icon name="error" size={14} />
            {importMessage}
          </div>
        )}

        {/* Body */}
        <div className="flex-1 overflow-auto">
          {loading && (
            <div className="flex items-center justify-center h-full">
              <LoadingState variant="spinner" message="Loading preview..." />
            </div>
          )}

          {!loading && error && (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-8">
              <Icon name="error" className="text-3xl text-red-400" />
              <p className="text-sm text-red-300">{error}</p>
            </div>
          )}

          {!loading && !error && data && (
            <PreviewBody
              data={data}
              onImageClick={(url) => setFullImage(url)}
            />
          )}
        </div>
      </aside>

      {/* Full image modal */}
      {fullImage && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/90 p-6"
          role="dialog"
          aria-label="Full size image"
          data-testid="drive-preview-full-image"
          onClick={() => setFullImage(null)}
        >
          <img
            src={fullImage}
            alt={name}
            className="max-w-full max-h-full object-contain"
          />
          <button
            onClick={(e) => {
              e.stopPropagation();
              setFullImage(null);
            }}
            className="absolute top-6 right-6 w-10 h-10 rounded-full bg-slate-800/80 text-white hover:bg-slate-700 flex items-center justify-center"
            aria-label="Close full image"
          >
            <Icon name="close" size={20} />
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Renderer dispatch
// ---------------------------------------------------------------------------

function PreviewBody({
  data,
  onImageClick,
}: {
  data: DrivePreviewData;
  onImageClick: (url: string) => void;
}) {
  switch (data.kind) {
    case 'image':
      return <ImagePreview data={data} onImageClick={onImageClick} />;
    case 'pdf':
      return <PdfPreview data={data} />;
    case 'sheet':
      return <SheetPreview data={data} />;
    case 'slides':
      return <SlidesPreview data={data} />;
    case 'doc':
      return <DocPreview data={data} />;
    default:
      return <OtherPreview data={data} />;
  }
}

// ---------------------------------------------------------------------------
// Image renderer
// ---------------------------------------------------------------------------

function ImagePreview({
  data,
  onImageClick,
}: {
  data: DrivePreviewData;
  onImageClick: (url: string) => void;
}) {
  const src = data.thumbnail_url ?? data.export_url;
  return (
    <div className="flex flex-col items-center justify-center h-full p-6 gap-3">
      <button
        onClick={() => onImageClick(data.export_url)}
        className="block focus:outline-none focus:ring-2 focus:ring-blue-500 rounded-lg"
        title="Click to view full size"
      >
        <img
          src={src}
          alt={data.name}
          loading="lazy"
          className="max-w-full max-h-[75vh] rounded-lg shadow-lg object-contain cursor-zoom-in"
          data-testid="drive-preview-image"
        />
      </button>
      <p className="text-xs text-slate-500">Click the image to view full size.</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PDF renderer
// ---------------------------------------------------------------------------

function PdfPreview({ data }: { data: DrivePreviewData }) {
  const [iframeError, setIframeError] = useState(false);
  if (iframeError) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-center p-8">
        <p className="text-slate-400 text-sm">Could not load PDF in browser.</p>
        {data.export_url && (
          <a
            href={data.export_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-slate-400 hover:text-white underline"
          >
            Open in Drive
          </a>
        )}
      </div>
    );
  }
  return (
    <iframe
      src={data.export_url}
      className="w-full h-full border-0"
      title={`Preview of ${data.name}`}
      data-testid="drive-preview-pdf"
      onError={() => setIframeError(true)}
    />
  );
}

// ---------------------------------------------------------------------------
// Sheet renderer
// ---------------------------------------------------------------------------

function SheetPreview({ data }: { data: DrivePreviewData }) {
  const sample = data.sample as SheetSample | null;
  if (!sample || (!sample.headers.length && !sample.rows.length)) {
    return (
      <FallbackToExport
        data={data}
        message="This sheet is empty or could not be read."
      />
    );
  }
  return (
    <div className="p-4 overflow-auto">
      <table
        className="w-full text-xs border-collapse"
        data-testid="drive-preview-sheet"
      >
        <thead>
          <tr className="bg-slate-800">
            {sample.headers.map((h, i) => (
              <th
                key={i}
                className="border border-slate-700 px-2 py-1.5 text-left font-semibold text-slate-200"
              >
                {h || `Column ${i + 1}`}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sample.rows.map((row, ri) => (
            <tr key={ri} className={ri % 2 === 0 ? 'bg-slate-900/60' : 'bg-slate-900/30'}>
              {row.map((cell, ci) => (
                <td
                  key={ci}
                  className="border border-slate-800 px-2 py-1.5 text-slate-300 align-top"
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {sample.truncated && (
        <p className="mt-3 text-xs text-slate-500">
          Showing the first rows and columns. Open in Drive to see everything.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Slides renderer
// ---------------------------------------------------------------------------

function SlidesPreview({ data }: { data: DrivePreviewData }) {
  const sample = data.sample as SlidesSample | null;
  const slides = sample?.slides ?? [];
  const [activeSlide, setActiveSlide] = useState(0);
  if (slides.length === 0) {
    return <FallbackToExport data={data} message="No slides to show yet." />;
  }
  const current = slides[activeSlide] ?? slides[0];
  return (
    <div
      className="p-4 flex flex-col gap-3"
      data-testid="drive-preview-slides"
    >
      <div className="flex items-center justify-between">
        <button
          data-testid="slide-prev"
          onClick={() => setActiveSlide((i) => Math.max(0, i - 1))}
          disabled={activeSlide === 0}
          className="p-1 text-slate-400 hover:text-white disabled:opacity-30 transition-opacity"
        >
          <span className="material-symbols-outlined text-[20px]">chevron_left</span>
        </button>
        <span className="text-xs text-slate-500">Slide {activeSlide + 1} of {slides.length}</span>
        <button
          data-testid="slide-next"
          onClick={() => setActiveSlide((i) => Math.min(slides.length - 1, i + 1))}
          disabled={activeSlide === slides.length - 1}
          className="p-1 text-slate-400 hover:text-white disabled:opacity-30 transition-opacity"
        >
          <span className="material-symbols-outlined text-[20px]">chevron_right</span>
        </button>
      </div>
      {current && (
        <div className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden shadow">
          <img
            src={current.thumbnail_url}
            alt={`Slide ${activeSlide + 1}`}
            className="w-full h-auto object-cover"
          />
        </div>
      )}
      {sample?.truncated && (
        <p className="text-xs text-slate-500">
          Showing the first slides. Open in Drive to see the full deck.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Doc renderer
// ---------------------------------------------------------------------------

function DocPreview({ data }: { data: DrivePreviewData }) {
  const sample = data.sample as DocSample | null;
  const blocks = sample?.blocks ?? [];
  if (blocks.length === 0) {
    return <FallbackToExport data={data} message="This doc is empty." />;
  }
  return (
    <div
      className="p-6 text-slate-200 max-w-3xl mx-auto"
      data-testid="drive-preview-doc"
    >
      {blocks.map((b, i) => {
        if (b.type === 'heading') {
          return (
            <h2
              key={i}
              className="text-lg font-bold text-slate-100 mt-4 mb-2 first:mt-0"
            >
              {b.text}
            </h2>
          );
        }
        return (
          <p key={i} className="text-sm leading-relaxed text-slate-300 mb-3 whitespace-pre-wrap">
            {b.text}
          </p>
        );
      })}
      {sample?.truncated && (
        <p className="mt-4 text-xs text-slate-500">
          Showing the first part of the doc. Open in Drive to read the rest.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Fallbacks
// ---------------------------------------------------------------------------

function OtherPreview({ data }: { data: DrivePreviewData }) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-8">
      <Icon name="insert_drive_file" className="text-5xl text-slate-400" />
      <p className="text-sm text-slate-300">This file type cannot be previewed here.</p>
      {data.web_view_link && (
        <a
          href={data.web_view_link}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm text-white transition-colors"
        >
          <Icon name="open_in_new" size={16} />
          Open in Google Drive
        </a>
      )}
    </div>
  );
}

function FallbackToExport({
  data,
  message,
}: {
  data: DrivePreviewData;
  message: string;
}) {
  if (data.thumbnail_url) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 p-6">
        <img
          src={data.thumbnail_url}
          alt={data.name}
          className="max-w-full max-h-[60vh] rounded-lg shadow-lg object-contain"
        />
        <p className="text-xs text-slate-500">{message}</p>
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-8">
      <Icon name="info" className="text-3xl text-slate-400" />
      <p className="text-sm text-slate-300">{message}</p>
      {data.web_view_link && (
        <a
          href={data.web_view_link}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm text-white transition-colors"
        >
          <Icon name="open_in_new" size={16} />
          Open in Google Drive
        </a>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const MIME_LABELS: Record<string, string> = {
  'application/vnd.google-apps.document': 'Google Doc',
  'application/vnd.google-apps.presentation': 'Google Slides',
  'application/vnd.google-apps.spreadsheet': 'Google Sheets',
  'application/vnd.google-apps.drawing': 'Google Drawing',
  'application/vnd.google-apps.folder': 'Folder',
  'application/pdf': 'PDF',
};

function prettyMime(mime: string): string {
  if (MIME_LABELS[mime]) return MIME_LABELS[mime];
  if (mime.startsWith('image/')) return 'Image';
  return 'File';
}
