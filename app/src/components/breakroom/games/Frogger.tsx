import { useEffect, useRef, useState } from 'react'
import { frogStatus, type Lane } from './froggerLogic'
import { useConfirm } from '../../../hooks/useConfirm'
import ConfirmModal from '../../ConfirmModal'

const COLS = 11
const ROWS = 9

const ROAD_COLORS = ['#ef4444', '#f97316', '#eab308']

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
    { type: 'safe',  occupied: [],                  dir:  0, speed: 0,   offset: 0 },
    { type: 'river', occupied: [0, 1, 4, 5, 8, 9],  dir:  1, speed: 0.9, offset: 0 },
    { type: 'river', occupied: [2, 3, 6, 7, 10],    dir: -1, speed: 0.8, offset: 0 },
    { type: 'river', occupied: [1, 2, 5, 6, 9, 10], dir:  1, speed: 1.0, offset: 0 },
    { type: 'safe',  occupied: [],                  dir:  0, speed: 0,   offset: 0 },
    { type: 'road',  occupied: [0, 4, 8],           dir: -1, speed: 1.5, offset: 0 },
    { type: 'road',  occupied: [2, 6, 10],          dir:  1, speed: 1.3, offset: 0 },
    { type: 'road',  occupied: [1, 5, 9],           dir: -1, speed: 1.7, offset: 0 },
    { type: 'safe',  occupied: [],                  dir:  0, speed: 0,   offset: 0 },
  ]
}

// ─── drawing helpers ────────────────────────────────────────────────────────

function rrect(
  ctx: CanvasRenderingContext2D,
  x: number, y: number, w: number, h: number, r: number,
) {
  r = Math.min(r, w / 2, h / 2)
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.lineTo(x + w - r, y)
  ctx.arcTo(x + w, y, x + w, y + r, r)
  ctx.lineTo(x + w, y + h - r)
  ctx.arcTo(x + w, y + h, x + w - r, y + h, r)
  ctx.lineTo(x + r, y + h)
  ctx.arcTo(x, y + h, x, y + h - r, r)
  ctx.lineTo(x, y + r)
  ctx.arcTo(x, y, x + r, y, r)
  ctx.closePath()
}

function drawSafeLane(ctx: CanvasRenderingContext2D, r: number, cell: number) {
  const y = r * cell
  const totalW = COLS * cell
  ctx.fillStyle = '#15803d'
  ctx.fillRect(0, y, totalW, cell)
  ctx.fillStyle = '#16a34a'
  const stride = Math.max(6, Math.floor(cell / 5))
  for (let x = 0; x < totalW; x += stride * 2) ctx.fillRect(x, y, stride, cell)
  ctx.fillStyle = '#4ade80'
  ctx.fillRect(0, y, totalW, 2)
  ctx.fillStyle = '#14532d'
  ctx.fillRect(0, y + cell - 1, totalW, 1)
}

function drawRiverLane(ctx: CanvasRenderingContext2D, r: number, cell: number) {
  const y = r * cell
  const totalW = COLS * cell
  ctx.fillStyle = '#1d4ed8'
  ctx.fillRect(0, y, totalW, cell)
  ctx.fillStyle = '#3b82f6'
  const step = Math.max(16, Math.floor(cell * 0.7))
  for (let x = 0; x < totalW; x += step) {
    ctx.fillRect(x, y + Math.floor(cell * 0.16), Math.floor(cell * 0.4), 2)
    ctx.fillRect(x + Math.floor(step * 0.4), y + Math.floor(cell * 0.44), Math.floor(cell * 0.28), 2)
  }
  ctx.fillStyle = '#1e3a8a'
  ctx.fillRect(0, y, totalW, 1)
  ctx.fillRect(0, y + cell - 1, totalW, 1)
}

