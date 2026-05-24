import { useState, useEffect } from 'react';
import Icon from './Icon';
import { api } from '../lib/api';

interface FileShareResponse {
  token: string;
  url: string;
}

interface Props {
  filePath: string;
  fileName: string;
  onClose: () => void;
}

export default function FileShareModal({ filePath, fileName, onClose }: Props) {
  const [url, setUrl] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [revoked, setRevoked] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.post<FileShareResponse>(
          `/files/share?path=${encodeURIComponent(filePath)}`
        );
        if (!cancelled) {
          const fullUrl = res.url.startsWith('http')
            ? res.url
            : `${window.location.origin}${res.url}`;
          setUrl(fullUrl);
          setToken(res.token);
        }
      } catch {
        if (!cancelled) setError('Could not create share link. Please try again.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [filePath]);

  useEffect(() => {
    if (url && navigator.clipboard) {
      navigator.clipboard.writeText(url).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2500);
      }).catch(() => {});
    }
  }, [url]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const handleCopy = async () => {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {}
  };

  const handleRevoke = async () => {
    if (!token) return;
    setRevoking(true);
    try {
      await api.delete(`/shares/${token}`);
      setRevoked(true);
    } catch {
      setError('Could not revoke the link.');
    } finally {
      setRevoking(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Share ${fileName}`}
      data-testid="file-share-modal"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="w-full max-w-sm mx-4 bg-slate-900 border border-slate-700 rounded-xl shadow-2xl p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Icon name="link" size={16} className="text-blue-400" />
            <span className="text-sm font-semibold text-white">Share file</span>
          </div>
          <button
            data-testid="file-share-modal-close"
            onClick={onClose}
            className="text-slate-500 hover:text-white transition-colors"
            aria-label="Close"
          >
            <Icon name="close" size={16} />
          </button>
        </div>

        <p className="text-xs text-slate-400 truncate mb-4" title={fileName}>{fileName}</p>

        {loading && (
          <div className="flex items-center justify-center py-6 text-slate-400 text-sm">
            <Icon name="hourglass_empty" size={16} className="mr-2 animate-spin" />
            Creating link...
          </div>
        )}

        {error && <p className="text-sm text-red-400">{error}</p>}

        {!loading && !error && revoked && (
          <p className="text-sm text-slate-400 text-center py-4">Link revoked.</p>
        )}

        {!loading && !error && !revoked && url && (
          <>
            <div className="flex items-center gap-2 bg-slate-800 rounded-lg px-3 py-2 mb-2">
              <span
                data-testid="file-share-url"
                className="text-xs text-slate-300 truncate flex-1 font-mono"
              >
                {url}
              </span>
              <button
                data-testid="file-share-copy"
                onClick={handleCopy}
                className="shrink-0 text-slate-400 hover:text-blue-400 transition-colors"
                title="Copy link"
              >
                <Icon name={copied ? 'check' : 'content_copy'} size={14} />
              </button>
            </div>

            {copied && (
              <p data-testid="file-share-copied" className="text-xs text-green-400 mb-2">
                Copied to clipboard!
              </p>
            )}

            <p className="text-xs text-slate-500 mb-3">
              Expires in 7 days. Anyone with the link can view this file.
            </p>

            <button
              data-testid="file-share-revoke"
              onClick={handleRevoke}
              disabled={revoking}
              className="flex items-center gap-1.5 text-xs text-red-400 hover:text-red-300 transition-colors disabled:opacity-50"
            >
              <Icon name="link_off" size={14} />
              {revoking ? 'Revoking...' : 'Revoke link'}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
