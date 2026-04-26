import { useState, useEffect, useCallback } from 'react';
import Icon from '../components/Icon';
import TopBar from '../components/TopBar';
import ConfirmModal from '../components/ConfirmModal';
import QuickLook from '../components/QuickLook';
import ProvenanceChip, { type Provenance } from '../components/ProvenanceChip';
import { useConfirm } from '../hooks/useConfirm';
import { api, ApiError } from '../lib/api';

// --- Types ---

interface Project {
  name: string;
  path: string;
  has_git: boolean;
  file_count: number;
  last_modified: string | null;
  description: string | null;
  project_type: string;
}

interface ProjectsResponse {
  projects: Project[];
}

interface BrowseEntry {
  name: string;
  kind: 'folder' | 'file';
  path: string;
  item_count: number | null;
  size: number | null;
  size_display: string;
  last_modified: string | null;
}

interface Breadcrumb {
  name: string;
  path: string;
}

interface BrowseResponse {
  current_path: string;
  parent_path: string;
  breadcrumbs: Breadcrumb[];
  entries: BrowseEntry[];
}

interface RecentDoc {
  name: string;
  path: string;
  size: number;
  size_display: string;
  last_modified: string | null;
  snippet: string;
}

interface RecentDocsResponse {
  files: RecentDoc[];
}

interface TimelineFile {
  id: string;
  name: string;
  path: string;
  size: number;
  modified_at: string;
  mime_type: string;
  provenance: Provenance | null;
}

interface TimelineResponse {
  files: TimelineFile[];
}

// --- Helpers ---

const typeConfig: Record<string, { icon: string; color: string; label: string }> = {
  node: { icon: 'javascript', color: 'text-yellow-400', label: 'Node.js' },
  python: { icon: 'code', color: 'text-blue-400', label: 'Python' },
  rust: { icon: 'memory', color: 'text-orange-400', label: 'Rust' },
  go: { icon: 'speed', color: 'text-cyan-400', label: 'Go' },
  folder: { icon: 'folder', color: 'text-slate-400', label: 'Folder' },
};

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

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function agentArtifactTitle(name: string): { title: string; timeLabel: string | null } {
  const stem = name.endsWith('.md') ? name.slice(0, -3) : name;
  const match = stem.match(/^(.+)-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2})$/);
  if (!match) return { title: name, timeLabel: null };
  const slug = match[1];
  const tsRaw = match[2];
  const words = slug.split('-').filter(Boolean).map((w) => {
    if (w.length <= 3 && /^[a-z]+$/.test(w)) return w.toUpperCase();
    return w.charAt(0).toUpperCase() + w.slice(1);
  });
  const title = words.join(' ') || name;
  const isoCompat = tsRaw.replace(/T(\d{2})-(\d{2})$/, 'T$1:$2:00Z');
  const d = new Date(isoCompat);
  if (Number.isNaN(d.getTime())) return { title, timeLabel: null };
  const timeLabel = d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
  return { title, timeLabel };
}

function fileIcon(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() ?? '';
  const map: Record<string, string> = {
    ts: 'code', tsx: 'code', js: 'javascript', jsx: 'javascript',
    py: 'code', rs: 'memory', go: 'speed', md: 'article',
    json: 'data_object', toml: 'settings', yaml: 'settings', yml: 'settings',
    html: 'web', css: 'palette', svg: 'image', png: 'image',
    jpg: 'image', jpeg: 'image', gif: 'image', txt: 'description',
    lock: 'lock', sh: 'terminal', zsh: 'terminal', bash: 'terminal',
  };
  return map[ext] || 'description';
}

// --- Component ---

