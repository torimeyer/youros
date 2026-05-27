import { useCallback, useEffect, useRef, useState } from 'react'
import { frogStatus, type Lane } from './froggerLogic'
import { useConfirm } from '../../../hooks/useConfirm'
import ConfirmModal from '../../ConfirmModal'

const COLS = 11
const ROWS = 9
const CELL = 32
const TICK = 420
const mod = (n: number, m: number) => ((n % m) + m) % m

/** Car fill colors, one per road lane (rows 5, 6, 7) */
const ROAD_COLORS = ['#ef4444', '#f97316', '#eab308']

type LaneDef = Lane & { dir: number }

function initLanes(): LaneDef[] {
  return [
    { type: 'safe',  occupied: [],                 dir:  0 },
    { type: 'river', occupied: [0, 1, 4, 5, 8, 9], dir:  1 },
    { type: 'river', occupied: [2, 3, 6, 7, 10],   dir: -1 },
    { type: 'river', occupied: [1, 2, 5, 6, 9, 10], dir:  1 },
    { type: 'safe',  occupied: [],                 dir:  0 },
    { type: 'road',  occupied: [0, 4, 8],          dir: -1 },
    { type: 'road',  occupied: [2, 6, 10],         dir:  1 },
    { type: 'road',  occupied: [1, 5, 9],          dir: -1 },
    { type: 'safe',  occupied: [],                 dir:  0 },
  ]
}

// ─── drawing primitives ────────────────────────────────────────────────────

function rrect(
  ctx: CanvasRenderingContext2D,
  x: number, y: number, w: number, h: number, r: number,
) {
  r = Math.min(r, w / 2, h / 2)
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.lineTo(x + w - r, y)
  ctx.arcTo(x + w, y,     x + w, y + r,     r)
  ctx.lineTo(x + w, y + h - r)
  ctx.arcTo(x + w, y + h, x + w - r, y + h, r)
  ctx.lineTo(x + r, y + h)
  ctx.arcTo(x,     y + h, x,     y + h - r, r)
  ctx.lineTo(x,    y + r)
  ctx.arcTo(x,     y,     x + r, y,         r)
  ctx.closePath()
}

// ─── lane backgrounds ──────────────────────────────────────────────────────

function drawSafeLane(ctx: CanvasRenderingContext2D, r: number) {
  const y = r * CELL
  // base grass
  ctx.fillStyle = '#15803d'
  ctx.fillRect(0, y, COLS * CELL, CELL)
  // alternating blade texture
  ctx.fillStyle = '#16a34a'
  for (let x = 0; x < COLS * CELL; x += 8) {
    ctx.fillRect(x, y, 4, CELL)
  }
  // bright top edge
  ctx.fillStyle = '#4ade80'
  ctx.fillRect(0, y, COLS * CELL, 2)
  // dark bottom shadow
  ctx.fillStyle = '#14532d'
  ctx.fillRect(0, y + CELL - 1, COLS * CELL, 1)
}

function drawRiverLane(ctx: CanvasRenderingContext2D, r: number) {
  const y = r * CELL
  // deep water
  ctx.fillStyle = '#1d4ed8'
  ctx.fillRect(0, y, COLS * CELL, CELL)
  // shimmer streaks
  ctx.fillStyle = '#3b82f6'
  for (let x = 0; x < COLS * CELL; x += 22) {
    ctx.fillRect(x,     y + 5,  13, 2)
    ctx.fillRect(x + 9, y + 14,  9, 2)
    ctx.fillRect(x + 2, y + 23, 15, 2)
  }
  // dark borders
  ctx.fillStyle = '#1e3a8a'
  ctx.fillRect(0, y,           COLS * CELL, 1)
  ctx.fillRect(0, y + CELL - 1, COLS * CELL, 1)
}

