import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { paddleHit, spinFromPaddle } from './pongLogic'
import { useConfirm } from '../../../hooks/useConfirm'
import ConfirmModal from '../../ConfirmModal'

// Logical game dimensions. All physics live in this space; canvas scales up.
const W = 480
const H = 300
const PADDLE_H = 60
const PADDLE_W = 10
const BALL_R = 6
const PLAYER_SPEED = 6
const CPU_SPEED = 3.8
const SPIN_MAX = 5
const BASE_VX = 4.5
const MAX_VX = 8
const VX_ACCEL = 1.04   // speed boost per paddle hit (caps at MAX_VX)
const MAX_H_RATIO = 0.75
const WIN = 5

// Neon arcade palette
const CLR_BG = '#000511'
const CLR_NET = '#00ffee'
const CLR_PLAYER = '#00ffee'   // cyan
const CLR_CPU = '#ff2d9b'      // hot pink
const CLR_BALL = '#ffffff'
const CLR_SCORE = '#00ffee'

export default function Pong() {
  const { confirm, confirmProps } = useConfirm()
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const scaleRef = useRef(1)
  const [score, setScore] = useState({ you: 0, cpu: 0 })
  const scoreRef = useRef({ you: 0, cpu: 0 })

  const s = useRef({
    bx: W / 2,
    by: H / 2,
    vx: BASE_VX,
    vy: 2,
    py: H / 2 - PADDLE_H / 2,
    cy: H / 2 - PADDLE_H / 2,
    up: false,
    down: false,
    paused: false,
    mouseY: -1,  // -1 means mouse not in use
  }).current

  useEffect(() => {
    scoreRef.current = score
  }, [score])

  // Resize canvas to fill container, capped at 75 vh height
  const resizeCanvas = useCallback(() => {
    const el = containerRef.current
    const canvas = canvasRef.current
    if (!el || !canvas) return
    const containerW = el.getBoundingClientRect().width
    const maxH = window.innerHeight * MAX_H_RATIO
    const scaleByW = containerW / W
    const scaleByH = maxH / H
    const scale = Math.min(scaleByW, scaleByH)
    scaleRef.current = scale
    const dpr = window.devicePixelRatio || 1
    const displayW = Math.round(W * scale)
    const displayH = Math.round(H * scale)
    canvas.width = displayW * dpr
    canvas.height = displayH * dpr
    canvas.style.width = `${displayW}px`
    canvas.style.height = `${displayH}px`
  }, [])

  // Run synchronously before first paint so the canvas has the correct size immediately
  useLayoutEffect(() => {
    resizeCanvas()
  }, [resizeCanvas])

  // Track container and window resize
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

  function resetMatch() {
    s.bx = W / 2
    s.by = H / 2
    s.vx = (Math.random() > 0.5 ? 1 : -1) * BASE_VX
    s.vy = (Math.random() > 0.5 ? 1 : -1) * 2
    s.py = H / 2 - PADDLE_H / 2
    s.cy = H / 2 - PADDLE_H / 2
    s.paused = false
    const zero = { you: 0, cpu: 0 }
    scoreRef.current = zero
    setScore(zero)
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

    // Mouse tracks paddle position relative to canvas logical space
    function onMouseMove(e: MouseEvent) {
      const canvas = canvasRef.current
      if (!canvas) return
      const rect = canvas.getBoundingClientRect()
      s.mouseY = (e.clientY - rect.top) / scaleRef.current
    }
    function onMouseLeave() {
      s.mouseY = -1
    }
    const cvs = canvasRef.current
    cvs?.addEventListener('mousemove', onMouseMove)
    cvs?.addEventListener('mouseleave', onMouseLeave)

    let raf = 0

    function frame() {
      if (!s.paused) {
        // Player movement: mouse overrides keys when active
        if (s.mouseY >= 0) {
          const target = s.mouseY - PADDLE_H / 2
          s.py = Math.max(0, Math.min(H - PADDLE_H, target))
        } else {
          if (s.up) s.py = Math.max(0, s.py - PLAYER_SPEED)
          if (s.down) s.py = Math.min(H - PADDLE_H, s.py + PLAYER_SPEED)
        }

        // CPU AI tracks ball center with limited speed
        const cc = s.cy + PADDLE_H / 2
        if (cc < s.by - 4) s.cy = Math.min(H - PADDLE_H, s.cy + CPU_SPEED)
        else if (cc > s.by + 4) s.cy = Math.max(0, s.cy - CPU_SPEED)

        // Ball movement
        s.bx += s.vx
        s.by += s.vy

        // Top/bottom wall bounce. Use abs to prevent sticking.
        if (s.by < BALL_R) { s.by = BALL_R; s.vy = Math.abs(s.vy) }
        if (s.by > H - BALL_R) { s.by = H - BALL_R; s.vy = -Math.abs(s.vy) }

        // Player paddle bounce (left side)
        if (s.bx - BALL_R <= 10 + PADDLE_W && s.vx < 0 && paddleHit(s.by, s.py, PADDLE_H)) {
          s.vx = Math.min(MAX_VX, Math.abs(s.vx) * VX_ACCEL)
          s.vy = spinFromPaddle(s.by, s.py, PADDLE_H, SPIN_MAX)
          s.bx = 10 + PADDLE_W + BALL_R
        }

        // CPU paddle bounce (right side)
        const cpuX = W - 10 - PADDLE_W
        if (s.bx + BALL_R >= cpuX && s.vx > 0 && paddleHit(s.by, s.cy, PADDLE_H)) {
          s.vx = -Math.min(MAX_VX, Math.abs(s.vx) * VX_ACCEL)
          s.vy = spinFromPaddle(s.by, s.cy, PADDLE_H, SPIN_MAX)
          s.bx = cpuX - BALL_R
        }

        // Scoring: reset ball to center after a point
        if (s.bx < -BALL_R) {
          setScore((p) => {
            const next = { ...p, cpu: p.cpu + 1 }
            scoreRef.current = next
            return next
          })
          s.bx = W / 2; s.by = H / 2
          s.vx = BASE_VX; s.vy = (Math.random() > 0.5 ? 1 : -1) * 2
        } else if (s.bx > W + BALL_R) {
          setScore((p) => {
            const next = { ...p, you: p.you + 1 }
            scoreRef.current = next
            return next
          })
          s.bx = W / 2; s.by = H / 2
          s.vx = -BASE_VX; s.vy = (Math.random() > 0.5 ? 1 : -1) * 2
        }
      }

      // Draw, scaling all logical coordinates to canvas display size
      const canvas = canvasRef.current
      const ctx = canvas?.getContext('2d')
      if (ctx && canvas) {
        const scale = scaleRef.current
        const dpr = window.devicePixelRatio || 1
        ctx.setTransform(scale * dpr, 0, 0, scale * dpr, 0, 0)

        // 1. Background
        ctx.fillStyle = CLR_BG
        ctx.fillRect(0, 0, W, H)

        // 2. Center net, glowing dashes
        ctx.save()
        ctx.shadowColor = CLR_NET
        ctx.shadowBlur = 12
        ctx.strokeStyle = 'rgba(0, 255, 238, 0.55)'
        ctx.lineWidth = 2
        ctx.setLineDash([12, 10])
        ctx.beginPath()
        ctx.moveTo(W / 2, 0)
        ctx.lineTo(W / 2, H)
        ctx.stroke()
        ctx.setLineDash([])
        ctx.restore()

        // 3. Score on canvas
        const sc = scoreRef.current
        ctx.save()
        ctx.shadowColor = CLR_SCORE
        ctx.shadowBlur = 14
        ctx.fillStyle = CLR_SCORE
        ctx.font = 'bold 36px "Courier New", Courier, monospace'
        ctx.textAlign = 'right'
        ctx.fillText(String(sc.you), W / 2 - 32, 46)
        ctx.textAlign = 'left'
        ctx.fillText(String(sc.cpu), W / 2 + 32, 46)
        ctx.restore()

        // 4. Player paddle, cyan glow
        ctx.save()
        ctx.shadowColor = CLR_PLAYER
        ctx.shadowBlur = 18
        ctx.fillStyle = CLR_PLAYER
        ctx.fillRect(10, s.py, PADDLE_W, PADDLE_H)
        ctx.restore()

        // 5. CPU paddle, pink glow
        ctx.save()
        ctx.shadowColor = CLR_CPU
        ctx.shadowBlur = 18
        ctx.fillStyle = CLR_CPU
        ctx.fillRect(W - 10 - PADDLE_W, s.cy, PADDLE_W, PADDLE_H)
        ctx.restore()

        // 6. Ball, bright white with outer halo
        ctx.save()
        ctx.shadowColor = '#99ffff'
        ctx.shadowBlur = 28
        ctx.fillStyle = CLR_BALL
        ctx.beginPath()
        ctx.arc(s.bx, s.by, BALL_R, 0, Math.PI * 2)
        ctx.fill()
        ctx.restore()

        // 7. CRT scanlines
        for (let y = 0; y < H; y += 3) {
          ctx.fillStyle = 'rgba(0, 0, 0, 0.07)'
          ctx.fillRect(0, y, W, 1)
        }

        // 8. Vignette, darken corners
        const vig = ctx.createRadialGradient(W / 2, H / 2, H * 0.28, W / 2, H / 2, H * 0.82)
        vig.addColorStop(0, 'rgba(0,0,0,0)')
        vig.addColorStop(1, 'rgba(0,0,0,0.52)')
        ctx.fillStyle = vig
        ctx.fillRect(0, 0, W, H)
      }

      raf = requestAnimationFrame(frame)
    }

    raf = requestAnimationFrame(frame)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('keydown', kd)
      window.removeEventListener('keyup', ku)
      cvs?.removeEventListener('mousemove', onMouseMove)
      cvs?.removeEventListener('mouseleave', onMouseLeave)
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
    <div ref={containerRef} className="flex w-full flex-col items-center gap-3">
      <div className="flex flex-wrap gap-4 text-sm text-slate-600 dark:text-slate-400">
        <span className="font-medium">
          You {score.you} : {score.cpu} CPU
        </span>
        <span>Arrow keys, W/S, or mouse to move. First to {WIN}.</span>
      </div>
      <canvas
        ref={canvasRef}
        data-testid="pong-canvas"
        className="max-w-full rounded-lg border border-slate-300 dark:border-slate-700"
      />
      <ConfirmModal {...confirmProps} />
    </div>
  )
}
