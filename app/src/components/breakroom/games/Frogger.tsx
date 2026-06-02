import { useEffect, useRef, useState } from 'react'
import { frogStatus, type Lane } from './froggerLogic'
import { isOverlayOpen } from '../storage'
import { useConfirm } from '../../../hooks/useConfirm'
import ConfirmModal from '../../ConfirmModal'

const COLS = 11
const ROWS = 9

const mod = (n: number, m: number) => ((n % m) + m) % m

type LaneDef = Lane & {
  dir: number
  /** cells per second */
  speed: number
  /** accumulated sub-cell offset in pixels for smooth rendering */
  offset: number
}

function initLanes(): LaneDef[] {
  return [
    { type: 'safe', occupied: [], dir: 0, speed: 0, offset: 0 },
    { type: 'river', occupied: [0, 1, 4, 5, 8, 9], dir: 1, speed: 0.9, offset: 0 },
    { type: 'river', occupied: [2, 3, 6, 7, 10], dir: -1, speed: 0.8, offset: 0 },
    { type: 'river', occupied: [1, 2, 5, 6, 9, 10], dir: 1, speed: 1.0, offset: 0 },
    { type: 'safe', occupied: [], dir: 0, speed: 0, offset: 0 },
    { type: 'road', occupied: [0, 4, 8], dir: -1, speed: 1.5, offset: 0 },
    { type: 'road', occupied: [2, 6, 10], dir: 1, speed: 1.3, offset: 0 },
    { type: 'road', occupied: [1, 5, 9], dir: -1, speed: 1.7, offset: 0 },
    { type: 'safe', occupied: [], dir: 0, speed: 0, offset: 0 },
  ]
}

// ─── drawing helpers (classic) ───────────────────────────────────────────────

function drawLane(ctx: CanvasRenderingContext2D, r: number, cell: number, type: 'safe' | 'river' | 'road') {
  const y = r * cell
  const totalW = COLS * cell
  if (type === 'safe') {
    ctx.fillStyle = '#15803d' // grass
    ctx.fillRect(0, y, totalW, cell)
  } else if (type === 'river') {
    ctx.fillStyle = '#1d4ed8' // water
    ctx.fillRect(0, y, totalW, cell)
  } else {
    ctx.fillStyle = '#374151' // road
    ctx.fillRect(0, y, totalW, cell)
    // dashed lane markings
    ctx.strokeStyle = 'rgba(255,255,255,0.4)'
    ctx.lineWidth = 2
    ctx.setLineDash([cell / 4, cell / 4])
    ctx.beginPath()
    ctx.moveTo(0, y + cell / 2)
    ctx.lineTo(totalW, y + cell / 2)
    ctx.stroke()
    ctx.setLineDash([])
  }
}

function drawObstacle(ctx: CanvasRenderingContext2D, x: number, y: number, cell: number, type: 'car' | 'log', color: string) {
  if (type === 'car') {
    ctx.fillStyle = color
    ctx.fillRect(x + 3, y + 5, cell - 6, cell - 10)
    ctx.fillStyle = 'rgba(255,255,255,0.85)' // windshield
    ctx.fillRect(x + cell / 2, y + 7, cell / 3, cell - 14)
  } else {
    ctx.fillStyle = '#8b5e34' // log
    ctx.fillRect(x + 1, y + 6, cell - 2, cell - 12)
    ctx.strokeStyle = 'rgba(0,0,0,0.25)'
    ctx.lineWidth = 1
    ctx.strokeRect(x + 1, y + 6, cell - 2, cell - 12)
  }
}

function drawObstacles(
  ctx: CanvasRenderingContext2D,
  occupied: number[],
  r: number,
  cell: number,
  dir: number,
  offset: number,
  isLog: boolean,
  color: string,
) {
  const totalW = COLS * cell
  const y = r * cell
  for (const c of occupied) {
    const rawX = c * cell + dir * offset
    for (const baseX of [rawX, rawX + totalW, rawX - totalW]) {
      drawObstacle(ctx, baseX, y, cell, isLog ? 'log' : 'car', color)
    }
  }
}

function drawFrog(ctx: CanvasRenderingContext2D, col: number, row: number, cell: number, offsetX: number) {
  const cx = col * cell + cell / 2 + offsetX
  const cy = row * cell + cell / 2
  const rad = cell * 0.32
  ctx.fillStyle = '#22c55e'
  ctx.beginPath()
  ctx.arc(cx, cy, rad, 0, Math.PI * 2)
  ctx.fill()
  // eyes
  ctx.fillStyle = '#ffffff'
  ctx.beginPath()
  ctx.arc(cx - rad / 2, cy - rad / 2, rad / 4, 0, Math.PI * 2)
  ctx.arc(cx + rad / 2, cy - rad / 2, rad / 4, 0, Math.PI * 2)
  ctx.fill()
  ctx.fillStyle = '#000000'
  ctx.beginPath()
  ctx.arc(cx - rad / 2, cy - rad / 2, rad / 8, 0, Math.PI * 2)
  ctx.arc(cx + rad / 2, cy - rad / 2, rad / 8, 0, Math.PI * 2)
  ctx.fill()
}

// ─── component ──────────────────────────────────────────────────────────────

