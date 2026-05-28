import { useCallback, useEffect, useState } from 'react'
import {
  buildDeck,
  shuffle,
  isValidSet,
  tableauNeedsExpansion,
  type SetCard,
} from './setLogic'
import { recordHighScore, loadBest } from '../storage'
import { useConfirm } from '../../../hooks/useConfirm'
import ConfirmModal from '../../ConfirmModal'

const GAME_ID = 'set'
const INITIAL = 12
const MAX = 21

const COLOR: Record<SetCard['color'], string> = {
  red: '#dc2626',
  green: '#16a34a',
  purple: '#2563eb',
}

// Global SVG patterns for spiritualist shading
function GlobalPatternDefs() {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      style={{ position: 'absolute', width: 0, height: 0, overflow: 'hidden' }}
    >
      <defs>
        {(['red', 'green', 'purple'] as const).map((color) => (
          <pattern
            key={color}
            id={`set-hatch-${color}`}
            patternUnits="userSpaceOnUse"
            width="8"
            height="8"
            patternTransform="rotate(15 0 0)"
          >
            {/* Soft, organic dots for spiritual "vibration" */}
            <circle cx="2" cy="2" r="1.2" fill={COLOR[color]} fillOpacity="0.6" />
          </pattern>
        ))}
      </defs>
    </svg>
  )
}

// Organic, botanical shapes for the "Seer's Convergence"
function Shape({ card }: { card: SetCard }) {
  const c = COLOR[card.color]
  const fill =
    card.shading === 'solid'
      ? c
      : card.shading === 'striped'
        ? `url(#set-hatch-${card.color})`
        : 'none'

  const shapeProps = {
    stroke: c,
    strokeWidth: 2,
    fill,
    strokeLinejoin: 'round' as const,
    strokeLinecap: 'round' as const,
  }

  return (
    <svg viewBox="0 0 40 80" width="52" height="104" aria-hidden="true" className="filter drop-shadow-sm">
      {card.shape === 'oval' && (
        // Botanical "Seed" shape
        <path
          d="M 20 10 C 35 10, 35 70, 20 70 C 5 70, 5 10, 20 10 Z"
          {...shapeProps}
        />
      )}
      {card.shape === 'diamond' && (
        // "Celestial Eye" or Diamond
        <path
          d="M 20 6 L 36 40 L 20 74 L 4 40 Z M 20 30 A 10 10 0 1 1 20 50 A 10 10 0 1 1 20 30"
          {...shapeProps}
          fillRule="evenodd"
        />
      )}
      {card.shape === 'squiggle' && (
        // Ethereal "Life Force" spiral/curve
        <path
          d={[
            'M 20 10',
            'C 30 10, 35 25, 20 40',
            'C 5 55, 10 70, 20 70',
            'C 30 70, 35 55, 20 40',
            'C 5 25, 10 10, 20 10',
            'Z',
          ].join(' ')}
          {...shapeProps}
        />
      )}
    </svg>
  )
}

function CardFace({ card }: { card: SetCard }) {
  return (
    <div className="flex items-center justify-center gap-2">
      {Array.from({ length: card.number }).map((_, i) => (
        <Shape key={i} card={card} />
      ))}
    </div>
  )
}

function cardClass(sel: boolean, matched: boolean, disabled: boolean): string {
  const base = [
    'relative flex min-h-[9rem] items-center justify-center rounded-3xl border-2',
    'transition-all duration-300 ease-in-out',
    'shadow-[0_4px_12px_rgba(0,0,0,0.05)]',
    disabled && !sel && !matched ? 'cursor-default opacity-80' : 'cursor-pointer',
  ]

  if (matched) {
    return [
      ...base,
      'scale-[1.08] -translate-y-3 border-[#d8e2dc]',
      'shadow-[0_15px_35px_rgba(181,131,141,0.2)]',
      'bg-[#f8edeb] dark:bg-[#3d3133]',
    ].join(' ')
  }

  if (sel) {
    return [
      ...base,
      'scale-[1.05] -translate-y-2 border-[#b5838d]',
      'shadow-[0_10px_25px_rgba(0,0,0,0.1)]',
      'bg-[#fae1dd] dark:bg-[#4a3a3d]',
    ].join(' ')
  }

  return [
    ...base,
    'border-[#f4f1de] dark:border-slate-800',
    // Aged parchment feel
    'bg-[#fdfcf0] dark:bg-[#1a1a1a]',
    'hover:-translate-y-1 hover:scale-[1.02]',
    'hover:shadow-[0_8px_20px_rgba(0,0,0,0.08)]',
    'hover:border-[#e9edc9] dark:hover:border-slate-700',
  ].join(' ')
}

