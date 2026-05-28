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

const NUM_COLOR = [
  '',
  'text-[#0a3055]', // 1
  'text-[#166534]', // 2
  'text-[#9d2424]', // 3
  'text-[#581c87]', // 4
  'text-[#9a3412]', // 5
  'text-[#1e3a8a]', // 6
  'text-[#1a1c1e]', // 7
  'text-[#64748b]', // 8
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
    if (status !== 'playing' || board[r][c].revealed) return
    setBoard(toggleFlag(board, r, c))
  }

  return (
    <div className="flex flex-col gap-4 font-serif">
      <div className="flex flex-col gap-1 items-center italic">
        <h2 className="text-3xl text-[#0a3055] drop-shadow-sm">Minesweeper</h2>
        <p className="text-[10px] text-slate-500 uppercase tracking-widest">Navigate the archipelago without waking the sea dragons.</p>
      </div>

      {/* Status bar */}
      <div className="flex items-center justify-between border-b border-[#0a3055]/20 bg-[#f8f8f0] px-4 py-2 text-sm shadow-sm">
        <span className="flex items-center gap-2 font-bold text-[#9d2424]">
          <span className="text-xl">🐉</span> {String(minesLeft).padStart(3, '0')}
        </span>
        <span className="text-[10px] uppercase tracking-tighter text-slate-400">
          {status === 'playing'
            ? 'Read the ripples · Mark the beasts'
            : status === 'won'
              ? 'Board cleared'
              : 'The beast is awake'}
        </span>
        <div className="flex items-center justify-end gap-3">
          {best != null && (
            <span className="text-xs text-[#0a3055]/60">
              Record: {Math.round(best / 1000)}s
            </span>
          )}
          <button
            type="button"
            onClick={reset}
            className="text-xs font-bold text-[#0a3055] hover:underline"
          >
            Reset
          </button>
        </div>
      </div>

      {/* Board frame */}
      <div
        className="w-full bg-[#0a3055] p-1 shadow-2xl"
        style={{ backgroundImage: 'radial-gradient(circle at center, #0a3055 0%, #051a2e 100%)' }}
      >
        <div
          className="grid gap-[1px]"
          style={{ gridTemplateColumns: `repeat(${COLS}, 1fr)` }}
        >
          {board.map((row, r) =>
            row.map((cell, c) => {
              if (cell.revealed) {
                return (
                  <div
                    key={`${r}-${c}`}
                    className={[
                      'flex aspect-square w-full select-none items-center justify-center text-xs font-bold',
                      'bg-[#cbd5e0] relative overflow-hidden',
                      cell.adj && !cell.mine ? NUM_COLOR[cell.adj] : '',
                    ].join(' ')}
                    style={{
                      backgroundImage: 'linear-gradient(rgba(255,255,255,0.2) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.2) 1px, transparent 1px)',
                      backgroundSize: '4px 4px'
                    }}
                  >
                    {cell.mine ? '🐉' : cell.adj ? (
                       <span className="relative z-10 drop-shadow-sm scale-125">{cell.adj}</span>
                    ) : ''}
                    {/* Water ripple effect */}
                    {!cell.mine && !cell.adj && <div className="absolute inset-0 opacity-10 bg-[radial-gradient(circle,transparent_40%,rgba(255,255,255,0.5)_100%)]" />}
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
                  className={[
                    'flex aspect-square w-full select-none items-center justify-center relative overflow-hidden',
                    'bg-[#f8f8f0] transition-all duration-300',
                    status === 'playing' && !cell.flagged ? 'hover:bg-white' : '',
                  ].join(' ')}
                >
                  {/* Stylized wave pattern for unrevealed cells */}
                  <div className="absolute inset-0 opacity-20 pointer-events-none" style={{
                    background: 'radial-gradient(circle at 100% 100%, #0a3055 0%, transparent 70%)'
                  }} />
                  {cell.flagged ? (
                    <span className="text-sm z-10 drop-shadow-md">🏮</span>
                  ) : (
                    <div className="w-full h-full opacity-10 hover:opacity-20 transition-opacity" style={{
                      background: 'url("data:image/svg+xml,%3Csvg width=\'20\' height=\'20\' viewBox=\'0 0 20 20\' xmlns=\'http://www.w3.org/2000/svg\'%3E%3Cpath d=\'M0 10c5-5 5 5 10 0s5-5 10 0\' fill=\'none\' stroke=\'%230a3055\' stroke-width=\'1\'/%3E%3C/svg%3E") repeat-x bottom'
                    }} />
                  )}
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
