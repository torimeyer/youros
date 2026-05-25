import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Icon from './Icon'
import releaseNotes from '../data/releaseNotes'
import { useAppStore } from '../stores/app'

// Backwards compatible localStorage key. The store reads it on first
// paint as a cache, but the server is the source of truth.
const LAST_SEEN_KEY = 'myos-whats-new-last-seen'

// Computes how many release notes are newer than the supplied date.
// Exported so tests can verify the count logic without rendering the
// component.
export function computeUnseenCount(lastSeen: string | null | undefined): number {
  if (!lastSeen) return releaseNotes.length > 0 ? 1 : 0
  return releaseNotes.filter((group) => group.date > lastSeen).length
}

// Legacy helper kept so existing tests that import getUnseenCount keep
// working. Reads the cached value out of localStorage when available.
export function getUnseenCount(): number {
  let lastSeen: string | null = null
  try {
    lastSeen = typeof localStorage !== 'undefined' ? localStorage.getItem(LAST_SEEN_KEY) : null
  } catch {
    lastSeen = null
  }
  return computeUnseenCount(lastSeen)
}

export default function WhatsNew() {
  const whatsNewLastSeen = useAppStore((s) => s.whatsNewLastSeen)
  const navigate = useNavigate()
  const [unseenCount, setUnseenCount] = useState(() => computeUnseenCount(whatsNewLastSeen))

  // Recompute the badge whenever the persisted last-seen date changes.
  // The Releases page is what actually marks everything as seen, so this
  // effect just reflects that change back into the badge.
  useEffect(() => {
    setUnseenCount(computeUnseenCount(whatsNewLastSeen))
  }, [whatsNewLastSeen])

  const handleClick = useCallback(() => {
    navigate('/releases')
  }, [navigate])

  return (
    <button
      onClick={handleClick}
      className="group relative flex items-center gap-3 w-full px-4 py-1.5 rounded-lg transition-colors duration-200 cursor-pointer text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800/30"
      title="What's New"
      data-testid="whats-new-button"
    >
      <Icon name="auto_awesome" className="text-lg" />
      <span className="text-xs font-medium">What&apos;s New</span>
      {unseenCount > 0 && (
        <span
          className="ml-auto min-w-[16px] h-4 bg-blue-500 text-white text-[10px] font-bold rounded-full flex items-center justify-center px-1"
          data-testid="whats-new-badge"
        >
          {unseenCount > 9 ? '9+' : unseenCount}
        </span>
      )}
    </button>
  )
}
