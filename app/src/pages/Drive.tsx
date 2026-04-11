import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import Icon from '../components/Icon';
import TopBar from '../components/TopBar';
import { api } from '../lib/api';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AuthStatus {
  authenticated: boolean;
  email: string | null;
  credentials_file_present: boolean;
  needs_reauth: boolean;
}

interface DriveFile {
  id: string;
  name: string;
  mimeType: string;
  modifiedTime: string;
  iconLink: string;
  webViewLink: string;
  size: string | null;
}

interface FilesResponse {
  files: DriveFile[];
  cached: boolean;
}

interface SyncResponse {
  ok: boolean;
  file_count: number;
  synced_at: number;
}

interface PreviewNotAvailable {
  previewable: false;
  webViewLink: string;
  mimeType: string;
}

interface ThumbnailResponse {
  thumbnailLink: string | null;
  name: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function timeAgo(iso: string | null): string {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return 'yesterday';
  return `${days}d ago`;
}

function syncTimeLabel(ts: number | null): string {
  if (!ts) return 'Never synced';
  return `Last synced ${timeAgo(new Date(ts * 1000).toISOString())}`;
}

const MIME_LABELS: Record<string, string> = {
  'application/vnd.google-apps.document': 'Google Doc',
  'application/vnd.google-apps.presentation': 'Google Slides',
  'application/vnd.google-apps.spreadsheet': 'Google Sheets',
  'application/vnd.google-apps.drawing': 'Google Drawing',
  'application/vnd.google-apps.folder': 'Folder',
  'application/pdf': 'PDF',
};

const MIME_ICONS: Record<string, { icon: string; color: string }> = {
  'application/vnd.google-apps.document': { icon: 'description', color: 'text-blue-400' },
  'application/vnd.google-apps.presentation': { icon: 'slideshow', color: 'text-orange-400' },
  'application/vnd.google-apps.spreadsheet': { icon: 'table_chart', color: 'text-green-400' },
  'application/vnd.google-apps.drawing': { icon: 'brush', color: 'text-purple-400' },
  'application/vnd.google-apps.folder': { icon: 'folder', color: 'text-yellow-400' },
  'application/pdf': { icon: 'picture_as_pdf', color: 'text-red-400' },
};

function mimeIcon(mimeType: string): { icon: string; color: string } {
  return MIME_ICONS[mimeType] ?? { icon: 'insert_drive_file', color: 'text-slate-400' };
}

function mimeLabel(mimeType: string): string {
  return MIME_LABELS[mimeType] ?? 'File';
}

// ---------------------------------------------------------------------------
// Filter types and helpers
// ---------------------------------------------------------------------------

type FileTypeFilter = 'all' | 'docs' | 'slides' | 'sheets' | 'pdfs' | 'images' | 'folders';
type ModifiedFilter = 'all' | 'today' | 'week' | 'month';

const FILE_TYPE_OPTIONS: { value: FileTypeFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'docs', label: 'Docs' },
  { value: 'slides', label: 'Slides' },
  { value: 'sheets', label: 'Sheets' },
  { value: 'pdfs', label: 'PDFs' },
  { value: 'images', label: 'Images' },
  { value: 'folders', label: 'Folders' },
];

const MODIFIED_OPTIONS: { value: ModifiedFilter; label: string }[] = [
  { value: 'all', label: 'All time' },
  { value: 'today', label: 'Today' },
  { value: 'week', label: 'This week' },
  { value: 'month', label: 'This month' },
];

function matchesFileType(file: DriveFile, filter: FileTypeFilter): boolean {
  switch (filter) {
    case 'all':
      return true;
    case 'docs':
      return file.mimeType === 'application/vnd.google-apps.document';
    case 'slides':
      return file.mimeType === 'application/vnd.google-apps.presentation';
    case 'sheets':
      return file.mimeType === 'application/vnd.google-apps.spreadsheet';
    case 'pdfs':
      return file.mimeType === 'application/pdf';
    case 'images':
      return file.mimeType.startsWith('image/');
    case 'folders':
      return file.mimeType === 'application/vnd.google-apps.folder';
    default:
      return true;
  }
}

function matchesModified(file: DriveFile, filter: ModifiedFilter): boolean {
  if (filter === 'all') return true;
  if (!file.modifiedTime) return false;
  const modified = new Date(file.modifiedTime).getTime();
  if (Number.isNaN(modified)) return false;
  const now = Date.now();
  const diff = now - modified;
  const day = 24 * 60 * 60 * 1000;
  switch (filter) {
    case 'today':
      return diff < day;
    case 'week':
      return diff < 7 * day;
    case 'month':
      return diff < 30 * day;
    default:
      return true;
  }
}

