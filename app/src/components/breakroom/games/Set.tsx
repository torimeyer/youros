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

// Enriched, vibrant palette
const COLOR: Record<SetCard['color'], string> = {
  red: '#dc2020',
  green: '#15a34a',
  purple: '#7c3aed',
}

// One hidden SVG at the document level supplies hatch patterns for every card.
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
            width="10"
            height="10"
            patternTransform="rotate(45 0 0)"
          >
            {/* Single line per 10-unit tile: generous gap so stripes read clearly at any size */}
            <line x1="0" y1="0" x2="0" y2="10" stroke={COLOR[color]} strokeWidth="3" />
          </pattern>
        ))}
      </defs>
    </svg>
  )
}

// viewBox="0 0 40 80". All shapes sit in a 40 x 80 coordinate space.
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
    strokeWidth: 2.5,
    fill,
    strokeLinejoin: 'round' as const,
    strokeLinecap: 'round' as const,
  }

  return (
    <svg viewBox="0 0 40 80" width="52" height="104" aria-hidden="true">
      {card.shape === 'oval' && (
        // Rounded rectangle, clean oval proportions
        <rect x="6" y="8" width="28" height="64" rx="14" {...shapeProps} />
      )}
      {card.shape === 'diamond' && (
        <polygon points="20,6 36,40 20,74 4,40" {...shapeProps} />
      )}
      {card.shape === 'squiggle' && (
        // Closed S-curve: right-bulge top, left-bulge bottom, consistent thickness.
        // Outer left edge ↓, hook across bottom, inner right edge ↑, hook across top.
        <path
          d={[
            'M 20 6',
            'C 32 4, 36 12, 32 20',   // arc right, downward
            'C 28 28, 12 32, 8 40',   // crossover toward left
            'C 4 48, 8 60, 20 68',    // arc left, continuing down
            'C 24 74, 32 74, 32 68',  // bottom hook (right side)
            'C 26 58, 14 52, 18 44',  // inner right edge, up through crossover
            'C 22 36, 34 32, 36 24',  // continuing up-right
            'C 38 16, 32 2, 20 6',    // top hook back to start
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
    'relative flex min-h-[9rem] items-center justify-center rounded-2xl border-2',
    'transition-all duration-150',
    // Soft base shadow for a lifted-card feel
    'shadow-[0_2px_8px_rgba(0,0,0,0.07)]',
    disabled && !sel && !matched ? 'cursor-default' : 'cursor-pointer',
  ]

  if (matched) {
    // Brief "pop" on a confirmed match
    return [
      ...base,
      'accent-border scale-[1.07] -translate-y-2',
      'shadow-[0_10px_28px_rgba(0,0,0,0.14)]',
      'bg-gradient-to-b from-green-50 to-emerald-50 dark:from-slate-800 dark:to-slate-700',
    ]
      .filter(Boolean)
      .join(' ')
  }

  if (sel) {
    return [
      ...base,
      'accent-border scale-[1.04] -translate-y-1.5',
      'shadow-[0_8px_22px_rgba(0,0,0,0.12)]',
      // Subtle accent tint on selected card
      'bg-gradient-to-b from-blue-50 to-indigo-50 dark:from-slate-800 dark:to-slate-800',
    ]
      .filter(Boolean)
      .join(' ')
  }

  return [
    ...base,
    'border-slate-200 dark:border-slate-700',
    // Delicate paper-like gradient on resting cards
    'bg-gradient-to-b from-white to-slate-50 dark:from-slate-900 dark:to-slate-950',
    'hover:-translate-y-0.5 hover:scale-[1.02]',
    'hover:shadow-[0_6px_16px_rgba(0,0,0,0.10)]',
    'hover:border-slate-300 dark:hover:border-slate-600',
  ]
    .filter(Boolean)
    .join(' ')
}

export default function SetGame() {
  const { confirm, confirmProps } = useConfirm()
  const [deck, setDeck] = useState<SetCard[]>([])
  const [tableau, setTableau] = useState<SetCard[]>([])
  const [selected, setSelected] = useState<number[]>([])
  const [found, setFound] = useState(0)
  const [message, setMessage] = useState('')
  const [best, setBest] = useState<number | null>(loadBest(GAME_ID)?.best ?? null)
  // Indices of the three cards currently mid-match-animation
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
          title: 'No more sets',
          message: `You found ${score} set${score === 1 ? '' : 's'}.${isBest ? ' New best!' : ''} Play again?`,
          confirmLabel: 'Play again',
          cancelLabel: 'Done',
        })
        if (again) deal()
      }
    },
    [confirm, deal],
  )

  function toggle(i: number) {
    // Block during "not a set" flash and during match animation
    if (message === 'Not a set' || matchedIndices.length > 0) return
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
      setMessage('Not a set')
      setTimeout(() => {
        setSelected([])
        setMessage('')
      }, 700)
      return
    }

    // Valid set: show match pop, then resolve after a short beat
    setMatchedIndices(next)
    setMessage('Set!')

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
    <div className="flex flex-col gap-4">
      {/* Global SVG pattern defs (hatch fills, invisible) */}
      <GlobalPatternDefs />

      {/* Score row */}
      <div className="flex items-center gap-4 text-sm">
        <span className="font-medium text-slate-700 dark:text-slate-300">Found: {found}</span>
        {best != null && <span className="text-slate-500">Best: {best}</span>}
        <span className="text-slate-500">Cards left: {deck.length}</span>
        {message && (
          <span
            className={`font-semibold transition-opacity ${
              message === 'Set!' ? 'accent-text' : 'text-red-500 dark:text-red-400'
            }`}
          >
            {message}
          </span>
        )}
      </div>

      {/* Card grid */}
      <div className="grid grid-cols-4 gap-3">
        {tableau.map((card, i) => {
          const sel = selected.includes(i)
          const matched = matchedIndices.includes(i)
          return (
            <button
              key={`${card.number}-${card.color}-${card.shading}-${card.shape}`}
              type="button"
              data-testid={`set-card-${i}`}
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