function drawRoadLane(ctx: CanvasRenderingContext2D, r: number, cell: number) {
  const y = r * cell
  const totalW = COLS * cell
  ctx.fillStyle = '#374151'
  ctx.fillRect(0, y, totalW, cell)
  ctx.fillStyle = '#4b5563'
  const tx = Math.max(5, Math.floor(cell * 0.2))
  for (let x = 0; x < totalW; x += tx * 2) {
    ctx.fillRect(x, y + Math.floor(cell * 0.1), Math.floor(tx * 0.6), 2)
    ctx.fillRect(x + tx, y + Math.floor(cell * 0.62), Math.floor(tx * 0.6), 2)
  }
  ctx.fillStyle = '#fbbf24'
  const dashW = Math.max(8, Math.floor(cell * 0.38))
  for (let x = 0; x < totalW; x += dashW * 2) ctx.fillRect(x, y + Math.floor(cell / 2) - 1, dashW, 2)
  ctx.fillStyle = '#d1d5db'
  ctx.fillRect(0, y, totalW, 1)
  ctx.fillRect(0, y + cell - 1, totalW, 1)
}

function drawOneCar(ctx: CanvasRenderingContext2D, x: number, y: number, cell: number, color: string) {
  const pad = Math.floor(cell * 0.06)
  const vpad = Math.floor(cell * 0.16)
  const bx = x + pad
  const by = y + vpad
  const w = cell - pad * 2
  const h = cell - vpad * 2
  const r = Math.min(3, Math.floor(cell * 0.1))
  const lw = Math.max(2, Math.floor(cell * 0.08))

  ctx.fillStyle = color
  rrect(ctx, bx, by, w, h, r)
  ctx.fill()

  ctx.fillStyle = 'rgba(186,230,253,0.75)'
  ctx.fillRect(bx + 4, by + 2, w - 8, Math.floor(h * 0.44))

  ctx.fillStyle = '#fef3c7'
  ctx.fillRect(bx + 1, by + 2, lw, lw)
  ctx.fillRect(bx + w - 1 - lw, by + 2, lw, lw)
  ctx.fillStyle = '#fee2e2'
  ctx.fillRect(bx + 1, by + h - 2 - lw, lw, lw)
  ctx.fillRect(bx + w - 1 - lw, by + h - 2 - lw, lw, lw)

  ctx.strokeStyle = 'rgba(0,0,0,0.28)'
  ctx.lineWidth = 1
  rrect(ctx, bx, by, w, h, r)
  ctx.stroke()
}

function drawOneLog(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, cell: number) {
  const r = Math.min(4, Math.floor(cell * 0.12))
  ctx.fillStyle = '#92400e'
  rrect(ctx, x, y, w, h, r)
  ctx.fill()
  ctx.fillStyle = '#78350f'
  for (let gi = 4; gi < h - 2; gi += Math.max(4, Math.floor(cell * 0.16))) {
    ctx.fillRect(x + 5, y + gi, w - 10, 1)
  }
  ctx.fillStyle = '#a16207'
  ctx.fillRect(x + 5, y + 2, w - 10, 2)
  ctx.strokeStyle = '#b45309'
  ctx.lineWidth = 1.5
  rrect(ctx, x + 1, y + 1, w - 2, h - 2, Math.max(3, r - 1))
  ctx.stroke()
  ctx.fillStyle = 'rgba(255,255,255,0.10)'
  ctx.fillRect(x + 4, y + 2, w - 8, 3)
}

/**
 * Draw vehicles (cars or logs) at fractional offset positions.
 * rawX = col * cell + dir * offset. Draw at rawX and wrapped copies so
 * objects wrap smoothly at canvas edges without a visible jump.
 */