function drawRoadLane(ctx: CanvasRenderingContext2D, r: number) {
  const y = r * CELL
  // asphalt
  ctx.fillStyle = '#374151'
  ctx.fillRect(0, y, COLS * CELL, CELL)
  // subtle gravel texture
  ctx.fillStyle = '#4b5563'
  for (let x = 0; x < COLS * CELL; x += 7) {
    ctx.fillRect(x,     y + 3,  3, 2)
    ctx.fillRect(x + 3, y + 20, 3, 2)
  }
  // yellow centre dashes
  ctx.fillStyle = '#fbbf24'
  for (let x = 0; x < COLS * CELL; x += 20) {
    ctx.fillRect(x, y + CELL / 2 - 1, 12, 2)
  }
  // grey edge lines
  ctx.fillStyle = '#d1d5db'
  ctx.fillRect(0, y,           COLS * CELL, 1)
  ctx.fillRect(0, y + CELL - 1, COLS * CELL, 1)
}

// ─── sprites ───────────────────────────────────────────────────────────────

function drawCar(
  ctx: CanvasRenderingContext2D,
  c: number, r: number, color: string,
) {
  const x = c * CELL + 2
  const y = r * CELL + 5
  const w = CELL - 4
  const h = CELL - 10

  // body
  ctx.fillStyle = color
  rrect(ctx, x, y, w, h, 3)
  ctx.fill()

  // windshield
  ctx.fillStyle = 'rgba(186,230,253,0.75)'
  ctx.fillRect(x + 4, y + 2, w - 8, Math.floor(h * 0.44))

  // headlights
  ctx.fillStyle = '#fef3c7'
  ctx.fillRect(x + 1,     y + 2, 3, 3)
  ctx.fillRect(x + w - 4, y + 2, 3, 3)

  // tail lights
  ctx.fillStyle = '#fee2e2'
  ctx.fillRect(x + 1,     y + h - 5, 3, 3)
  ctx.fillRect(x + w - 4, y + h - 5, 3, 3)

  // outline
  ctx.strokeStyle = 'rgba(0,0,0,0.28)'
  ctx.lineWidth = 1
  rrect(ctx, x, y, w, h, 3)
  ctx.stroke()
}

/**
 * Groups consecutive occupied cells into multi-cell logs so they render
 * as one long plank instead of many tiny squares.
 */
function drawLogs(ctx: CanvasRenderingContext2D, occupied: number[], r: number) {
  const sorted = [...occupied].sort((a, b) => a - b)
  const groups: Array<{ start: number; len: number }> = []
  let i = 0
  while (i < sorted.length) {
    let j = i
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++
    groups.push({ start: sorted[i], len: j - i + 1 })
    i = j + 1
  }

  for (const { start, len } of groups) {
    const x = start * CELL + 1
    const y = r * CELL + 6
    const w = len * CELL - 2
    const h = CELL - 12

    // log body
    ctx.fillStyle = '#92400e'
    rrect(ctx, x, y, w, h, 4)
    ctx.fill()

    // wood grain lines
    ctx.fillStyle = '#78350f'
    for (let gi = 4; gi < h - 2; gi += 5) {
      ctx.fillRect(x + 5, y + gi, w - 10, 1)
    }

    // lighter highlight grain
    ctx.fillStyle = '#a16207'
    ctx.fillRect(x + 5, y + 2, w - 10, 2)

    // end-grain border ring
    ctx.strokeStyle = '#b45309'
    ctx.lineWidth = 1.5
    rrect(ctx, x + 1, y + 1, w - 2, h - 2, 3)
    ctx.stroke()

    // top sheen
    ctx.fillStyle = 'rgba(255,255,255,0.10)'
    ctx.fillRect(x + 4, y + 2, w - 8, 3)
  }
}

