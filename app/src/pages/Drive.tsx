import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import Icon from '../components/Icon';
import TopBar from '../components/TopBar';
import { LoadingState, ErrorBanner, EmptyState } from '../components/ui';
import { api, ApiError } from '../lib/api';
import GoogleSetupGuideModal from '../components/GoogleSetupGuideModal';
import DrivePreview from '../components/DrivePreview';

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

// Seed the Drive file list from localStorage so the page paints rows
// immediately on a return visit. The backend has its own 6-hour cache,
// but a cache miss still costs ~600ms round trip to Google on the cold
// path and the JSON is 58 KB so the first-paint benefits from having
// something to render while the network catches up. Same pattern as
// Gmail/Tasks. Needle 316.
const DRIVE_CACHE_KEY = 'myos.driveCache.v1';

function readDriveCache(): DriveFile[] {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return [];
    const raw = window.localStorage.getItem(DRIVE_CACHE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as DriveFile[]) : [];
  } catch {
    return [];
  }
}

function writeDriveCache(files: DriveFile[]) {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return;
    window.localStorage.setItem(DRIVE_CACHE_KEY, JSON.stringify(files));
  } catch {
    // Quota or serialization errors are not fatal.
  }
}

function mimeIcon(mimeType: string): { icon: string; color: string } {
  return MIME_ICONS[mimeType] ?? { icon: 'insert_drive_file', color: 'text-slate-400' };
}

function mimeLabel(mimeType: string): string {
  return MIME_LABELS[mimeType] ?? 'File';
}

