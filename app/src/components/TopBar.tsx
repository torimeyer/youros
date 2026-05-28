import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import Icon from './Icon'
import { useAppStore } from '../stores/app'

interface TopBarProps {
  title: string
}

const isMac = () => navigator.platform.toUpperCase().includes('MAC') || navigator.userAgent.toUpperCase().includes('MAC OS')
const modKey = isMac() ? '⌘' : 'Ctrl+'

export default function TopBar({ title }: TopBarProps) {
  const navigate = useNavigate()
  const toggleChat = useAppStore((s) => s.toggleChat)
  const setCommandPaletteOpen = useAppStore((s) => s.setCommandPaletteOpen)
  const displayOsName = useAppStore((s) => s.displayOsName())
  const chatOpen = useAppStore((s) => s.chatOpen)
  const chatWidth = useAppStore((s) => s.chatWidth)
  const tourComplete = useAppStore((s) => s.tourComplete)
  const setShowTour = useAppStore((s) => s.setShowTour)
  const [isDesktop, setIsDesktop] = useState(() =>
    typeof window !== 'undefined' ? window.innerWidth >= 1024 : true
  )
  useEffect(() => {
    const mql = window.matchMedia('(min-width: 1024px)')
    const handler = (e: MediaQueryListEvent) => setIsDesktop(e.matches)
    mql.addEventListener('change', handler)
    return () => mql.removeEventListener('change', handler)
  }, [])
  const [isOffline, setIsOffline] = useState(!navigator.onLine)

  useEffect(() => {
    const goOnline = () => setIsOffline(false)
    const goOffline = () => setIsOffline(true)
    window.addEventListener('online', goOnline)
    window.addEventListener('offline', goOffline)
    return () => {
      window.removeEventListener('online', goOnline)
      window.removeEventListener('offline', goOffline)
    }
  }, [])

  return (
    <>
    {/* Flow spacer: same height as the fixed header so page content never
        slides underneath it. Pages must NOT add their own top padding for
        the TopBar offset. This spacer is the single source of truth. */}
    <div className="h-14 sm:h-16 shrink-0" aria-hidden="true" data-testid="topbar-spacer" />
    <header
      className="fixed top-0 left-0 lg:left-56 h-14 sm:h-16 bg-white dark:bg-slate-950/80 backdrop-blur-md border-b border-slate-200 dark:border-slate-800/50 flex items-center justify-between px-4 sm:px-8 z-40 transition-[right] duration-200"
      style={{ right: chatOpen && isDesktop ? chatWidth : 0 }}
    >
      <div className="flex items-center gap-4 pl-10 lg:pl-0">
        <span className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-sm sm:text-base">{title}</span>
      </div>

      <div data-tour="search" className="flex-1 max-w-md mx-2 sm:mx-8 hidden sm:block">
        <button
          onClick={() => setCommandPaletteOpen(true)}
          className="w-full relative group flex items-center bg-white dark:bg-slate-900 rounded-lg pl-10 pr-4 py-2 text-sm text-slate-500 hover:text-slate-600 dark:hover:text-slate-400 transition-all cursor-pointer border border-transparent hover:border-slate-200 dark:hover:border-slate-700"
        >
          <Icon name="search" className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 group-hover:text-blue-500" />
          <span>Search {displayOsName}</span>
          <kbd className="ml-auto text-[10px] font-mono text-slate-600 bg-slate-100 dark:bg-slate-800 rounded px-1.5 py-0.5 border border-slate-200 dark:border-slate-700">
            {`${modKey}K`}
          </kbd>
        </button>
      </div>

      <div className="flex items-center gap-2 sm:gap-4">
        {!tourComplete && (
          <button
            onClick={() => setShowTour(true)}
            className="flex items-center gap-1.5 px-2 py-1 text-slate-600 dark:text-slate-400 hover:text-blue-600 dark:hover:text-blue-400 transition-all text-xs"
            title="Start tour"
            data-testid="start-tour-btn"
          >
            <Icon name="play_circle" size={16} />
            <span className="hidden sm:inline">Start tour</span>
          </button>
        )}
        <button
          onClick={() => {
            navigate('/tasks')
            setTimeout(() => window.dispatchEvent(new CustomEvent('myos-quick-add-task')), 100)
          }}
          className="p-2.5 sm:p-2 text-slate-600 dark:text-slate-400 hover:text-blue-600 dark:hover:text-blue-400 transition-all"
          title="Add Needle"
        >
          <Icon name="add_task" />
        </button>
        <button
          onClick={toggleChat}
          className="p-2.5 sm:p-2 text-slate-600 dark:text-slate-400 hover:text-blue-600 dark:hover:text-blue-400 transition-all"
          title={`Toggle Chat (${modKey}L)`}
        >
          <Icon name="chat" />
        </button>
        {isOffline && (
          <div className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-amber-900/50 border border-amber-700/50">
            <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
            <span className="text-xs text-amber-700 dark:text-amber-300">Offline</span>
          </div>
        )}
        <div className="w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-bold bg-gradient-to-br from-pink-500 to-purple-600">
          {displayOsName.charAt(0).toUpperCase()}
        </div>
      </div>
    </header>
    </>
  )
}
