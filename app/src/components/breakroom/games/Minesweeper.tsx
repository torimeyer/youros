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

const ROWS = 9
const COLS = 9
const MINES = 10
const GAME_ID = 'minesweeper'
const NUM_COLOR = [
  '',
  'text-blue-600',
  'text-green-600',
  'text-red-600',
  'text-indigo-800',
  'text-rose-800',
  'text-teal-600',
  'text-slate-800',
  'text-slate-500',
]

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
        title: 'Boom',
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
        title: 'Cleared',
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
      <div className="flex flex-wrap items-center gap-4 text-sm text-slate-600 dark:text-slate-400">
        <span>Mines left: {minesLeft}</span>
        {best != null && <span>Best: {Math.round(best / 1000)}s</span>}
        <span>Left-click to reveal, right-click to flag.</span>
      </div>
      <div
        className="inline-grid gap-0.5"
        style={{ gridTemplateColumns: `repeat(${COLS}, 1.75rem)` }}
      >
        {board.map((row, r) =>
          row.map((cell, c) => {
            const base =
              'flex h-7 w-7 select-none items-center justify-center rounded-sm text-sm font-bold'
            if (cell.revealed) {
              return (
                <div
                  key={`${r}-${c}`}
                  className={`${base} bg-slate-200 dark:bg-slate-800 ${cell.adj ? NUM_COLOR[cell.adj] : ''}`}
                >
                  {cell.mine ? '✸' : cell.adj ? cell.adj : ''}
                </div>
              )
            }
            return (
              <button
                key={`${r}-${c}`}
                type="button"
                data-testid={`mine-${r}-${c}`}
                onClick={() => clickCell(r, c)}
                onContextMenu={(e) => rightClick(e, r, c)}
                className={`${base} bg-slate-300 hover:bg-slate-400 dark:bg-slate-700 dark:hover:bg-slate-600`}
              >
                {cell.flagged ? '⚑' : ''}
              </button>
            )
          }),
        )}
      </div>
      <ConfirmModal {...confirmProps} />
    </div>
  )
}
