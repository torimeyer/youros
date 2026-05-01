import { useState, useEffect } from "react";
import { api } from "../lib/api";
import Icon from "./Icon";

interface DriveLink {
  linked: boolean;
  doc_id?: string;
  web_view_link?: string;
}

interface SpecDriveSyncProps {
  specPath: string;
}

export function SpecDriveSync({ specPath }: SpecDriveSyncProps) {
  const [driveLink, setDriveLink] = useState<DriveLink | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastSynced, setLastSynced] = useState<Date | null>(null);

  useEffect(() => {
    fetchDriveLink();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [specPath]);

  const fetchDriveLink = async () => {
    try {
      const encoded = encodeURIComponent(specPath);
      const data = await api.get<DriveLink>(`/specs/${encoded}/drive-link`);
      setDriveLink(data);
    } catch {
      setDriveLink({ linked: false });
    }
  };

  const handlePublish = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setLoading(true);
    setError(null);
    try {
      const encoded = encodeURIComponent(specPath);
      const data = await api.post<{ doc_id: string; web_view_link: string }>(
        `/specs/${encoded}/publish`,
        {}
      );
      setDriveLink({ linked: true, ...data });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Could not publish to Drive.";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const handleSync = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setLoading(true);
    setError(null);
    try {
      const encoded = encodeURIComponent(specPath);
      const data = await api.post<{ doc_id: string; web_view_link: string }>(
        `/specs/${encoded}/sync`,
        {}
      );
      setDriveLink({ linked: true, ...data });
      setLastSynced(new Date());
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Could not sync to Drive.";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const handlePull = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setLoading(true);
    setError(null);
    try {
      const encoded = encodeURIComponent(specPath);
      await api.post(`/specs/${encoded}/pull`, {});
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Could not pull from Drive.";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  if (!driveLink) return null;

  return (
    <div className="flex flex-wrap items-center gap-2 mt-2" data-testid="spec-drive-sync">
      {driveLink.linked && driveLink.web_view_link && (
        <a
          href={driveLink.web_view_link}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => e.stopPropagation()}
          aria-label="Open in Google Drive"
          className="flex items-center gap-1.5 text-xs text-blue-400 hover:text-blue-300 border border-blue-500/30 rounded-lg px-3 py-1.5 transition-colors"
          data-testid="drive-link"
        >
          <Icon name="open_in_new" size={14} />
          Open in Drive
        </a>
      )}

      {!driveLink.linked && (
        <button
          type="button"
          onClick={handlePublish}
          disabled={loading}
          aria-label="Publish this spec to Google Drive"
          className="border border-slate-600 text-slate-400 hover:text-slate-200 text-xs font-medium px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
          data-testid="publish-to-drive-button"
        >
          <Icon name="upload" size={14} />
          {loading ? "Publishing..." : "Publish to Drive"}
        </button>
      )}

      {driveLink.linked && (
        <>
          <button
            type="button"
            onClick={handleSync}
            disabled={loading}
            aria-label="Push local changes to Google Drive"
            className="border border-slate-600 text-slate-400 hover:text-slate-200 text-xs font-medium px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
            data-testid="sync-to-drive-button"
          >
            <Icon name="sync" size={14} />
            {loading ? "Syncing..." : "Sync to Drive"}
          </button>
          <button
            type="button"
            onClick={handlePull}
            disabled={loading}
            aria-label="Import changes from Google Drive"
            className="border border-slate-600 text-slate-400 hover:text-slate-200 text-xs font-medium px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
            data-testid="pull-from-drive-button"
          >
            <Icon name="cloud_download" size={14} />
            {loading ? "Pulling..." : "Pull from Drive"}
          </button>
        </>
      )}

      {lastSynced && (
        <span className="text-xs text-slate-500" data-testid="last-synced">
          Synced {lastSynced.toLocaleTimeString()}
        </span>
      )}

      {error && (
        <span className="text-xs text-red-400" data-testid="drive-sync-error">
          {error}
        </span>
      )}
    </div>
  );
}
