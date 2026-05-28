import { useEffect, useRef, useState } from 'react'
import { frogStatus, type Lane } from './froggerLogic'
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

// ─── drawing helpers (Basquiat-style) ───────────────────────────────────────

function drawUrbanLane(ctx: CanvasRenderingContext2D, r: number, cell: number, type: 'safe' | 'river' | 'road') {
  const y = r * cell
  const totalW = COLS * cell
  
  if (type === 'safe') {
    ctx.fillStyle = '#1a1c1e' // Stark black asphalt
    ctx.fillRect(0, y, totalW, cell)
    ctx.strokeStyle = '#ef4444' // Jagged red lines
    ctx.lineWidth = 2
    ctx.beginPath()
    for(let i=0; i<totalW; i+=20) {
      ctx.moveTo(i, y + Math.random() * 5)
      ctx.lineTo(i + 15, y + 5 + Math.random() * 5)
    }
    ctx.stroke()
  } else if (type === 'river') {
    ctx.fillStyle = '#3b82f6' // Harsh blue
    ctx.fillRect(0, y, totalW, cell)
    ctx.fillStyle = 'rgba(0,0,0,0.1)'
    ctx.font = 'bold 12px serif'
    ctx.fillText('SAMO©', 10, y + 20)
    ctx.fillText('PAY FOR SOUP', 100, y + 20)
  } else {
    ctx.fillStyle = '#eab308' // Dirty yellow
    ctx.fillRect(0, y, totalW, cell)
    ctx.strokeStyle = '#000'
    ctx.lineWidth = 1
    ctx.strokeRect(2, y + 2, totalW - 4, cell - 4)
  }
}

function drawOneObstacle(ctx: CanvasRenderingContext2D, x: number, y: number, cell: number, type: 'car' | 'log', color: string) {
  if (type === 'car') {
    ctx.fillStyle = color
    ctx.fillRect(x + 2, y + 4, cell - 4, cell - 8)
    ctx.strokeStyle = '#000'
    ctx.lineWidth = 2
    ctx.strokeRect(x + 2, y + 4, cell - 4, cell - 8)
    ctx.fillStyle = '#fff'
    ctx.fillRect(x + 6, y + 8, cell - 12, cell/3)
    ctx.strokeStyle = '#000'
    ctx.beginPath(); ctx.moveTo(x+4, y+6); ctx.lineTo(x+8, y+10); ctx.stroke()
    ctx.beginPath(); ctx.moveTo(x+8, y+6); ctx.lineTo(x+4, y+10); ctx.stroke()
  } else {
    ctx.fillStyle = '#000'
    ctx.fillRect(x + 4, y + 4, cell - 8, cell - 8)
    ctx.strokeStyle = '#fff'
    ctx.strokeRect(x + 6, y + 6, cell - 12, cell - 12)
    ctx.beginPath(); ctx.arc(x + cell/4 + 2, y + cell/2, cell/6, 0, Math.PI*2); ctx.stroke()
    ctx.beginPath(); ctx.arc(x + 3*cell/4 - 2, y + cell/2, cell/6, 0, Math.PI*2); ctx.stroke()
  }
}

function drawVehicles(
  ctx: CanvasRenderingContext2D,
  occupied: number[], r: number,
  cell: number, dir: number, offset: number,
  isLog: boolean, color: string,
) {
  const totalW = COLS * cell
  const y = r * cell
  for (const c of occupied) {
    const rawX = c * cell + dir * offset
    for (const baseX of [rawX, rawX + totalW, rawX - totalW]) {
      drawOneObstacle(ctx, baseX, y, cell, isLog ? 'log' : 'car', color)
    }
  }
}

function drawArtist(
  ctx: CanvasRenderingContext2D,
  col: number, row: number,
  cell: number, offsetX: number,
) {
  const s = cell / 32
  const cx = col * cell + cell / 2 + offsetX
  const cy = row * cell + cell / 2
  ctx.strokeStyle = '#000'
  ctx.lineWidth = 3
  ctx.beginPath()
  ctx.moveTo(cx, cy - 10*s); ctx.lineTo(cx, cy + 5*s)
  ctx.moveTo(cx, cy - 5*s); ctx.lineTo(cx - 10*s, cy - 8*s)
  ctx.moveTo(cx, cy - 5*s); ctx.lineTo(cx + 10*s, cy - 8*s)
  ctx.moveTo(cx, cy + 5*s); ctx.lineTo(cx - 8*s, cy + 12*s)
  ctx.moveTo(cx, cy + 5*s); ctx.lineTo(cx + 8*s, cy + 12*s)
  ctx.stroke()
  ctx.fillStyle = '#000'
  ctx.beginPath(); ctx.arc(cx, cy - 12*s, 6*s, 0, Math.PI*2); ctx.fill()
  ctx.fillStyle = '#facc15'
  ctx.beginPath()
  ctx.moveTo(cx - 8*s, cy - 18*s); ctx.lineTo(cx - 8*s, cy - 25*s)
  ctx.lineTo(cx - 4*s, cy - 21*s); ctx.lineTo(cx, cy - 25*s)
  ctx.lineTo(cx + 4*s, cy - 21*s); ctx.lineTo(cx + 8*s, cy - 25*s)
  ctx.lineTo(cx + 8*s, cy - 18*s); ctx.closePath(); ctx.fill()
  ctx.strokeStyle = '#000'; ctx.lineWidth = 1; ctx.stroke()
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

      ctx.fillStyle = '#f8f8f0' // Aged paper background
      ctx.fillRect(0, 0, COLS * cell, ROWS * cell)

      for (let r = 0; r < ROWS; r++) {
        const ln = lanes[r]
        drawUrbanLane(ctx, r, cell, ln.type)
        if (ln.type === 'river') {
          drawVehicles(ctx, ln.occupied, r, cell, ln.dir, ln.offset, true, '#000')
        } else if (ln.type === 'road') {
          drawVehicles(ctx, ln.occupied, r, cell, ln.dir, ln.offset, false, r % 2 === 0 ? '#ef4444' : '#eab308')
        }
      }

      const fl = lanes[frog.row]
      const frogOffX =
        fl.type === 'river' && fl.dir !== 0 && fl.occupied.includes(frog.col)
          ? fl.dir * fl.offset
          : 0
      drawArtist(ctx, frog.col, frog.row, cell, frogOffX)
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
        title: kind === 'home' ? 'You won!' : 'Game over',
        message:
          kind === 'home'
            ? 'You made it across. Play again?'
            : 'You didn\'t make it. Try again?',
        confirmLabel: 'Play again',
        cancelLabel: 'Quit',
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
    <div className="flex flex-col items-center gap-4 w-full font-serif">
      <div className="flex flex-col gap-1 items-center italic self-start">
        <h2 className="text-3xl text-black drop-shadow-sm uppercase font-black">The Concrete Crown</h2>
        <p className="text-[10px] text-slate-500 tracking-widest">Navigate the 1980s avenues to make your mark.</p>
      </div>

      <div className="flex flex-wrap gap-4 text-xs text-slate-600 self-start border-l-2 border-black pl-3 py-1">
        <span className="font-bold uppercase">Tags: {wins}</span>
        <span className="italic uppercase">Ride the boomboxes. Avoid the cruisers. Reach the top billboard.</span>
      </div>
      <div ref={containerRef} className="w-full">
        <canvas
          ref={canvasRef}
          data-testid="frogger-canvas"
          className="rounded-lg border-2 border-black shadow-[8px_8px_0_rgba(0,0,0,1)] block mx-auto"
        />
      </div>
      <ConfirmModal {...confirmProps} />
    </div>
  )
}
