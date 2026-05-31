import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { step, type Pt } from './snakeLogic'
import { recordHighScore, loadBest, isOverlayOpen } from '../storage'
import { useConfirm } from '../../../hooks/useConfirm'
import ConfirmModal from '../../ConfirmModal'

const COLS = 20
const ROWS = 20
const GAME_ID = 'snake'
const TICK = 130
const MIN_CELL = 8
const MAX_H_RATIO = 0.75

// Classic palette
const CLR_BG = '#111827'
const CLR_GRID = '#1f2937'
const CLR_SNAKE = '#22c55e'
const CLR_HEAD = '#16a34a'
const CLR_FOOD = '#ef4444'

function randomFood(snake: Pt[]): Pt {
  for (;;) {
    const p = { x: Math.floor(Math.random() * COLS), y: Math.floor(Math.random() * ROWS) }
    if (!snake.some((s) => s.x === p.x && s.y === p.y)) return p
  }
}

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

  useEffect(() => {
    snakeRef.current = snake
  }, [snake])
  useEffect(() => {
    foodRef.current = food
  }, [food])

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

  useLayoutEffect(() => {
    resizeCanvas()
  }, [resizeCanvas])
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const obs = new ResizeObserver(resizeCanvas)
    obs.observe(el)
    window.addEventListener('resize', resizeCanvas)
    return () => {
      obs.disconnect()
      window.removeEventListener('resize', resizeCanvas)
    }
  }, [resizeCanvas])

  const reset = useCallback(() => {
    const start = [{ x: 10, y: 10 }]
    setSnake(start)
    setFood(randomFood(start))
    setScore(0)
    setDead(false)
    dir.current = { x: 1, y: 0 }
    nextDir.current = { x: 1, y: 0 }
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
      if (isOverlayOpen()) return // pause while how-to-play / dialog is open
      dir.current = nextDir.current
      setSnake((prev) => {
        const r = step(prev, dir.current, foodRef.current, COLS, ROWS)
        if (r.dead) {
          setDead(true)
          return prev
        }
        if (r.ate) {
          setScore((sc) => sc + 1)
          setFood(randomFood(r.snake))
        }
        return r.snake
      })
    }, TICK)
    return () => clearInterval(id)
  }, [dead])

  useEffect(() => {
    if (!dead) return
    const isBest = recordHighScore(GAME_ID, score)
    if (isBest) setBest(score)
    void confirm({
      title: 'Game over',
      message: `You scored ${score}.${isBest ? ' New best!' : ''} Play again?`,
      confirmLabel: 'Play again',
      cancelLabel: 'Quit',
    }).then((a) => {
      if (a) reset()
    })
  }, [dead])

  useEffect(() => {
    let animId: number
    const render = () => {
      const canvas = canvasRef.current
      const ctx = canvas?.getContext('2d')
      if (!ctx) {
        animId = requestAnimationFrame(render)
        return
      }
      const CELL = cellRef.current
      const W = COLS * CELL
      const H = ROWS * CELL
      const dpr = window.devicePixelRatio || 1
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

      // Background + light grid
      ctx.fillStyle = CLR_BG
      ctx.fillRect(0, 0, W, H)
      ctx.strokeStyle = CLR_GRID
      ctx.lineWidth = 1
      for (let x = 0; x <= COLS; x++) {
        ctx.beginPath()
        ctx.moveTo(x * CELL, 0)
        ctx.lineTo(x * CELL, H)
        ctx.stroke()
      }
      for (let y = 0; y <= ROWS; y++) {
        ctx.beginPath()
        ctx.moveTo(0, y * CELL)
        ctx.lineTo(W, y * CELL)
        ctx.stroke()
      }

      // Food
      const f = foodRef.current
      ctx.fillStyle = CLR_FOOD
      ctx.fillRect(f.x * CELL + 2, f.y * CELL + 2, CELL - 4, CELL - 4)

      // Snake
      snakeRef.current.forEach((seg, i) => {
        ctx.fillStyle = i === 0 ? CLR_HEAD : CLR_SNAKE
        ctx.fillRect(seg.x * CELL + 1, seg.y * CELL + 1, CELL - 2, CELL - 2)
      })

      animId = requestAnimationFrame(render)
    }
    animId = requestAnimationFrame(render)
    return () => cancelAnimationFrame(animId)
  }, [])

  return (
    <div ref={containerRef} className="flex w-full flex-col items-center gap-4">
      <div className="flex w-full flex-col gap-1">
        <h2 className="text-2xl font-bold text-slate-900 dark:text-white">Snake</h2>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Steer with the arrow keys. Eat the food, avoid the walls and your tail. Score: {score}
          {best != null ? ` · Best: ${best}` : ''}
        </p>
      </div>

      <canvas ref={canvasRef} data-testid="snake-canvas" className="max-w-full rounded border border-slate-300 dark:border-slate-700" />
      <ConfirmModal {...confirmProps} />
    </div>
  )
}
