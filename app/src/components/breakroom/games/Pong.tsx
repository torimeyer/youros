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

// De Stijl / Mondrian palette
const CLR_BG = '#ffffff'
const CLR_LINE = '#000000'
const CLR_BALL = '#e02424' // Primary Red
const CLR_YELLOW = '#facd15'
const CLR_BLUE = '#2563eb'

export default function Pong() {
  const { confirm, confirmProps } = useConfirm()
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const scaleRef = useRef(1)
  const [score, setScore] = useState({ you: 0, cpu: 0 })
  const scoreRef = useRef({ you: 0, cpu: 0 })
  const [lastHit, setLastHit] = useState<'none' | 'you' | 'cpu'>('none')

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
    mouseY: -1,
    hitFlash: 0, // frame counter for hit effects
  }).current

  useEffect(() => {
    scoreRef.current = score
  }, [score])

  // Resize canvas
  const resizeCanvas = useCallback(() => {
    const el = containerRef.current
    const canvas = canvasRef.current
    if (!el || !canvas) return
    const containerW = el.getBoundingClientRect().width
    const maxH = window.innerHeight * MAX_H_RATIO
    const scale = Math.min(containerW / W, maxH / H)
    scaleRef.current = scale
    const dpr = window.devicePixelRatio || 1
    canvas.width = Math.round(W * scale) * dpr
    canvas.height = Math.round(H * scale) * dpr
    canvas.style.width = `${Math.round(W * scale)}px`
    canvas.style.height = `${Math.round(H * scale)}px`
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

  function resetMatch() {
    s.bx = W / 2; s.by = H / 2
    s.vx = (Math.random() > 0.5 ? 1 : -1) * BASE_VX
    s.vy = (Math.random() > 0.5 ? 1 : -1) * 2
    s.py = H / 2 - PADDLE_H / 2
    s.cy = H / 2 - PADDLE_H / 2
    s.paused = false
    setScore({ you: 0, cpu: 0 })
  }

  useEffect(() => {
    function key(e: KeyboardEvent, down: boolean) {
      if (['ArrowUp', 'w'].includes(e.key)) { s.up = down; e.preventDefault() }
      else if (['ArrowDown', 's'].includes(e.key)) { s.down = down; e.preventDefault() }
    }
    const kd = (e: KeyboardEvent) => key(e, true)
    const ku = (e: KeyboardEvent) => key(e, false)
    window.addEventListener('keydown', kd)
    window.addEventListener('keyup', ku)

    const cvs = canvasRef.current
    const onMouseMove = (e: MouseEvent) => {
      const rect = cvs?.getBoundingClientRect()
      if (rect) s.mouseY = (e.clientY - rect.top) / scaleRef.current
    }
    cvs?.addEventListener('mousemove', onMouseMove)
    cvs?.addEventListener('mouseleave', () => { s.mouseY = -1 })

    let raf = 0
    function frame() {
      if (!s.paused) {
        if (s.mouseY >= 0) s.py = Math.max(0, Math.min(H - PADDLE_H, s.mouseY - PADDLE_H / 2))
        else {
          if (s.up) s.py = Math.max(0, s.py - PLAYER_SPEED)
          if (s.down) s.py = Math.min(H - PADDLE_H, s.py + PLAYER_SPEED)
        }
        const cc = s.cy + PADDLE_H / 2
        if (cc < s.by - 4) s.cy = Math.min(H - PADDLE_H, s.cy + CPU_SPEED)
        else if (cc > s.by + 4) s.cy = Math.max(0, s.cy - CPU_SPEED)

        s.bx += s.vx; s.by += s.vy
        if (s.by < BALL_R) { s.by = BALL_R; s.vy = Math.abs(s.vy) }
        if (s.by > H - BALL_R) { s.by = H - BALL_R; s.vy = -Math.abs(s.vy) }

        if (s.bx - BALL_R <= 10 + PADDLE_W && s.vx < 0 && paddleHit(s.by, s.py, PADDLE_H)) {
          s.vx = Math.min(MAX_VX, Math.abs(s.vx) * VX_ACCEL)
          s.vy = spinFromPaddle(s.by, s.py, PADDLE_H, SPIN_MAX)
          s.bx = 10 + PADDLE_W + BALL_R
          s.hitFlash = 10; setLastHit('you')
        }
        const cpuX = W - 10 - PADDLE_W
        if (s.bx + BALL_R >= cpuX && s.vx > 0 && paddleHit(s.by, s.cy, PADDLE_H)) {
          s.vx = -Math.min(MAX_VX, Math.abs(s.vx) * VX_ACCEL)
          s.vy = spinFromPaddle(s.by, s.cy, PADDLE_H, SPIN_MAX)
          s.bx = cpuX - BALL_R
          s.hitFlash = 10; setLastHit('cpu')
        }

        if (s.bx < -BALL_R) {
          setScore(p => { const n = { ...p, cpu: p.cpu + 1 }; scoreRef.current = n; return n })
          s.bx = W/2; s.by = H/2; s.vx = BASE_VX; s.vy = (Math.random() > 0.5 ? 1 : -1) * 2
        } else if (s.bx > W + BALL_R) {
          setScore(p => { const n = { ...p, you: p.you + 1 }; scoreRef.current = n; return n })
          s.bx = W/2; s.by = H/2; s.vx = -BASE_VX; s.vy = (Math.random() > 0.5 ? 1 : -1) * 2
        }
        if (s.hitFlash > 0) s.hitFlash--
        else if (lastHit !== 'none') setLastHit('none')
      }

      const ctx = canvasRef.current?.getContext('2d')
      if (ctx) {
        const dpr = window.devicePixelRatio || 1
        ctx.setTransform(scaleRef.current * dpr, 0, 0, scaleRef.current * dpr, 0, 0)
        ctx.fillStyle = CLR_BG; ctx.fillRect(0, 0, W, H)

        // Mondrian Grid
        ctx.strokeStyle = CLR_LINE; ctx.lineWidth = 4
        ctx.strokeRect(2, 2, W - 4, H - 4)
        ctx.beginPath(); ctx.moveTo(W / 2, 0); ctx.lineTo(W / 2, H); ctx.stroke()
        
        // Dynamic primary blocks on hit
        if (s.hitFlash > 0) {
          ctx.fillStyle = lastHit === 'you' ? CLR_BLUE : CLR_YELLOW
          if (lastHit === 'you') ctx.fillRect(4, 4, W/4, H/3)
          else ctx.fillRect(W - W/4 - 4, H - H/3 - 4, W/4, H/3)
        }

        const sc = scoreRef.current
        ctx.fillStyle = CLR_LINE; ctx.font = 'bold 40px serif'
        ctx.textAlign = 'right'; ctx.fillText(String(sc.you), W/2 - 20, 50)
        ctx.textAlign = 'left'; ctx.fillText(String(sc.cpu), W/2 + 20, 50)

        // Paddles as thick black lines
        ctx.fillRect(10, s.py, PADDLE_W, PADDLE_H)
        ctx.fillRect(W - 10 - PADDLE_W, s.cy, PADDLE_W, PADDLE_H)

        // Ball as primary red square
        ctx.fillStyle = CLR_BALL
        ctx.fillRect(s.bx - BALL_R, s.by - BALL_R, BALL_R * 2, BALL_R * 2)
      }
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)
    return () => { cancelAnimationFrame(raf); window.removeEventListener('keydown', kd); window.removeEventListener('keyup', ku) }
  }, [s, lastHit])

  useEffect(() => {
    if (score.you >= WIN || score.cpu >= WIN) {
      s.paused = true
      void confirm({
        title: score.you >= WIN ? 'Equilibrium Restored' : 'Discord Prevails',
        message: 'The debute of forces has reached its conclusion. Seek absolute balance again?',
        confirmLabel: 'Commence Debate',
        cancelLabel: 'Rest',
      }).then(a => { if (a) resetMatch() })
    }
  }, [score, confirm])

  return (
    <div ref={containerRef} className="flex w-full flex-col items-center gap-4 font-serif">
      <div className="flex flex-col gap-1 items-center italic self-start">
        <h2 className="text-3xl text-black drop-shadow-sm font-black uppercase tracking-tighter">The Absolute Balance</h2>
        <p className="text-[10px] text-slate-500 tracking-[0.2em] uppercase">The eternal debate between the vertical and the horizontal.</p>
      </div>

      <div className="flex flex-wrap gap-4 text-xs text-slate-600 self-start border-l-4 border-black pl-3 py-1">
        <span className="font-bold uppercase">Alignment: {score.you} : {score.cpu}</span>
        <span className="italic">Strive for perfect geometric equilibrium.</span>
      </div>

      <canvas ref={canvasRef} data-testid="pong-canvas" className="max-w-full border-4 border-black shadow-xl" />
      <ConfirmModal {...confirmProps} />
    </div>
  )
}