function drawVehicles(
  ctx: CanvasRenderingContext2D,
  occupied: number[], r: number,
  cell: number, dir: number, offset: number,
  isLog: boolean, color: string,
) {
  const totalW = COLS * cell
  const y = r * cell

  if (isLog) {
    const sorted = [...occupied].sort((a, b) => a - b)
    const groups: { start: number; len: number }[] = []
    let i = 0
    while (i < sorted.length) {
      let j = i
      while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++
      groups.push({ start: sorted[i], len: j - i + 1 })
      i = j + 1
    }
    for (const { start, len } of groups) {
      const rawX = start * cell + dir * offset
      const logW = len * cell - 2
      const logY = y + Math.floor(cell * 0.19)
      const logH = Math.floor(cell * 0.62)
      for (const baseX of [rawX, rawX + totalW, rawX - totalW]) {
        drawOneLog(ctx, baseX + 1, logY, logW, logH, cell)
      }
    }
  } else {
    for (const c of occupied) {
      const rawX = c * cell + dir * offset
      for (const baseX of [rawX, rawX + totalW, rawX - totalW]) {
        drawOneCar(ctx, baseX, y, cell, color)
      }
    }
  }
}

function drawFrog(
  ctx: CanvasRenderingContext2D,
  col: number, row: number,
  cell: number, offsetX: number,
) {
  const s = cell / 32
  const cx = col * cell + cell / 2 + offsetX
  const cy = row * cell + cell / 2

  ctx.fillStyle = '#22c55e'
  ctx.beginPath(); ctx.ellipse(cx - 8 * s, cy + 7 * s, 4 * s, 3 * s, -0.5, 0, Math.PI * 2); ctx.fill()
  ctx.beginPath(); ctx.ellipse(cx + 8 * s, cy + 7 * s, 4 * s, 3 * s, 0.5, 0, Math.PI * 2); ctx.fill()

  ctx.fillStyle = '#4ade80'
  ctx.beginPath(); ctx.ellipse(cx, cy + 1 * s, 10 * s, 8 * s, 0, 0, Math.PI * 2); ctx.fill()

  ctx.fillStyle = '#22c55e'
  ctx.beginPath(); ctx.ellipse(cx, cy - 5 * s, 8 * s, 7 * s, 0, 0, Math.PI * 2); ctx.fill()

  ctx.fillStyle = '#4ade80'
  ctx.beginPath(); ctx.ellipse(cx - 9 * s, cy + 1 * s, 3 * s, 2 * s, -0.3, 0, Math.PI * 2); ctx.fill()
  ctx.beginPath(); ctx.ellipse(cx + 9 * s, cy + 1 * s, 3 * s, 2 * s, 0.3, 0, Math.PI * 2); ctx.fill()

  ctx.fillStyle = '#ffffff'
  ctx.beginPath(); ctx.arc(cx - 5 * s, cy - 9 * s, 3.5 * s, 0, Math.PI * 2); ctx.fill()
  ctx.beginPath(); ctx.arc(cx + 5 * s, cy - 9 * s, 3.5 * s, 0, Math.PI * 2); ctx.fill()

  ctx.fillStyle = '#0f172a'
  ctx.beginPath(); ctx.arc(cx - 5 * s, cy - 9 * s, 2 * s, 0, Math.PI * 2); ctx.fill()
  ctx.beginPath(); ctx.arc(cx + 5 * s, cy - 9 * s, 2 * s, 0, Math.PI * 2); ctx.fill()

  ctx.fillStyle = '#ffffff'
  ctx.beginPath(); ctx.arc(cx - 4 * s, cy - 10 * s, 0.8 * s, 0, Math.PI * 2); ctx.fill()
  ctx.beginPath(); ctx.arc(cx + 6 * s, cy - 10 * s, 0.8 * s, 0, Math.PI * 2); ctx.fill()

  ctx.strokeStyle = '#16a34a'
  ctx.lineWidth = 1.2 * s
  ctx.beginPath(); ctx.arc(cx, cy - 2 * s, 4 * s, 0.3, Math.PI - 0.3); ctx.stroke()

  ctx.strokeStyle = '#16a34a'
  ctx.lineWidth = s
  ctx.beginPath(); ctx.ellipse(cx, cy + 1 * s, 10 * s, 8 * s, 0, 0, Math.PI * 2); ctx.stroke()
}

// ─── component ──────────────────────────────────────────────────────────────

