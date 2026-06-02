import { useState } from 'react'
import PageShell from '../components/PageShell'
import GameTile from '../components/breakroom/GameTile'
import GameShell from '../components/breakroom/GameShell'
import HowToPlay from '../components/breakroom/HowToPlay'
import { GAMES } from '../components/breakroom/registry'
import { hasSeenHowTo, setNeverShowHowTo } from '../components/breakroom/storage'

export default function BreakRoom() {
  const [activeId, setActiveId] = useState<string | null>(null)
  const [restartKey, setRestartKey] = useState(0)
  // The how-to-play splash overlays the (already mounted) game. Game loops
  // pause themselves while it is open via storage.isOverlayOpen(), so the
  // game never advances behind the instructions. Restarting (the key flip)
  // never re-opens it.
  const [showHowTo, setShowHowTo] = useState(false)
  const active = GAMES.find((g) => g.id === activeId) ?? null
  const Active = active?.component

  function openGame(id: string) {
    setActiveId(id)
    setShowHowTo(!hasSeenHowTo(id))
  }

  return (
    <PageShell title="The Arcade">
        <div className="mx-auto max-w-6xl">
          {!active || !Active ? (
            <>
              <p className="mb-6 text-sm text-slate-600 dark:text-slate-400">
                Step away for a minute. Pick something to play.
              </p>
              <div
                data-testid="breakroom-grid"
                className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
              >
                {GAMES.map((g) => (
                  <GameTile key={g.id} game={g} onPlay={openGame} />
                ))}
              </div>
            </>
          ) : (
            <GameShell
              gameId={active.id}
              title={active.name}
              icon={active.icon}
              onExit={() => setActiveId(null)}
              onRestart={() => setRestartKey((k) => k + 1)}
              onHelp={() => setShowHowTo(true)}
            >
              <Active key={restartKey} />
              {showHowTo && (
                <HowToPlay
                  title={active.name}
                  steps={active.howTo}
                  onPlay={() => setShowHowTo(false)}
                  onNeverShow={() => setNeverShowHowTo(active.id)}
                />
              )}
            </GameShell>
          )}
        </div>
    </PageShell>
  )
}
