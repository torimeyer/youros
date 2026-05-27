import { useCallback, useState } from 'react'
import { scorePegs, isWin, randomCode, type PegScore } from './mastermindLogic'
import { useConfirm } from '../../../hooks/useConfirm'
import ConfirmModal from '../../ConfirmModal'

const COLORS = [
  { key: 'red',    hex: '#ef4444', light: '#fca5a5', shadow: '#991b1b' },
  { key: 'orange', hex: '#f97316', light: '#fdba74', shadow: '#9a3412' },
  { key: 'yellow', hex: '#eab308', light: '#fde047', shadow: '#854d0e' },
  { key: 'green',  hex: '#22c55e', light: '#86efac', shadow: '#166534' },
  { key: 'blue',   hex: '#3b82f6', light: '#93c5fd', shadow: '#1e3a8a' },
  { key: 'purple', hex: '#a855f7', light: '#d8b4fe', shadow: '#581c87' },
]
const KEYS = COLORS.map((c) => c.key)
const LEN = 4
const MAX = 10

const colorOf = (k: string) => COLORS.find((c) => c.key === k)

/** A glossy 3D peg using radial-gradient for the highlight. */
function GlossyPeg({ colorKey, size = 26 }: { colorKey?: string; size?: number }) {
  const c = colorKey ? colorOf(colorKey) : null
  if (!c) {
    return (
      <span
        style={{
          display: 'inline-block',
          width: size,
          height: size,
          borderRadius: '50%',
          background:
            'radial-gradient(circle at 40% 35%, rgba(255,255,255,0.12) 0%, rgba(0,0,0,0.25) 100%)',
          border: '1.5px solid rgba(255,255,255,0.12)',
          boxShadow: 'inset 0 2px 4px rgba(0,0,0,0.6)',
          flexShrink: 0,
        }}
      />
    )
  }
  return (
    <span
      style={{
        display: 'inline-block',
        width: size,
        height: size,
        borderRadius: '50%',
        background: `radial-gradient(circle at 35% 28%, ${c.light} 0%, ${c.hex} 48%, ${c.shadow} 100%)`,
        boxShadow: `0 2px 6px rgba(0,0,0,0.55), inset 0 1px 3px rgba(255,255,255,0.45), 0 0 0 1.5px ${c.shadow}`,
        flexShrink: 0,
      }}
    />
  )
}

/** 2×2 grid of small feedback pegs: black = exact, white = partial, empty = miss. */
function FeedbackPegs({ score }: { score: PegScore }) {
  const marks = [
    ...Array(score.exact).fill('exact'),
    ...Array(score.partial).fill('partial'),
    ...Array(LEN - score.exact - score.partial).fill('none'),
  ]
  return (
    <span
      style={{
        display: 'grid',
        gridTemplateColumns: '1fr 1fr',
        gap: 3,
      }}
    >
      {marks.map((m, i) => (
        <span
          key={i}
          style={{
            width: 9,
            height: 9,
            borderRadius: '50%',
            background:
              m === 'exact'
                ? 'radial-gradient(circle at 35% 30%, #555 0%, #000 100%)'
                : m === 'partial'
                  ? 'radial-gradient(circle at 35% 30%, #fff 0%, #d1d5db 100%)'
                  : 'rgba(255,255,255,0.06)',
            border:
              m === 'partial'
                ? '1px solid #9ca3af'
                : m === 'none'
                  ? '1px solid rgba(255,255,255,0.1)'
                  : 'none',
            boxShadow: m !== 'none' ? '0 1px 3px rgba(0,0,0,0.6)' : 'none',
          }}
        />
      ))}
    </span>
  )
}

/** Empty 2×2 placeholder when a row hasn't been guessed yet. */
function EmptyFeedback() {
  return (
    <span
      style={{
        display: 'grid',
        gridTemplateColumns: '1fr 1fr',
        gap: 3,
      }}
    >
      {Array.from({ length: 4 }).map((_, i) => (
        <span
          key={i}
          style={{
            width: 9,
            height: 9,
            borderRadius: '50%',
            background: 'rgba(255,255,255,0.04)',
            border: '1px solid rgba(255,255,255,0.07)',
          }}
        />
      ))}
    </span>
  )
}