// ---------------------------------------------------------------------------
// Credentials file picker (Step 1)
// ---------------------------------------------------------------------------

function CredentialsPicker({ onSaved }: { onSaved: () => void }) {
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const uploadFile = async (file: File) => {
    setUploading(true);
    setError(null);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const resp = await fetch('/api/drive/credentials', {
        method: 'POST',
        body: formData,
      });
      const data = (await resp.json()) as { ok: boolean; error?: string };
      if (data.ok) {
        setSaved(true);
        // Move to Step 2 after a short pause so the user sees the confirmation.
        setTimeout(() => onSaved(), 1200);
      } else {
        setError(data.error ?? 'Something went wrong. Please try again.');
      }
    } catch {
      setError('Could not upload the file. Check your connection and try again.');
    } finally {
      setUploading(false);
    }
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) uploadFile(file);
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) uploadFile(file);
  };

  return (
    <div className="mb-6">
      <p className="text-sm font-medium text-slate-300 mb-3 text-left">
        Step 1: Upload your credentials file
      </p>

      {saved ? (
        <div className="flex items-center gap-2 p-4 bg-green-500/10 border border-green-500/30 rounded-lg text-green-300 text-sm">
          <Icon name="check_circle" size={18} />
          Credentials saved. Moving on...
        </div>
      ) : (
        <>
          <div
            role="button"
            tabIndex={0}
            onClick={() => inputRef.current?.click()}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click(); }}
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            className={`flex flex-col items-center justify-center gap-2 p-8 border-2 border-dashed rounded-xl cursor-pointer transition-colors ${
              dragging
                ? 'border-blue-400 bg-blue-500/10'
                : 'border-slate-600 hover:border-slate-400 bg-slate-800/40'
            }`}
          >
            {uploading ? (
              <>
                <span
                  className="w-5 h-5 border-2 border-slate-500 border-t-blue-400 rounded-full animate-spin"
                  role="status"
                />
                <span className="text-sm text-slate-400">Saving...</span>
              </>
            ) : (
              <>
                <Icon name="upload_file" className="text-3xl text-slate-400" />
                <span className="text-sm text-slate-300">Drop your Google credentials file here</span>
                <span className="text-xs text-slate-500">or click to browse</span>
              </>
            )}
          </div>

          <input
            ref={inputRef}
            type="file"
            accept=".json,application/json"
            className="hidden"
            onChange={handleFileChange}
            aria-label="Select credentials file"
          />

          {error && (
            <div className="mt-3 flex items-start gap-2 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-red-300 text-sm">
              <Icon name="error" size={16} className="flex-shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Connect screen
// ---------------------------------------------------------------------------

function ConnectScreen({
  hasCredentialsFile,
}: {
  hasCredentialsFile: boolean;
}) {
  const [credsSaved, setCredsSaved] = useState(hasCredentialsFile);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showSetupGuide, setShowSetupGuide] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const handleConnect = async () => {
    setLoading(true);
    setError(null);
    try {
      const { url } = await api.get<{ url: string }>('/drive/auth/url');
      window.location.href = url;
    } catch (err: unknown) {
      setLoading(false);
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    }
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] text-center px-4">
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-10 max-w-md w-full">
        <Icon name="cloud" className="text-5xl text-blue-400 mb-4" />
        <h2 className="text-2xl font-bold mb-2">Connect Google Drive</h2>
        <p className="text-slate-400 text-sm mb-6">
          Browse and preview your Docs, Slides, and Sheets right here in myOS.
        </p>

        {!credsSaved && (
          <CredentialsPicker onSaved={() => setCredsSaved(true)} />
        )}

        {credsSaved && (
          <>
            <p className="text-sm font-medium text-slate-300 mb-3 text-left">
              Step 2: Connect your account
            </p>

            {error && (
              <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 mb-4 text-red-300 text-sm">
                {error}
              </div>
            )}

            <button
              onClick={handleConnect}
              disabled={loading}
              className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed rounded-xl text-white font-medium transition-colors"
            >
              {loading ? (
                <>
                  <span
                    className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"
                    role="status"
                  />
                  Waiting for sign-in...
                </>
              ) : (
                <>
                  <Icon name="login" size={18} />
                  Connect your Google account
                </>
              )}
            </button>
          </>
        )}

        <button
          type="button"
          onClick={() => setShowSetupGuide(true)}
          className="inline-flex items-center gap-1 mt-4 text-slate-600 text-xs hover:text-slate-400 underline"
        >
          Setup guide
          <Icon name="help_outline" size={12} />
        </button>

        <p className="text-slate-600 text-xs mt-3">
          myOS can browse, preview, and upload files to a dedicated "myOS" folder in your Drive. It cannot access or modify files you did not upload through myOS.
        </p>
      </div>

      {showSetupGuide && <SetupGuideModal onClose={() => setShowSetupGuide(false)} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Setup guide modal
// ---------------------------------------------------------------------------

function SetupGuideModal({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-slate-900 border border-slate-700 rounded-2xl p-8 max-w-lg w-full max-h-[90vh] overflow-y-auto text-left"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-4">
          <h2 className="text-xl font-bold">Set up your Google credentials</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-slate-500 hover:text-slate-300"
            aria-label="Close"
          >
            <Icon name="close" size={20} />
          </button>
        </div>

        <p className="text-sm text-slate-400 mb-5">
          myOS needs a credentials file from Google so it can connect to your Drive, Calendar, and Gmail. You only have to do this once. Here is how.
        </p>

        <ol className="space-y-4 text-sm text-slate-300">
          <li>
            <div className="font-semibold text-slate-200 mb-1">1. Open Google Cloud Console</div>
            <p className="text-slate-400">
              Go to{' '}
              <a
                href="https://console.cloud.google.com"
                target="_blank"
                rel="noreferrer"
                className="text-blue-400 hover:text-blue-300 underline"
              >
                console.cloud.google.com
              </a>{' '}
              and sign in with the Google account you want to connect. Create a new project if you do not already have one.
            </p>
          </li>

          <li>
            <div className="font-semibold text-slate-200 mb-1">2. Turn on the APIs you want</div>
            <p className="text-slate-400 mb-2">
              In the search bar, find and enable each of these:
            </p>
            <ul className="list-disc list-inside space-y-1 text-slate-400 ml-2">
              <li>Google Drive API</li>
              <li>Google Calendar API</li>
              <li>Gmail API</li>
            </ul>
            <p className="text-slate-500 text-xs mt-2">
              For each one, open it and click the blue "Enable" button. This takes about a minute.
            </p>
          </li>

          <li>
            <div className="font-semibold text-slate-200 mb-1">3. Create OAuth credentials</div>
            <p className="text-slate-400">
              Go to "APIs and Services" then "Credentials". Click "Create Credentials" and pick "OAuth client ID". Choose "Desktop app" as the type, give it any name, and click Create.
            </p>
          </li>

          <li>
            <div className="font-semibold text-slate-200 mb-1">4. Download the JSON file</div>
            <p className="text-slate-400">
              After creating the client, click the download button next to it. You will get a small .json file. Drag that file into the upload box on this page.
            </p>
          </li>
        </ol>

        <div className="mt-6 p-3 bg-slate-800/50 border border-slate-700 rounded-lg">
          <p className="text-xs text-slate-400">
            Your credentials file stays on your computer. myOS never uploads it anywhere.
          </p>
        </div>

        <div className="mt-4 p-3 bg-slate-800/30 border border-slate-700 rounded-lg">
          <p className="text-xs text-slate-300 font-medium mb-1">Want to use Gemini chat too?</p>
          <p className="text-xs text-slate-400">
            Recommended: reuse this same Google Cloud project. Three steps:
          </p>
          <ol className="text-xs text-slate-400 list-decimal ml-5 mt-1 space-y-1">
            <li>
              Enable{' '}
              <a
                href="https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com"
                target="_blank"
                rel="noreferrer"
                className="text-blue-400 hover:text-blue-300 underline"
              >
                "Generative Language API"
              </a>{' '}
              in the API library. It takes about 30 seconds.
            </li>
            <li>Open Credentials and click Create credentials, API key.</li>
            <li>
              Edit the new key and restrict it to "Generative Language API" under API restrictions. It only appears in the dropdown after step 1. Paste the key into Settings under AI Provider.
            </li>
          </ol>
          <p className="text-xs text-slate-500 mt-2">
            Only using Gemini chat and nothing else from Google? Grab a free key at{' '}
            <a
              href="https://aistudio.google.com/apikey"
              target="_blank"
              rel="noreferrer"
              className="text-blue-400 hover:text-blue-300 underline"
            >
              Google AI Studio
            </a>{' '}
            instead. It ties to your personal Google account and is one click.
          </p>
        </div>

        <button
          type="button"
          onClick={onClose}
          className="w-full mt-5 py-3 bg-slate-800 hover:bg-slate-700 rounded-xl font-medium transition-colors"
        >
          Got it
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Preview panel (overlay)
// ---------------------------------------------------------------------------

function PreviewPanel({
  file,
  onClose,
}: {
  file: DriveFile;
  onClose: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [thumbnailUrl, setThumbnailUrl] = useState<string | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [notPreviewable, setNotPreviewable] = useState<PreviewNotAvailable | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingFull, setLoadingFull] = useState(false);
  const objectUrlRef = useRef<string | null>(null);

  // Close on Escape.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  // Revoke object URL on unmount.
  useEffect(() => {
    return () => {
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = null;
      }
    };
  }, [file.id]);

  // Load thumbnail first (fast), fall back to full preview if no thumbnail.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setThumbnailUrl(null);
    setPdfUrl(null);
    setNotPreviewable(null);
    setError(null);
    setLoadingFull(false);

    async function load() {
      try {
        // Try the fast thumbnail endpoint first.
        const thumbResp = await fetch(`/api/drive/files/${encodeURIComponent(file.id)}/thumbnail`);
        if (thumbResp.ok) {
          const thumbData = (await thumbResp.json()) as ThumbnailResponse;
          if (!cancelled && thumbData.thumbnailLink) {
            setThumbnailUrl(thumbData.thumbnailLink);
            setLoading(false);
            return;
          }
        }

        // No thumbnail available, fall back to full preview.
        const resp = await fetch(`/api/drive/files/${encodeURIComponent(file.id)}/preview`);
        if (!resp.ok) {
          throw new Error(`Could not load this file (${resp.status}).`);
        }
        const contentType = resp.headers.get('content-type') ?? '';
        if (contentType.includes('application/json')) {
          const data = (await resp.json()) as PreviewNotAvailable;
          if (!cancelled) setNotPreviewable(data);
        } else {
          const blob = await resp.blob();
          const url = URL.createObjectURL(blob);
          objectUrlRef.current = url;
          if (!cancelled) setPdfUrl(url);
        }
      } catch (err: unknown) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Could not load preview.');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [file.id]);

  const loadFullPreview = async () => {
    setLoadingFull(true);
    try {
      const resp = await fetch(`/api/drive/files/${encodeURIComponent(file.id)}/preview`);
      if (!resp.ok) {
        throw new Error(`Could not load full preview (${resp.status}).`);
      }
      const contentType = resp.headers.get('content-type') ?? '';
      if (contentType.includes('application/json')) {
        const data = (await resp.json()) as PreviewNotAvailable;
        setNotPreviewable(data);
      } else {
        const blob = await resp.blob();
        if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
        const url = URL.createObjectURL(blob);
        objectUrlRef.current = url;
        setPdfUrl(url);
        setThumbnailUrl(null);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not load full preview.');
    } finally {
      setLoadingFull(false);
    }
  };

  const { icon, color } = mimeIcon(file.mimeType);

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
        aria-label={`Preview of ${file.name}`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700 flex-shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <Icon name={icon} className={`text-xl ${color} flex-shrink-0`} />
            <div className="min-w-0">
              <p className="text-sm font-medium text-slate-100 truncate">{file.name}</p>
              <p className="text-[11px] text-slate-500">{mimeLabel(file.mimeType)}</p>
            </div>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0 ml-3">
            {file.webViewLink && (
              <a
                href={file.webViewLink}
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

        {/* Body */}
        <div className="flex-1 overflow-hidden">
          {loading && (
            <div className="flex flex-col items-center justify-center h-full gap-3">
              <div
                className="w-6 h-6 border-2 border-slate-700 border-t-blue-400 rounded-full animate-spin"
                role="status"
                aria-label="Loading preview"
              />
              <p className="text-sm text-slate-500">Preparing preview...</p>
            </div>
          )}

          {!loading && error && (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-8">
              <Icon name="error" className="text-3xl text-red-400" />
              <p className="text-sm text-red-300">{error}</p>
              {file.webViewLink && (
                <a
                  href={file.webViewLink}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm text-white transition-colors"
                >
                  <Icon name="open_in_new" size={16} />
                  Open in Google Drive
                </a>
              )}
            </div>
          )}

          {!loading && notPreviewable && (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-8">
              <Icon name={icon} className={`text-5xl ${color}`} />
              <p className="text-sm text-slate-300">
                This file type can't be previewed here.
              </p>
              {notPreviewable.webViewLink && (
                <a
                  href={notPreviewable.webViewLink}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm text-white transition-colors"
                >
                  <Icon name="open_in_new" size={16} />
                  Open in Google Drive
                </a>
              )}
            </div>
          )}

          {!loading && thumbnailUrl && !pdfUrl && (
            <div className="flex flex-col items-center justify-center h-full gap-4 px-8">
              <img
                src={thumbnailUrl}
                alt={`Thumbnail of ${file.name}`}
                className="max-w-full max-h-[60vh] rounded-lg shadow-lg object-contain"
              />
              <button
                onClick={loadFullPreview}
                disabled={loadingFull}
                className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded-lg text-sm text-white transition-colors"
              >
                {loadingFull ? (
                  <>
                    <span
                      className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"
                      role="status"
                    />
                    Loading full document...
                  </>
                ) : (
                  <>
                    <Icon name="description" size={16} />
                    View full document
                  </>
                )}
              </button>
            </div>
          )}

          {!loading && pdfUrl && (
            <iframe
              src={pdfUrl}
              className="w-full h-full border-0"
              title={`Preview of ${file.name}`}
            />
          )}
        </div>
      </aside>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function Drive() {
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [files, setFiles] = useState<DriveFile[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [filesError, setFilesError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [fileTypeFilter, setFileTypeFilter] = useState<FileTypeFilter>('all');
  const [modifiedFilter, setModifiedFilter] = useState<ModifiedFilter>('all');
  const [previewFile, setPreviewFile] = useState<DriveFile | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [lastSyncedAt, setLastSyncedAt] = useState<number | null>(null);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [connectBanner, setConnectBanner] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  const searchTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const uploadInputRef = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [apiNotEnabled, setApiNotEnabled] = useState(false);

  // Apply all three filters (file type, modified, search) client-side with AND logic.
  const filteredFiles = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return files.filter((file) => {
      if (!matchesFileType(file, fileTypeFilter)) return false;
      if (!matchesModified(file, modifiedFilter)) return false;
      if (needle && !file.name.toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [files, fileTypeFilter, modifiedFilter, search]);

  const hasActiveFilters =
    fileTypeFilter !== 'all' || modifiedFilter !== 'all' || search.trim().length > 0;

  const handleUpload = async (file: File) => {
    setUploading(true);
    setUploadError(null);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const resp = await fetch('/api/drive/files/upload', {
        method: 'POST',
        body: formData,
      });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        throw new Error(data.detail ?? 'Upload failed. Please try again.');
      }
      await fetchFiles(search || undefined);
    } catch (err: unknown) {
      setUploadError(err instanceof Error ? err.message : 'Upload failed. Please try again.');
    } finally {
      setUploading(false);
      if (uploadInputRef.current) uploadInputRef.current.value = '';
    }
  };

  const fetchStatus = useCallback(async () => {
    try {
      const status = await api.get<AuthStatus>('/drive/auth/status');
      setAuthStatus(status);
      return status;
    } catch {
      setAuthStatus({ authenticated: false, email: null, credentials_file_present: false, needs_reauth: false });
      return null;
    }
  }, []);

  const fetchFiles = useCallback(async (q?: string) => {
    setFilesLoading(true);
    setFilesError(null);
    try {
      const path = q ? `/drive/files?q=${encodeURIComponent(q)}` : '/drive/files';
      const res = await api.get<FilesResponse>(path);
      setFiles(res.files ?? []);
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: { api_not_enabled?: boolean } } } })
        ?.response?.data?.detail;
      if (detail && typeof detail === 'object' && detail.api_not_enabled) {
        setApiNotEnabled(true);
      } else {
        setFilesError('Could not load your Drive files. Check your connection and try again.');
      }
    } finally {
      setFilesLoading(false);
    }
  }, []);

  // Check for OAuth callback result in the URL on mount.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const connected = params.get('connected');
    const oauthError = params.get('error');
    if (connected === 'true') {
      setConnectBanner({ type: 'success', message: 'Google Drive connected!' });
      // Clean the URL so a refresh doesn't re-show the banner.
      window.history.replaceState({}, '', window.location.pathname);
    } else if (oauthError) {
      setConnectBanner({ type: 'error', message: 'Could not connect Google Drive. Please try again.' });
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  // Load auth status on mount.
  useEffect(() => {
    fetchStatus().then((status) => {
      if (status?.authenticated) {
        fetchFiles();
      }
    });
  }, [fetchStatus, fetchFiles]);

  // Debounced search.
  useEffect(() => {
    if (searchTimeout.current) clearTimeout(searchTimeout.current);
    if (!authStatus?.authenticated) return;
    searchTimeout.current = setTimeout(() => {
      fetchFiles(search || undefined);
    }, 400);
    return () => {
      if (searchTimeout.current) clearTimeout(searchTimeout.current);
    };
  }, [search, authStatus, fetchFiles]);

  const handleSync = async () => {
    setSyncing(true);
    setSyncError(null);
    try {
      const res = await api.post<SyncResponse>('/drive/sync');
      setLastSyncedAt(res.synced_at);
      await fetchFiles(search || undefined);
    } catch {
      setSyncError('Sync failed. Please try again.');
    } finally {
      setSyncing(false);
    }
  };



  const handleDisconnect = async () => {
    try {
      await api.post('/drive/auth/revoke');
      setAuthStatus({ authenticated: false, email: null, credentials_file_present: true, needs_reauth: false });
      setFiles([]);
      setPreviewFile(null);
    } catch {
      // Best effort.
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <TopBar title="Drive" />

      <div className="pt-20 p-8 max-w-6xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold">Google Drive</h1>
        </div>

        {/* OAuth callback banner */}
        {connectBanner && (
          <div
            className={`flex items-center justify-between gap-3 p-4 rounded-lg mb-6 text-sm ${
              connectBanner.type === 'success'
                ? 'bg-green-500/10 border border-green-500/30 text-green-300'
                : 'bg-red-500/10 border border-red-500/30 text-red-300'
            }`}
          >
            <div className="flex items-center gap-2">
              <Icon name={connectBanner.type === 'success' ? 'check_circle' : 'error'} size={18} />
              <span>{connectBanner.message}</span>
            </div>
            <button
              onClick={() => setConnectBanner(null)}
              className="text-slate-400 hover:text-white"
              aria-label="Dismiss"
            >
              <Icon name="close" size={16} />
            </button>
          </div>
        )}

        {/* API not enabled */}
        {apiNotEnabled && (
          <div className="max-w-md">
            <div className="bg-slate-900/40 border border-amber-800/40 p-8 rounded-2xl">
              <div className="w-12 h-12 rounded-full bg-amber-500/20 flex items-center justify-center mb-4">
                <Icon name="warning" className="text-amber-400" size={24} />
              </div>
              <h2 className="text-xl font-semibold mb-2">Drive API not enabled</h2>
              <p className="text-slate-400 mb-4">
                Your Google Cloud project has the Google Drive API disabled. You need to turn it on once. Reconnecting will not fix this.
              </p>
              <a
                href="https://console.cloud.google.com/apis/library/drive.googleapis.com"
                target="_blank"
                rel="noreferrer"
                className="w-full block text-center py-3 mb-3 bg-blue-600 hover:bg-blue-700 rounded-xl font-medium transition-colors"
              >
                Enable Drive API in Google Cloud
              </a>
              <p className="text-xs text-slate-500 mb-4">
                After clicking Enable on Google's page, wait 1-2 minutes for the change to propagate, then come back and click Retry.
              </p>
              <button
                onClick={() => { setApiNotEnabled(false); fetchFiles() }}
                className="w-full py-3 bg-slate-700 hover:bg-slate-600 rounded-xl font-medium transition-colors"
              >
                Retry
              </button>
            </div>
          </div>
        )}

        {/* Auth loading */}
        {authStatus === null && !apiNotEnabled && (
          <div className="flex items-center gap-3 py-8 text-slate-500">
            <span
              className="w-4 h-4 border-2 border-slate-700 border-t-slate-400 rounded-full animate-spin"
              role="status"
            />
            <span className="text-sm">Checking connection...</span>
          </div>
        )}

        {/* Connect screen */}
        {authStatus !== null && !authStatus.authenticated && !apiNotEnabled && (
          <ConnectScreen
            hasCredentialsFile={authStatus.credentials_file_present}
          />
        )}

        {/* Authenticated view */}
        {authStatus?.authenticated && !apiNotEnabled && (
          <>
            {/* Reconnect banner — shown when drive.file scope is missing */}
            {authStatus.needs_reauth && (
              <div className="flex items-center justify-between gap-3 p-4 rounded-lg mb-5 text-sm bg-amber-500/15 border border-amber-400/60 text-amber-100">
                <div className="flex items-center gap-2">
                  <Icon name="warning" size={18} />
                  <span>
                    Reconnect your Google account to enable file uploads.
                  </span>
                </div>
                <button
                  onClick={async () => {
                    try {
                      const { url } = await api.get<{ url: string }>('/drive/auth/url');
                      window.location.href = url;
                    } catch {
                      // ignore
                    }
                  }}
                  className="flex-shrink-0 px-3 py-1.5 bg-amber-500/30 hover:bg-amber-500/50 border border-amber-400/60 rounded-lg text-amber-50 font-medium text-xs transition-colors"
                >
                  Reconnect
                </button>
              </div>
            )}

            {/* Account + sync bar */}
            <div className="flex items-center justify-between mb-5 flex-wrap gap-3">
              <div className="flex items-center gap-2 text-sm text-slate-400">
                <Icon name="account_circle" className="text-base" />
                <span>{authStatus.email ?? 'Google account connected'}</span>
                <button
                  onClick={handleDisconnect}
                  className="ml-2 text-xs text-slate-600 hover:text-red-400 transition-colors"
                >
                  Disconnect
                </button>
              </div>

              <div className="flex items-center gap-3">
                {syncError && (
                  <span className="text-xs text-red-400">{syncError}</span>
                )}
                {uploadError && (
                  <span className="text-xs text-red-400">{uploadError}</span>
                )}
                <span className="text-xs text-slate-600">
                  {syncTimeLabel(lastSyncedAt)}
                </span>

                {/* Upload file button */}
                <input
                  ref={uploadInputRef}
                  type="file"
                  className="hidden"
                  aria-label="Select file to upload to Drive"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) handleUpload(f);
                  }}
                />
                <button
                  onClick={() => uploadInputRef.current?.click()}
                  disabled={uploading || authStatus.needs_reauth}
                  title={authStatus.needs_reauth ? 'Reconnect your account to enable uploads' : 'Upload a file to Drive'}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600/80 hover:bg-blue-600 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-sm text-white transition-colors border border-blue-500/50"
                >
                  {uploading ? (
                    <>
                      <span
                        className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin"
                        role="status"
                      />
                      Uploading...
                    </>
                  ) : (
                    <>
                      <Icon name="upload" className="text-base" />
                      Upload to Drive
                    </>
                  )}
                </button>

                <button
                  onClick={handleSync}
                  disabled={syncing}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 rounded-lg text-sm text-slate-300 transition-colors border border-slate-700"
                >
                  <Icon
                    name="sync"
                    className={`text-base ${syncing ? 'animate-spin' : ''}`}
                  />
                  {syncing ? 'Syncing...' : 'Sync now'}
                </button>
              </div>
            </div>

            {/* Filter bar: file type pills, modified dropdown, search input */}
            <div className="flex items-center gap-3 mb-4 flex-wrap">
              {/* File type pills */}
              <div className="flex items-center gap-1 bg-slate-900/60 border border-slate-800 rounded-lg p-1 flex-wrap">
                {FILE_TYPE_OPTIONS.map((opt) => {
                  const active = fileTypeFilter === opt.value;
                  return (
                    <button
                      key={opt.value}
                      onClick={() => setFileTypeFilter(opt.value)}
                      className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                        active
                          ? 'bg-blue-600 text-white'
                          : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'
                      }`}
                    >
                      {opt.label}
                    </button>
                  );
                })}
              </div>

              {/* Modified dropdown */}
              <div className="relative">
                <select
                  value={modifiedFilter}
                  onChange={(e) => setModifiedFilter(e.target.value as ModifiedFilter)}
                  className="appearance-none bg-slate-900 border border-slate-800 rounded-lg pl-3 pr-8 py-1.5 text-xs text-slate-300 cursor-pointer focus:outline-none focus:border-blue-500 transition-colors"
                  aria-label="Filter by modified time"
                >
                  {MODIFIED_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
                <Icon
                  name="expand_more"
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none"
                  size={14}
                />
              </div>

              {/* Search input */}
              <div className="relative flex-1 min-w-[200px]">
                <Icon
                  name="search"
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500"
                  size={16}
                />
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search by filename..."
                  className="w-full bg-slate-900 border border-slate-800 rounded-lg pl-9 pr-8 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500 transition-colors"
                />
                {search && (
                  <button
                    onClick={() => setSearch('')}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
                    aria-label="Clear search"
                  >
                    <Icon name="close" size={14} />
                  </button>
                )}
              </div>
            </div>

            {/* File list */}
            {filesLoading && files.length === 0 && (
              <div className="flex items-center gap-3 py-8 text-slate-500">
                <span
                  className="w-4 h-4 border-2 border-slate-700 border-t-slate-400 rounded-full animate-spin"
                  role="status"
                />
                <span className="text-sm">Loading files...</span>
              </div>
            )}

            {filesError && (
              <div className="flex items-center gap-3 p-4 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm mb-4">
                <Icon name="error" className="text-lg" />
                <span>{filesError}</span>
              </div>
            )}

            {!filesLoading && !filesError && files.length === 0 && (
              <div className="text-center py-12 text-slate-500">
                <Icon name="cloud_off" className="text-4xl mb-2" />
                <p>No files found. Try syncing.</p>
                <button
                  onClick={handleSync}
                  className="mt-3 text-sm text-blue-400 hover:text-blue-300"
                >
                  Sync now
                </button>
              </div>
            )}

            {!filesLoading && !filesError && files.length > 0 && filteredFiles.length === 0 && (
              <div className="text-center py-12 text-slate-500">
                <Icon name="filter_alt_off" className="text-4xl mb-2" />
                <p>No files match your filters.</p>
                {hasActiveFilters && (
                  <button
                    onClick={() => {
                      setFileTypeFilter('all');
                      setModifiedFilter('all');
                      setSearch('');
                    }}
                    className="mt-3 text-sm text-blue-400 hover:text-blue-300"
                  >
                    Clear filters
                  </button>
                )}
              </div>
            )}

            {filteredFiles.length > 0 && (
              <div className="flex flex-col gap-1">
                <div className="grid grid-cols-[1fr_120px_auto_80px] gap-4 px-4 py-2 text-[10px] font-bold uppercase tracking-widest text-slate-600">
                  <span>Name</span>
                  <span>Type</span>
                  <span></span>
                  <span className="text-right">Modified</span>
                </div>

                {filteredFiles.map((file) => {
                  const { icon, color } = mimeIcon(file.mimeType);
                  const isSelected = previewFile?.id === file.id;
                  return (
                    <div
                      key={file.id}
                      className={`grid grid-cols-[1fr_120px_auto_80px] gap-4 items-center border rounded-lg px-4 py-3 transition-colors ${
                        isSelected
                          ? 'bg-blue-600/10 border-blue-500/50'
                          : 'bg-slate-900/60 border-slate-800 hover:border-blue-500/30 hover:bg-slate-800/60'
                      }`}
                    >
                      <button
                        onClick={() => setPreviewFile(file)}
                        className="flex items-center gap-3 min-w-0 cursor-pointer text-left"
                      >
                        <Icon name={icon} className={`text-xl ${color} flex-shrink-0`} />
                        <span className="text-sm truncate text-slate-100">{file.name}</span>
                      </button>
                      <span className="text-xs text-slate-400 truncate">
                        {mimeLabel(file.mimeType)}
                      </span>
                      {file.webViewLink ? (
                        <a
                          href={file.webViewLink}
                          target="_blank"
                          rel="noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="flex items-center gap-1 text-xs text-slate-500 hover:text-blue-400 transition-colors whitespace-nowrap"
                          title="Open in Google Drive"
                        >
                          <Icon name="open_in_new" size={12} />
                          Open
                        </a>
                      ) : (
                        <span />
                      )}
                      <span className="text-xs text-slate-500 text-right">
                        {timeAgo(file.modifiedTime)}
                      </span>
                    </div>
                  );
                })}

                <p className="mt-4 text-xs text-slate-600 text-center">
                  {filteredFiles.length} of {files.length} file{files.length === 1 ? '' : 's'}
                </p>
              </div>
            )}
          </>
        )}
      </div>

      {/* Preview overlay */}
      {previewFile && (
        <PreviewPanel
          file={previewFile}
          onClose={() => setPreviewFile(null)}
        />
      )}
    </div>
  );
}
