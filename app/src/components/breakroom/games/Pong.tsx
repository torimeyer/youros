import { useEffect, useRef, useState } from 'react'
import { paddleHit, reflectX, spinFromPaddle } from './pongLogic'
import { useConfirm } from '../../../hooks/useConfirm'
import ConfirmModal from '../../ConfirmModal'

const W = 480
const H = 300
const PADDLE_H = 60
const PADDLE_W = 10
const BALL_R = 6
const WIN = 5

export default function Pong() {
  const { confirm, confirmProps } = useConfirm()
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [score, setScore] = useState({ you: 0, cpu: 0 })
  const s = useRef({
    bx: W / 2,
    by: H / 2,
    vx: 4,
    vy: 2,
    py: H / 2 - PADDLE_H / 2,
    cy: H / 2 - PADDLE_H / 2,
    up: false,
    down: false,
    paused: false,
  }).current

  function resetMatch() {
    s.bx = W / 2
    s.by = H / 2
    s.vx = 4
    s.vy = 2
    s.py = H / 2 - PADDLE_H / 2
    s.cy = H / 2 - PADDLE_H / 2
    s.paused = false
    setScore({ you: 0, cpu: 0 })
  }

  useEffect(() => {
    function key(e: KeyboardEvent, down: boolean) {
      if (e.key === 'ArrowUp' || e.key === 'w') {
        s.up = down
        e.preventDefault()
      } else if (e.key === 'ArrowDown' || e.key === 's') {
        s.down = down
        e.preventDefault()
      }
    }
    const kd = (e: KeyboardEvent) => key(e, true)
    const ku = (e: KeyboardEvent) => key(e, false)
    window.addEventListener('keydown', kd)
    window.addEventListener('keyup', ku)
    const ctx = canvasRef.current?.getContext('2d') ?? null
    let raf = 0
    function frame() {
      if (!s.paused) {
        if (s.up) s.py = Math.max(0, s.py - 5)
        if (s.down) s.py = Math.min(H - PADDLE_H, s.py + 5)
        const cc = s.cy + PADDLE_H / 2
        if (cc < s.by - 6) s.cy = Math.min(H - PADDLE_H, s.cy + 3.5)
        else if (cc > s.by + 6) s.cy = Math.max(0, s.cy - 3.5)
        s.bx += s.vx
        s.by += s.vy
        if (s.by < BALL_R) {
          s.by = BALL_R
          s.vy = -s.vy
        }
        if (s.by > H - BALL_R) {
          s.by = H - BALL_R
          s.vy = -s.vy
        }
        if (s.bx - BALL_R <= 10 + PADDLE_W && s.vx < 0 && paddleHit(s.by, s.py, PADDLE_H)) {
          s.vx = reflectX(s.vx)
          s.vy = spinFromPaddle(s.by, s.py, PADDLE_H, 5)
          s.bx = 10 + PADDLE_W + BALL_R
        }
        const cpuX = W - 10 - PADDLE_W
        if (s.bx + BALL_R >= cpuX && s.vx > 0 && paddleHit(s.by, s.cy, PADDLE_H)) {
          s.vx = reflectX(s.vx)
          s.vy = spinFromPaddle(s.by, s.cy, PADDLE_H, 5)
          s.bx = cpuX - BALL_R
        }
        if (s.bx < -BALL_R) {
          setScore((p) => ({ ...p, cpu: p.cpu + 1 }))
          s.bx = W / 2
          s.by = H / 2
          s.vx = 4
          s.vy = 2
        } else if (s.bx > W + BALL_R) {
          setScore((p) => ({ ...p, you: p.you + 1 }))
          s.bx = W / 2
          s.by = H / 2
          s.vx = -4
          s.vy = 2
        }
      }
      if (ctx) {
        ctx.fillStyle = '#0f172a'
        ctx.fillRect(0, 0, W, H)
        ctx.fillStyle = '#334155'
        for (let y = 0; y < H; y += 20) ctx.fillRect(W / 2 - 1, y, 2, 10)
        ctx.fillStyle = '#e2e8f0'
        ctx.fillRect(10, s.py, PADDLE_W, PADDLE_H)
        ctx.fillRect(W - 10 - PADDLE_W, s.cy, PADDLE_W, PADDLE_H)
        ctx.beginPath()
        ctx.arc(s.bx, s.by, BALL_R, 0, Math.PI * 2)
        ctx.fill()
      }
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('keydown', kd)
      window.removeEventListener('keyup', ku)
    }
  }, [s])

  useEffect(() => {
    if (score.you >= WIN || score.cpu >= WIN) {
      s.paused = true
      const won = score.you >= WIN
      void confirm({
        title: won ? 'You win' : 'CPU wins',
        message: `${score.you} to ${score.cpu}. Play again?`,
        confirmLabel: 'Play again',
        cancelLabel: 'Done',
      }).then((again) => {
        if (again) resetMatch()
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [score])

  return (
    <div className="flex flex-col items-start gap-3">
      <div className="flex flex-wrap gap-4 text-sm text-slate-600 dark:text-slate-400">
        <span className="font-medium">
          You {score.you} : {score.cpu} CPU
        </span>
        <span>Arrow keys or W / S to move. First to {WIN}.</span>
      </div>
      <canvas
        ref={canvasRef}
        width={W}
        height={H}
        data-testid="pong-canvas"
        className="max-w-full rounded-lg border border-slate-300 dark:border-slate-700"
      />
      <ConfirmModal {...confirmProps} />
    </div>
  )
}
