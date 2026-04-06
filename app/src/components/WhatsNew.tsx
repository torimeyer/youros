import { useState, useEffect, useCallback } from 'react'
import Icon from './Icon'
import releaseNotes from '../data/releaseNotes'

const LAST_SEEN_KEY = 'myos-whats-new-last-seen'

function getLastSeenDate(): string | null {
  return localStorage.getItem(LAST_SEEN_KEY)
}

function setLastSeenDate(date: string) {
  localStorage.setItem(LAST_SEEN_KEY, date)
}

export function getUnseenCount(): number {
  const lastSeen = getLastSeenDate()
  if (!lastSeen) return releaseNotes.length > 0 ? 1 : 0
  return releaseNotes.filter((group) => group.date > lastSeen).length
}

export default function WhatsNew() {
  const [open, setOpen] = useState(false)
  const [unseenCount, setUnseenCount] = useState(getUnseenCount)

  const handleOpen = useCallback(() => {
    setOpen(true)
    if (releaseNotes.length > 0) {
      setLastSeenDate(releaseNotes[0].date)
      setUnseenCount(0)
    }
  }, [])

  // Recalculate unseen count when localStorage changes from another tab
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === LAST_SEEN_KEY) {
        setUnseenCount(getUnseenCount())
      }
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  return (
    <div className="relative">
      <button
        onClick={handleOpen}
        className="relative p-2 text-slate-400 hover:text-yellow-400 transition-all"
        title="What's New"
        data-testid="whats-new-button"
      >
        <Icon name="auto_awesome" />
        {unseenCount > 0 && (
          <span
            className="absolute top-1 right-1 min-w-[16px] h-4 bg-yellow-500 text-white text-[9px] font-bold rounded-full flex items-center justify-center px-1"
            data-testid="whats-new-badge"
          >
            {unseenCount > 9 ? '9+' : unseenCount}
          </span>
        )}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40 bg-black/50" onClick={() => setOpen(false)} />
          <div
            className="fixed inset-x-0 top-20 mx-auto w-full max-w-lg max-h-[70vh] bg-slate-900 border border-slate-800 rounded-xl shadow-2xl z-50 overflow-hidden flex flex-col"
            data-testid="whats-new-modal"
          >
            <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between shrink-0">
              <div className="flex items-center gap-2">
                <Icon name="auto_awesome" className="text-yellow-400" size={20} />
                <span className="text-lg font-semibold text-white">What's New</span>
              </div>
              <button
                onClick={() => setOpen(false)}
                className="text-slate-500 hover:text-white transition-colors"
                data-testid="whats-new-close"
              >
                <Icon name="close" size={20} />
              </button>
            </div>

            <div className="overflow-y-auto flex-1 px-6 py-4 space-y-6">
              {releaseNotes.map((group) => (
                <div key={group.date}>
                  <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wide mb-3">
                    {group.label}
                  </h3>
                  <div className="space-y-3">
                    {group.entries.map((entry) => (
                      <div
                        key={entry.title}
                        className="bg-slate-800/50 rounded-lg px-4 py-3"
                      >
                        <p className="text-sm font-medium text-white">{entry.title}</p>
                        <p className="text-xs text-slate-400 mt-1 leading-relaxed">
                          {entry.description}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
