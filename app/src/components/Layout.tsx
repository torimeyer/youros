import { useEffect, useCallback } from 'react'
import { Outlet, useNavigate } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { ChatPanel } from './ChatPanel'
import { CommandPalette } from './CommandPalette'
import GuidedTour from './GuidedTour'
import NotificationToasts from './NotificationToast'
import { useAppStore } from '../stores/app'

// Sidebar page order for Cmd+1 through Cmd+8
const NAV_ROUTES = [
  '/',            // Cmd+1: Home
  '/tasks',       // Cmd+2: Tasks
  '/timeline',    // Cmd+3: Timeline
  '/ideas',       // Cmd+4: Ideas
  '/agents',      // Cmd+5: Agents
  '/files',       // Cmd+6: Files
  '/transcripts', // Cmd+7: Transcripts
  '/settings',    // Cmd+8: Settings
]

const ACCENT_CSS_MAP: Record<string, string> = {
  blue: '#3b82f6',
  pink: '#ec4899',
  purple: '#8b5cf6',
  cyan: '#06b6d4',
  orange: '#f97316',
}

export function Layout() {
  const chatOpen = useAppStore((s) => s.chatOpen)
  const chatWidth = useAppStore((s) => s.chatWidth)
  const isResizing = useAppStore((s) => s.isResizing)
  const darkMode = useAppStore((s) => s.darkMode)
  const accentColor = useAppStore((s) => s.accentColor)
  const commandPaletteOpen = useAppStore((s) => s.commandPaletteOpen)
  const setCommandPaletteOpen = useAppStore((s) => s.setCommandPaletteOpen)
  const toggleCommandPalette = useAppStore((s) => s.toggleCommandPalette)
  const toggleChat = useAppStore((s) => s.toggleChat)
  const showTour = useAppStore((s) => s.showTour)
  const setShowTour = useAppStore((s) => s.setShowTour)
  const tourComplete = useAppStore((s) => s.tourComplete)
  const navigate = useNavigate()

  const closeCommandPalette = useCallback(() => setCommandPaletteOpen(false), [setCommandPaletteOpen])

  // Auto-start the guided tour for first-time users who haven't completed it yet.
  useEffect(() => {
    if (!tourComplete) {
      // Tiny delay so the app chrome mounts before the tour points at elements.
      const t = setTimeout(() => setShowTour(true), 800)
      return () => clearTimeout(t)
    }
  }, [tourComplete, setShowTour])

  // Global keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Skip when user is typing in an input, textarea, or contenteditable
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement)?.isContentEditable) {
        return
      }

      if (!(e.metaKey || e.ctrlKey)) return

      switch (e.key) {
        case 'k':
          e.preventDefault()
          toggleCommandPalette()
          break
        case 'l':
          e.preventDefault()
          toggleChat()
          break
        case 'n':
          e.preventDefault()
          navigate('/tasks?new=1')
          break
        case '1':
        case '2':
        case '3':
        case '4':
        case '5':
        case '6':
        case '7':
        case '8': {
          const index = parseInt(e.key) - 1
          if (index < NAV_ROUTES.length) {
            e.preventDefault()
            navigate(NAV_ROUTES[index])
          }
          break
        }
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [toggleCommandPalette, toggleChat, navigate])

  // Apply dark/light mode to the document element
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', darkMode ? 'dark' : 'light')
  }, [darkMode])

  // Apply accent color as a CSS variable
  useEffect(() => {
    const hex = ACCENT_CSS_MAP[accentColor] || ACCENT_CSS_MAP.blue
    document.documentElement.style.setProperty('--color-accent', hex)
  }, [accentColor])

  return (
    <div data-theme={darkMode ? 'dark' : 'light'}>
      <Sidebar />
      <ChatPanel />
      <CommandPalette open={commandPaletteOpen} onClose={closeCommandPalette} />
      {showTour && <GuidedTour onComplete={() => setShowTour(false)} />}
      <NotificationToasts />
      <main
        data-testid="main-content"
        className={`ml-56 min-h-screen min-w-0 overflow-x-hidden ${isResizing ? '' : 'transition-[margin] duration-200'}`}
        style={chatOpen ? { marginRight: chatWidth } : undefined}
      >
        <Outlet />
      </main>
    </div>
  )
}
