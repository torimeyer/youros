import type { ReactNode } from 'react'
import TopBar from './TopBar'

interface PageShellProps {
  /** Page title, forwarded to TopBar. */
  title: string
  /** Page content: the header row plus the page body. */
  children: ReactNode
  /**
   * Full-height pages (e.g. Activity, Timeline) whose body must fill the
   * viewport and manage its own scrolling. Swaps the centered, max-width
   * content column for a flex column that stretches to fill the height.
   */
  fullHeight?: boolean
  /** Escape hatch for one-off content classes. Avoid unless truly needed. */
  className?: string
}

// The single, shared frame for every main page. Centralizing these classes is
// the whole point: pages used to hand-roll this wrapper and drifted (different
// max widths, no top padding, the title jammed against the header bar). Change
// the look here once and every page stays in sync.
const BASE = 'min-h-dvh bg-white dark:bg-slate-950 text-slate-900 dark:text-white'

// `pt-6 sm:pt-8` is the breathing room below the fixed header. The TopBar
// flow-spacer still owns the header OFFSET (so content never slides under the
// header); this top padding is content spacing on top of that, not offset
// compensation. `max-w-6xl mx-auto` centers every page at the same width.
const CONTENT = 'px-4 pt-6 pb-4 sm:px-8 sm:pt-8 sm:pb-8 max-w-6xl mx-auto'
const CONTENT_FULL = 'px-4 pt-6 pb-4 sm:px-8 sm:pt-8 sm:pb-8 flex-1 flex flex-col'

export default function PageShell({ title, children, fullHeight, className }: PageShellProps) {
  const content = `${fullHeight ? CONTENT_FULL : CONTENT} ${className ?? ''}`.trim()
  return (
    <div className={BASE}>
      <TopBar title={title} />
      <div className={content}>{children}</div>
    </div>
  )
}
