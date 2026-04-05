import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Icon from './Icon'
import { useAppStore } from '../stores/app'

export interface Command {
  id: string
  label: string
  icon: string
  shortcut?: string
  action: () => void
  section: 'Navigate' | 'Actions'
}

interface CommandPaletteProps {
  open: boolean
  onClose: () => void
}

export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const [query, setQuery] = useState('')
  const [selectedIndex, setSelectedIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  const toggleChat = useAppStore((s) => s.toggleChat)

  const commands = useMemo<Command[]>(() => [
    { id: 'nav-home', label: 'Go to Home', icon: 'home', section: 'Navigate', action: () => { navigate('/'); onClose() } },
    { id: 'nav-tasks', label: 'Go to Tasks', icon: 'checklist', section: 'Navigate', action: () => { navigate('/tasks'); onClose() } },
    { id: 'nav-ideas', label: 'Go to Ideas', icon: 'lightbulb', section: 'Navigate', action: () => { navigate('/ideas'); onClose() } },
    { id: 'nav-agents', label: 'Go to Agents', icon: 'smart_toy', section: 'Navigate', action: () => { navigate('/agents'); onClose() } },
    { id: 'nav-timeline', label: 'Go to Timeline', icon: 'timeline', section: 'Navigate', action: () => { navigate('/timeline'); onClose() } },
    { id: 'nav-files', label: 'Go to Files', icon: 'folder', section: 'Navigate', action: () => { navigate('/files'); onClose() } },
    { id: 'nav-transcripts', label: 'Go to Transcripts', icon: 'record_voice_over', section: 'Navigate', action: () => { navigate('/transcripts'); onClose() } },
    { id: 'nav-settings', label: 'Go to Settings', icon: 'settings', section: 'Navigate', action: () => { navigate('/settings'); onClose() } },
    { id: 'action-chat', label: 'Toggle Chat', icon: 'chat', shortcut: '⌘L', section: 'Actions', action: () => { toggleChat(); onClose() } },
    { id: 'action-new-task', label: 'Create New Task', icon: 'add_task', shortcut: '⌘N', section: 'Actions', action: () => { navigate('/tasks?new=1'); onClose() } },
  ], [navigate, onClose, toggleChat])

  const filtered = useMemo(() => {
    if (!query.trim()) return commands
    const lower = query.toLowerCase()
    return commands.filter((cmd) =>
      cmd.label.toLowerCase().includes(lower) ||
      cmd.section.toLowerCase().includes(lower)
    )
  }, [query, commands])

  // Reset state when opening
  useEffect(() => {
    if (open) {
      setQuery('')
      setSelectedIndex(0)
      // Focus the input after a brief delay to ensure the modal is rendered
      requestAnimationFrame(() => {
        inputRef.current?.focus()
      })
    }
  }, [open])

  // Keep selected index in bounds when filtered results change
  useEffect(() => {
    if (selectedIndex >= filtered.length) {
      setSelectedIndex(Math.max(0, filtered.length - 1))
    }
  }, [filtered.length, selectedIndex])

  // Scroll selected item into view
  useEffect(() => {
    if (!listRef.current) return
    const items = listRef.current.querySelectorAll('[data-command-item]')
    const selected = items[selectedIndex]
    if (selected) {
      selected.scrollIntoView?.({ block: 'nearest' })
    }
  }, [selectedIndex])

  const executeSelected = useCallback(() => {
    if (filtered.length > 0 && selectedIndex < filtered.length) {
      filtered[selectedIndex].action()
    }
  }, [filtered, selectedIndex])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        setSelectedIndex((prev) => (prev + 1) % filtered.length)
        break
      case 'ArrowUp':
        e.preventDefault()
        setSelectedIndex((prev) => (prev - 1 + filtered.length) % filtered.length)
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
  }, [filtered.length, executeSelected, onClose])

  if (!open) return null

  // Group filtered commands by section
  const sections = filtered.reduce<Record<string, Command[]>>((acc, cmd) => {
    if (!acc[cmd.section]) acc[cmd.section] = []
    acc[cmd.section].push(cmd)
    return acc
  }, {})

  // Compute a flat index for each item so section headers don't break selection
  let flatIndex = 0

  return (
    <div
      className="fixed inset-0 z-[100] flex items-start justify-center pt-[20vh]"
      onClick={onClose}
      data-testid="command-palette-backdrop"
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />

      {/* Modal */}
      <div
        className="relative w-full max-w-lg bg-slate-900 border border-slate-700 rounded-xl shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        data-testid="command-palette"
      >
        {/* Search input */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-slate-800">
          <Icon name="search" className="text-slate-500" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            className="flex-1 bg-transparent text-sm text-slate-200 placeholder-slate-500 outline-none"
            placeholder="Type a command or search..."
            data-testid="command-palette-input"
          />
          <kbd className="hidden sm:inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-mono text-slate-500 bg-slate-800 rounded border border-slate-700">
            ESC
          </kbd>
        </div>

        {/* Results */}
        <div ref={listRef} className="max-h-80 overflow-y-auto py-2" data-testid="command-palette-list">
          {filtered.length === 0 && (
            <div className="px-4 py-8 text-center text-sm text-slate-500">
              No matching commands
            </div>
          )}

          {Object.entries(sections).map(([sectionName, items]) => (
            <div key={sectionName}>
              <div className="px-4 py-1.5 text-[10px] font-bold uppercase tracking-widest text-slate-500">
                {sectionName}
              </div>
              {items.map((cmd) => {
                const idx = flatIndex++
                const isSelected = idx === selectedIndex
                return (
                  <button
                    key={cmd.id}
                    data-command-item
                    onClick={() => cmd.action()}
                    onMouseEnter={() => setSelectedIndex(idx)}
                    className={`w-full flex items-center gap-3 px-4 py-2.5 text-sm transition-colors cursor-pointer ${
                      isSelected
                        ? 'bg-blue-500/20 text-blue-100'
                        : 'text-slate-300 hover:bg-slate-800/50'
                    }`}
                    data-testid={`command-item-${cmd.id}`}
                  >
                    <Icon name={cmd.icon} className={`text-lg ${isSelected ? 'text-blue-400' : 'text-slate-500'}`} />
                    <span className="flex-1 text-left">{cmd.label}</span>
                    {cmd.shortcut && (
                      <kbd className="text-[10px] font-mono text-slate-500 bg-slate-800 rounded px-1.5 py-0.5 border border-slate-700">
                        {cmd.shortcut}
                      </kbd>
                    )}
                  </button>
                )
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
