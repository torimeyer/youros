import { useCallback, useState } from 'react'
import { rollDie, applyMove, isWin, CATS, SNAKES } from './catsAndSnakesLogic'
import { loadBest, recordFewestRolls, hasSeenHowTo, setNeverShowHowTo } from '../storage'

const GAME_ID = 'catsandsnakes'

// Pre-built grid: row 0 = top, col 0 = left. Snake order.
const GRID: number[][] = (() => {
  const rows: number[][] = []
  for (let r = 0; r < 10; r++) {
    const row: number[] = []
    const rfb = 9 - r
    for (let c = 0; c < 10; c++) {
      row.push(rfb % 2 === 0 ? rfb * 10 + c + 1 : (rfb + 1) * 10 - c)
    }
    rows.push(row)
  }
  return rows
})()

type Phase = 'idle' | 'player-moving' | 'computer-moving' | 'done'

export default function CatsAndSnakes() {
  const [playerPos, setPlayerPos] = useState(0) // 0 = not yet on board
  const [compPos, setCompPos] = useState(0)
  const [rollCount, setRollCount] = useState(0)
  const [lastRoll, setLastRoll] = useState<number | null>(null)
  const [phase, setPhase] = useState<Phase>('idle')
  const [playerWon, setPlayerWon] = useState(false)
  const [showResult, setShowResult] = useState(false)
  const [showHowTo, setShowHowTo] = useState(() => !hasSeenHowTo(GAME_ID))
  const [bestRolls, setBestRolls] = useState<number | undefined>(
    () => loadBest(GAME_ID)?.bestRolls,
  )

  const isOverlayOpen = showHowTo || showResult
  const canRoll = phase === 'idle' && !isOverlayOpen

  const doRoll = useCallback(() => {
    if (!canRoll) return
    const die = rollDie()
    const newCount = rollCount + 1
    setLastRoll(die)
    setRollCount(newCount)
    setPhase('player-moving')

    const nextPlayer = applyMove(playerPos, die)
    setPlayerPos(nextPlayer)

    if (isWin(nextPlayer)) {
      const improved = recordFewestRolls(GAME_ID, newCount)
      if (improved) setBestRolls(newCount)
      setPlayerWon(true)
      setShowResult(true)
      setPhase('done')
      return
    }

    setTimeout(() => {
      setPhase('computer-moving')
      const compNext = applyMove(compPos, rollDie())
      setCompPos(compNext)
      if (isWin(compNext)) {
        setPlayerWon(false)
        setShowResult(true)
        setPhase('done')
        return
      }
      setPhase('idle')
    }, 700)
  }, [canRoll, rollCount, playerPos, compPos])

  const restart = useCallback(() => {
    setPlayerPos(0)
    setCompPos(0)
    setRollCount(0)
    setLastRoll(null)
    setPhase('idle')
    setShowResult(false)
    setPlayerWon(false)
  }, [])

  const dismissHowTo = useCallback((never: boolean) => {
    if (never) setNeverShowHowTo(GAME_ID)
    setShowHowTo(false)
  }, [])

  return (
    <div className="flex flex-col gap-4">
      {/* Best score */}
      <div className="flex items-center justify-between text-sm text-slate-500 dark:text-slate-400">
        <span>
          {rollCount > 0 && phase !== 'done'
            ? `Roll ${rollCount}`
            : phase === 'done' && playerWon
              ? `Won in ${rollCount} roll${rollCount === 1 ? '' : 's'}`
              : null}
        </span>
        {bestRolls != null && (
          <span>Best: {bestRolls} roll{bestRolls === 1 ? '' : 's'}</span>
        )}
      </div>

      {/* Board */}
      <div className="overflow-x-auto">
        <div className="grid grid-cols-10 gap-px min-w-[300px]">
          {GRID.map((row) =>
            row.map((sq) => {
              const isCat = sq in CATS
              const isSnake = sq in SNAKES
              const hasPlayer = playerPos !== 0 && playerPos === sq
              const hasComp = compPos !== 0 && compPos === sq
              return (
                <div
                  key={sq}
                  className={[
                    'relative flex flex-col items-center justify-center rounded-sm border text-xs aspect-square',
                    isCat
                      ? 'bg-green-100 border-green-300 dark:bg-green-900/40 dark:border-green-700'
                      : isSnake
                        ? 'bg-red-100 border-red-300 dark:bg-red-900/40 dark:border-red-700'
                        : 'bg-slate-100 border-slate-200 dark:bg-slate-800 dark:border-slate-700',
                  ].join(' ')}
                >
                  <span className="text-[8px] leading-none text-slate-400 dark:text-slate-500">
                    {sq}
                  </span>
                  {isCat && <span className="text-[10px] leading-none">🐱</span>}
                  {isSnake && <span className="text-[10px] leading-none">🐍</span>}
                  {(hasPlayer || hasComp) && (
                    <div className="absolute inset-0 flex items-center justify-center gap-px">
                      {hasPlayer && <span className="text-sm leading-none drop-shadow">🧑</span>}
                      {hasComp && <span className="text-sm leading-none drop-shadow">🤖</span>}
                    </div>
                  )}
                </div>
              )
            }),
          )}
        </div>
      </div>

      {/* Controls */}
      <div className="flex items-center gap-3 flex-wrap">
        <button
          type="button"
          onClick={doRoll}
          disabled={!canRoll}
          className="flex items-center gap-2 rounded-lg px-5 py-2.5 font-semibold text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          🎲 Roll
        </button>
        <span className="text-sm text-slate-600 dark:text-slate-400 min-h-[1.5rem]">
          {phase === 'computer-moving'
            ? '🤖 computer rolling…'
            : lastRoll != null
              ? `You rolled a ${lastRoll}`
              : 'Tap Roll to start!'}
        </span>
        <button
          type="button"
          onClick={() => setShowHowTo(true)}
          className="ml-auto text-sm text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 transition-colors"
        >
          How to play
        </button>
      </div>

      {/* Key */}
      <div className="flex gap-4 text-xs text-slate-500 dark:text-slate-400">
        <span>🐱 Cat — ride up</span>
        <span>🐍 Snake — slide down</span>
        <span>🧑 You</span>
        <span>🤖 Computer</span>
      </div>

      {/* Win / lose dialog */}
      {showResult && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={playerWon ? 'You win!' : 'You lose!'}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
        >
          <div className="bg-white dark:bg-slate-900 rounded-xl shadow-xl p-8 flex flex-col items-center gap-4 max-w-sm w-full mx-4">
            <span className="text-5xl">{playerWon ? '🏆' : '😿'}</span>
            <h2 className="text-xl font-bold text-slate-900 dark:text-white">
              {playerWon ? 'You win!' : 'The cat wins!'}
            </h2>
            <p className="text-slate-600 dark:text-slate-400 text-center text-sm">
              {playerWon
                ? `You reached square 100 in ${rollCount} roll${rollCount === 1 ? '' : 's'}!`
                : 'The computer pounced to 100 first. Better luck next time!'}
            </p>
            <button
              type="button"
              onClick={restart}
              className="rounded-lg px-6 py-2.5 font-semibold text-white bg-indigo-600 hover:bg-indigo-700 transition-colors"
            >
              Play again
            </button>
          </div>
        </div>
      )}

      {/* How-to-play splash */}
      {showHowTo && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="How to play Cats & Snakes"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
        >
          <div className="bg-white dark:bg-slate-900 rounded-xl shadow-xl p-8 flex flex-col gap-4 max-w-sm w-full mx-4">
            <h2 className="text-xl font-bold text-slate-900 dark:text-white">How to play</h2>
            <ol className="list-decimal list-inside space-y-2 text-slate-700 dark:text-slate-300 text-sm">
              <li>Tap <strong>Roll</strong> to toss a die (1–6) and move your token forward.</li>
              <li>Land on a <strong>🐱 cat</strong> and ride it up the board.</li>
              <li>Land on a <strong>🐍 snake</strong> and slide back down.</li>
              <li>Reach square 100 <em>exactly</em> to win — overshoot and you bounce back.</li>
              <li>Race your computer opponent. First to 100 wins!</li>
            </ol>
            <div className="flex gap-3 justify-end mt-2">
              <button
                type="button"
                onClick={() => dismissHowTo(true)}
                className="text-sm text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 transition-colors"
              >
                Don't show again
              </button>
              <button
                type="button"
                onClick={() => dismissHowTo(false)}
                className="rounded-lg px-5 py-2 font-semibold text-white bg-indigo-600 hover:bg-indigo-700 transition-colors"
              >
                Got it
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
