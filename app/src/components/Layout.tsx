import { useState, useEffect, useCallback } from 'react'
import { Outlet, useNavigate } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { ChatPanel } from './ChatPanel'
import { CommandPalette } from './CommandPalette'
import GuidedTour from './GuidedTour'
import NotificationToasts from './NotificationToast'
import ReleaseNotesWatcher from './ReleaseNotesWatcher'
import { useUserActivity } from '../hooks/useUserActivity'
import { useAdhdMode } from '../hooks/useAdhdMode'
import { useAppStore } from '../stores/app'
import AdhdCheckin from './AdhdCheckin'
import WelcomeBack from './WelcomeBack'
import { Footer } from './Footer'

// Sidebar page order for Cmd+1 through Cmd+8
const NAV_ROUTES = [
  '/',            // Cmd+1: Home
  '/tasks',       // Cmd+2: Tasks
  '/timeline',    // Cmd+3: Timeline
  '/activity',    // Cmd+4: Activity
  '/agents',      // Cmd+5: Agents
  '/specs',       // Cmd+6: Specs
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

// lg breakpoint in Tailwind is 1024px
const LG_BREAKPOINT = 1024

export function Layout() {
  const chatOpen = useAppStore((s) => s.chatOpen)
  const chatWidth = useAppStore((s) => s.chatWidth)
  const isResizing = useAppStore((s) => s.isResizing)
  const darkMode = useAppStore((s) => s.darkMode)
  const accentColor = useAppStore((s) => s.accentColor)
  const teamAccentColor = useAppStore((s) => s.teamAccentColor)
  const instanceMode = useAppStore((s) => s.instanceMode)
  const compactMode = useAppStore((s) => s.compactMode)
  const fontSize = useAppStore((s) => s.fontSize)
  const sidebarPosition = useAppStore((s) => s.sidebarPosition)
  const cardStyle = useAppStore((s) => s.cardStyle)
  const commandPaletteOpen = useAppStore((s) => s.commandPaletteOpen)
  const setCommandPaletteOpen = useAppStore((s) => s.setCommandPaletteOpen)
  const toggleCommandPalette = useAppStore((s) => s.toggleCommandPalette)
  const toggleChat = useAppStore((s) => s.toggleChat)
  const setChatOpen = useAppStore((s) => s.setChatOpen)
  const showTour = useAppStore((s) => s.showTour)
  const setShowTour = useAppStore((s) => s.setShowTour)
  const tourComplete = useAppStore((s) => s.tourComplete)
  const navigate = useNavigate()

  useUserActivity()
  const { config: adhdConfig, checkIn, showWelcomeBack, contextRebuild, dismissWelcomeBack } = useAdhdMode()

  // Track whether the viewport is at desktop width so we can decide
  // whether the chat panel should push main content or overlay it.
  const [isDesktop, setIsDesktop] = useState(() =>
    typeof window !== 'undefined' ? window.innerWidth >= LG_BREAKPOINT : true
  )
  useEffect(() => {
    const mql = window.matchMedia(`(min-width: ${LG_BREAKPOINT}px)`)
    const handler = (e: MediaQueryListEvent) => setIsDesktop(e.matches)
    mql.addEventListener('change', handler)
    return () => mql.removeEventListener('change', handler)
  }, [])

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
        case 'm':
        case 'M':
          if (e.shiftKey) {
            e.preventDefault()
            setChatOpen(true)
          }
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
  }, [toggleCommandPalette, toggleChat, setChatOpen, navigate])

  // Apply dark/light mode to the document element
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', darkMode ? 'dark' : 'light')
  }, [darkMode])

  // Apply accent color as a CSS variable
  useEffect(() => {
    const hex = ACCENT_CSS_MAP[accentColor] || ACCENT_CSS_MAP.blue
    document.documentElement.style.setProperty('--color-accent', hex)
  }, [accentColor])

  // Apply team accent color when in team mode
  useEffect(() => {
    document.documentElement.style.setProperty('--color-team', teamAccentColor)
    document.documentElement.setAttribute('data-instance-mode', instanceMode)
  }, [teamAccentColor, instanceMode])

  // Apply font size as a CSS variable on :root
  useEffect(() => {
    const sizeMap: Record<string, string> = { small: '14px', medium: '16px', large: '18px' }
    document.documentElement.style.setProperty('--app-font-size', sizeMap[fontSize] || '16px')
    document.documentElement.setAttribute('data-font-size', fontSize)
  }, [fontSize])

  // Apply compact mode as a data attribute on :root
  useEffect(() => {
    document.documentElement.setAttribute('data-compact', compactMode ? 'true' : 'false')
  }, [compactMode])

  // Apply sidebar position as a data attribute
  useEffect(() => {
    document.documentElement.setAttribute('data-sidebar', sidebarPosition)
  }, [sidebarPosition])

  // Apply card style as a data attribute
  useEffect(() => {
    document.documentElement.setAttribute('data-card-style', cardStyle)
  }, [cardStyle])

  return (
    <div data-theme={darkMode ? 'dark' : 'light'}>
      <Sidebar />
      <ChatPanel />
      <CommandPalette open={commandPaletteOpen} onClose={closeCommandPalette} />
      {showTour && <GuidedTour onComplete={() => setShowTour(false)} />}
      <NotificationToasts />
      <ReleaseNotesWatcher />
      {adhdConfig.enabled && checkIn && (
        <AdhdCheckin
          running_count={checkIn.running_count}
          agents={checkIn.agents}
          intervalSeconds={adhdConfig.check_in_seconds}
        />
      )}
      {showWelcomeBack && contextRebuild && (
        <WelcomeBack context={contextRebuild} onDismiss={dismissWelcomeBack} />
      )}
      <main
        data-testid="main-content"
        className={`min-h-dvh min-w-0 overflow-x-hidden ${isResizing ? '' : 'transition-[margin] duration-200'} ${
          sidebarPosition === 'right' ? 'ml-0 lg:mr-56' : 'ml-0 lg:ml-56'
        }`}
        style={chatOpen && isDesktop ? (sidebarPosition === 'right' ? { marginLeft: chatWidth } : { marginRight: chatWidth }) : undefined}
      >
        <Outlet />
        <Footer />
      </main>
    </div>
  )
}