export default function Frogger() {
  const { confirm, confirmProps } = useConfirm()
  const confirmRef = useRef(confirm)
  useEffect(() => { confirmRef.current = confirm }, [confirm])

  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const cellRef = useRef(32)
  const rafRef = useRef(0)
  const [wins, setWins] = useState(0)

  useEffect(() => {
    // Mutable game state lives outside React to avoid re-render overhead.
    const game = {
      frog: { col: 5, row: 8 },
      lanes: initLanes(),
      status: 'playing' as 'playing' | 'dead' | 'home',
      lastT: 0,
    }

    // Forward refs so draw/tick/endRound can reference each other.
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

      ctx.clearRect(0, 0, COLS * cell, ROWS * cell)

      let roadIdx = 0
      for (let r = 0; r < ROWS; r++) {
        const ln = lanes[r]
        if (ln.type === 'safe') {
          drawSafeLane(ctx, r, cell)
        } else if (ln.type === 'river') {
          drawRiverLane(ctx, r, cell)
          drawVehicles(ctx, ln.occupied, r, cell, ln.dir, ln.offset, true, '')
        } else {
          drawRoadLane(ctx, r, cell)
          drawVehicles(ctx, ln.occupied, r, cell, ln.dir, ln.offset, false, ROAD_COLORS[roadIdx % ROAD_COLORS.length])
          roadIdx++
        }
      }

      const fl = lanes[frog.row]
      const frogOffX =
        fl.type === 'river' && fl.dir !== 0 && fl.occupied.includes(frog.col)
          ? fl.dir * fl.offset
          : 0
      drawFrog(ctx, frog.col, frog.row, cell, frogOffX)
    }

    function check() {
      const { frog, lanes } = game
      if (frog.col < 0 || frog.col >= COLS) { endRound('dead'); return }
      if (frog.row === 0) { endRound('home'); return }
      const st = frogStatus(frog.col, lanes[frog.row])
      if (st.dead) endRound('dead')
    }

    function endRound(kind: 'dead' | 'home') {
      game.status = kind
      cancelAnimationFrame(rafRef.current)
      draw()
      if (kind === 'home') setWins((w) => w + 1)
      void confirmRef.current({
        title: kind === 'home' ? 'Home safe' : 'Splat',
        message:
          kind === 'home'
            ? 'You made it across. Go again?'
            : 'The frog did not make it. Try again?',
        confirmLabel: kind === 'home' ? 'Again' : 'Retry',
        cancelLabel: 'Done',
      }).then((again) => {
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

      const cell = cellRef.current
      const { frog, lanes } = game

      for (let r = 0; r < ROWS; r++) {
        const ln = lanes[r]
        if (ln.dir === 0) continue
        ln.offset += (ln.speed * cell * dt) / 1000

        // Each time offset crosses one full cell, shift occupied[] by one cell.
        while (ln.offset >= cell) {
          ln.offset -= cell
          const frogOnLog =
            r === frog.row && ln.type === 'river' && ln.occupied.includes(frog.col)
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
      if (game.status !== 'playing') return
      let moved = true
      if      (e.key === 'ArrowUp')    game.frog.row = Math.max(0, game.frog.row - 1)
      else if (e.key === 'ArrowDown')  game.frog.row = Math.min(ROWS - 1, game.frog.row + 1)
      else if (e.key === 'ArrowLeft')  game.frog.col = Math.max(0, game.frog.col - 1)
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
    <div className="flex flex-col items-center gap-3 w-full">
      <div className="flex flex-wrap gap-4 text-sm text-slate-600 dark:text-slate-400 self-start">
        <span className="font-medium">Crossings: {wins}</span>
        <span>Arrow keys. Reach the top. Ride logs across the river. Dodge cars on the road.</span>
      </div>
      <div ref={containerRef} className="w-full">
        <canvas
          ref={canvasRef}
          data-testid="frogger-canvas"
          className="rounded-lg border border-slate-300 dark:border-slate-700 block mx-auto"
        />
      </div>
      <ConfirmModal {...confirmProps} />
    </div>
  )
}
