import { useEffect, useCallback } from 'react'
import { Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { ChatPanel } from './ChatPanel'
import { CommandPalette } from './CommandPalette'
import { useAppStore } from '../stores/app'

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

  const closeCommandPalette = useCallback(() => setCommandPaletteOpen(false), [setCommandPaletteOpen])

  // Global Cmd+K / Ctrl+K listener
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        toggleCommandPalette()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [toggleCommandPalette])

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
      <main
        className={`ml-56 min-h-screen ${isResizing ? '' : 'transition-[margin] duration-200'}`}
        style={chatOpen ? { marginRight: chatWidth } : undefined}
      >
        <Outlet />
      </main>
    </div>
  )
}
