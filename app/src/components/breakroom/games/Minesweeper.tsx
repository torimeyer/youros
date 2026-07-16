import { useCallback, useState, type MouseEvent } from 'react'
import {
  createBoard,
  randomMines,
  floodReveal,
  toggleFlag,
  isWin,
  countFlags,
  type Cell,
} from './minesweeperLogic'
import { recordBestTime, loadBest } from '../storage'
import { useConfirm } from '../../../hooks/useConfirm'
import ConfirmModal from '../../ConfirmModal'

// Intermediate board: 16 x 16 with 40 mines. Big, readable cells.
const ROWS = 16
const COLS = 16
const MINES = 40
const GAME_ID = 'minesweeper'

// Classic Minesweeper number colors (1-8).
const NUM_COLOR = [
  '',
  'text-[#0a3055]', // 1 blue
  'text-[#166534]', // 2 green
  'text-[#9d2424]', // 3 red
  'text-[#581c87]', // 4 purple
  'text-[#9a3412]', // 5 maroon
  'text-[#1e3a8a]', // 6 teal-ish
  'text-[#1a1c1e]', // 7 black
  'text-[#64748b]', // 8 gray
]

// Inline style for the raised bevel effect on unrevealed tiles.
const BEVEL_STYLE = {
  boxShadow:
    'inset 2px 2px 0 rgba(255,255,255,0.65), inset -2px -2px 0 rgba(0,0,0,0.28)',
}

const blank = () => createBoard(ROWS, COLS, [])

export default function Minesweeper() {
  const { confirm, confirmProps } = useConfirm()
  const [board, setBoard] = useState<Cell[][]>(blank)
  const [started, setStarted] = useState(false)
  const [status, setStatus] = useState<'playing' | 'won' | 'lost'>('playing')
  const [best, setBest] = useState<number | null>(loadBest(GAME_ID)?.bestMs ?? null)
  const [startMs, setStartMs] = useState(0)
  const [flagMode, setFlagMode] = useState(false)

  const reset = useCallback(() => {
    setBoard(blank())
    setStarted(false)
    setStatus('playing')
    setStartMs(0)
    setFlagMode(false)
  }, [])

  const minesLeft = MINES - countFlags(board)

  function flagAt(r: number, c: number) {
    if (status !== 'playing' || board[r][c].revealed) return
    setBoard(toggleFlag(board, r, c))
  }

  function clickCell(r: number, c: number) {
    if (status !== 'playing' || board[r][c].flagged) return
    if (flagMode) {
      flagAt(r, c)
      return
    }
    let b = board
    let started0 = started
    if (!started0) {
      b = createBoard(ROWS, COLS, randomMines(ROWS, COLS, MINES, Math.random, [r, c]))
      started0 = true
      setStarted(true)
      setStartMs(Date.now())
    }
    if (b[r][c].mine) {
      setBoard(b.map((row) => row.map((cell) => (cell.mine ? { ...cell, revealed: true } : cell))))
      setStatus('lost')
      void confirm({
        title: 'Game over',
        message: 'You hit a mine. Try again?',
        confirmLabel: 'Try again',
        cancelLabel: 'Quit',
      }).then((again) => {
        if (again) reset()
      })
      return
    }
    const nb = floodReveal(b, r, c)
    setBoard(nb)
    if (isWin(nb)) {
      setStatus('won')
      const ms = Date.now() - (startMs || Date.now())
      const isBest = recordBestTime(GAME_ID, ms)
      if (isBest) setBest(ms)
      void confirm({
        title: 'You won!',
        message: `Board cleared in ${Math.round(ms / 1000)}s.${isBest ? ' New best!' : ''} Play again?`,
        confirmLabel: 'Play again',
        cancelLabel: 'Quit',
      }).then((again) => {
        if (again) reset()
      })
    }
  }

  function rightClick(e: MouseEvent, r: number, c: number) {
    e.preventDefault()
    flagAt(r, c)
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 className="text-2xl font-bold text-slate-900 dark:text-white">Minesweeper</h2>
        <p className="text-xs text-slate-500 dark:text-slate-400">Clear every safe square without hitting a mine.</p>
      </div>

      {/* Status bar */}
      <div className="flex items-center justify-between gap-3 rounded-lg bg-slate-100 px-4 py-2 text-sm dark:bg-slate-800">
        <span className="flex items-center gap-2 font-bold text-slate-700 dark:text-slate-200">
          <span>💣</span> {String(Math.max(0, minesLeft)).padStart(2, '0')}
        </span>
        <button
          type="button"
          data-testid="mine-flag-toggle"
          onClick={() => setFlagMode((f) => !f)}
          aria-pressed={flagMode}
          className={[
            'rounded-lg px-3 py-1 text-sm font-medium transition-colors',
            flagMode
              ? 'bg-pink-500 text-white'
              : 'bg-white text-slate-700 hover:bg-slate-50 dark:bg-slate-700 dark:text-slate-200',
          ].join(' ')}
        >
          {flagMode ? '🚩 Flag mode' : '⛏️ Dig mode'}
        </button>
        <div className="flex items-center gap-3">
          {best != null && <span className="text-xs text-slate-500">Best: {Math.round(best / 1000)}s</span>}
          <button type="button" onClick={reset} className="text-xs font-bold text-slate-600 hover:underline dark:text-slate-300">
            Reset
          </button>
        </div>
      </div>

      {/* Board frame — raised bevel edge around the minefield */}
      <div
        className="w-full rounded border-2 border-slate-400 bg-slate-400 p-1 dark:border-slate-600 dark:bg-slate-700"
        style={{ boxShadow: 'inset 2px 2px 0 rgba(255,255,255,0.4), inset -2px -2px 0 rgba(0,0,0,0.3)' }}
      >
        <div className="grid gap-[1px]" style={{ gridTemplateColumns: `repeat(${COLS}, 1fr)` }}>
          {board.map((row, r) =>
            row.map((cell, c) => {
              if (cell.revealed) {
                // Revealed cell — flat, shows number or mine.
                return (
                  <div
                    key={`${r}-${c}`}
                    className={[
                      'flex aspect-square w-full select-none items-center justify-center text-xs font-bold',
                      'border border-slate-300 bg-slate-200 dark:border-slate-400 dark:bg-slate-300',
                      cell.adj && !cell.mine ? NUM_COLOR[cell.adj] : '',
                    ].join(' ')}
                  >
                    {cell.mine ? '💣' : cell.adj ? cell.adj : ''}
                  </div>
                )
              }
              // Unrevealed cell — raised bevel, presses in when clicked.
              return (
                <button
                  key={`${r}-${c}`}
                  type="button"
                  data-testid={`mine-${r}-${c}`}
                  onClick={() => clickCell(r, c)}
                  onContextMenu={(e) => rightClick(e, r, c)}
                  style={BEVEL_STYLE}
                  className={[
                    'flex aspect-square w-full select-none items-center justify-center rounded-sm text-sm',
                    'bg-slate-300 transition-[filter] duration-75 dark:bg-slate-500',
                    status === 'playing' && !cell.flagged
                      ? 'hover:brightness-110 active:brightness-90 active:[box-shadow:inset_-2px_-2px_0_rgba(255,255,255,0.65),inset_2px_2px_0_rgba(0,0,0,0.28)]'
                      : '',
                  ].join(' ')}
                >
                  {cell.flagged ? '🚩' : ''}
                </button>
              )
            }),
          )}
        </div>
      </div>
      <ConfirmModal {...confirmProps} />
    </div>
  )
}