function drawFrog(ctx: CanvasRenderingContext2D, col: number, row: number) {
  const cx = col * CELL + CELL / 2
  const cy = row * CELL + CELL / 2

  // hind legs
  ctx.fillStyle = '#22c55e'
  ctx.beginPath()
  ctx.ellipse(cx - 8, cy + 7, 4, 3, -0.5, 0, Math.PI * 2)
  ctx.fill()
  ctx.beginPath()
  ctx.ellipse(cx + 8, cy + 7, 4, 3, 0.5, 0, Math.PI * 2)
  ctx.fill()

  // body
  ctx.fillStyle = '#4ade80'
  ctx.beginPath()
  ctx.ellipse(cx, cy + 1, 10, 8, 0, 0, Math.PI * 2)
  ctx.fill()

  // head
  ctx.fillStyle = '#22c55e'
  ctx.beginPath()
  ctx.ellipse(cx, cy - 5, 8, 7, 0, 0, Math.PI * 2)
  ctx.fill()

  // front legs
  ctx.fillStyle = '#4ade80'
  ctx.beginPath()
  ctx.ellipse(cx - 9, cy + 1, 3, 2, -0.3, 0, Math.PI * 2)
  ctx.fill()
  ctx.beginPath()
  ctx.ellipse(cx + 9, cy + 1, 3, 2, 0.3, 0, Math.PI * 2)
  ctx.fill()

  // eye whites
  ctx.fillStyle = '#ffffff'
  ctx.beginPath()
  ctx.arc(cx - 5, cy - 9, 3.5, 0, Math.PI * 2)
  ctx.fill()
  ctx.beginPath()
  ctx.arc(cx + 5, cy - 9, 3.5, 0, Math.PI * 2)
  ctx.fill()

  // pupils
  ctx.fillStyle = '#0f172a'
  ctx.beginPath()
  ctx.arc(cx - 5, cy - 9, 2, 0, Math.PI * 2)
  ctx.fill()
  ctx.beginPath()
  ctx.arc(cx + 5, cy - 9, 2, 0, Math.PI * 2)
  ctx.fill()

  // eye shine
  ctx.fillStyle = '#ffffff'
  ctx.beginPath()
  ctx.arc(cx - 4, cy - 10, 0.8, 0, Math.PI * 2)
  ctx.fill()
  ctx.beginPath()
  ctx.arc(cx + 6, cy - 10, 0.8, 0, Math.PI * 2)
  ctx.fill()

  // smile
  ctx.strokeStyle = '#16a34a'
  ctx.lineWidth = 1.2
  ctx.beginPath()
  ctx.arc(cx, cy - 2, 4, 0.3, Math.PI - 0.3)
  ctx.stroke()

  // body outline
  ctx.strokeStyle = '#16a34a'
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.ellipse(cx, cy + 1, 10, 8, 0, 0, Math.PI * 2)
  ctx.stroke()
}

// ─── component ─────────────────────────────────────────────────────────────

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
    let roadIdx = 0
    for (let r = 0; r < ROWS; r++) {
      const ln = g.lanes[r]
      if (ln.type === 'safe') {
        drawSafeLane(ctx, r)
      } else if (ln.type === 'river') {
        drawRiverLane(ctx, r)
        drawLogs(ctx, ln.occupied, r)
      } else if (ln.type === 'road') {
        drawRoadLane(ctx, r)
        const carColor = ROAD_COLORS[roadIdx % ROAD_COLORS.length]
        ln.occupied.forEach((c) => drawCar(ctx, c, r, carColor))
        roadIdx++
      }
    }
    drawFrog(ctx, g.frog.col, g.frog.row)
  }, [g])

  const endRound = useCallback(
    (kind: 'dead' | 'home') => {
      g.status = kind
      if (kind === 'home') setWins((w) => w + 1)
      void confirm({
        title: kind === 'home' ? 'Home safe' : 'Splat',
        message:
          kind === 'home'
            ? 'You made it across. Go again?'
            : 'The frog did not make it. Try again?',
        confirmLabel: kind === 'home' ? 'Again' : 'Retry',
        cancelLabel: 'Done',
      }).then((again) => {
        if (again) {
          g.frog  = { col: 5, row: 8 }
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
      if      (e.key === 'ArrowUp')    g.frog.row = Math.max(0,        g.frog.row - 1)
      else if (e.key === 'ArrowDown')  g.frog.row = Math.min(ROWS - 1, g.frog.row + 1)
      else if (e.key === 'ArrowLeft')  g.frog.col = Math.max(0,        g.frog.col - 1)
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
