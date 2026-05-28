import { useState, useEffect, useRef } from 'react';
import TopBar from '../components/TopBar';
import { api } from '../lib/api';

interface Source {
  id: string;
  title: string;
  type: string;
  size: number;
  tags: string[];
  source_url: string;
  upload_time: string;
}

function formatSize(bytes: number): string {
  if (bytes === 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function typeLabel(type: string): string {
  const labels: Record<string, string> = {
    pdf: 'PDF',
    md: 'Markdown',
    txt: 'Text',
    docx: 'Word doc',
    url: 'Web page',
  };
  return labels[type] ?? type.toUpperCase();
}

export default function Library() {
  const [sources, setSources] = useState<Source[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showUpload, setShowUpload] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  async function loadSources() {
    try {
      const data = await api.get('/sources') as { sources?: Source[] };
      setSources(data.sources ?? []);
    } catch {
      setError('Could not load library.');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadSources(); }, []);

  async function handleDelete(id: string) {
    setDeleting(true);
    try {
      await api.delete(`/sources/${id}`);
      setSources(prev => prev.filter(s => s.id !== id));
    } catch {
      setError('Delete failed.');
    } finally {
      setDeleting(false);
      setDeleteId(null);
    }
  }

  return (
    <div className="flex flex-col h-full">
      <TopBar title="Library" />

      <div className="flex-1 overflow-auto p-6">
        {error && (
          <div className="mb-4 p-3 rounded bg-red-50 text-red-700 text-sm">{error}</div>
        )}

        <div className="mb-4 flex justify-end">
          <button
            onClick={() => setShowUpload(true)}
            className="px-4 py-2 rounded bg-blue-600 text-white text-sm font-medium hover:bg-blue-700"
          >
            Upload file or URL
          </button>
        </div>

        {loading ? (
          <p className="text-sm text-gray-600">Loading…</p>
        ) : sources.length === 0 ? (
          <div className="text-center py-16 text-gray-600">
            <p className="text-base font-medium mb-1">No sources yet</p>
            <p className="text-sm">Upload a brand guide, ICP doc, or past posts and your agents will read them automatically.</p>
          </div>
        ) : (
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b text-left text-gray-600 text-xs uppercase tracking-wide">
                <th className="pb-2 pr-4 font-medium">Name</th>
                <th className="pb-2 pr-4 font-medium">Type</th>
                <th className="pb-2 pr-4 font-medium">Size</th>
                <th className="pb-2 pr-4 font-medium">Tags</th>
                <th className="pb-2 font-medium">Added</th>
                <th className="pb-2" />
              </tr>
            </thead>
            <tbody>
              {sources.map(s => (
                <tr key={s.id} className="border-b hover:bg-gray-50">
                  <td className="py-3 pr-4 font-medium text-gray-800 max-w-xs truncate">{s.title}</td>
                  <td className="py-3 pr-4 text-gray-600">{typeLabel(s.type)}</td>
                  <td className="py-3 pr-4 text-gray-600">{formatSize(s.size)}</td>
                  <td className="py-3 pr-4">
                    <div className="flex flex-wrap gap-1">
                      {(s.tags ?? []).map(t => (
                        <span key={t} className="px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 text-xs">{t}</span>
                      ))}
                    </div>
                  </td>
                  <td className="py-3 text-gray-600 text-xs whitespace-nowrap">
                    {new Date(s.upload_time).toLocaleDateString()}
                  </td>
                  <td className="py-3 pl-2">
                    {deleteId === s.id ? (
                      <span className="flex gap-2 text-xs">
                        <button
                          onClick={() => handleDelete(s.id)}
                          disabled={deleting}
                          className="text-red-600 hover:underline disabled:opacity-50"
                        >
                          {deleting ? 'Removing…' : 'Confirm'}
                        </button>
                        <button onClick={() => setDeleteId(null)} className="text-gray-600 hover:underline">Cancel</button>
                      </span>
                    ) : (
                      <button
                        onClick={() => setDeleteId(s.id)}
                        className="text-gray-600 hover:text-red-500 text-xs"
                        aria-label="Delete source"
                      >
                        Remove
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showUpload && (
        <UploadModal
          onClose={() => setShowUpload(false)}
          onUploaded={() => { setShowUpload(false); loadSources(); }}
        />
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Upload modal
// ---------------------------------------------------------------------------

interface UploadModalProps {
  onClose: () => void;
  onUploaded: () => void;
}

function UploadModal({ onClose, onUploaded }: UploadModalProps) {
  const [mode, setMode] = useState<'file' | 'url'>('file');
  const [tags, setTags] = useState('brand');
  const [url, setUrl] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setUploading(true);
    try {
      if (mode === 'file') {
        if (!file) { setError('Pick a file first.'); setUploading(false); return; }
        const form = new FormData();
        form.append('file', file);
        form.append('tags', tags);
        await fetch('/api/sources', { method: 'POST', body: form });
      } else {
        if (!url.trim()) { setError('Enter a URL.'); setUploading(false); return; }
        await api.post('/sources', { url: url.trim(), tags: tags.split(',').map(t => t.trim()).filter(Boolean) });
      }
      onUploaded();
    } catch {
      setError('Upload failed. Try again.');
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" data-testid="upload-modal">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
        <h2 className="text-base font-semibold mb-4">Add to library</h2>

        <div className="flex gap-2 mb-4">
          <button
            onClick={() => setMode('file')}
            className={`px-3 py-1 rounded text-sm ${mode === 'file' ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-600'}`}
          >
            File
          </button>
          <button
            onClick={() => setMode('url')}
            className={`px-3 py-1 rounded text-sm ${mode === 'url' ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-600'}`}
          >
            Web page URL
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {mode === 'file' ? (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">File (PDF, MD, DOCX, TXT)</label>
              <input
                ref={fileRef}
                type="file"
                accept=".pdf,.md,.docx,.txt"
                onChange={e => setFile(e.target.files?.[0] ?? null)}
                className="block w-full text-sm text-gray-600"
                data-testid="file-input"
              />
            </div>
          ) : (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">URL</label>
              <input
                type="url"
                value={url}
                onChange={e => setUrl(e.target.value)}
                placeholder="https://example.com/brand-guide"
                className="w-full border rounded px-3 py-2 text-sm"
                data-testid="url-input"
              />
            </div>
          )}

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Tags <span className="text-gray-600 font-normal">(comma-separated, used by agents to find the right files)</span>
            </label>
            <input
              type="text"
              value={tags}
              onChange={e => setTags(e.target.value)}
              placeholder="brand, icp, blog"
              className="w-full border rounded px-3 py-2 text-sm"
              data-testid="tags-input"
            />
          </div>

          {error && <p className="text-red-600 text-sm">{error}</p>}

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} className="px-4 py-2 rounded text-sm text-gray-600 hover:bg-gray-100">
              Cancel
            </button>
            <button
              type="submit"
              disabled={uploading}
              className="px-4 py-2 rounded bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
              data-testid="upload-submit"
            >
              {uploading ? 'Uploading…' : 'Add to library'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
