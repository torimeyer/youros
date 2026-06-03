import type { ReactNode, HTMLAttributes } from 'react'
import TopBar from './TopBar'

// Extends the div attributes so pages can forward data-testid, data-*, aria-*,
// etc. onto the outer wrapper (these used to live on each page's own
// min-h-dvh div; PageShell now carries them so tests/automation keep working).
interface PageShellProps extends HTMLAttributes<HTMLDivElement> {
  /** @deprecated No longer rendered; prop kept for backward compatibility. */
  title?: string
  /** Page content: the header row plus the page body. */
  children: ReactNode
  /**
   * Full-height pages (e.g. Activity, Timeline) whose body must fill the
   * viewport and manage its own scrolling. Swaps the centered, max-width
   * content column for a flex column that stretches to fill the height.
   */
  fullHeight?: boolean
}

// The single, shared frame for every main page. Centralizing these classes is
// the whole point: pages used to hand-roll this wrapper and drifted (different
// max widths, no top padding, the title jammed against the header bar). Change
// the look here once and every page stays in sync.
const BASE = 'min-h-dvh bg-white dark:bg-slate-950 text-slate-900 dark:text-white'
// fullHeight pages need the outer to be a flex column so the content's flex-1
// actually stretches; min-h-0 on the content lets inner overflow regions scroll.
const BASE_FULL = `${BASE} flex flex-col`

// `pt-6 sm:pt-8` is the breathing room below the fixed header. The TopBar
// flow-spacer still owns the header OFFSET (so content never slides under the
// header); this top padding is content spacing on top of that, not offset
// compensation. `max-w-6xl mx-auto` centers every page at the same width.
const CONTENT = 'px-4 pt-6 pb-4 sm:px-8 sm:pt-8 sm:pb-8 max-w-6xl mx-auto'
const CONTENT_FULL = 'px-4 pt-6 pb-4 sm:px-8 sm:pt-8 sm:pb-8 flex-1 flex flex-col min-h-0'

export default function PageShell({ title: _title, children, fullHeight, className, ...rest }: PageShellProps) {
  const base = `${fullHeight ? BASE_FULL : BASE}${className ? ` ${className}` : ''}`
  return (
    <div className={base} {...rest}>
      <TopBar />
      <div className={fullHeight ? CONTENT_FULL : CONTENT}>{children}</div>
    </div>
  )
}
