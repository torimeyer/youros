import { useState, useEffect, useCallback } from 'react';
import Icon from '../components/Icon';
import TopBar from '../components/TopBar';
import { api } from '../lib/api';

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

function fileIcon(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() ?? '';
  const map: Record<string, string> = {
    ts: 'code',
    tsx: 'code',
    js: 'javascript',
    jsx: 'javascript',
    py: 'code',
    rs: 'memory',
    go: 'speed',
    md: 'article',
    json: 'data_object',
    toml: 'settings',
    yaml: 'settings',
    yml: 'settings',
    html: 'web',
    css: 'palette',
    svg: 'image',
    png: 'image',
    jpg: 'image',
    jpeg: 'image',
    gif: 'image',
    txt: 'description',
    lock: 'lock',
    sh: 'terminal',
    zsh: 'terminal',
    bash: 'terminal',
  };
  return map[ext] || 'description';
}

// --- Component ---

export default function Files() {
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

  // Fetch project list (root view)
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

  // Fetch directory contents
  const fetchDirectory = useCallback(async (path: string) => {
    setBrowseLoading(true);
    setBrowseError(null);
    try {
      const res = await api.get<BrowseResponse>(`/projects/browse?path=${encodeURIComponent(path)}`);
      setBrowseData(res);
    } catch {
      setBrowseError('Could not load this folder. It may not exist or the API may be down.');
    } finally {
      setBrowseLoading(false);
    }
  }, []);

  // Load data whenever the path changes
  useEffect(() => {
    if (currentPath === null) {
      fetchProjects();
    } else {
      fetchDirectory(currentPath);
    }
  }, [currentPath, fetchProjects, fetchDirectory]);

  // Navigation helpers
  const navigateTo = (path: string) => setCurrentPath(path);
  const navigateToRoot = () => {
    setCurrentPath(null);
    setBrowseData(null);
  };
  const navigateUp = () => {
    if (browseData && browseData.parent_path) {
      setCurrentPath(browseData.parent_path);
    } else {
      navigateToRoot();
    }
  };

  // Open a file with the system default app
  const openFile = async (path: string) => {
    try {
      await api.post('/projects/open-file', { path });
    } catch {
      // Silently fail. The file may still open.
    }
  };

  const refresh = () => {
    if (currentPath === null) {
      fetchProjects();
    } else {
      fetchDirectory(currentPath);
    }
  };

  // --- Render ---

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <TopBar title="Files" />

      <div className="pt-16 p-8 max-w-6xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">Files</h1>
          </div>
          <button
            onClick={refresh}
            className="flex items-center gap-2 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm text-slate-300 transition-colors border border-slate-700"
          >
            <Icon name="refresh" className="text-base" />
            Refresh
          </button>
        </div>

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
                {/* Header row */}
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

            {browseData && browseData.entries.length === 0 && !browseLoading && (
              <div className="text-center py-12 text-slate-500">
                <Icon name="folder_open" className="text-4xl mb-2" />
                <p>This folder is empty.</p>
              </div>
            )}

            {browseData && browseData.entries.length > 0 && (
              <div className="flex flex-col gap-1">
                {/* Header row */}
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
                          openFile(entry.path);
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
      </div>
    </div>
  );
}