export default function Frogger() {
  const { confirm, confirmProps } = useConfirm()
  const confirmRef = useRef(confirm)
  useEffect(() => {
    confirmRef.current = confirm
  }, [confirm])

  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const cellRef = useRef(32)
  const rafRef = useRef(0)
  const [wins, setWins] = useState(0)

  useEffect(() => {
    const game = {
      frog: { col: 5, row: 8 },
      lanes: initLanes(),
      status: 'playing' as 'playing' | 'dead' | 'home',
      lastT: 0,
    }

    const startLoop = () => {
      game.lastT = performance.now()
      rafRef.current = requestAnimationFrame(tick)
    }

    function draw() {
      const canvas = canvasRef.current
      if (!canvas) return
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      const cell = cellRef.current
      const { frog, lanes } = game

      for (let r = 0; r < ROWS; r++) {
        const ln = lanes[r]
        drawLane(ctx, r, cell, ln.type)
        if (ln.type === 'river') {
          drawObstacles(ctx, ln.occupied, r, cell, ln.dir, ln.offset, true, '#8b5e34')
        } else if (ln.type === 'road') {
          drawObstacles(ctx, ln.occupied, r, cell, ln.dir, ln.offset, false, r % 2 === 0 ? '#ef4444' : '#eab308')
        }
      }

      const fl = lanes[frog.row]
      const frogOffX =
        fl.type === 'river' && fl.dir !== 0 && fl.occupied.includes(frog.col) ? fl.dir * fl.offset : 0
      drawFrog(ctx, frog.col, frog.row, cell, frogOffX)
    }

    function check() {
      const { frog, lanes } = game
      if (frog.col < 0 || frog.col >= COLS) {
        endRound('dead')
        return
      }
      if (frog.row === 0) {
        endRound('home')
        return
      }
      const st = frogStatus(frog.col, lanes[frog.row])
      if (st.dead) endRound('dead')
    }

    function endRound(kind: 'dead' | 'home') {
      game.status = kind
      cancelAnimationFrame(rafRef.current)
      draw()
      if (kind === 'home') setWins((w) => w + 1)
      void confirmRef
        .current({
          title: kind === 'home' ? 'You won!' : 'Game over',
          message: kind === 'home' ? 'You made it across. Play again?' : "You didn't make it. Try again?",
          confirmLabel: 'Play again',
          cancelLabel: 'Quit',
        })
        .then((again) => {
          if (again) {
            game.frog = { col: 5, row: 8 }
            game.lanes = initLanes()
            game.status = 'playing'
            startLoop()
          }
        })
    }

    function tick(now: number) {
      const dt = Math.min(now - game.lastT, 50)
      game.lastT = now

      if (game.status !== 'playing') {
        rafRef.current = requestAnimationFrame(tick)
        return
      }

      // Pause movement while a how-to-play / dialog overlay is open.
      if (isOverlayOpen()) {
        draw()
        rafRef.current = requestAnimationFrame(tick)
        return
      }

      const cell = cellRef.current
      const { frog, lanes } = game

      for (let r = 0; r < ROWS; r++) {
        const ln = lanes[r]
        if (ln.dir === 0) continue
        ln.offset += (ln.speed * cell * dt) / 1000
        while (ln.offset >= cell) {
          ln.offset -= cell
          const frogOnLog = r === frog.row && ln.type === 'river' && ln.occupied.includes(frog.col)
          ln.occupied = ln.occupied.map((c) => mod(c + ln.dir, COLS))
          if (frogOnLog) frog.col += ln.dir
        }
      }

      check()
      if (game.status === 'playing') {
        draw()
        rafRef.current = requestAnimationFrame(tick)
      }
    }

    function onKey(e: KeyboardEvent) {
      if (game.status !== 'playing' || isOverlayOpen()) return
      let moved = true
      if (e.key === 'ArrowUp') game.frog.row = Math.max(0, game.frog.row - 1)
      else if (e.key === 'ArrowDown') game.frog.row = Math.min(ROWS - 1, game.frog.row + 1)
      else if (e.key === 'ArrowLeft') game.frog.col = Math.max(0, game.frog.col - 1)
      else if (e.key === 'ArrowRight') game.frog.col = Math.min(COLS - 1, game.frog.col + 1)
      else moved = false
      if (moved) {
        e.preventDefault()
        draw()
        check()
      }
    }

    function updateSize() {
      const container = containerRef.current
      const canvas = canvasRef.current
      if (!container || !canvas) return
      const maxH = Math.floor(window.innerHeight * 0.75)
      let cell = Math.floor(container.clientWidth / COLS)
      cell = Math.min(cell, Math.floor(maxH / ROWS))
      cell = Math.max(cell, 24)
      cellRef.current = cell
      const dpr = window.devicePixelRatio || 1
      canvas.width = COLS * cell * dpr
      canvas.height = ROWS * cell * dpr
      canvas.style.width = `${COLS * cell}px`
      canvas.style.height = `${ROWS * cell}px`
      const ctx = canvas.getContext('2d')
      if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      draw()
    }

    const ro = new ResizeObserver(updateSize)
    if (containerRef.current) ro.observe(containerRef.current)

    window.addEventListener('keydown', onKey)
    updateSize()
    startLoop()

    return () => {
      cancelAnimationFrame(rafRef.current)
      window.removeEventListener('keydown', onKey)
      ro.disconnect()
    }
  }, [])

  return (
    <div className="flex w-full flex-col items-center gap-4">
      <div className="flex w-full flex-col gap-1">
        <h2 className="text-2xl font-bold text-slate-900 dark:text-white">Frogger</h2>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Use the arrow keys. Cross the road and river to reach the top. Wins: {wins}
        </p>
      </div>
      <div ref={containerRef} className="w-full">
        <canvas
          ref={canvasRef}
          data-testid="frogger-canvas"
          className="mx-auto block rounded-lg border border-slate-300 dark:border-slate-700"
        />
      </div>
      <ConfirmModal {...confirmProps} />
    </div>
  )
}
