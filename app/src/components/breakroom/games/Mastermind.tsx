import { useCallback, useState } from 'react'
import { scorePegs, isWin, randomCode, type PegScore } from './mastermindLogic'
import { useConfirm } from '../../../hooks/useConfirm'
import ConfirmModal from '../../ConfirmModal'

const COLORS = [
  { key: 'red',    name: 'Burning Giraffe', hex: '#ef4444', emoji: '🦒' },
  { key: 'orange', name: 'Melting Clock',  hex: '#f97316', emoji: '🫠' },
  { key: 'yellow', name: 'Floating Egg',   hex: '#eab308', emoji: '🥚' },
  { key: 'green',  name: 'Grasshopper',    hex: '#22c55e', emoji: '🦗' },
  { key: 'blue',   name: 'Subconscious',   hex: '#3b82f6', emoji: '🌌' },
  { key: 'purple', name: 'Alchemist Cloud',hex: '#a855f7', emoji: '☁️' },
]
const KEYS = COLORS.map((c) => c.key)
const LEN = 4
const MAX = 10

const colorOf = (k: string) => COLORS.find((c) => c.key === k)

/** A surreal, dream-like object replacing the standard peg. */
function SurrealObject({ colorKey, size = 30 }: { colorKey?: string; size?: number }) {
  const c = colorKey ? colorOf(colorKey) : null
  if (!c) {
    return (
      <span
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: size,
          height: size,
          borderRadius: '50%',
          background: 'rgba(255,255,255,0.05)',
          border: '1px dashed rgba(255,255,255,0.1)',
          fontSize: size * 0.5,
          color: 'rgba(255,255,255,0.1)',
        }}
      >
        ?
      </span>
    )
  }
  return (
    <span
      title={c.name}
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: size,
        height: size,
        fontSize: size * 0.8,
        filter: 'drop-shadow(0 2px 4px rgba(0,0,0,0.5))',
        cursor: 'default',
        animation: 'float 3s ease-in-out infinite'
      }}
    >
      {c.emoji}
      <style>{`
        @keyframes float {
          0% { transform: translateY(0px) rotate(0deg); }
          50% { transform: translateY(-3px) rotate(5deg); }
          100% { transform: translateY(0px) rotate(0deg); }
        }
      `}</style>
    </span>
  )
}

/** 2x2 grid of feedback eyes: Open Eye = exact, Squinting Eye = partial, Empty = miss. */
function FeedbackEyes({ score }: { score: PegScore }) {
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
            width: 14,
            height: 14,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 10,
            opacity: m === 'none' ? 0.1 : 0.8,
            filter: m === 'none' ? 'grayscale(1)' : 'none'
          }}
        >
          {m === 'exact' ? '👁️' : m === 'partial' ? '😑' : '⚪'}
        </span>
      ))}
    </span>
  )
}

