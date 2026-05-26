import { useCallback, useEffect, useRef, useState } from 'react'
import { frogStatus, type Lane } from './froggerLogic'
import { useConfirm } from '../../../hooks/useConfirm'
import ConfirmModal from '../../ConfirmModal'

const COLS = 11
const ROWS = 9
const CELL = 32
const TICK = 420
const mod = (n: number, m: number) => ((n % m) + m) % m

type LaneDef = Lane & { dir: number }

function initLanes(): LaneDef[] {
  return [
    { type: 'safe', occupied: [], dir: 0 },
    { type: 'river', occupied: [0, 1, 4, 5, 8, 9], dir: 1 },
    { type: 'river', occupied: [2, 3, 6, 7, 10], dir: -1 },
    { type: 'river', occupied: [1, 2, 5, 6, 9, 10], dir: 1 },
    { type: 'safe', occupied: [], dir: 0 },
    { type: 'road', occupied: [0, 4, 8], dir: -1 },
    { type: 'road', occupied: [2, 6, 10], dir: 1 },
    { type: 'road', occupied: [1, 5, 9], dir: -1 },
    { type: 'safe', occupied: [], dir: 0 },
  ]
}

export default function Frogger() {
  const { confirm, confirmProps } = useConfirm()
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [wins, setWins] = useState(0)
  const g = useRef({
    frog: { col: 5, row: 8 },
    lanes: initLanes(),
    status: 'playing' as 'playing' | 'dead' | 'home',
  }).current

  const draw = useCallback(() => {
    const ctx = canvasRef.current?.getContext('2d')
    if (!ctx) return
    for (let r = 0; r < ROWS; r++) {
      const ln = g.lanes[r]
      ctx.fillStyle = ln.type === 'river' ? '#1e3a8a' : ln.type === 'road' ? '#1f2937' : '#14532d'
      ctx.fillRect(0, r * CELL, COLS * CELL, CELL)
      if (ln.type === 'road') {
        ctx.fillStyle = '#ef4444'
        ln.occupied.forEach((c) => ctx.fillRect(c * CELL + 2, r * CELL + 4, CELL - 4, CELL - 8))
      } else if (ln.type === 'river') {
        ctx.fillStyle = '#92400e'
        ln.occupied.forEach((c) => ctx.fillRect(c * CELL, r * CELL + 6, CELL, CELL - 12))
      }
    }
    ctx.fillStyle = '#4ade80'
    ctx.beginPath()
    ctx.arc(g.frog.col * CELL + CELL / 2, g.frog.row * CELL + CELL / 2, CELL / 2 - 4, 0, Math.PI * 2)
    ctx.fill()
  }, [g])

  const endRound = useCallback(
    (kind: 'dead' | 'home') => {
      g.status = kind
      if (kind === 'home') setWins((w) => w + 1)
      void confirm({
        title: kind === 'home' ? 'Home safe' : 'Splat',
        message: kind === 'home' ? 'You made it across. Go again?' : 'The frog did not make it. Try again?',
        confirmLabel: kind === 'home' ? 'Again' : 'Retry',
        cancelLabel: 'Done',
      }).then((again) => {
        if (again) {
          g.frog = { col: 5, row: 8 }
          g.lanes = initLanes()
          g.status = 'playing'
          draw()
        }
      })
    },
    [g, confirm, draw],
  )

  const check = useCallback(() => {
    if (g.status !== 'playing') return
    if (g.frog.row === 0) {
      endRound('home')
      return
    }
    const st = frogStatus(g.frog.col, g.lanes[g.frog.row])
    if (st.dead || g.frog.col < 0 || g.frog.col >= COLS) endRound('dead')
  }, [g, endRound])

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (g.status !== 'playing') return
      let moved = true
      if (e.key === 'ArrowUp') g.frog.row = Math.max(0, g.frog.row - 1)
      else if (e.key === 'ArrowDown') g.frog.row = Math.min(ROWS - 1, g.frog.row + 1)
      else if (e.key === 'ArrowLeft') g.frog.col = Math.max(0, g.frog.col - 1)
      else if (e.key === 'ArrowRight') g.frog.col = Math.min(COLS - 1, g.frog.col + 1)
      else moved = false
      if (moved) {
        e.preventDefault()
        draw()
        check()
      }
    }
    window.addEventListener('keydown', onKey)
    const id = setInterval(() => {
      if (g.status !== 'playing') return
      g.lanes = g.lanes.map((ln) =>
        ln.dir === 0 ? ln : { ...ln, occupied: ln.occupied.map((c) => mod(c + ln.dir, COLS)) },
      )
      const ln = g.lanes[g.frog.row]
      if (ln.type === 'river' && ln.dir !== 0) g.frog.col += ln.dir
      draw()
      check()
    }, TICK)
    draw()
    return () => {
      window.removeEventListener('keydown', onKey)
      clearInterval(id)
    }
  }, [g, draw, check])

  return (
    <div className="flex flex-col items-start gap-3">
      <div className="flex flex-wrap gap-4 text-sm text-slate-600 dark:text-slate-400">
        <span className="font-medium">Crossings: {wins}</span>
        <span>Arrow keys. Reach the top: cross the roads, ride the logs, stay out of the water.</span>
      </div>
      <canvas
        ref={canvasRef}
        width={COLS * CELL}
        height={ROWS * CELL}
        data-testid="frogger-canvas"
        className="max-w-full rounded-lg border border-slate-300 dark:border-slate-700"
      />
      <ConfirmModal {...confirmProps} />
    </div>
  )
}
