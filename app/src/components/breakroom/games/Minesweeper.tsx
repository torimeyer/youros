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

// Classic Windows Expert: 30 columns x 16 rows, 99 mines
const ROWS = 16
const COLS = 30
const MINES = 99
const GAME_ID = 'minesweeper'

// Classic Minesweeper digit colors (index = adjacent mine count)
const NUM_COLOR = [
  '',
  'text-blue-600 dark:text-blue-400',       // 1 - blue
  'text-green-600 dark:text-green-400',     // 2 - green
  'text-red-600 dark:text-red-400',         // 3 - red
  'text-indigo-800 dark:text-indigo-400',   // 4 - navy
  'text-rose-800 dark:text-rose-400',       // 5 - maroon
  'text-teal-600 dark:text-teal-400',       // 6 - teal
  'text-slate-800 dark:text-slate-200',     // 7 - black
  'text-slate-500 dark:text-slate-400',     // 8 - grey
]

// Inline-style for the raised bevel effect on unrevealed tiles
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

  const reset = useCallback(() => {
    setBoard(blank())
    setStarted(false)
    setStatus('playing')
    setStartMs(0)
  }, [])

  const minesLeft = MINES - countFlags(board)

  function clickCell(r: number, c: number) {
    if (status !== 'playing' || board[r][c].flagged) return
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
        title: 'Boom 💥',
        message: 'You hit a mine. Play again?',
        confirmLabel: 'Play again',
        cancelLabel: 'Done',
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
        title: '🎉 Cleared!',
        message: `Solved in ${Math.round(ms / 1000)}s.${isBest ? ' New best!' : ''} Play again?`,
        confirmLabel: 'Play again',
        cancelLabel: 'Done',
      }).then((again) => {
        if (again) reset()
      })
    }
  }

  function rightClick(e: MouseEvent, r: number, c: number) {
    e.preventDefault()
    if (status !== 'playing' || board[r][c].revealed) return
    setBoard(toggleFlag(board, r, c))
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Status bar */}
      <div className="flex items-center justify-between rounded border border-slate-200 bg-slate-100 px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-800">
        <span className="flex min-w-[3.5rem] items-center gap-1 font-mono font-bold tabular-nums text-slate-700 dark:text-slate-200">
          💣 {String(minesLeft).padStart(3, '0')}
        </span>
        <span className="text-xs text-slate-500 dark:text-slate-400">
          {status === 'playing'
            ? 'Left-click reveal · right-click flag'
            : status === 'won'
              ? '🎉 Board cleared!'
              : '💥 Better luck next time'}
        </span>
        <div className="flex min-w-[3.5rem] items-center justify-end gap-2">
          {best != null && (
            <span className="text-xs text-slate-400 dark:text-slate-500">
              {Math.round(best / 1000)}s
            </span>
          )}
          <button
            type="button"
            onClick={reset}
            aria-label="New game"
            className="rounded px-1.5 py-0.5 text-xs font-semibold text-slate-600 transition-colors hover:bg-slate-200 dark:text-slate-300 dark:hover:bg-slate-700"
          >
            ▶ New
          </button>
        </div>
      </div>

      {/* Board frame: full container width, cells fill available space */}
      <div
        className="w-full rounded border-2 border-slate-400 bg-slate-400 p-0.5 dark:border-slate-600 dark:bg-slate-600"
        style={{ boxShadow: 'inset 2px 2px 0 rgba(255,255,255,0.4), inset -2px -2px 0 rgba(0,0,0,0.3)' }}
      >
        <div
          className="grid gap-px"
          style={{ gridTemplateColumns: `repeat(${COLS}, 1fr)` }}
        >
          {board.map((row, r) =>
            row.map((cell, c) => {
              // Revealed cell: flat, shows number or mine
              if (cell.revealed) {
                return (
                  <div
                    key={`${r}-${c}`}
                    className={[
                      'flex aspect-square w-full select-none items-center justify-center text-xs font-bold',
                      'border border-slate-400 bg-slate-200 dark:border-slate-600 dark:bg-slate-800',
                      cell.adj && !cell.mine ? NUM_COLOR[cell.adj] : '',
                    ].join(' ')}
                  >
                    {cell.mine ? '💣' : cell.adj ? cell.adj : ''}
                  </div>
                )
              }

              // Unrevealed cell: raised bevel, clickable
              return (
                <button
                  key={`${r}-${c}`}
                  type="button"
                  data-testid={`mine-${r}-${c}`}
                  onClick={() => clickCell(r, c)}
                  onContextMenu={(e) => rightClick(e, r, c)}
                  style={BEVEL_STYLE}
                  className={[
                    'flex aspect-square w-full select-none items-center justify-center rounded-sm text-xs',
                    'bg-slate-300 transition-[filter] duration-75 dark:bg-slate-600',
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