/** Empty 2x2 placeholder eyes. */
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
            width: 14,
            height: 14,
            borderRadius: '50%',
            background: 'rgba(0,0,0,0.1)',
            border: '1px solid rgba(255,255,255,0.05)',
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
        title: 'Awakened',
        message: 'The subconscious code is broken. You have escaped the alchemist’s dream. Re-enter the dream?',
        confirmLabel: 'Dream Again',
        cancelLabel: 'Stay Awake',
      }).then((a) => {
        if (a) reset()
      })
    } else if (ng.length >= MAX) {
      setStatus('lost')
      void confirm({
        title: 'Trapped in the Void',
        message: 'The code remains hidden. The dream deepens. Try to wake up again?',
        confirmLabel: 'Escape Again',
        cancelLabel: 'Succumb',
      }).then((a) => {
        if (a) reset()
      })
    }
  }

  return (
    <div className="flex flex-col items-center gap-6 select-none max-h-[75vh] overflow-y-auto pb-4 px-2">
      <div className="flex flex-col gap-1 items-center italic">
        <h2 className="text-3xl font-serif text-[#eab308] drop-shadow-lg">The Alchemist's Dream</h2>
        <p className="text-xs text-slate-400">Decipher the mad alchemist's neuroses.</p>
      </div>

      {/* Board frame - Barren Desert Plain */}
      <div
        className="rounded-lg relative overflow-hidden"
        style={{
          background: 'linear-gradient(180deg, #d4a373 0%, #faedcd 60%, #e9edc9 100%)',
          padding: 8,
          boxShadow: '0 20px 50px rgba(0,0,0,0.3), inset 0 -10px 30px rgba(0,0,0,0.1)',
          width: '100%',
          maxWidth: 360,
          border: '1px solid #bc6c25'
        }}
      >
        {/* Distant horizon line */}
        <div className="absolute top-[40%] left-0 right-0 h-[1px] bg-[#bc6c25]/30 pointer-events-none" />

        {/* Board body */}
        <div
          className="rounded-md"
          style={{
            background: 'rgba(255, 255, 255, 0.1)',
            backdropFilter: 'blur(4px)',
            padding: '12px',
            border: '1px solid rgba(255,255,255,0.2)'
          }}
        >
          {/* Secret code slot - Floating Eyes */}
          <div
            className="flex items-center justify-between rounded-md px-4 py-2 mb-6"
            style={{
              background: 'rgba(0,0,0,0.05)',
              border: '1px solid rgba(0,0,0,0.1)',
            }}
          >
            <span className="text-[10px] font-serif uppercase tracking-[0.2em] text-[#bc6c25]">
              Subconscious
            </span>
            <div className="flex gap-2">
              {status === 'playing'
                ? Array.from({ length: LEN }).map((_, i) => (
                    <span
                      key={i}
                      style={{
                        width: 24,
                        height: 24,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: 16,
                        opacity: 0.2
                      }}
                    >
                      ❓
                    </span>
                  ))
                : secret.map((k, i) => <SurrealObject key={i} colorKey={k} size={24} />)}
            </div>
          </div>

          {/* Guess rows */}
          <div className="flex flex-col gap-2">
            {[...Array(MAX)].map((_, i) => MAX - 1 - i).map((rowIdx) => {
              const guess = guesses[rowIdx]
              const isActive =
                !guess && rowIdx === guesses.length && status === 'playing'
              const objectsToShow: (string | undefined)[] = guess
                ? guess.pegs
                : isActive
                  ? Array.from({ length: LEN }, (_, i) => current[i])
                  : Array.from({ length: LEN }, () => undefined)

              return (
                <div
                  key={rowIdx}
                  className="flex items-center gap-4 rounded px-3 py-2 transition-all duration-500"
                  style={{
                    background: isActive
                      ? 'rgba(255,255,255,0.3)'
                      : 'rgba(255,255,255,0.05)',
                    borderBottom: '1px solid rgba(188, 108, 37, 0.2)',
                    transform: isActive ? 'scale(1.02)' : 'none'
                  }}
                >
                  <span className="w-6 shrink-0 text-right text-[10px] font-serif text-[#bc6c25]/60 italic">
                    {rowIdx + 1}
                  </span>

                  <div className="flex gap-3 shrink-0">
                    {objectsToShow.map((k, pegIdx) => (
                      <SurrealObject key={pegIdx} colorKey={k} size={32} />
                    ))}
                  </div>

                  <div className="flex items-center justify-center shrink-0 ml-auto">
                    {guess ? <FeedbackEyes score={guess.score} /> : <EmptyFeedback />}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {/* Controls */}
      {status === 'playing' && (
        <div className="flex flex-col gap-4 w-full" style={{ maxWidth: 360 }}>
          <div className="flex items-center gap-4">
            <button
              type="button"
              onClick={() => setCurrent(current.slice(0, -1))}
              disabled={!current.length}
              className="font-serif text-sm text-[#bc6c25] hover:text-[#d4a373] disabled:opacity-30 transition-colors underline decoration-dotted"
            >
              Recall
            </button>
            <button
              type="button"
              data-testid="mm-submit"
              onClick={submit}
              disabled={current.length !== LEN}
              className="rounded-full bg-[#bc6c25] text-[#faedcd] px-6 py-2 text-sm font-serif shadow-lg disabled:opacity-40 transition-all hover:bg-[#d4a373] hover:translate-y-[-2px] active:translate-y-[0px]"
            >
              Manifest
            </button>
            <span className="text-[10px] font-serif text-[#bc6c25] ml-auto uppercase tracking-widest">
              Depth: {guesses.length} / {MAX}
            </span>
          </div>

          <div className="flex flex-wrap gap-3 justify-center pt-2">
            {COLORS.map((c) => (
              <button
                key={c.key}
                type="button"
                data-testid={`mm-color-${c.key}`}
                onClick={() => pick(c.key)}
                aria-label={c.name}
                className="w-12 h-12 flex items-center justify-center rounded-xl bg-[#faedcd]/80 border border-[#bc6c25]/30 shadow-md transition-all duration-300 hover:scale-125 hover:shadow-xl hover:bg-white active:scale-95"
              >
                <span className="text-2xl">{c.emoji}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Game over: show code */}
      {status !== 'playing' && (
        <div className="flex flex-col items-center gap-3">
          <span className="text-xs font-serif uppercase tracking-widest text-[#bc6c25]">Subconscious Reality:</span>
          <div className="flex gap-4">
            {secret.map((k, i) => (
              <div key={i} className="flex flex-col items-center gap-1">
                <SurrealObject colorKey={k} size={40} />
                <span className="text-[8px] uppercase text-[#bc6c25]">{colorOf(k)?.name}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <ConfirmModal {...confirmProps} />
    </div>
  )
}
