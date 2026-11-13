import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Icon from './Icon'
import { api } from '../lib/api'
import { useAppStore } from '../stores/app'

export interface DiscoveredRepo {
  name: string
  path: string
  is_active: boolean
  branch: string | null
}

interface ReposResponse {
  repos?: DiscoveredRepo[]
  active_repo_path?: string | null
}

interface ActiveRepoResponse {
  active_repo_path?: string | null
  name?: string
}

export function deriveRepoBasename(repoPath: string | null | undefined): string {
  if (!repoPath) return 'torios'
  const trimmed = repoPath.replace(/\/+$/, '')
  if (!trimmed) return 'torios'
  const parts = trimmed.split('/')
  return parts[parts.length - 1] || 'torios'
}

interface RepoSwitcherProps {
  compact?: boolean
  onNavigate?: () => void
}

export default function RepoSwitcher({ compact = false, onNavigate }: RepoSwitcherProps) {
  const navigate = useNavigate()
  const activeRepoPath = useAppStore((s) => s.activeRepoPath)
  const setActiveRepoPath = useAppStore((s) => s.setActiveRepoPath)

  const [open, setOpen] = useState(false)
  const [repos, setRepos] = useState<DiscoveredRepo[]>([])
  const [fetchedActivePath, setFetchedActivePath] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [switchingPath, setSwitchingPath] = useState<string | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)

  const effectivePath = activeRepoPath ?? fetchedActivePath
  const activeLabel = deriveRepoBasename(effectivePath)

  const fetchRepos = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get<ReposResponse>('/projects/repos')
      if (res && Array.isArray(res.repos)) {
        setRepos(res.repos)
      }
      if (res && typeof res.active_repo_path === 'string' && res.active_repo_path) {
        setFetchedActivePath(res.active_repo_path)
      }
    } catch {
      // Silent fallback so unmocked or offline states never disrupt Sidebar rendering.
    } finally {
      setLoading(false)
    }
  }, [])

  const handleToggle = () => {
    const next = !open
    setOpen(next)
    if (next) {
      void fetchRepos()
    }
  }

  const handleSelectRepo = async (path: string) => {
    setSwitchingPath(path)
    try {
      const res = await api.post<ActiveRepoResponse>('/projects/active-repo', { path })
      const resolved =
        res && typeof res.active_repo_path === 'string' && res.active_repo_path
          ? res.active_repo_path
          : path
      setActiveRepoPath(resolved)
      setFetchedActivePath(resolved)
      setOpen(false)
    } catch {
      // Keep menu open if selection fails so the user can retry.
    } finally {
      setSwitchingPath(null)
    }
  }

  const handleManageRepos = () => {
    setOpen(false)
    onNavigate?.()
    navigate('/projects')
  }

  useEffect(() => {
    if (!open) return
    const onMouseDown = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onMouseDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onMouseDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        data-testid="repo-switcher"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Active repository: ${activeLabel}`}
        title={effectivePath || activeLabel}
        onClick={handleToggle}
        className={`flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-800 hover:bg-slate-700 hover:border-slate-400 text-slate-200 transition-colors cursor-pointer ${
          compact ? 'p-1.5 justify-center w-9 h-9' : 'w-full px-2.5 py-1.5 text-left'
        }`}
      >
        <Icon name="folder" className="text-sm accent-text shrink-0" />
        {!compact && (
          <>
            <span className="text-xs font-mono font-medium truncate flex-1">{activeLabel}</span>
            <Icon
              name="expand_more"
              className={`text-sm text-slate-500 transition-transform duration-150 shrink-0 ${
                open ? 'rotate-180' : ''
              }`}
            />
          </>
        )}
      </button>

      {open && (
        <div
          role="listbox"
          aria-label="Discovered git repositories"
          className="absolute left-0 right-0 mt-1.5 min-w-[210px] rounded-xl border border-slate-700 bg-slate-800 shadow-2xl z-50 overflow-hidden"
        >
          <div className="max-h-60 overflow-y-auto py-1">
            {loading && repos.length === 0 ? (
              <div className="px-3 py-2 text-xs text-slate-500">Scanning repositories...</div>
            ) : repos.length === 0 ? (
              <div className="px-3 py-2 text-xs text-slate-500">No repositories found</div>
            ) : (
              repos.map((repo) => {
                const isSelected = effectivePath
                  ? repo.path === effectivePath
                  : repo.is_active
                return (
                  <button
                    key={repo.path}
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    disabled={switchingPath === repo.path}
                    onClick={() => {
                      void handleSelectRepo(repo.path)
                    }}
                    className={`flex items-center gap-2 w-full px-3 py-2 text-left text-xs transition-colors cursor-pointer ${
                      isSelected
                        ? 'accent-highlight'
                        : 'text-slate-300 hover:text-white hover:bg-slate-700'
                    }`}
                  >
                    <Icon
                      name={isSelected ? 'check' : 'folder'}
                      className={`text-sm shrink-0 ${
                        isSelected ? 'accent-text' : 'text-slate-500'
                      }`}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="font-mono font-medium truncate">{repo.name}</div>
                      {repo.branch && (
                        <div className="text-[10px] font-mono text-slate-500 truncate">
                          {repo.branch}
                        </div>
                      )}
                    </div>
                  </button>
                )
              })
            )}
          </div>

          <div className="border-t border-slate-700 p-1">
            <button
              type="button"
              onClick={handleManageRepos}
              className="flex items-center gap-2 w-full px-2.5 py-1.5 rounded-lg text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-700 transition-colors cursor-pointer"
            >
              <Icon name="settings" className="text-sm text-slate-500" />
              <span>Manage repos</span>
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
