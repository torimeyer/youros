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
  purple: '#7c3aed',
}

function Shape({ card }: { card: SetCard }) {
  const c = COLOR[card.color]
  const fill = card.shading === 'open' ? 'none' : c
  const fillOpacity = card.shading === 'striped' ? 0.3 : 1
  const props = { stroke: c, strokeWidth: 4, fill, fillOpacity }
  return (
    <svg viewBox="0 0 40 80" width="20" height="40" aria-hidden="true">
      {card.shape === 'oval' && <rect x="7" y="8" width="26" height="64" rx="13" {...props} />}
      {card.shape === 'diamond' && <polygon points="20,6 36,40 20,74 4,40" {...props} />}
      {card.shape === 'squiggle' && (
        <path d="M12 10 C 38 6, 26 36, 30 50 C 34 70, 6 64, 10 70 L 10 70" {...props} />
      )}
    </svg>
  )
}

function CardFace({ card }: { card: SetCard }) {
  return (
    <div className="flex items-center justify-center gap-1">
      {Array.from({ length: card.number }).map((_, i) => (
        <Shape key={i} card={card} />
      ))}
    </div>
  )
}

export default function SetGame() {
  const { confirm, confirmProps } = useConfirm()
  const [deck, setDeck] = useState<SetCard[]>([])
  const [tableau, setTableau] = useState<SetCard[]>([])
  const [selected, setSelected] = useState<number[]>([])
  const [found, setFound] = useState(0)
  const [message, setMessage] = useState('')
  const [best, setBest] = useState<number | null>(loadBest(GAME_ID)?.best ?? null)

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
    if (message === 'Not a set') return
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
    setFound(score)
    setMessage('Set!')
    void finishIfDone(t, d, score)
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-4 text-sm">
        <span className="font-medium text-slate-700 dark:text-slate-300">Found: {found}</span>
        {best != null && <span className="text-slate-500">Best: {best}</span>}
        <span className="text-slate-500">Cards left: {deck.length}</span>
        {message && <span className="font-semibold accent-text">{message}</span>}
      </div>
      <div className="grid grid-cols-3 gap-3 sm:grid-cols-4">
        {tableau.map((card, i) => {
          const sel = selected.includes(i)
          return (
            <button
              key={`${card.number}-${card.color}-${card.shading}-${card.shape}`}
              type="button"
              data-testid={`set-card-${i}`}
              aria-pressed={sel}
              onClick={() => toggle(i)}
              className={`flex h-24 items-center justify-center rounded-xl border-2 bg-white transition dark:bg-slate-900 ${
                sel
                  ? 'accent-border bg-slate-100 dark:bg-slate-800'
                  : 'border-slate-200 hover:border-slate-400 dark:border-slate-700'
              }`}
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
