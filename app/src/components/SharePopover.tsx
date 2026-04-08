import { useState, useEffect, useRef } from 'react';
import Icon from './Icon';
import { api } from '../lib/api';

interface ShareResponse {
  token: string;
  url: string;
  share: {
    expires_in_days: number;
    expires_at: string;
  };
}

interface Props {
  shareType: 'task_list' | 'agent_output' | 'label_view';
  contentIds: string[];
  title: string;
  onClose: () => void;
}

export default function SharePopover({ shareType, contentIds, title, onClose }: Props) {
  const [url, setUrl] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [revoked, setRevoked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.post<ShareResponse>('/shares', {
          share_type: shareType,
          content_ids: contentIds,
          title,
          expires_in_days: 7,
        });
        if (!cancelled) {
          setUrl(res.url);
          setToken(res.token);
        }
      } catch {
        if (!cancelled) {
          setError('Could not create share link. Please try again.');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [shareType, contentIds, title]);

  // Auto-copy once we have the URL
  useEffect(() => {
    if (url && navigator.clipboard) {
      navigator.clipboard.writeText(url).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2500);
      }).catch(() => {});
    }
  }, [url]);

  // Close on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        onClose();
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [onClose]);

  const handleCopy = async () => {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      // fallback: select the text
    }
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
      ref={popoverRef}
      className="absolute right-0 top-full mt-2 w-80 bg-slate-900 border border-slate-700 rounded-xl shadow-xl z-50 p-4"
    >
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Icon name="link" size={16} className="text-blue-400" />
          <span className="text-sm font-semibold text-white">Share link</span>
        </div>
        <button onClick={onClose} className="text-slate-500 hover:text-white transition-colors">
          <Icon name="close" size={16} />
        </button>
      </div>

      {loading && (
        <div className="flex items-center justify-center py-6 text-slate-400 text-sm">
          <Icon name="hourglass_empty" size={16} className="mr-2 animate-spin" />
          Creating link...
        </div>
      )}

      {error && (
        <p className="text-sm text-red-400">{error}</p>
      )}

      {!loading && !error && revoked && (
        <p className="text-sm text-slate-400 text-center py-4">Link revoked.</p>
      )}

      {!loading && !error && !revoked && url && (
        <>
          <div className="flex items-center gap-2 bg-slate-800 rounded-lg px-3 py-2 mb-2">
            <span className="text-xs text-slate-300 truncate flex-1 font-mono">{url}</span>
            <button
              onClick={handleCopy}
              className="shrink-0 text-slate-400 hover:text-blue-400 transition-colors"
              title="Copy link"
            >
              <Icon name={copied ? 'check' : 'content_copy'} size={14} />
            </button>
          </div>

          {copied && (
            <p className="text-xs text-green-400 mb-2">Copied to clipboard!</p>
          )}

          <p className="text-xs text-slate-500 mb-3">Expires in 7 days. Anyone with the link can view this.</p>

          <button
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
  );
}
