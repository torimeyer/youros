import { useCallback, useState } from 'react'
import { scorePegs, isWin, randomCode, type PegScore } from './mastermindLogic'
import { useConfirm } from '../../../hooks/useConfirm'
import ConfirmModal from '../../ConfirmModal'

// Classic six-color Mastermind palette.
const COLORS = [
  { key: 'red', name: 'Red', hex: '#ef4444' },
  { key: 'orange', name: 'Orange', hex: '#f97316' },
  { key: 'yellow', name: 'Yellow', hex: '#eab308' },
  { key: 'green', name: 'Green', hex: '#22c55e' },
  { key: 'blue', name: 'Blue', hex: '#3b82f6' },
  { key: 'purple', name: 'Purple', hex: '#a855f7' },
]
const KEYS = COLORS.map((c) => c.key)
const LEN = 4
const MAX = 10

const colorOf = (k: string) => COLORS.find((c) => c.key === k)

/** A single peg: a solid color disc, or an empty slot when no color is set. */
function Peg({ colorKey, size = 30 }: { colorKey?: string; size?: number }) {
  const c = colorKey ? colorOf(colorKey) : null
  return (
    <span
      title={c?.name}
      aria-label={c?.name}
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        background: c ? c.hex : 'transparent',
        border: c ? '1px solid rgba(0,0,0,0.25)' : '2px dashed rgba(100,116,139,0.5)',
        boxShadow: c ? 'inset 0 -2px 3px rgba(0,0,0,0.2)' : 'none',
        display: 'inline-block',
      }}
    />
  )
}

/** Classic feedback: filled black = exact, filled white = right color/wrong spot. */
function Feedback({ score }: { score: PegScore }) {
  const marks = [
    ...Array(score.exact).fill('exact'),
    ...Array(score.partial).fill('partial'),
    ...Array(LEN - score.exact - score.partial).fill('none'),
  ]
  return (
    <span className="grid grid-cols-2 gap-1">
      {marks.map((m, i) => (
        <span
          key={i}
          className="h-2.5 w-2.5 rounded-full"
          style={{
            background: m === 'exact' ? '#111827' : m === 'partial' ? '#ffffff' : 'transparent',
            border:
              m === 'none'
                ? '1px solid rgba(100,116,139,0.35)'
                : '1px solid rgba(0,0,0,0.5)',
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
        title: 'You won!',
        message: 'You cracked the code. Play again?',
        confirmLabel: 'Play again',
        cancelLabel: 'Quit',
      }).then((a) => {
        if (a) reset()
      })
    } else if (ng.length >= MAX) {
      setStatus('lost')
      void confirm({
        title: 'Game over',
        message: 'You ran out of guesses. Try again?',
        confirmLabel: 'Try again',
        cancelLabel: 'Quit',
      }).then((a) => {
        if (a) reset()
      })
    }
  }

  return (
    <div className="flex flex-col items-center gap-5">
      <div className="flex flex-col items-center gap-1">
        <h2 className="text-2xl font-bold text-slate-900 dark:text-white">Mastermind</h2>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Crack the hidden four-color code in ten guesses.
        </p>
      </div>

      {/* Secret row */}
      <div className="flex items-center gap-3 rounded-lg bg-slate-100 px-4 py-2 dark:bg-slate-800">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Secret</span>
        <div className="flex gap-2">
          {status === 'playing'
            ? Array.from({ length: LEN }).map((_, i) => (
                <span
                  key={i}
                  className="flex h-6 w-6 items-center justify-center rounded-full bg-slate-300 text-xs font-bold text-slate-500 dark:bg-slate-600 dark:text-slate-300"
                >
                  ?
                </span>
              ))
            : secret.map((k, i) => <Peg key={i} colorKey={k} size={24} />)}
        </div>
      </div>

      {/* Guess history (most recent at the bottom) */}
      <div className="flex w-full max-w-xs flex-col gap-1">
        {Array.from({ length: MAX }).map((_, rowIdx) => {
          const guess = guesses[rowIdx]
          return (
            <div
              key={rowIdx}
              className="flex items-center gap-3 rounded px-2 py-1"
              style={{ background: rowIdx % 2 ? 'transparent' : 'rgba(100,116,139,0.06)' }}
            >
              <span className="w-5 shrink-0 text-right text-[10px] text-slate-400">{rowIdx + 1}</span>
              <div className="flex gap-2">
                {Array.from({ length: LEN }).map((_, i) => (
                  <Peg key={i} colorKey={guess?.pegs[i]} size={22} />
                ))}
              </div>
              <div className="ml-auto">{guess && <Feedback score={guess.score} />}</div>
            </div>
          )
        })}
      </div>

      {/* Active guess + palette + controls — always visible, never clipped */}
      {status === 'playing' && (
        <div className="flex w-full max-w-xs flex-col items-center gap-3 border-t border-slate-200 pt-4 dark:border-slate-700">
          <div className="flex items-center gap-3">
            <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Your guess</span>
            <div className="flex gap-2">
              {Array.from({ length: LEN }).map((_, i) => (
                <Peg key={i} colorKey={current[i]} size={28} />
              ))}
            </div>
          </div>

          <div className="flex flex-wrap justify-center gap-2">
            {COLORS.map((c) => (
              <button
                key={c.key}
                type="button"
                data-testid={`mm-color-${c.key}`}
                onClick={() => pick(c.key)}
                aria-label={c.name}
                className="h-10 w-10 rounded-full border border-black/20 shadow-sm transition-transform hover:scale-110 active:scale-95"
                style={{ background: c.hex }}
              />
            ))}
          </div>

          <div className="flex items-center gap-4">
            <button
              type="button"
              onClick={() => setCurrent(current.slice(0, -1))}
              disabled={!current.length}
              className="text-sm font-medium text-slate-600 underline decoration-dotted disabled:opacity-30 dark:text-slate-300"
            >
              Undo
            </button>
            <button
              type="button"
              data-testid="mm-submit"
              onClick={submit}
              disabled={current.length !== LEN}
              className="rounded-full bg-pink-500 px-6 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-pink-600 disabled:opacity-40"
            >
              Check
            </button>
            <span className="text-xs text-slate-500">Guess {guesses.length + 1} of {MAX}</span>
          </div>
        </div>
      )}

      {/* Game over: reveal the code */}
      {status !== 'playing' && (
        <div className="flex flex-col items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">The code was</span>
          <div className="flex gap-3">
            {secret.map((k, i) => (
              <div key={i} className="flex flex-col items-center gap-1">
                <Peg colorKey={k} size={34} />
                <span className="text-[10px] text-slate-500">{colorOf(k)?.name}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <ConfirmModal {...confirmProps} />
    </div>
  )
}
