import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Icon from './Icon'
import { useAppStore } from '../stores/app'
import { api } from '../lib/api'

interface TaskResult {
  id: string
  priority: string
  title: string
}

type AnyItem = Record<string, unknown>

export interface UniversalSearchProps {
  open: boolean
  onClose: () => void
}

const isMac = () =>
  navigator.platform.toUpperCase().includes('MAC') ||
  navigator.userAgent.toUpperCase().includes('MAC OS')
const modKey = isMac() ? '⌘' : 'Ctrl+'

const priorityColor = (p: string) => {
  if (p === 'P0') return 'text-pink-400'
  if (p === 'P1') return 'text-orange-400'
  if (p === 'P3') return 'text-slate-400'
  return 'text-blue-400'
}

function Highlight({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>
  const idx = text.toLowerCase().indexOf(query.toLowerCase())
  if (idx === -1) return <>{text}</>
  return (
    <>
      {text.slice(0, idx)}
      <strong className="font-bold">{text.slice(idx, idx + query.length)}</strong>
      {text.slice(idx + query.length)}
    </>
  )
}

function extractLabel(item: AnyItem): string {
  return String(
    item.title ?? item.name ?? item.label ?? item.subject ?? item.id ?? JSON.stringify(item).slice(0, 80)
  )
}

function flattenResponse(resp: unknown): AnyItem[] {
  if (!resp || typeof resp !== 'object') return []
  if (Array.isArray(resp)) return resp as AnyItem[]
  const obj = resp as Record<string, unknown>
  for (const key of ['results', 'items', 'needles', 'events', 'hits', 'matches', 'entries']) {
    if (Array.isArray(obj[key])) return obj[key] as AnyItem[]
  }
  if (obj.title || obj.name || obj.id) return [obj as AnyItem]
  return []
}

const NAV_ITEMS = [
  { id: 'home', label: 'Home', icon: 'home', path: '/' },
  { id: 'tasks', label: 'Tasks', icon: 'checklist', path: '/tasks' },
  { id: 'inbox', label: 'Inbox', icon: 'inbox', path: '/inbox' },
  { id: 'agents', label: 'Agents', icon: 'smart_toy', path: '/agents' },
  { id: 'adoption', label: 'Adoption', icon: 'trending_up', path: '/adoption' },
  { id: 'specs', label: 'Specs', icon: 'description', path: '/specs' },
  { id: 'files', label: 'Files', icon: 'folder', path: '/files' },
  { id: 'drive', label: 'Drive', icon: 'cloud', path: '/drive' },
  { id: 'gmail', label: 'Gmail', icon: 'mail', path: '/gmail' },
  { id: 'calendar', label: 'Calendar', icon: 'calendar_month', path: '/calendar' },
  { id: 'imessage', label: 'iMessage', icon: 'chat_bubble', path: '/imessage' },
  { id: 'slack', label: 'Slack', icon: 'chat', path: '/slack' },
  { id: 'github', label: 'GitHub', icon: 'code', path: '/github' },
  { id: 'jira', label: 'Jira', icon: 'bug_report', path: '/jira' },
  { id: 'confluence', label: 'Confluence', icon: 'menu_book', path: '/confluence' },
  { id: 'timeline', label: 'Timeline', icon: 'timeline', path: '/timeline' },
  { id: 'transcripts', label: 'Transcripts', icon: 'record_voice_over', path: '/transcripts' },
  { id: 'activity', label: 'Activity', icon: 'local_activity', path: '/activity' },
  { id: 'sessions', label: 'Sessions', icon: 'history', path: '/sessions' },
  { id: 'costs', label: 'Costs', icon: 'attach_money', path: '/costs' },
  { id: 'releases', label: 'Releases', icon: 'new_releases', path: '/releases' },
  { id: 'workflows', label: 'Workflows', icon: 'account_tree', path: '/workflows' },
  { id: 'settings', label: 'Settings', icon: 'settings', path: '/settings' },
]

export function UniversalSearch({ open, onClose }: UniversalSearchProps) {
  const [query, setQuery] = useState('')
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [tasksLoading, setTasksLoading] = useState(false)
  const [deepLoading, setDeepLoading] = useState(false)
  const [recallLoading, setRecallLoading] = useState(false)
  const [tasksResults, setTasksResults] = useState<TaskResult[]>([])
  const [deepResults, setDeepResults] = useState<AnyItem[]>([])
  const [recallResults, setRecallResults] = useState<AnyItem[]>([])

  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const navigate = useNavigate()
  const toggleChat = useAppStore((s) => s.toggleChat)

  const q = query.trim()

  const filteredNav = useMemo(() => {
    if (!q) return NAV_ITEMS
    const lower = q.toLowerCase()
    return NAV_ITEMS.filter(
      (n) => n.label.toLowerCase().includes(lower) || 'navigate'.startsWith(lower)
    )
  }, [q])

  const actions = useMemo(
    () => [
      {
        id: 'toggle-chat',
        label: 'Toggle Chat',
        icon: 'chat',
        shortcut: `${modKey}L`,
        fn: () => { toggleChat(); onClose() },
      },
      {
        id: 'new-task',
        label: 'Create New Task',
        icon: 'add_task',
        shortcut: `${modKey}N`,
        fn: () => { navigate('/tasks?new=1'); onClose() },
      },
    ],
    [toggleChat, onClose, navigate]
  )

  const filteredActions = useMemo(() => {
    if (!q) return actions
    const lower = q.toLowerCase()
    return actions.filter(
      (a) => a.label.toLowerCase().includes(lower) || 'actions'.startsWith(lower)
    )
  }, [q, actions])

  // Section offsets for flat keyboard navigation
  const navCount = filteredNav.length
  const actionsCount = filteredActions.length
  const tasksCount = q ? tasksResults.length : 0
  const deepCount = q ? deepResults.length : 0
  const recallCount = q ? recallResults.length : 0

  const actionsOffset = navCount
  const tasksOffset = actionsOffset + actionsCount
  const deepOffset = tasksOffset + tasksCount
  const recallOffset = deepOffset + deepCount
  const totalItems = recallOffset + recallCount

  // Debounced parallel search across all 3 endpoints
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)

    if (!q) {
      setTasksResults([])
      setDeepResults([])
      setRecallResults([])
      setTasksLoading(false)
      setDeepLoading(false)
      setRecallLoading(false)
      return
    }

    setTasksLoading(true)
    setDeepLoading(true)
    setRecallLoading(true)

    debounceRef.current = setTimeout(async () => {
      const encoded = encodeURIComponent(q)
      const [tr, dr, rr] = await Promise.allSettled([
        api.get<{ tasks: TaskResult[]; query: string }>(`/search?q=${encoded}`),
        api.get<unknown>(`/search/deep?q=${encoded}`),
        api.get<unknown>(`/search/recall?q=${encoded}`),
      ])

      if (tr.status === 'fulfilled') {
        setTasksResults(tr.value?.tasks ?? [])
      } else {
        console.error('Tasks search failed:', tr.reason)
        setTasksResults([])
      }
      setTasksLoading(false)

      if (dr.status === 'fulfilled') {
        setDeepResults(flattenResponse(dr.value))
      } else {
        console.error('Deep search failed:', dr.reason)
        setDeepResults([])
      }
      setDeepLoading(false)

      if (rr.status === 'fulfilled') {
        setRecallResults(flattenResponse(rr.value))
      } else {
        console.error('Recall search failed:', rr.reason)
        setRecallResults([])
      }
      setRecallLoading(false)
    }, 150)

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [q])

  // Reset on open
  useEffect(() => {
    if (open) {
      setQuery('')
      setSelectedIndex(0)
      setTasksResults([])
      setDeepResults([])
      setRecallResults([])
      setTasksLoading(false)
      setDeepLoading(false)
      setRecallLoading(false)
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }, [open])

  // Clamp selectedIndex when result counts change
  useEffect(() => {
    if (totalItems > 0 && selectedIndex >= totalItems) {
      setSelectedIndex(totalItems - 1)
    }
  }, [totalItems, selectedIndex])

  // Scroll selected item into view
  useEffect(() => {
    if (!listRef.current) return
    const items = listRef.current.querySelectorAll('[data-us-item]')
    items[selectedIndex]?.scrollIntoView?.({ block: 'nearest' })
  }, [selectedIndex])

  const executeSelected = useCallback(() => {
    if (selectedIndex < navCount) {
      navigate(filteredNav[selectedIndex].path)
      onClose()
    } else if (selectedIndex < tasksOffset) {
      filteredActions[selectedIndex - actionsOffset].fn()
    } else if (selectedIndex < deepOffset) {
      navigate('/tasks')
      onClose()
    } else if (selectedIndex < recallOffset) {
      // audit/needle items — no dedicated route
    } else if (selectedIndex < totalItems) {
      navigate('/transcripts')
      onClose()
    }
  }, [
    selectedIndex, navCount, actionsOffset, tasksOffset, deepOffset, recallOffset, totalItems,
    filteredNav, filteredActions, navigate, onClose,
  ])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault()
          setSelectedIndex((prev) => (prev + 1) % Math.max(1, totalItems))
          break
        case 'ArrowUp':
          e.preventDefault()
          setSelectedIndex((prev) => (prev - 1 + Math.max(1, totalItems)) % Math.max(1, totalItems))
          break
        case 'Enter':
          e.preventDefault()
          executeSelected()
          break
        case 'Escape':
          e.preventDefault()
          onClose()
          break
      }
    },
    [totalItems, executeSelected, onClose]
  )

  if (!open) return null

  const rowCls = (idx: number) =>
    `w-full flex items-center gap-3 px-4 py-2.5 text-sm transition-colors cursor-pointer ${
      idx === selectedIndex
        ? 'bg-blue-500/20 text-blue-100'
        : 'text-slate-300 hover:bg-slate-800/50'
    }`

  const sectionHeader = (label: string, accent: string, bordered = true) => (
    <div
      className={`px-4 py-1.5 text-[10px] font-bold uppercase tracking-widest ${accent}${
        bordered ? ' border-t border-slate-800/50 mt-1 pt-2' : ''
      }`}
    >
      {label}
    </div>
  )

  const spinnerRow = (label: string, testId: string) => (
    <div
      className="px-4 py-2 text-xs text-slate-500 flex items-center gap-2 border-t border-slate-800/50 mt-1 pt-2"
      data-testid={testId}
    >
      <Icon name="refresh" className="text-xs animate-spin" />
      {label}
    </div>
  )

  return (
    <div
      className="fixed inset-0 z-[100] flex items-start justify-center pt-[20vh]"
      onClick={onClose}
      data-testid="universal-search-backdrop"
    >
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div
        className="relative w-full max-w-lg bg-slate-900 border border-slate-700 rounded-xl shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        data-testid="universal-search"
      >
        {/* Input */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-slate-800">
          <Icon name="search" className="text-slate-500" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            className="flex-1 bg-transparent text-sm text-slate-200 placeholder-slate-500 outline-none"
            placeholder="Search everywhere..."
            data-testid="universal-search-input"
          />
          <kbd className="hidden sm:inline-flex items-center px-1.5 py-0.5 text-[10px] font-mono text-slate-500 bg-slate-800 rounded border border-slate-700">
            ESC
          </kbd>
        </div>

        {/* Results */}
        <div ref={listRef} className="max-h-96 overflow-y-auto py-2" data-testid="universal-search-list">

          {/* Navigate */}
          {filteredNav.length > 0 && (
            <div data-testid="section-navigate">
              {sectionHeader('Navigate', 'text-blue-400', false)}
              {filteredNav.map((n, i) => {
                const idx = i
                const sel = idx === selectedIndex
                return (
                  <button
                    key={n.id}
                    data-us-item
                    onClick={() => { navigate(n.path); onClose() }}
                    onMouseEnter={() => setSelectedIndex(idx)}
                    className={rowCls(idx)}
                    data-testid={`nav-item-${n.id}`}
                  >
                    <Icon name={n.icon} className={`text-lg ${sel ? 'text-blue-400' : 'text-blue-500/60'}`} />
                    <span className="flex-1 text-left">
                      <Highlight text={n.label} query={q} />
                    </span>
                  </button>
                )
              })}
            </div>
          )}

          {/* Actions */}
          {filteredActions.length > 0 && (
            <div data-testid="section-actions">
              {sectionHeader('Actions', 'text-purple-400', filteredNav.length > 0)}
              {filteredActions.map((a, i) => {
                const idx = actionsOffset + i
                const sel = idx === selectedIndex
                return (
                  <button
                    key={a.id}
                    data-us-item
                    onClick={a.fn}
                    onMouseEnter={() => setSelectedIndex(idx)}
                    className={rowCls(idx)}
                    data-testid={`action-item-${a.id}`}
                  >
                    <Icon name={a.icon} className={`text-lg ${sel ? 'text-purple-400' : 'text-purple-500/60'}`} />
                    <span className="flex-1 text-left">
                      <Highlight text={a.label} query={q} />
                    </span>
                    <kbd className="text-[10px] font-mono text-slate-500 bg-slate-800 rounded px-1.5 py-0.5 border border-slate-700">
                      {a.shortcut}
                    </kbd>
                  </button>
                )
              })}
            </div>
          )}

          {/* Tasks */}
          {q && (
            tasksLoading
              ? spinnerRow('Searching tasks...', 'tasks-loading')
              : tasksResults.length > 0
                ? (
                  <div data-testid="section-tasks">
                    {sectionHeader('Tasks', 'text-pink-400')}
                    {tasksResults.map((t, i) => {
                      const idx = tasksOffset + i
                      const sel = idx === selectedIndex
                      return (
                        <button
                          key={t.id}
                          data-us-item
                          onClick={() => { navigate('/tasks'); onClose() }}
                          onMouseEnter={() => setSelectedIndex(idx)}
                          className={rowCls(idx)}
                          data-testid={`task-item-${t.id}`}
                        >
                          <Icon name="checklist" className={`text-lg ${sel ? 'text-pink-400' : 'text-pink-500/60'}`} />
                          <span className="flex-1 text-left">
                            <Highlight text={t.title} query={q} />
                          </span>
                          <span className={`text-xs font-medium ${priorityColor(t.priority)}`}>
                            {t.priority}
                          </span>
                        </button>
                      )
                    })}
                  </div>
                )
                : null
          )}

          {/* Needles + Audit */}
          {q && (
            deepLoading
              ? spinnerRow('Searching tasks & audit...', 'deep-loading')
              : deepResults.length > 0
                ? (
                  <div data-testid="section-deep">
                    {sectionHeader('Tasks & Audit', 'text-orange-400')}
                    {deepResults.map((d, i) => {
                      const idx = deepOffset + i
                      const sel = idx === selectedIndex
                      const label = extractLabel(d)
                      return (
                        <button
                          key={i}
                          data-us-item
                          onClick={() => {}}
                          onMouseEnter={() => setSelectedIndex(idx)}
                          className={rowCls(idx)}
                          data-testid={`deep-item-${i}`}
                        >
                          <Icon name="manage_search" className={`text-lg ${sel ? 'text-orange-400' : 'text-orange-500/60'}`} />
                          <span className="flex-1 text-left">
                            <Highlight text={label} query={q} />
                          </span>
                        </button>
                      )
                    })}
                  </div>
                )
                : null
          )}

          {/* Transcripts */}
          {q && (
            recallLoading
              ? spinnerRow('Searching transcripts...', 'recall-loading')
              : recallResults.length > 0
                ? (
                  <div data-testid="section-transcripts">
                    {sectionHeader('Transcripts', 'text-cyan-400')}
                    {recallResults.map((r, i) => {
                      const idx = recallOffset + i
                      const sel = idx === selectedIndex
                      const label = extractLabel(r)
                      return (
                        <button
                          key={i}
                          data-us-item
                          onClick={() => { navigate('/transcripts'); onClose() }}
                          onMouseEnter={() => setSelectedIndex(idx)}
                          className={rowCls(idx)}
                          data-testid={`transcript-item-${i}`}
                        >
                          <Icon name="record_voice_over" className={`text-lg ${sel ? 'text-cyan-400' : 'text-cyan-500/60'}`} />
                          <span className="flex-1 text-left">
                            <Highlight text={label} query={q} />
                          </span>
                        </button>
                      )
                    })}
                  </div>
                )
                : null
          )}

          {/* Empty state: only when no nav/action matches AND no query results */}
          {filteredNav.length === 0 &&
            filteredActions.length === 0 &&
            !tasksLoading &&
            !deepLoading &&
            !recallLoading &&
            tasksResults.length === 0 &&
            deepResults.length === 0 &&
            recallResults.length === 0 && (
              <div className="px-4 py-8 text-center text-sm text-slate-500">
                No results
              </div>
            )}
        </div>
      </div>
    </div>
  )
}
