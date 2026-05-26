import { useState } from 'react'
import TopBar from '../components/TopBar'
import GameTile from '../components/breakroom/GameTile'
import GameShell from '../components/breakroom/GameShell'
import { GAMES } from '../components/breakroom/registry'

export default function BreakRoom() {
  const [activeId, setActiveId] = useState<string | null>(null)
  const [restartKey, setRestartKey] = useState(0)
  const active = GAMES.find((g) => g.id === activeId) ?? null
  const Active = active?.component

  return (
    <div className="min-h-dvh bg-slate-50 text-slate-900 dark:bg-slate-950 dark:text-white">
      <TopBar title="Break Room" />
      <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
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
                  <GameTile key={g.id} game={g} onPlay={setActiveId} />
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
            >
              <Active key={restartKey} />
            </GameShell>
          )}
        </div>
      </div>
    </div>
  )
}
