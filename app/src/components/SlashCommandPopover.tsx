/**
 * SlashCommandPopover
 *
 * Shows a filtered list of slash commands anchored above the chat input.
 * Arrow keys move the highlight. Enter or click runs the selected command.
 * Esc closes the popover without running anything.
 *
 * Each row always shows /name and the description side by side (no hover-tooltip).
 */
import { useEffect, useRef, useState } from 'react'
import type { SlashCommand } from '../lib/slashCommands'

interface Props {
  commands: SlashCommand[]
  onSelect: (command: SlashCommand) => void
  onClose: () => void
}

const CATEGORY_COLORS: Record<SlashCommand['category'], string> = {
  workflow: 'text-blue-400 bg-blue-500/10',
  mode: 'text-purple-400 bg-purple-500/10',
  data: 'text-orange-400 bg-orange-500/10',
  system: 'text-slate-400 bg-slate-500/10',
}

export default function SlashCommandPopover({ commands, onSelect, onClose }: Props) {
  const [activeIndex, setActiveIndex] = useState(0)
  const listRef = useRef<HTMLDivElement>(null)

  // Reset highlight when the filtered list changes.
  useEffect(() => {
    setActiveIndex(0)
  }, [commands.length])

  // Scroll the active row into view when it changes.
  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-idx="${activeIndex}"]`) as HTMLElement | null
    el?.scrollIntoView?.({ block: 'nearest' })
  }, [activeIndex])

  if (commands.length === 0) return null

  return (
    <div
      data-testid="slash-command-popover"
      className="absolute bottom-full left-0 right-0 mb-1 bg-slate-900 border border-slate-700 rounded-lg shadow-xl overflow-hidden z-50"
    >
      <div
        ref={listRef}
        className="max-h-[280px] overflow-y-auto"
      >
        {commands.map((cmd, i) => (
          <button
            key={cmd.id}
            data-testid={`slash-cmd-${cmd.id.slice(1)}`}
            data-idx={i}
            onMouseDown={(e) => {
              // Prevent input blur before we can call onSelect.
              e.preventDefault()
              onSelect(cmd)
            }}
            onMouseEnter={() => setActiveIndex(i)}
            className={`w-full text-left px-3 py-2 flex items-center gap-3 transition-colors border-b border-slate-800/50 last:border-0 ${
              i === activeIndex ? 'bg-slate-800' : 'hover:bg-slate-800/50'
            }`}
          >
            {/* Category badge */}
            <span
              className={`text-xs px-1.5 py-0.5 rounded font-mono shrink-0 ${CATEGORY_COLORS[cmd.category]}`}
            >
              {cmd.category}
            </span>
            {/* Command name */}
            <span className="text-slate-200 font-mono text-sm shrink-0 min-w-[90px]">
              {cmd.id}
            </span>
            {/* Description always visible */}
            <span className="text-slate-500 text-xs truncate">
              {cmd.description}
            </span>
          </button>
        ))}
      </div>
      <div className="px-3 py-1.5 border-t border-slate-800 flex items-center gap-3 text-[10px] text-slate-600">
        <span><kbd className="font-mono">↑↓</kbd> navigate</span>
        <span><kbd className="font-mono">↵</kbd> select</span>
        <span><kbd className="font-mono">Esc</kbd> close</span>
      </div>
    </div>
  )
}

/**
 * Hook that handles keyboard navigation for the popover.
 * Returns a keydown handler to attach to the chat input.
 */
export function useSlashPopoverKeys(opts: {
  open: boolean
  count: number
  activeIndex: number
  setActiveIndex: (i: number) => void
  onConfirm: () => void
  onClose: () => void
}): (e: React.KeyboardEvent) => boolean {
  return (e: React.KeyboardEvent) => {
    if (!opts.open) return false
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      opts.setActiveIndex((opts.activeIndex + 1) % opts.count)
      return true
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      opts.setActiveIndex((opts.activeIndex - 1 + opts.count) % opts.count)
      return true
    }
    if (e.key === 'Enter') {
      e.preventDefault()
      opts.onConfirm()
      return true
    }
    if (e.key === 'Escape') {
      opts.onClose()
      return true
    }
    return false
  }
}