export default function Mastermind() {
  const { confirm, confirmProps } = useConfirm()
  const [secret, setSecret] = useState<string[]>(() => randomCode(KEYS, LEN))
  const [guesses, setGuesses] = useState<{ pegs: string[]; score: PegScore }[]>([])
  const [current, setCurrent] = useState<string[]>([])
  const [status, setStatus] = useState<'playing' | 'won' | 'lost'>('playing')

  const reset = useCallback(() => {
    setSecret(randomCode(KEYS, LEN))
    setGuesses([])
    setCurrent([])
    setStatus('playing')
  }, [])

  function pick(k: string) {
    if (status !== 'playing' || current.length >= LEN) return
    setCurrent([...current, k])
  }

  function submit() {
    if (current.length !== LEN || status !== 'playing') return
    const score = scorePegs(secret, current)
    const ng = [...guesses, { pegs: current, score }]
    setGuesses(ng)
    setCurrent([])
    if (isWin(score, LEN)) {
      setStatus('won')
      void confirm({
        title: 'Cracked it',
        message: `Solved in ${ng.length} guess${ng.length === 1 ? '' : 'es'}. Play again?`,
        confirmLabel: 'Play again',
        cancelLabel: 'Done',
      }).then((a) => {
        if (a) reset()
      })
    } else if (ng.length >= MAX) {
      setStatus('lost')
      void confirm({
        title: 'Out of guesses',
        message: 'The code is revealed below. Play again?',
        confirmLabel: 'Play again',
        cancelLabel: 'Done',
      }).then((a) => {
        if (a) reset()
      })
    }
  }

  return (
    <div className="flex flex-col gap-4 select-none">
      {/* ── Board frame ── */}
      <div
        className="rounded-xl overflow-hidden"
        style={{
          background:
            'linear-gradient(160deg, #5c2f0a 0%, #3d1a00 40%, #2a1000 100%)',
          padding: 3,
          boxShadow:
            '0 8px 28px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,200,100,0.15)',
        }}
      >
        {/* Board body */}
        <div
          className="rounded-xl"
          style={{
            background: 'linear-gradient(180deg, #1a1a2e 0%, #12122a 100%)',
            padding: '10px 12px',
          }}
        >
          {/* Secret code slot */}
          <div
            className="flex items-center justify-between rounded-lg px-3 py-2 mb-3"
            style={{
              background:
                'linear-gradient(90deg, rgba(92,47,10,0.8) 0%, rgba(61,26,0,0.9) 100%)',
              border: '1px solid rgba(255,180,0,0.18)',
            }}
          >
            <span className="text-xs font-semibold tracking-widest uppercase text-amber-400/60">
              Code
            </span>
            <div className="flex gap-1.5">
              {status === 'playing'
                ? Array.from({ length: LEN }).map((_, i) => (
                    <span
                      key={i}
                      style={{
                        width: 18,
                        height: 18,
                        borderRadius: '50%',
                        display: 'inline-block',
                        background:
                          'radial-gradient(circle at 40% 35%, rgba(255,200,80,0.2), rgba(150,80,0,0.4))',
                        border: '1px solid rgba(255,180,0,0.25)',
                      }}
                    />
                  ))
                : secret.map((k, i) => <GlossyPeg key={i} colorKey={k} size={18} />)}
            </div>
          </div>

          {/* Guess rows */}
          <div className="flex flex-col gap-1">
            {Array.from({ length: MAX }).map((_, rowIdx) => {
              const guess = guesses[rowIdx]
              const isActive =
                !guess && rowIdx === guesses.length && status === 'playing'
              const pegsToShow: (string | undefined)[] = guess
                ? guess.pegs
                : isActive
                  ? Array.from({ length: LEN }, (_, i) => current[i])
                  : Array.from({ length: LEN }, () => undefined)

              return (
                <div
                  key={rowIdx}
                  className="flex items-center gap-2 rounded-lg px-2 py-1.5 transition-all duration-200"
                  style={{
                    background: isActive
                      ? 'linear-gradient(90deg, rgba(255,255,255,0.07) 0%, rgba(255,255,255,0.04) 100%)'
                      : 'rgba(255,255,255,0.025)',
                    border: isActive
                      ? '1px solid rgba(255,255,255,0.13)'
                      : '1px solid rgba(255,255,255,0.04)',
                    boxShadow: isActive
                      ? '0 0 14px rgba(139,92,246,0.12)'
                      : 'none',
                  }}
                >
                  {/* Row number */}
                  <span className="w-4 shrink-0 text-right text-xs font-mono text-slate-600">
                    {MAX - rowIdx}
                  </span>

                  {/* Peg slots */}
                  <div className="flex gap-1.5 flex-1">
                    {pegsToShow.map((k, pegIdx) => (
                      <GlossyPeg key={pegIdx} colorKey={k} size={24} />
                    ))}
                  </div>

                  {/* Feedback peg grid */}
                  <div className="flex items-center justify-center w-8 h-8">
                    {guess ? <FeedbackPegs score={guess.score} /> : <EmptyFeedback />}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {/* ── Controls ── */}
      {status === 'playing' && (
        <div className="flex flex-col gap-3">
          {/* Action row */}
          <div className="flex items-center gap-3 flex-wrap">
            <button
              type="button"
              onClick={() => setCurrent(current.slice(0, -1))}
              disabled={!current.length}
              className="rounded px-2 py-1 text-sm text-slate-400 hover:text-slate-200 disabled:opacity-30 transition-colors"
            >
              Undo
            </button>
            <button
              type="button"
              data-testid="mm-submit"
              onClick={submit}
              disabled={current.length !== LEN}
              className="rounded-lg accent-highlight px-4 py-1.5 text-sm font-semibold disabled:opacity-40 transition-all hover:scale-105 active:scale-95"
            >
              Guess
            </button>
            <span className="text-xs text-slate-600 ml-auto">
              {guesses.length} / {MAX}
            </span>
          </div>

          {/* Color palette */}
          <div className="flex flex-wrap gap-2 pt-1">
            {COLORS.map((c) => (
              <button
                key={c.key}
                type="button"
                data-testid={`mm-color-${c.key}`}
                onClick={() => pick(c.key)}
                aria-label={c.key}
                className="rounded-full transition-transform duration-100 hover:scale-110 active:scale-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
                style={{
                  width: 36,
                  height: 36,
                  background: `radial-gradient(circle at 35% 28%, ${c.light} 0%, ${c.hex} 48%, ${c.shadow} 100%)`,
                  boxShadow: `0 3px 9px rgba(0,0,0,0.45), inset 0 1px 3px rgba(255,255,255,0.4), 0 0 0 2px ${c.shadow}`,
                  border: 'none',
                  cursor: 'pointer',
                }}
              />
            ))}
          </div>
        </div>
      )}

      {/* ── Game over: show code ── */}
      {status !== 'playing' && (
        <div className="flex items-center gap-2 text-sm">
          <span className="text-slate-500 dark:text-slate-400">Code:</span>
          <div className="flex gap-1.5">
            {secret.map((k, i) => (
              <GlossyPeg key={i} colorKey={k} size={22} />
            ))}
          </div>
        </div>
      )}

      <ConfirmModal {...confirmProps} />
    </div>
  )
}
