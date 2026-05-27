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

function fillRoundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
) {
  ctx.beginPath()
  ctx.roundRect(x, y, w, h, r)
  ctx.fill()
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

  // Refs for RAF renderer: synced from state each render
  const snakeRef = useRef<Pt[]>(snake)
  const foodRef = useRef<Pt>(food)

  useEffect(() => { snakeRef.current = snake }, [snake])
  useEffect(() => { foodRef.current = food }, [food])

  // Compute cell size from container width and height cap, then resize canvas
  const resizeCanvas = useCallback(() => {
    const el = containerRef.current
    const canvas = canvasRef.current
    if (!el || !canvas) return
    const w = el.getBoundingClientRect().width
    const maxH = window.innerHeight * MAX_H_RATIO
    const byW = Math.floor(w / COLS)
    const byH = Math.floor(maxH / ROWS)
    const cs = Math.max(MIN_CELL, Math.min(byW, byH))
    cellRef.current = cs
    const dpr = window.devicePixelRatio || 1
    canvas.width = Math.round(COLS * cs * dpr)
    canvas.height = Math.round(ROWS * cs * dpr)
    canvas.style.width = `${COLS * cs}px`
    canvas.style.height = `${ROWS * cs}px`
  }, [])

  // Run synchronously before first paint so canvas has correct size right away
  useLayoutEffect(() => {
    resizeCanvas()
  }, [resizeCanvas])

  // Track container width changes and window height changes
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

  // Arrow key input: cannot reverse directly into the snake
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

  // Game tick: advances snake every TICK ms
  useEffect(() => {
    if (dead) return
    const id = setInterval(() => {
      dir.current = nextDir.current
      setSnake((prev) => {
        const r = step(prev, dir.current, food, COLS, ROWS)
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
  }, [dead, food])

  // Game-over dialog
  useEffect(() => {
    if (!dead) return
    const isBest = recordHighScore(GAME_ID, score)
    if (isBest) setBest(score)
    void confirm({
      title: 'Game over',
      message: `Score ${score}.${isBest ? ' New best!' : ''} Play again?`,
      confirmLabel: 'Play again',
      cancelLabel: 'Done',
    }).then((a) => {
      if (a) reset()
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dead])

  // Neon arcade renderer: RAF loop, scales to devicePixelRatio for crispness
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
      const now = Date.now()

      // Apply DPR scale once per frame so all coordinates stay in logical pixels
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

      // Background
      const bg = ctx.createLinearGradient(0, 0, W, H)
      bg.addColorStop(0, '#050d1f')
      bg.addColorStop(1, '#0d1b35')
      ctx.fillStyle = bg
      ctx.fillRect(0, 0, W, H)

      // Subtle dot grid
      ctx.fillStyle = 'rgba(255,255,255,0.06)'
      for (let cx = 0; cx < COLS; cx++) {
        for (let cy = 0; cy < ROWS; cy++) {
          ctx.fillRect(cx * CELL + CELL / 2 - 0.5, cy * CELL + CELL / 2 - 0.5, 1, 1)
        }
      }

      // Food: glowing pulsing crimson orb
      const pulse = 0.78 + 0.22 * Math.sin(now / 480)
      const fx = foodRef.current.x * CELL + CELL / 2
      const fy = foodRef.current.y * CELL + CELL / 2
      const foodR = (CELL / 2 - 0.5) * pulse
      const foodGrad = ctx.createRadialGradient(fx - 1, fy - 1.5, 0, fx, fy, foodR + 3)
      foodGrad.addColorStop(0, '#ffb3c1')
      foodGrad.addColorStop(0.35, '#ff3a5c')
      foodGrad.addColorStop(0.75, '#c0113a')
      foodGrad.addColorStop(1, 'rgba(100,0,20,0)')
      ctx.shadowBlur = 18 * pulse
      ctx.shadowColor = '#ff2244'
      ctx.fillStyle = foodGrad
      ctx.beginPath()
      ctx.arc(fx, fy, foodR, 0, Math.PI * 2)
      ctx.fill()
      ctx.shadowBlur = 0

      // Snake segments (tail to head so head renders on top)
      const snakeArr = snakeRef.current
      const len = snakeArr.length

      for (let i = len - 1; i >= 0; i--) {
        const seg = snakeArr[i]
        const t = len > 1 ? i / (len - 1) : 0 // 0 = head, 1 = tail
        const isHead = i === 0

        const rx = seg.x * CELL + 1
        const ry = seg.y * CELL + 1
        const rw = CELL - 2
        const rh = CELL - 2

        if (isHead) {
          // Bright neon-green head with strong glow
          ctx.shadowBlur = 20
          ctx.shadowColor = '#39ff14'
          ctx.fillStyle = 'rgba(57, 255, 20, 0.95)'
          fillRoundRect(ctx, rx, ry, rw, rh, 5)

          // Eyes: positioned toward the direction of travel
          ctx.shadowBlur = 0
          ctx.fillStyle = 'rgba(5, 20, 5, 0.9)'
          const d = dir.current
          let ex1: number, ey1: number, ex2: number, ey2: number
          if (d.x === 1) {
            // Moving right: eyes on right side, stacked vertically
            ex1 = rx + rw * 0.68; ey1 = ry + rh * 0.28
            ex2 = rx + rw * 0.68; ey2 = ry + rh * 0.68
          } else if (d.x === -1) {
            // Moving left: eyes on left side
            ex1 = rx + rw * 0.28; ey1 = ry + rh * 0.28
            ex2 = rx + rw * 0.28; ey2 = ry + rh * 0.68
          } else if (d.y === -1) {
            // Moving up: eyes on top, side by side
            ex1 = rx + rw * 0.28; ey1 = ry + rh * 0.28
            ex2 = rx + rw * 0.68; ey2 = ry + rh * 0.28
          } else {
            // Moving down: eyes on bottom
            ex1 = rx + rw * 0.28; ey1 = ry + rh * 0.72
            ex2 = rx + rw * 0.68; ey2 = ry + rh * 0.72
          }
          ctx.beginPath()
          ctx.arc(ex1, ey1, 1.5, 0, Math.PI * 2)
          ctx.fill()
          ctx.beginPath()
          ctx.arc(ex2, ey2, 1.5, 0, Math.PI * 2)
          ctx.fill()
        } else {
          // Body: green fading to teal-dim toward tail
          const brightness = Math.round(195 - t * 120) // 195 near head, 75 at tail
          const alpha = (0.92 - t * 0.38).toFixed(2)   // 0.92 near head, 0.54 at tail
          ctx.shadowBlur = Math.round(10 - t * 6)       // 10 near head, 4 at tail
          ctx.shadowColor = '#22c55e'
          ctx.fillStyle = `rgba(0, ${brightness}, 45, ${alpha})`
          fillRoundRect(ctx, rx, ry, rw, rh, 3)
        }
      }

      ctx.shadowBlur = 0
      animId = requestAnimationFrame(render)
    }

    animId = requestAnimationFrame(render)
    return () => cancelAnimationFrame(animId)
  }, []) // empty deps: runs for the lifetime of the component

  return (
    <div ref={containerRef} className="flex w-full flex-col items-center gap-3">
      <div className="flex flex-wrap gap-4 text-sm text-slate-600 dark:text-slate-400">
        <span className="font-medium">Score: {score}</span>
        {best != null && <span>Best: {best}</span>}
        <span>Arrow keys to steer.</span>
      </div>
      <canvas
        ref={canvasRef}
        data-testid="snake-canvas"
        className="max-w-full rounded-lg border border-slate-300 dark:border-slate-700"
      />
      <ConfirmModal {...confirmProps} />
    </div>
  )
}
