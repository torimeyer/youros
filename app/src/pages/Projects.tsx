import { useState, useEffect, useCallback } from 'react';
import { formatRelative } from '../lib/time';
import Icon from '../components/Icon';
import TopBar from '../components/TopBar';
import { Card, EmptyState, ErrorBanner, LoadingState } from '../components/ui';
import { api } from '../lib/api';
import { reportError } from '../lib/reportError';

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

const typeConfig: Record<string, { icon: string; color: string; label: string }> = {
  node: { icon: 'javascript', color: 'bg-yellow-500/20 text-yellow-400', label: 'Node.js' },
  python: { icon: 'code', color: 'bg-blue-500/20 text-blue-400', label: 'Python' },
  rust: { icon: 'memory', color: 'bg-orange-500/20 text-orange-400', label: 'Rust' },
  go: { icon: 'speed', color: 'bg-cyan-500/20 text-cyan-400', label: 'Go' },
  folder: { icon: 'folder', color: 'bg-slate-500/20 text-slate-400', label: 'Folder' },
};



export default function Projects() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchProjects = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get<ProjectsResponse>('/projects');
      setProjects(res.projects);
    } catch (e) {
      reportError('Failed to fetch projects', e);
      setError('Could not load your projects. Check that myOS is running and try again.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchProjects();
  }, [fetchProjects]);

  return (
    <div className="min-h-dvh bg-slate-950 text-white">
      <TopBar title="Projects" />

      <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6 sm:mb-8">
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold mb-1">Projects</h1>
            <p className="text-slate-400">
              All directories in the myOS workspace.
            </p>
          </div>
          <button
            onClick={fetchProjects}
            className="flex items-center gap-2 px-4 py-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm text-slate-300 transition-colors"
          >
            <Icon name="refresh" size={16} />
            Refresh
          </button>
        </div>

        {/* Loading state */}
        {loading && projects.length === 0 && (
          <LoadingState variant="spinner" message="Loading projects..." />
        )}

        {/* Error state */}
        {error && (
          <div className="mb-6">
            <ErrorBanner
              message={error}
              action={{ label: 'Try again', onClick: fetchProjects }}
            />
          </div>
        )}

        {/* Empty state */}
        {!loading && !error && projects.length === 0 && (
          <EmptyState
            icon="folder_open"
            title="No projects yet"
            description="Add a directory to your workspace and projects will appear here."
          />
        )}

        {/* Project cards */}
        {projects.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {projects.map((project) => {
              const cfg = typeConfig[project.project_type] || typeConfig.folder;
              return (
                <Card key={project.name} hover padding="lg">
                  {/* Top row: icon + name */}
                  <div className="flex items-start gap-4 mb-4">
                    <div
                      className={`w-12 h-12 rounded-xl flex items-center justify-center shrink-0 ${cfg.color}`}
                    >
                      <Icon name={cfg.icon} size={24} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <h3 className="text-lg font-semibold truncate">{project.name}</h3>
                      <p className="text-sm text-slate-500 truncate">{project.path}</p>
                    </div>
                  </div>

                  {/* Description */}
                  {project.description && (
                    <p className="text-sm text-slate-400 mb-4 line-clamp-2">
                      {project.description}
                    </p>
                  )}

                  {/* Metadata row */}
                  <div className="flex items-center gap-4 text-xs text-slate-500">
                    {/* Type badge */}
                    <span
                      className={`px-2 py-0.5 rounded-full font-medium ${cfg.color}`}
                    >
                      {cfg.label}
                    </span>

                    {/* File count */}
                    <span className="flex items-center gap-1">
                      <Icon name="description" size={14} />
                      {project.file_count} {project.file_count === 1 ? 'item' : 'items'}
                    </span>

                    {/* Git badge */}
                    {project.has_git && (
                      <span className="flex items-center gap-1 text-green-400">
                        <Icon name="commit" size={14} />
                        git
                      </span>
                    )}

                    {/* Last modified */}
                    {project.last_modified && (
                      <span className="ml-auto">{formatRelative(project.last_modified)}</span>
                    )}
                  </div>
                </Card>
              );
            })}
          </div>
        )}

        {/* Summary footer */}
        {projects.length > 0 && (
          <div className="mt-8 text-sm text-slate-500 text-center">
            {projects.length} {projects.length === 1 ? 'project' : 'projects'} in workspace
          </div>
        )}
      </div>
    </div>
  );
}
