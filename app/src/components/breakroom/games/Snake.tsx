import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { step, type Pt } from './snakeLogic'
import { recordHighScore, loadBest } from '../storage'
import { useConfirm } from '../../../hooks/useConfirm'
import ConfirmModal from '../../ConfirmModal'

const COLS = 20
const ROWS = 20
const GAME_ID = 'snake'
const TICK = 130
const MIN_CELL = 8
const MAX_H_RATIO = 0.75

function randomFood(snake: Pt[]): Pt {
  for (;;) {
    const p = { x: Math.floor(Math.random() * COLS), y: Math.floor(Math.random() * ROWS) }
    if (!snake.some((s) => s.x === p.x && s.y === p.y)) return p
  }
}

// Kusama palette
const CLR_SNAKE = '#facc15' // Kusama Yellow
const CLR_DOT = '#000000'
const CLR_FOOD = '#ea580c' // Pumpkin Orange
const CLR_VOID = '#000000'

export default function Snake() {
  const { confirm, confirmProps } = useConfirm()
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const cellRef = useRef<number>(16)

  const [snake, setSnake] = useState<Pt[]>([{ x: 10, y: 10 }])
  const [food, setFood] = useState<Pt>(() => ({ x: 5, y: 10 }))
  const [score, setScore] = useState(0)
  const [dead, setDead] = useState(false)
  const [best, setBest] = useState<number | null>(loadBest(GAME_ID)?.best ?? null)
  const dir = useRef<Pt>({ x: 1, y: 0 })
  const nextDir = useRef<Pt>({ x: 1, y: 0 })

  const snakeRef = useRef<Pt[]>(snake)
  const foodRef = useRef<Pt>(food)

  useEffect(() => { snakeRef.current = snake }, [snake])
  useEffect(() => { foodRef.current = food }, [food])

  const resizeCanvas = useCallback(() => {
    const el = containerRef.current
    const canvas = canvasRef.current
    if (!el || !canvas) return
    const w = el.getBoundingClientRect().width
    const maxH = window.innerHeight * MAX_H_RATIO
    const cs = Math.max(MIN_CELL, Math.min(Math.floor(w / COLS), Math.floor(maxH / ROWS)))
    cellRef.current = cs
    const dpr = window.devicePixelRatio || 1
    canvas.width = Math.round(COLS * cs * dpr)
    canvas.height = Math.round(ROWS * cs * dpr)
    canvas.style.width = `${COLS * cs}px`
    canvas.style.height = `${ROWS * cs}px`
  }, [])

  useLayoutEffect(() => { resizeCanvas() }, [resizeCanvas])
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const obs = new ResizeObserver(resizeCanvas)
    obs.observe(el)
    window.addEventListener('resize', resizeCanvas)
    return () => { obs.disconnect(); window.removeEventListener('resize', resizeCanvas) }
  }, [resizeCanvas])

  const reset = useCallback(() => {
    const start = [{ x: 10, y: 10 }]
    setSnake(start); setFood(randomFood(start)); setScore(0); setDead(false)
    dir.current = { x: 1, y: 0 }; nextDir.current = { x: 1, y: 0 }
  }, [])

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const d = dir.current
      if (e.key === 'ArrowUp' && d.y === 0) nextDir.current = { x: 0, y: -1 }
      else if (e.key === 'ArrowDown' && d.y === 0) nextDir.current = { x: 0, y: 1 }
      else if (e.key === 'ArrowLeft' && d.x === 0) nextDir.current = { x: -1, y: 0 }
      else if (e.key === 'ArrowRight' && d.x === 0) nextDir.current = { x: 1, y: 0 }
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    if (dead) return
    const id = setInterval(() => {
      dir.current = nextDir.current
      setSnake((prev) => {
        const r = step(prev, dir.current, food, COLS, ROWS)
        if (r.dead) { setDead(true); return prev }
        if (r.ate) { setScore((sc) => sc + 1); setFood(randomFood(r.snake)) }
        return r.snake
      })
    }, TICK)
    return () => clearInterval(id)
  }, [dead, food])

  useEffect(() => {
    if (!dead) return
    const isBest = recordHighScore(GAME_ID, score)
    if (isBest) setBest(score)
    void confirm({
      title: 'Game over',
      message: `You scored ${score}.${isBest ? ' New best!' : ''} Play again?`,
      confirmLabel: 'Play again',
      cancelLabel: 'Quit',
    }).then((a) => { if (a) reset() })
  }, [dead])

  useEffect(() => {
    let animId: number
    const render = () => {
      const canvas = canvasRef.current
      const ctx = canvas?.getContext('2d')
      if (!ctx) { animId = requestAnimationFrame(render); return }

      const CELL = cellRef.current
      const W = COLS * CELL
      const H = ROWS * CELL
      const dpr = window.devicePixelRatio || 1
      const now = Date.now()
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

      // 1. Background (The Void)
      ctx.fillStyle = CLR_VOID; ctx.fillRect(0, 0, W, H)

      // 2. Polka Dots (Pulse with score)
      ctx.fillStyle = '#f8edeb'
      for (let i = 0; i < 50; i++) {
        const x = ((Math.sin(i * 1234.5) + 1) / 2) * W
        const y = ((Math.cos(i * 5432.1) + 1) / 2) * H
        const r = (1 + Math.sin(now / 1000 + i)) * (2 + score/2)
        ctx.globalAlpha = 0.1 + 0.1 * Math.sin(now / 500 + i)
        ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill()
      }
      ctx.globalAlpha = 1

      // 3. The Hallucinatory Pumpkin (Food)
      const fx = foodRef.current.x * CELL + CELL / 2
      const fy = foodRef.current.y * CELL + CELL / 2
      const pulse = 0.8 + 0.2 * Math.sin(now / 300)
      ctx.fillStyle = CLR_FOOD
      ctx.beginPath(); ctx.arc(fx, fy, (CELL / 2.2) * pulse, 0, Math.PI * 2); ctx.fill()
      // Pumpkin dots
      ctx.fillStyle = CLR_DOT
      for(let i=0; i<5; i++) {
        const dx = Math.sin(i * 2) * 3; const dy = Math.cos(i * 2) * 3
        ctx.beginPath(); ctx.arc(fx + dx, fy + dy, 1.5, 0, Math.PI*2); ctx.fill()
      }

      // 4. The Snake
      const snakeArr = snakeRef.current
      snakeArr.forEach((seg, i) => {
        const rx = seg.x * CELL; const ry = seg.y * CELL
        ctx.fillStyle = CLR_SNAKE
        ctx.beginPath(); ctx.arc(rx + CELL/2, ry + CELL/2, CELL/2 - 1, 0, Math.PI*2); ctx.fill()
        
        // Polka dots on segments
        ctx.fillStyle = CLR_DOT
        const dotR = i === 0 ? 3 : 2
        ctx.beginPath(); ctx.arc(rx + CELL/2, ry + CELL/2, dotR, 0, Math.PI*2); ctx.fill()
        if (i === 0) { // Eyes on head
          ctx.fillStyle = '#fff'
          ctx.beginPath(); ctx.arc(rx + CELL/4, ry + CELL/3, 2, 0, Math.PI*2); ctx.fill()
          ctx.beginPath(); ctx.arc(rx + 3*CELL/4, ry + CELL/3, 2, 0, Math.PI*2); ctx.fill()
        }
      })

      animId = requestAnimationFrame(render)
    }
    animId = requestAnimationFrame(render)
    return () => cancelAnimationFrame(animId)
  }, [score])

  return (
    <div ref={containerRef} className="flex w-full flex-col items-center gap-4 font-serif">
      <div className="flex flex-col gap-1 items-center italic self-start">
        <h2 className="text-3xl text-[#facc15] drop-shadow-lg font-black uppercase">Snake</h2>
        <p className="text-[10px] text-slate-500 tracking-[0.3em] uppercase">Expand your pattern into the infinite void.</p>
      </div>

      <div className="flex flex-wrap gap-4 text-xs text-[#facc15] self-start border-l-2 border-[#facc15] pl-3 py-1 bg-black/40">
        <span className="font-bold uppercase tracking-widest">Self-Obliteration: {score}</span>
        {best != null && <span className="font-bold uppercase tracking-widest opacity-70">Best: {best}</span>}
        <span className="italic uppercase">Consume the pumpkins. Become the pattern.</span>
      </div>

      <canvas ref={canvasRef} data-testid="snake-canvas" className="max-w-full border-2 border-[#facc15] shadow-[0_0_20px_rgba(250,204,21,0.3)]" />
      <ConfirmModal {...confirmProps} />
    </div>
  )
}
