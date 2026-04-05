import Icon from './Icon'
import { useAppStore } from '../stores/app'

interface TopBarProps {
  title: string
}

export default function TopBar({ title }: TopBarProps) {
  const toggleChat = useAppStore((s) => s.toggleChat)
  const setCommandPaletteOpen = useAppStore((s) => s.setCommandPaletteOpen)
  const osName = useAppStore((s) => s.osName)

  return (
    <header className="fixed top-0 right-0 left-56 h-16 bg-slate-950/80 backdrop-blur-md border-b border-slate-800/50 flex items-center justify-between px-8 z-40">
      <div className="flex items-center gap-4">
        <span className="font-bold text-slate-100 tracking-tight">{title}</span>
      </div>

      <div className="flex-1 max-w-md mx-8">
        <button
          onClick={() => setCommandPaletteOpen(true)}
          className="w-full relative group flex items-center bg-slate-900 rounded-lg pl-10 pr-4 py-2 text-sm text-slate-500 hover:text-slate-400 transition-all cursor-pointer border border-transparent hover:border-slate-700"
        >
          <Icon name="search" className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 group-hover:text-blue-500" />
          <span>Search {osName}</span>
          <kbd className="ml-auto text-[10px] font-mono text-slate-600 bg-slate-800 rounded px-1.5 py-0.5 border border-slate-700">
            ⌘K
          </kbd>
        </button>
      </div>

      <div className="flex items-center gap-4">
        <button
          onClick={toggleChat}
          className="p-2 text-slate-400 hover:text-blue-400 transition-all"
          title="Toggle Chat (⌘L)"
        >
          <Icon name="chat" />
        </button>
        <button
          onClick={() => alert('No new notifications')}
          className="p-2 text-slate-400 hover:text-blue-400 transition-all relative"
        >
          <Icon name="notifications" />
          <span className="absolute top-2 right-2 w-2 h-2 bg-pink-500 rounded-full" />
        </button>
        <div className="w-8 h-8 rounded-full bg-gradient-to-br from-pink-500 to-purple-600 flex items-center justify-center text-white text-xs font-bold">
          {osName.charAt(0).toUpperCase()}
        </div>
      </div>
    </header>
  )
}