export default function SetGame() {
  const { confirm, confirmProps } = useConfirm()
  const [deck, setDeck] = useState<SetCard[]>([])
  const [tableau, setTableau] = useState<SetCard[]>([])
  const [selected, setSelected] = useState<number[]>([])
  const [found, setFound] = useState(0)
  const [message, setMessage] = useState('')
  const [best, setBest] = useState<number | null>(loadBest(GAME_ID)?.best ?? null)
  const [matchedIndices, setMatchedIndices] = useState<number[]>([])

  const deal = useCallback(() => {
    let d = shuffle(buildDeck())
    let t = d.slice(0, INITIAL)
    d = d.slice(INITIAL)
    while (tableauNeedsExpansion(t) && d.length > 0 && t.length < MAX) {
      t = t.concat(d.slice(0, 3))
      d = d.slice(3)
    }
    setDeck(d)
    setTableau(t)
    setSelected([])
    setFound(0)
    setMessage('')
    setMatchedIndices([])
  }, [])

  useEffect(() => {
    deal()
  }, [deal])

  const finishIfDone = useCallback(
    async (t: SetCard[], d: SetCard[], score: number) => {
      if (d.length === 0 && tableauNeedsExpansion(t)) {
        const isBest = recordHighScore(GAME_ID, score)
        if (isBest) setBest(score)
        const again = await confirm({
          title: 'Game over',
          message: `You found ${score} set${score === 1 ? '' : 's'}.${isBest ? ' New best!' : ''} Play again?`,
          confirmLabel: 'Play again',
          cancelLabel: 'Quit',
        })
        if (again) deal()
      }
    },
    [confirm, deal],
  )

  function toggle(i: number) {
    if (message === 'No match' || matchedIndices.length > 0) return
    if (selected.includes(i)) {
      setSelected(selected.filter((x) => x !== i))
      return
    }
    if (selected.length === 3) return

    const next = [...selected, i]
    setSelected(next)
    if (next.length < 3) return

    const [a, b, c] = next.map((idx) => tableau[idx])
    if (!isValidSet(a, b, c)) {
      setMessage('No match')
      setTimeout(() => {
        setSelected([])
        setMessage('')
      }, 700)
      return
    }

    setMatchedIndices(next)
    setMessage('Match!')

    setTimeout(() => {
      const chosen = new Set(next)
      let t = tableau.filter((_, idx) => !chosen.has(idx))
      let d = deck
      if (t.length < INITIAL) {
        t = t.concat(d.slice(0, 3))
        d = d.slice(3)
      }
      while (tableauNeedsExpansion(t) && d.length > 0 && t.length < MAX) {
        t = t.concat(d.slice(0, 3))
        d = d.slice(3)
      }
      const score = found + 1
      setTableau(t)
      setDeck(d)
      setSelected([])
      setMatchedIndices([])
      setFound(score)
      setMessage('')
      void finishIfDone(t, d, score)
    }, 420)
  }

  const isAnimating = matchedIndices.length > 0

  return (
    <div className="flex flex-col gap-6 italic">
      <GlobalPatternDefs />

      <div className="flex flex-col gap-1">
        <h2 className="text-2xl font-bold">Set</h2>
        <p className="text-xs text-slate-500 dark:text-slate-400">Find groups of 3 cards where each property is all-same or all-different.</p>
      </div>

      <div className="flex items-center gap-6 text-sm font-serif">
        <span className="text-slate-600 dark:text-slate-400">Sets found: {found}</span>
        {best != null && <span className="text-slate-500">Best: {best}</span>}
        <span className="text-slate-500">Remaining: {deck.length}</span>
        {message && (
          <span
            className={`font-semibold animate-pulse ${
              message === 'Match!' ? 'text-green-600' : 'text-red-500'
            }`}
          >
            {message}
          </span>
        )}
      </div>

      <div className="grid grid-cols-4 gap-4">
        {tableau.map((card, i) => {
          const sel = selected.includes(i)
          const matched = matchedIndices.includes(i)
          return (
            <button
              key={`${card.number}-${card.color}-${card.shading}-${card.shape}`}
              type="button"
              aria-pressed={sel}
              onClick={() => toggle(i)}
              disabled={isAnimating && !matched}
              className={cardClass(sel, matched, isAnimating)}
            >
              <CardFace card={card} />
            </button>
          )
        })}
      </div>

      <ConfirmModal {...confirmProps} />
    </div>
  )
}
