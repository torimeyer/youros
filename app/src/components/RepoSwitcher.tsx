import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Icon from './Icon'
import { api } from '../lib/api'
import { useAppStore } from '../stores/app'

export const ACTIVE_REPO_CHANGED_EVENT = 'myos:active-repo-changed'

export interface DiscoveredRepo {
  name: string
  path: string
  is_active: boolean
  branch: string | null
  ostk_initialized?: boolean
  ostk_socket_live?: boolean
}

interface ReposResponse {
  repos?: DiscoveredRepo[]
  active_repo_path?: string | null
}

interface ActiveRepoResponse {
  active_repo_path?: string | null
  name?: string
  ostk_initialized?: boolean
  ostk_socket_live?: boolean
}

interface OstkInitResponse {
  initialized?: boolean
  path?: string
  ostk_initialized?: boolean
  ostk_socket_live?: boolean
  output?: string
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
  const [initializingPath, setInitializingPath] = useState<string | null>(null)
  const [initError, setInitError] = useState<string | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)

  const effectivePath = activeRepoPath ?? fetchedActivePath
  const activeLabel = deriveRepoBasename(effectivePath)
  const activeRepoEntry = repos.find((repo) =>
    effectivePath ? repo.path === effectivePath : repo.is_active,
  )

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
      setInitError(null)
      void fetchRepos()
    }
  }

  const handleSelectRepo = async (targetPath: string) => {
    setSwitchingPath(targetPath)
    setInitError(null)
    try {
      const res = await api.post<ActiveRepoResponse>('/projects/active-repo', { path: targetPath })
      const resolved =
        res && typeof res.active_repo_path === 'string' && res.active_repo_path
          ? res.active_repo_path
          : targetPath
      setActiveRepoPath(resolved)
      setFetchedActivePath(resolved)
      if (typeof window !== 'undefined') {
        window.dispatchEvent(
          new CustomEvent(ACTIVE_REPO_CHANGED_EVENT, {
            detail: {
              path: resolved,
              ostk_initialized: res?.ostk_initialized,
              ostk_socket_live: res?.ostk_socket_live,
            },
          }),
        )
      }
      setOpen(false)
    } catch {
      // Keep menu open if selection fails so the user can retry.
    } finally {
      setSwitchingPath(null)
    }
  }

  const handleInitOstk = async (repo: DiscoveredRepo) => {
    setInitializingPath(repo.path)
    setInitError(null)
    const isActiveTarget = effectivePath ? repo.path === effectivePath : repo.is_active

    try {
      const res = await api.post<OstkInitResponse>('/projects/ostk-init', {
        path: repo.path,
      })
      const nextInitialized = res?.ostk_initialized ?? true
      const nextSocketLive = Boolean(res?.ostk_socket_live)

      setRepos((prev) =>
        prev.map((item) =>
          item.path === repo.path
            ? {
                ...item,
                ostk_initialized: nextInitialized,
                ostk_socket_live: nextSocketLive,
              }
            : item,
        ),
      )

      await fetchRepos()

      if (isActiveTarget) {
        let resolvedActive = res?.path || repo.path
        try {
          const activeRes = await api.get<ActiveRepoResponse>('/projects/active-repo')
          if (
            activeRes &&
            typeof activeRes.active_repo_path === 'string' &&
            activeRes.active_repo_path
          ) {
            resolvedActive = activeRes.active_repo_path
            setFetchedActivePath(resolvedActive)
          }
        } catch {
          // Non-fatal if active-repo refresh fails after successful ostk init.
        }
        setActiveRepoPath(resolvedActive)
        if (typeof window !== 'undefined') {
          window.dispatchEvent(
            new CustomEvent(ACTIVE_REPO_CHANGED_EVENT, {
              detail: {
                path: resolvedActive,
                ostk_initialized: nextInitialized,
                ostk_socket_live: nextSocketLive,
              },
            }),
          )
        }
      }
    } catch (err) {
      const msg =
        err instanceof Error && err.message ? err.message : 'Failed to initialize ostk'
      setInitError(msg)
    } finally {
      setInitializingPath(null)
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
          {activeRepoEntry?.ostk_initialized === false && (
            <div
              data-testid="ostk-uninitialized-banner"
              className="border-b border-slate-700 bg-slate-900 px-3 py-2 text-[11px] text-slate-400"
            >
              <span className="font-mono font-medium text-slate-200">{activeRepoEntry.name}</span>{' '}
              has no <span className="font-mono">.ostk</span> workspace yet.
            </div>
          )}

          {initError && (
            <div
              role="alert"
              data-testid="ostk-init-error"
              className="border-b border-slate-700 bg-slate-900 px-3 py-1.5 text-[11px] text-amber-400 break-words"
            >
              {initError}
            </div>
          )}

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
                const isInitializing = initializingPath === repo.path

                return (
                  <div key={repo.path}>
                    <button
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
                      {repo.ostk_initialized === true && (
                        <span
                          data-testid={`ostk-badge-${repo.name}`}
                          title={repo.ostk_socket_live ? 'ostk live' : 'ostk initialized'}
                          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border accent-border accent-highlight text-[10px] font-mono font-medium shrink-0"
                        >
                          {repo.ostk_socket_live && (
                            <span
                              data-testid={`ostk-live-dot-${repo.name}`}
                              aria-label="ostk live"
                              className="w-1.5 h-1.5 rounded-full accent-bg shrink-0"
                            />
                          )}
                          <span>ostk</span>
                        </span>
                      )}
                      {repo.ostk_initialized === false && (
                        <span
                          data-testid={`ostk-badge-${repo.name}`}
                          title="ostk not initialized"
                          className="inline-flex items-center px-1.5 py-0.5 rounded border border-slate-700 bg-slate-900 text-slate-500 text-[10px] font-mono shrink-0"
                        >
                          no .ostk
                        </span>
                      )}
                    </button>

                    {repo.ostk_initialized === false && (
                      <div className="pl-8 pr-3 pb-2 -mt-0.5">
                        <button
                          type="button"
                          data-testid={`init-ostk-${repo.name}`}
                          disabled={isInitializing}
                          onClick={() => {
                            void handleInitOstk(repo)
                          }}
                          className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-900 hover:bg-slate-700 hover:text-white hover:border-slate-400 text-slate-200 px-2 py-0.5 text-[11px] font-medium transition-colors cursor-pointer disabled:bg-slate-700 disabled:text-slate-500 disabled:border-slate-700 disabled:cursor-not-allowed"
                        >
                          <Icon name="bolt" className="text-xs accent-text shrink-0" />
                          <span>{isInitializing ? 'Initializing...' : 'Initialize ostk'}</span>
                        </button>
                      </div>
                    )}
                  </div>
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