// Returns true for file types we can render inside the preview panel.
// Everything else falls back to opening the Drive webViewLink in a new tab.
function isInlinePreviewable(mimeType: string): boolean {
  return (
    mimeType === 'application/vnd.google-apps.document' ||
    mimeType === 'application/vnd.google-apps.presentation' ||
    mimeType === 'application/vnd.google-apps.spreadsheet' ||
    mimeType === 'application/vnd.google-apps.drawing' ||
    mimeType === 'application/pdf' ||
    mimeType.startsWith('image/')
  );
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
        <div className="flex items-center gap-2 p-4 bg-green-500/10 border border-green-500/30 rounded-xl text-green-300 text-sm">
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
              <LoadingState variant="spinner" message="Saving..." />
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
            <div className="mt-3">
              <ErrorBanner message={error} />
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
      <div data-testid="connect-card" className="max-w-md mx-auto bg-slate-900/40 border border-slate-800 p-5 sm:px-8 sm:pb-8 rounded-2xl w-full">
        <div className="p-4 rounded-2xl mb-4 inline-block" style={{ backgroundColor: '#3b82f61a' }}>
          <span className="material-symbols-outlined" style={{ fontSize: '32px', color: '#3b82f6' }}>cloud</span>
        </div>
        <h2 className="text-xl font-bold mb-2">Connect Google Drive</h2>
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
              <div className="mb-4">
                <ErrorBanner message={error} />
              </div>
            )}

            <button
              onClick={handleConnect}
              disabled={loading}
              className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed rounded-xl text-white font-medium transition-colors"
            >
              {loading ? (
                <>
                  <Icon name="progress_activity" size={16} className="animate-spin" />
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

      {showSetupGuide && <GoogleSetupGuideModal onClose={() => setShowSetupGuide(false)} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function Drive() {
  // If we have rows in localStorage the user has connected before, so
  // we optimistically paint the main view as authenticated while the
  // real auth status round trip is in flight. If the real status comes
  // back unauthenticated (very rare: token revoked, cleared storage)
  // we switch to the connect screen. This avoids a 100ms "Checking
  // connection..." spinner on every return visit. Mirrors the pattern
  // used for Gmail/Tasks.
  const hasInitialCache = typeof window !== 'undefined' && readDriveCache().length > 0;
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(
    hasInitialCache
      ? {
          authenticated: true,
          email: null,
          credentials_file_present: true,
          needs_reauth: false,
        }
      : null,
  );
  // Paint rows instantly from localStorage on a return visit.
  const [files, setFiles] = useState<DriveFile[]>(() => readDriveCache());
  const [filesLoading, setFilesLoading] = useState(false);
  const [filesError, setFilesError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [fileTypeFilter, setFileTypeFilter] = useState<FileTypeFilter>('all');
  const [modifiedFilter, setModifiedFilter] = useState<ModifiedFilter>('all');
  const [previewFile, setPreviewFile] = useState<DriveFile | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [lastSyncedAt, setLastSyncedAt] = useState<number | null>(null);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [connectBanner, setConnectBanner] = useState<
    | { type: 'success' | 'error'; message: string }
    | { type: 'redirect_uri_mismatch'; redirectUri: string }
    | null
  >(null);

  const searchTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const uploadInputRef = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [apiNotEnabled, setApiNotEnabled] = useState(false);

  // Undo-delete state: optimistic delete with a 5s grace window.
  const [undoDelete, setUndoDelete] = useState<{
    id: string;
    name: string;
    timer: ReturnType<typeof setTimeout>;
  } | null>(null);

  // In-app error toast for trash failures (replaces window.alert).
  // Auto-dismisses after 6 seconds.
  const [trashError, setTrashError] = useState<string | null>(null);
  useEffect(() => {
    if (!trashError) return;
    const id = setTimeout(() => setTrashError(null), 6000);
    return () => clearTimeout(id);
  }, [trashError]);

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
      const list = res.files ?? [];
      setFiles(list);
      // Persist unfiltered results to localStorage so the next page
      // load can paint instantly. Skip for search queries so a
      // narrowed list does not replace the full cache, and skip on
      // empty results so a transient zero-result fetch does not pin
      // the UI blank on the next visit.
      if (!q && list.length > 0) writeDriveCache(list);
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
    } else if (oauthError === 'redirect_uri_mismatch') {
      setConnectBanner({ type: 'redirect_uri_mismatch', redirectUri: 'https://localhost:8000/api/drive/auth/callback' });
      window.history.replaceState({}, '', window.location.pathname);
    } else if (oauthError) {
      setConnectBanner({ type: 'error', message: 'Could not connect Google Drive. Please try again.' });
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  // Load auth status on mount.
  //
  // Fire status and files in parallel when we already have cached rows
  // (i.e. the user has visited before and is almost certainly still
  // authenticated). The old code waited for the ~100ms status round
  // trip before starting the ~600ms files round trip, which stacked
  // the two latencies. When the user has no cache we still wait on
  // status before kicking off the files fetch so we don't hit Drive
  // for an unauthenticated user. Regression guard for slow page load
  // on return visits.
  useEffect(() => {
    const hasCached = readDriveCache().length > 0;
    if (hasCached) {
      // Speculatively fire both in parallel. If status says we are
      // not authenticated we throw away the files result silently.
      const statusPromise = fetchStatus();
      const filesPromise = fetchFiles();
      statusPromise.then((status) => {
        if (!status?.authenticated) {
          // The page will render the connect flow anyway; the files
          // fetch result is harmless to ignore here.
          return;
        }
        return filesPromise;
      });
      return;
    }
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
      // Clear the localStorage cache too so a different account
      // connecting next does not see the previous account's files.
      try {
        if (typeof window !== 'undefined' && window.localStorage) {
          window.localStorage.removeItem(DRIVE_CACHE_KEY);
        }
      } catch {
        // Ignore storage errors.
      }
    } catch {
      // Best effort.
    }
  };

  // Delete a Drive file with a 5-second undo window. Optimistically removes
  // the file from the local list and shows an Undo toast. If the user hits
  // Undo the entry is restored via a sync. If they wait out the timer, the
  // backend trash call fires and the list refreshes.
  const deleteDriveFile = (id: string, name: string) => {
    // Commit any previous pending delete immediately.
    if (undoDelete) {
      clearTimeout(undoDelete.timer);
      api
        .delete(`/drive/files/${encodeURIComponent(undoDelete.id)}`)
        .catch(() => {});
    }

    // Optimistically remove from the local list.
    setFiles((prev) => prev.filter((f) => f.id !== id));
    if (previewFile?.id === id) setPreviewFile(null);

    const timer = setTimeout(async () => {
      try {
        await api.delete(`/drive/files/${encodeURIComponent(id)}`);
        await fetchFiles(search || undefined);
      } catch (err) {
        // Surface the real reason from the API when available.
        let reason = 'The file may be protected or the API may be down.';
        if (err instanceof ApiError) {
          const detail = err.response.data.detail;
          if (detail && typeof detail === 'object' && 'message' in detail) {
            reason = String((detail as { message: string }).message);
          } else if (typeof detail === 'string' && detail) {
            reason = detail;
          }
        }
        setTrashError(`Could not move "${name}" to trash. ${reason}`);
        await fetchFiles(search || undefined);
      }
      setUndoDelete(null);
    }, 5000);
    setUndoDelete({ id, name, timer });
  };

  const handleUndoDriveDelete = () => {
    if (!undoDelete) return;
    clearTimeout(undoDelete.timer);
    fetchFiles(search || undefined);
    setUndoDelete(null);
  };

  return (
    <div className="min-h-dvh bg-slate-950 text-white">
      <TopBar title="Drive" />

      <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8 max-w-6xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-xl sm:text-2xl font-bold">Google Drive</h1>
        </div>

        {/* OAuth callback banner */}
        {connectBanner && connectBanner.type === 'redirect_uri_mismatch' && (
          <div className="flex items-start justify-between gap-3 p-4 rounded-xl mb-6 text-sm bg-red-500/10 border border-red-500/30 text-red-300">
            <div className="flex items-start gap-2">
              <Icon name="error" size={18} className="flex-shrink-0 mt-0.5" />
              <div>
                <p className="font-medium mb-1">Google refused the connection because this URL is not registered:</p>
                <code className="block bg-slate-900/60 px-2 py-1 rounded text-xs font-mono text-red-200 mb-2">
                  {connectBanner.redirectUri}
                </code>
                <p className="mb-2">
                  Add it to <strong>Authorized redirect URIs</strong> in Google Cloud Console, then click Connect again.
                </p>
                <a
                  href="https://console.cloud.google.com/apis/credentials"
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-red-200 hover:text-white underline"
                >
                  Open Google Cloud Console credentials
                  <Icon name="open_in_new" size={12} />
                </a>
              </div>
            </div>
            <button
              onClick={() => setConnectBanner(null)}
              className="text-slate-400 hover:text-white flex-shrink-0"
              aria-label="Dismiss"
            >
              <Icon name="close" size={16} />
            </button>
          </div>
        )}
        {connectBanner && connectBanner.type !== 'redirect_uri_mismatch' && (
          <div
            className={`flex items-center justify-between gap-3 p-4 rounded-xl mb-6 text-sm ${
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
          <LoadingState variant="spinner" message="Checking connection..." />
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
            {/* Reconnect banner: shown when drive.file scope is missing */}
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
              <LoadingState variant="spinner" message="Loading files..." />
            )}

            {filesError && (
              <div className="mb-4">
                <ErrorBanner
                  message={filesError}
                  action={{ label: 'Retry', onClick: () => fetchFiles(search || undefined) }}
                />
              </div>
            )}

            {!filesLoading && !filesError && files.length === 0 && (
              <EmptyState
                icon="cloud_off"
                title="No files found"
                description="Click Sync to pull your latest files from Drive."
                action={{ label: 'Sync now', onClick: handleSync }}
              />
            )}

            {!filesLoading && !filesError && files.length > 0 && filteredFiles.length === 0 && (
              <EmptyState
                icon="filter_alt_off"
                title="No files match your filters"
                action={hasActiveFilters ? {
                  label: 'Clear filters',
                  onClick: () => {
                    setFileTypeFilter('all');
                    setModifiedFilter('all');
                    setSearch('');
                  },
                } : undefined}
              />
            )}

            {filteredFiles.length > 0 && (
              <div className="flex flex-col gap-1">
                <div className="grid grid-cols-[1fr_120px_auto_80px_36px] gap-4 px-4 py-2 text-[10px] font-bold uppercase tracking-widest text-slate-600">
                  <span>Name</span>
                  <span>Type</span>
                  <span></span>
                  <span className="text-right">Modified</span>
                  <span></span>
                </div>

                {filteredFiles.map((file) => {
                  const { icon, color } = mimeIcon(file.mimeType);
                  const isSelected = previewFile?.id === file.id;
                  const previewable = isInlinePreviewable(file.mimeType);
                  return (
                    <div
                      key={file.id}
                      className={`grid grid-cols-[1fr_120px_auto_80px_36px] gap-4 items-center border rounded-xl px-4 py-3 transition-colors ${
                        isSelected
                          ? 'bg-blue-600/10 border-blue-500/50'
                          : 'bg-slate-900/60 border-slate-800 hover:border-blue-500/30 hover:bg-slate-800/60'
                      }`}
                    >
                      <button
                        onClick={() => {
                          if (previewable) {
                            setPreviewFile(file);
                          } else if (file.webViewLink) {
                            window.open(file.webViewLink, '_blank', 'noopener,noreferrer');
                          }
                        }}
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
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          deleteDriveFile(file.id, file.name);
                        }}
                        className="flex items-center justify-center w-7 h-7 rounded-md text-slate-600 hover:text-red-400 hover:bg-red-500/10 transition-colors"
                        title={`Delete ${file.name}`}
                        data-testid={`drive-delete-${file.id}`}
                      >
                        <Icon name="delete" size={16} />
                      </button>
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

      {/* Drive preview panel */}
      {previewFile && (
        <DrivePreview
          fileId={previewFile.id}
          name={previewFile.name}
          mimeType={previewFile.mimeType}
          webViewLink={previewFile.webViewLink || undefined}
          onClose={() => setPreviewFile(null)}
        />
      )}

      {undoDelete && (
        <div
          className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 bg-slate-800 border border-slate-700 text-sm text-slate-200 px-4 py-3 rounded-xl shadow-lg"
          data-testid="undo-delete-drive-toast"
        >
          <span>Moved to trash.</span>
          <button
            onClick={handleUndoDriveDelete}
            className="font-medium text-blue-400 hover:text-blue-300"
            data-testid="undo-delete-drive-button"
          >
            Undo
          </button>
        </div>
      )}

      {trashError && (
        <div
          role="status"
          data-testid="drive-trash-error-toast"
          className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-start gap-3 bg-red-950 border border-red-800 text-red-200 text-sm px-4 py-3 rounded-xl shadow-lg max-w-md"
        >
          <Icon name="error" size={18} className="text-red-400 mt-0.5 flex-shrink-0" />
          <span className="flex-1">{trashError}</span>
          <button
            onClick={() => setTrashError(null)}
            className="text-slate-500 hover:text-slate-300"
            aria-label="Dismiss"
          >
            <Icon name="close" size={14} />
          </button>
        </div>
      )}
    </div>
  );
}