export default function Files() {
  // 'timeline' is the default view; 'files' shows the project explorer.
  const [view, setView] = useState<'timeline' | 'files'>('timeline');

  // Timeline state
  const [timelineFiles, setTimelineFiles] = useState<TimelineFile[]>([]);
  const [timelineLoading, setTimelineLoading] = useState(true);
  const [timelineError, setTimelineError] = useState<string | null>(null);

  // QuickLook state (shared across both views)
  const [quickLookTarget, setQuickLookTarget] = useState<{ path: string; mime: string } | null>(null);

  // null means we're at the root (project list), a string means we're browsing a directory
  const [currentPath, setCurrentPath] = useState<string | null>(null);

  // Project list state (root view)
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [projectsError, setProjectsError] = useState<string | null>(null);

  // Browse state (directory view)
  const [browseData, setBrowseData] = useState<BrowseResponse | null>(null);
  const [browseLoading, setBrowseLoading] = useState(false);
  const [browseError, setBrowseError] = useState<string | null>(null);
  const [browseForbidden, setBrowseForbidden] = useState(false);

  // Preview state for files-view side pane (pre-QuickLook)
  const [previewEntry, setPreviewEntry] = useState<BrowseEntry | null>(null);

  // Recent docs state
  const [recentDocs, setRecentDocs] = useState<RecentDoc[]>([]);
  const [recentDocsLoading, setRecentDocsLoading] = useState(true);

  // Transient toast for delete feedback
  const [deleteToast, setDeleteToast] = useState<
    { kind: 'success' | 'error'; message: string } | null
  >(null);

  const { confirm, confirmProps } = useConfirm();

  useEffect(() => {
    if (!deleteToast) return;
    const id = setTimeout(() => setDeleteToast(null), 4000);
    return () => clearTimeout(id);
  }, [deleteToast]);

  // --- Timeline fetch ---

  const fetchTimeline = useCallback(async () => {
    setTimelineLoading(true);
    setTimelineError(null);
    try {
      const res = await api.get<TimelineResponse>('/files/timeline?limit=50');
      setTimelineFiles(res.files ?? []);
    } catch {
      setTimelineError('Could not load files. Make sure the API is running.');
    } finally {
      setTimelineLoading(false);
    }
  }, []);

  // --- Files view fetches ---

  const fetchRecentDocs = useCallback(async () => {
    setRecentDocsLoading(true);
    try {
      const res = await api.get<RecentDocsResponse>('/docs/recent?limit=8');
      setRecentDocs(res.files ?? []);
    } catch {
      setRecentDocs([]);
    } finally {
      setRecentDocsLoading(false);
    }
  }, []);

  const fetchProjects = useCallback(async () => {
    setProjectsLoading(true);
    setProjectsError(null);
    try {
      const res = await api.get<ProjectsResponse>('/projects');
      setProjects(res.projects);
    } catch {
      setProjectsError('Could not load projects. Make sure the API is running.');
    } finally {
      setProjectsLoading(false);
    }
  }, []);

  const fetchDirectory = useCallback(async (path: string) => {
    setBrowseLoading(true);
    setBrowseError(null);
    setBrowseForbidden(false);
    setBrowseData(null);
    try {
      const res = await api.get<BrowseResponse>(`/projects/browse?path=${encodeURIComponent(path)}`);
      setBrowseData(res);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setBrowseForbidden(true);
      } else {
        setBrowseError('Could not load this folder. It may not exist or the API may be down.');
      }
    } finally {
      setBrowseLoading(false);
    }
  }, []);

  // Load data on mount and whenever view/path changes
  useEffect(() => {
    if (view === 'timeline') {
      fetchTimeline();
    } else if (currentPath === null) {
      fetchProjects();
      fetchRecentDocs();
    } else {
      fetchDirectory(currentPath);
    }
  }, [view, currentPath, fetchTimeline, fetchProjects, fetchRecentDocs, fetchDirectory]);

  const refresh = () => {
    if (view === 'timeline') {
      fetchTimeline();
    } else if (currentPath === null) {
      fetchProjects();
      fetchRecentDocs();
    } else {
      fetchDirectory(currentPath);
    }
  };

  // Navigation helpers (files view)
  const navigateTo = (path: string) => setCurrentPath(path);
  const navigateToRoot = () => { setCurrentPath(null); setBrowseData(null); };
  const navigateUp = () => {
    if (browseData && browseData.parent_path) {
      setCurrentPath(browseData.parent_path);
    } else {
      navigateToRoot();
    }
  };

  const openPreview = (entry: BrowseEntry) => setPreviewEntry(entry);
  const closePreview = () => setPreviewEntry(null);

  const deleteRecentDoc = async (doc: RecentDoc) => {
    const ok = await confirm({
      title: `Delete ${doc.name}?`,
      message: 'This cannot be undone.',
      confirmLabel: 'Delete',
      cancelLabel: 'Cancel',
      danger: true,
    });
    if (!ok) return;
    const previous = recentDocs;
    setRecentDocs((curr) => curr.filter((d) => d.path !== doc.path));
    try {
      await api.delete(`/docs/recent?path=${encodeURIComponent(doc.path)}`);
      setDeleteToast({ kind: 'success', message: `Deleted ${doc.name}` });
      fetchRecentDocs();
    } catch {
      setRecentDocs(previous);
      setDeleteToast({
        kind: 'error',
        message: `Could not delete ${doc.name}. Try again, or remove the file manually from the folder.`,
      });
    }
  };

  // --- Render ---

  return (
    <div className="min-h-dvh bg-slate-950 text-white">
      <TopBar title="Files" />

      <div className="pt-16 px-4 pb-4 sm:pt-20 sm:p-8 max-w-6xl mx-auto">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
          <div className="flex items-center gap-3">
            <h1 className="text-xl sm:text-2xl font-bold">Files</h1>
          </div>
          <div className="flex items-center gap-2">
            <button
              data-testid="files-view-toggle"
              onClick={() => {
                setView((v) => v === 'timeline' ? 'files' : 'timeline');
                setCurrentPath(null);
                setBrowseData(null);
              }}
              className="flex items-center gap-2 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm text-slate-300 transition-colors border border-slate-700"
            >
              <Icon name={view === 'timeline' ? 'folder' : 'history'} className="text-base" />
              {view === 'timeline' ? 'Projects' : 'Timeline'}
            </button>
            <button
              onClick={refresh}
              className="flex items-center gap-2 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm text-slate-300 transition-colors border border-slate-700"
            >
              <Icon name="refresh" className="text-base" />
              Refresh
            </button>
          </div>
        </div>

        {/* Timeline view (default) */}
        {view === 'timeline' && (
          <>
            {timelineLoading && (
              <p className="text-sm text-slate-500 py-4">Loading files...</p>
            )}

            {timelineError && !timelineLoading && (
              <div className="flex items-center gap-3 p-4 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm mb-4">
                <Icon name="error" className="text-lg" />
                <span>{timelineError}</span>
              </div>
            )}

            {!timelineLoading && !timelineError && timelineFiles.length === 0 && (
              <div className="text-center py-12 text-slate-500">
                <Icon name="folder_open" className="text-4xl mb-2" />
                <p>No files yet. Run an agent or upload one.</p>
              </div>
            )}

            {!timelineLoading && timelineFiles.length > 0 && (
              <div className="flex flex-col gap-1">
                {timelineFiles.map((file) => (
                  <button
                    key={file.id}
                    data-testid={`files-timeline-row-${file.id}`}
                    onClick={() => setQuickLookTarget({ path: file.path, mime: file.mime_type })}
                    className="flex items-center gap-4 bg-slate-900/40 border border-slate-800/60 rounded-lg px-4 py-3 hover:border-blue-500/40 hover:bg-slate-800/40 transition-colors text-left w-full"
                  >
                    <Icon name={fileIcon(file.name)} className="text-xl text-slate-400 flex-shrink-0" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-slate-200 truncate">{file.name}</p>
                      <ProvenanceChip provenance={file.provenance} />
                    </div>
                    <div className="text-right flex-shrink-0">
                      <p className="text-xs text-slate-500">{formatBytes(file.size)}</p>
                      <p className="text-xs text-slate-600">{timeAgo(file.modified_at)}</p>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </>
        )}

        {/* Files view: project explorer + recent docs */}
        {view === 'files' && (
          <>
            {/* Breadcrumb navigation (shown when inside a directory) */}
            {currentPath !== null && (
              <div className="flex items-center gap-1 mb-4 text-sm flex-wrap">
                <button
                  onClick={navigateUp}
                  className="flex items-center gap-1 px-2 py-1 text-slate-400 hover:text-white hover:bg-slate-800 rounded transition-colors mr-2"
                  title="Go up one level"
                >
                  <Icon name="arrow_back" size={16} />
                  Back
                </button>

                <button
                  onClick={navigateToRoot}
                  className="text-blue-400 hover:text-blue-300 transition-colors px-1"
                >
                  Projects
                </button>

                {browseData?.breadcrumbs.map((crumb, i) => (
                  <span key={crumb.path} className="flex items-center gap-1">
                    <Icon name="chevron_right" size={14} className="text-slate-600" />
                    {i === (browseData.breadcrumbs.length - 1) ? (
                      <span className="text-slate-200 font-medium px-1">{crumb.name}</span>
                    ) : (
                      <button
                        onClick={() => navigateTo(crumb.path)}
                        className="text-blue-400 hover:text-blue-300 transition-colors px-1"
                      >
                        {crumb.name}
                      </button>
                    )}
                  </span>
                ))}
              </div>
            )}

            {/* Recent Documents (root view only) */}
            {currentPath === null && !recentDocsLoading && recentDocs.length > 0 && (
              <div className="mb-8">
                <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3 flex items-center gap-2">
                  <Icon name="schedule" className="text-base text-slate-500" />
                  Recent Documents
                </h2>
                <div className="flex flex-col gap-1">
                  {recentDocs.map((doc) => {
                    const friendly = agentArtifactTitle(doc.name);
                    return (
                      <div
                        key={doc.path}
                        className="group grid grid-cols-[1fr_80px_80px_32px] gap-4 items-center bg-slate-900/40 border border-slate-800/60 rounded-lg px-4 py-2.5 hover:border-blue-500/40 hover:bg-slate-800/40 transition-colors w-full"
                        data-testid={`recent-doc-row-${doc.path}`}
                      >
                        <button
                          onClick={() => openPreview({
                            name: doc.name,
                            kind: 'file',
                            path: doc.path,
                            item_count: null,
                            size: doc.size,
                            size_display: doc.size_display,
                            last_modified: doc.last_modified,
                          })}
                          className="min-w-0 text-left"
                        >
                          <div className="flex items-center gap-2">
                            <Icon name="article" className="text-base text-blue-400 flex-shrink-0" />
                            <span className="text-sm text-slate-200 truncate">{friendly.title}</span>
                            {friendly.timeLabel && (
                              <span className="text-[11px] text-slate-600 truncate hidden sm:inline">{friendly.timeLabel}</span>
                            )}
                          </div>
                          {doc.snippet && (
                            <p className="text-[11px] text-slate-500 truncate mt-0.5 ml-6">{doc.snippet}</p>
                          )}
                        </button>
                        <span className="text-xs text-slate-500">{doc.size_display}</span>
                        <span className="text-xs text-slate-500 text-right">{timeAgo(doc.last_modified)}</span>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            deleteRecentDoc(doc);
                          }}
                          className="flex items-center justify-center w-7 h-7 rounded-md text-slate-500 hover:text-red-400 hover:bg-red-500/10 transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100"
                          title="Delete"
                          aria-label={`Delete ${doc.name}`}
                          data-testid={`recent-doc-delete-${doc.path}`}
                        >
                          <Icon name="delete" className="text-base" />
                        </button>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Root view: project cards */}
            {currentPath === null && (
              <>
                {projectsLoading && projects.length === 0 && (
                  <p className="text-sm text-slate-500 py-4">Loading projects...</p>
                )}

                {projectsError && (
                  <div className="flex items-center gap-3 p-4 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm mb-4">
                    <Icon name="error" className="text-lg" />
                    <span>{projectsError}</span>
                  </div>
                )}

                {!projectsLoading && !projectsError && projects.length === 0 && (
                  <div className="text-center py-12 text-slate-500">
                    <Icon name="folder_off" className="text-4xl mb-2" />
                    <p>No projects found.</p>
                  </div>
                )}

                {projects.length > 0 && (
                  <div className="flex flex-col gap-1">
                    <div className="grid grid-cols-[1fr_80px_60px_80px] gap-4 px-4 py-2 text-[10px] font-bold uppercase tracking-widest text-slate-600">
                      <span>Name</span>
                      <span>Type</span>
                      <span>Items</span>
                      <span className="text-right">Modified</span>
                    </div>

                    {projects.map((project) => {
                      const cfg = typeConfig[project.project_type] || typeConfig.folder;
                      return (
                        <button
                          key={project.name}
                          onClick={() => navigateTo(project.name)}
                          className="grid grid-cols-[1fr_80px_60px_80px] gap-4 items-center bg-slate-900/60 border border-slate-800 rounded-lg px-4 py-3 hover:border-blue-500/50 hover:bg-slate-800/60 transition-colors cursor-pointer text-left w-full"
                        >
                          <div className="flex items-center gap-3 min-w-0">
                            <Icon name={cfg.icon} className={`text-xl ${cfg.color}`} />
                            <div className="min-w-0">
                              <p className="text-sm font-medium truncate">{project.name}</p>
                              {project.description && (
                                <p className="text-[11px] text-slate-500 truncate">{project.description}</p>
                              )}
                            </div>
                            {project.has_git && (
                              <span className="text-[10px] text-green-400 bg-green-500/10 px-1.5 py-0.5 rounded font-medium flex-shrink-0">
                                git
                              </span>
                            )}
                          </div>
                          <span className="text-xs text-slate-400">{cfg.label}</span>
                          <span className="text-xs text-slate-500">{project.file_count}</span>
                          <span className="text-xs text-slate-500 text-right">{timeAgo(project.last_modified)}</span>
                        </button>
                      );
                    })}
                  </div>
                )}

                {projects.length > 0 && (
                  <p className="mt-6 text-xs text-slate-600 text-center">
                    Click a project to browse its files
                  </p>
                )}
              </>
            )}

            {/* Directory browser view */}
            {currentPath !== null && (
              <>
                {browseLoading && !browseData && (
                  <p className="text-sm text-slate-500 py-4">Loading folder contents...</p>
                )}

                {browseError && (
                  <div className="flex items-center gap-3 p-4 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm mb-4">
                    <Icon name="error" className="text-lg" />
                    <span>{browseError}</span>
                  </div>
                )}

                {browseForbidden && !browseLoading && (
                  <div
                    className="text-center py-12 text-slate-500"
                    data-testid="files-forbidden-empty-state"
                  >
                    <Icon name="lock" className="text-4xl mb-2" />
                    <p className="text-slate-300 text-sm font-medium mb-1">
                      This folder can't be browsed.
                    </p>
                    <p className="text-xs text-slate-500 max-w-md mx-auto">
                      Files only shows folders inside your workspace and
                      ~/.myos/files. Pick a project from the list to browse its
                      contents.
                    </p>
                  </div>
                )}

                {browseData && browseData.entries.length === 0 && !browseLoading && (
                  <div className="text-center py-12 text-slate-500">
                    <Icon name="folder_open" className="text-4xl mb-2" />
                    <p>This folder is empty.</p>
                  </div>
                )}

                {browseData && browseData.entries.length > 0 && (
                  <div className="flex flex-col gap-1">
                    <div className="grid grid-cols-[1fr_100px_80px] gap-4 px-4 py-2 text-[10px] font-bold uppercase tracking-widest text-slate-600">
                      <span>Name</span>
                      <span>Size</span>
                      <span className="text-right">Modified</span>
                    </div>

                    {browseData.entries.map((entry) => {
                      const isFolder = entry.kind === 'folder';
                      return (
                        <button
                          key={entry.name}
                          onClick={() => {
                            if (isFolder) {
                              navigateTo(entry.path);
                            } else {
                              openPreview(entry);
                            }
                          }}
                          className={`grid grid-cols-[1fr_100px_80px] gap-4 items-center border rounded-lg px-4 py-3 transition-colors cursor-pointer text-left w-full ${
                            isFolder
                              ? 'bg-slate-900/60 border-slate-800 hover:border-blue-500/50 hover:bg-slate-800/60'
                              : 'bg-slate-900/40 border-slate-800/60 hover:border-slate-700 hover:bg-slate-800/40'
                          }`}
                        >
                          <div className="flex items-center gap-3 min-w-0">
                            {isFolder ? (
                              <Icon name="folder" className="text-xl text-blue-400" />
                            ) : (
                              <Icon name={fileIcon(entry.name)} className="text-xl text-slate-400" />
                            )}
                            <span className={`text-sm truncate ${isFolder ? 'font-medium text-slate-100' : 'text-slate-300'}`}>
                              {entry.name}
                            </span>
                            {isFolder && (
                              <Icon name="chevron_right" size={16} className="text-slate-600 ml-auto flex-shrink-0" />
                            )}
                          </div>
                          <span className="text-xs text-slate-500">{entry.size_display}</span>
                          <span className="text-xs text-slate-500 text-right">{timeAgo(entry.last_modified)}</span>
                        </button>
                      );
                    })}
                  </div>
                )}

                {browseData && (
                  <p className="mt-6 text-xs text-slate-600 text-center">
                    {browseData.entries.length} {browseData.entries.length === 1 ? 'item' : 'items'} in this folder
                  </p>
                )}
              </>
            )}

            {/* Files-view side preview pane (legacy, pre-QuickLook) */}
            {previewEntry && (
              <div
                className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
                onClick={closePreview}
              >
                <div
                  className="bg-white p-8 rounded-xl text-center text-neutral-500 max-w-md"
                  onClick={(e) => e.stopPropagation()}
                >
                  <p className="mb-4">Preview coming soon.</p>
                  <p className="text-sm">Click outside to close, or open the file from the row menu.</p>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* QuickLook modal (timeline view) */}
      {quickLookTarget && (
        <QuickLook
          filePath={quickLookTarget.path}
          fileType={quickLookTarget.mime}
          isOpen={true}
          onClose={() => setQuickLookTarget(null)}
        />
      )}

      {/* In-app confirm dialog, used by delete flows. */}
      <ConfirmModal {...confirmProps} />

      {/* Transient toast for delete feedback (success or error). */}
      {deleteToast && (
        <div
          role="status"
          data-testid={
            deleteToast.kind === 'success'
              ? 'files-delete-success-toast'
              : 'files-delete-error-toast'
          }
          className={`fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-start gap-3 px-4 py-3 rounded-xl shadow-lg text-sm border ${
            deleteToast.kind === 'success'
              ? 'bg-slate-800 border-slate-700 text-slate-200'
              : 'bg-red-950 border-red-800 text-red-200'
          }`}
        >
          <Icon
            name={deleteToast.kind === 'success' ? 'check_circle' : 'error'}
            size={18}
            className={deleteToast.kind === 'success' ? 'text-green-400' : 'text-red-400'}
          />
          <span>{deleteToast.message}</span>
          <button
            onClick={() => setDeleteToast(null)}
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
