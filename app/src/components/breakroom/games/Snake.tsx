import { useCallback, useEffect, useRef, useState } from 'react'
import { step, type Pt } from './snakeLogic'
import { recordHighScore, loadBest } from '../storage'
import { useConfirm } from '../../../hooks/useConfirm'
import ConfirmModal from '../../ConfirmModal'

const COLS = 20
const ROWS = 20
const CELL = 16
const GAME_ID = 'snake'
const TICK = 130

function randomFood(snake: Pt[]): Pt {
  for (;;) {
    const p = { x: Math.floor(Math.random() * COLS), y: Math.floor(Math.random() * ROWS) }
    if (!snake.some((s) => s.x === p.x && s.y === p.y)) return p
  }
}

export default function Snake() {
  const { confirm, confirmProps } = useConfirm()
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [snake, setSnake] = useState<Pt[]>([{ x: 10, y: 10 }])
  const [food, setFood] = useState<Pt>(() => ({ x: 5, y: 10 }))
  const [score, setScore] = useState(0)
  const [dead, setDead] = useState(false)
  const [best, setBest] = useState<number | null>(loadBest(GAME_ID)?.best ?? null)
  const dir = useRef<Pt>({ x: 1, y: 0 })
  const nextDir = useRef<Pt>({ x: 1, y: 0 })

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

  useEffect(() => {
    const ctx = canvasRef.current?.getContext('2d')
    if (!ctx) return
    ctx.fillStyle = '#0f172a'
    ctx.fillRect(0, 0, COLS * CELL, ROWS * CELL)
    ctx.fillStyle = '#ef4444'
    ctx.fillRect(food.x * CELL, food.y * CELL, CELL, CELL)
    ctx.fillStyle = '#22c55e'
    snake.forEach((seg) => ctx.fillRect(seg.x * CELL + 1, seg.y * CELL + 1, CELL - 2, CELL - 2))
  }, [snake, food])

  return (
    <div className="flex flex-col items-start gap-3">
      <div className="flex flex-wrap gap-4 text-sm text-slate-600 dark:text-slate-400">
        <span className="font-medium">Score: {score}</span>
        {best != null && <span>Best: {best}</span>}
        <span>Arrow keys to steer.</span>
      </div>
      <canvas
        ref={canvasRef}
        width={COLS * CELL}
        height={ROWS * CELL}
        data-testid="snake-canvas"
        className="max-w-full rounded-lg border border-slate-300 dark:border-slate-700"
      />
      <ConfirmModal {...confirmProps} />
    </div>
  )
}
