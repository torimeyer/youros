import { useCallback, useEffect, useState } from 'react'
import {
  buildDeck,
  shuffle,
  canMoveToTableau,
  canMoveToFoundation,
  isWin,
  type Card,
  type Suit,
} from './solitaireLogic'
import { useConfirm } from '../../../hooks/useConfirm'
import ConfirmModal from '../../ConfirmModal'

const SUIT_SYMBOL: Record<Suit, string> = {
  hearts: '♥',
  diamonds: '♦',
  clubs: '♣',
  spades: '♠',
}

// Suggested ghost suit order for empty foundation slots
const FOUNDATION_SUITS: Suit[] = ['spades', 'hearts', 'diamonds', 'clubs']

const rankLabel = (r: number) =>
  r === 1 ? 'A' : r === 11 ? 'J' : r === 12 ? 'Q' : r === 13 ? 'K' : String(r)
const isRed = (s: Suit) => s === 'hearts' || s === 'diamonds'

interface Game {
  stock: Card[]
  waste: Card[]
  foundations: Card[][]
  tableau: Card[][]
}

type Sel = { zone: 'waste' } | { zone: 'tableau'; col: number; row: number } | null

function newGame(): Game {
  const deck = shuffle(buildDeck())
  const tableau: Card[][] = [[], [], [], [], [], [], []]
  let i = 0
  for (let col = 0; col < 7; col++) {
    for (let n = 0; n <= col; n++) {
      tableau[col].push({ ...deck[i], faceUp: n === col })
      i++
    }
  }
  const stock = deck.slice(i).map((c) => ({ ...c, faceUp: false }))
  return { stock, waste: [], foundations: [[], [], [], []], tableau }
}

function topOf(pile: Card[]): Card | null {
  return pile.length ? pile[pile.length - 1] : null
}

// ── Card back: deep indigo with diagonal lattice ─────────────────────────────
function CardBack() {
  return (
    <div
      className="h-16 w-11 rounded-md border border-indigo-400 shadow-md"
      style={{
        backgroundColor: '#312e81',
        backgroundImage: [
          'repeating-linear-gradient(45deg, rgba(255,255,255,0.12) 0px, rgba(255,255,255,0.12) 1px, transparent 1px, transparent 7px)',
          'repeating-linear-gradient(-45deg, rgba(255,255,255,0.12) 0px, rgba(255,255,255,0.12) 1px, transparent 1px, transparent 7px)',
        ].join(', '),
      }}
    />
  )
}

// ── Card face: rank corners + large center pip ────────────────────────────────
function CardFace({ card, selected }: { card: Card; selected?: boolean }) {
  const red = isRed(card.suit)
  const rank = rankLabel(card.rank)
  const suit = SUIT_SYMBOL[card.suit]
  const textColor = red ? '#dc2626' : '#111827'

  return (
    <div
      className={`relative h-16 w-11 select-none rounded-md border shadow-sm transition-all duration-100 ${
        selected ? 'accent-border ring-2 shadow-lg -translate-y-1' : 'border-slate-200'
      }`}
      style={{
        background: 'linear-gradient(160deg, #ffffff 0%, #f3f4f6 100%)',
        color: textColor,
      }}
    >
      {/* Top-left corner: rank + suit */}
      <div className="absolute left-0.5 top-0.5 flex flex-col items-center leading-none">
        <span className="text-[10px] font-bold leading-none">{rank}</span>
        <span className="text-[9px] leading-none">{suit}</span>
      </div>

      {/* Center pip */}
      <div className="flex h-full items-center justify-center text-xl leading-none">
        {suit}
      </div>

      {/* Bottom-right corner: rotated (upside-down) rank + suit */}
      <div className="absolute bottom-0.5 right-0.5 flex rotate-180 flex-col items-center leading-none">
        <span className="text-[10px] font-bold leading-none">{rank}</span>
        <span className="text-[9px] leading-none">{suit}</span>
      </div>
    </div>
  )
}

function CardView({ card, selected }: { card: Card; selected?: boolean }) {
  if (!card.faceUp) return <CardBack />
  return <CardFace card={card} selected={selected} />
}

// ── Empty pile placeholder ────────────────────────────────────────────────────
function EmptySlot({ ghost }: { ghost?: string }) {
  return (
    <div className="flex h-16 w-11 flex-col items-center justify-center gap-0.5 rounded-md border border-dashed border-white/20">
      {ghost && (
        <span className="text-lg leading-none text-white/20">{ghost}</span>
      )}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────
export default function Solitaire() {
  const { confirm, confirmProps } = useConfirm()
  const [game, setGame] = useState<Game>(newGame)
  const [sel, setSel] = useState<Sel>(null)

  const reset = useCallback(() => {
    setGame(newGame())
    setSel(null)
  }, [])

  useEffect(() => {
    if (isWin(game.foundations.map(topOf))) {
      void (async () => {
        const again = await confirm({
          title: 'You won',
          message: 'Nicely done. Play again?',
          confirmLabel: 'Play again',
          cancelLabel: 'Done',
        })
        if (again) reset()
      })()
    }
  }, [game, confirm, reset])

  function selCards(g: Game, s: Sel): Card[] {
    if (!s) return []
    if (s.zone === 'waste') {
      const t = topOf(g.waste)
      return t ? [t] : []
    }
    return g.tableau[s.col].slice(s.row)
  }

  function removeSelected(g: Game, s: Sel): Game {
    const ng: Game = {
      stock: g.stock,
      waste: g.waste.slice(),
      foundations: g.foundations.map((f) => f.slice()),
      tableau: g.tableau.map((t) => t.slice()),
    }
    if (s!.zone === 'waste') {
      ng.waste.pop()
    } else {
      ng.tableau[s!.col] = ng.tableau[s!.col].slice(0, s!.row)
      const col = ng.tableau[s!.col]
      if (col.length && !col[col.length - 1].faceUp) {
        col[col.length - 1] = { ...col[col.length - 1], faceUp: true }
      }
    }
    return ng
  }

  function drawStock() {
    setSel(null)
    setGame((g) => {
      if (g.stock.length === 0) {
        return {
          ...g,
          stock: [...g.waste].reverse().map((c) => ({ ...c, faceUp: false })),
          waste: [],
        }
      }
      const stock = g.stock.slice()
      const card = { ...stock.pop()!, faceUp: true }
      return { ...g, stock, waste: [...g.waste, card] }
    })
  }

  function tryTableau(destCol: number) {
    setGame((g) => {
      const moving = selCards(g, sel)
      if (!moving.length || !sel) return g
      if (sel.zone === 'tableau' && sel.col === destCol) return g
      if (!canMoveToTableau(moving[0], topOf(g.tableau[destCol]))) return g
      const ng = removeSelected(g, sel)
      ng.tableau[destCol] = ng.tableau[destCol].concat(moving.map((c) => ({ ...c, faceUp: true })))
      return ng
    })
    setSel(null)
  }

  function tryFoundation(fi: number) {
    setGame((g) => {
      const moving = selCards(g, sel)
      if (moving.length !== 1 || !sel) return g
      if (!canMoveToFoundation(moving[0], topOf(g.foundations[fi]))) return g
      const ng = removeSelected(g, sel)
      ng.foundations[fi] = ng.foundations[fi].concat([{ ...moving[0], faceUp: true }])
      return ng
    })
    setSel(null)
  }

  function clickTableauCard(col: number, row: number) {
    if (!sel) {
      if (game.tableau[col][row].faceUp) setSel({ zone: 'tableau', col, row })
      return
    }
    tryTableau(col)
  }

  function clickWaste() {
    if (!sel) {
      if (game.waste.length) setSel({ zone: 'waste' })
    } else if (sel.zone === 'waste') {
      setSel(null)
    }
  }

  const selSet = selCards(game, sel)
  const isSelCard = (c: Card) => sel != null && selSet.includes(c)
  const wasteTop = topOf(game.waste)

  return (
    <div
      className="flex flex-col gap-4 rounded-xl p-4 shadow-inner"
      style={{ background: 'linear-gradient(150deg, #14532d 0%, #166534 60%, #15803d 100%)' }}
    >
      <p className="text-sm text-green-100/70">
        Click a card to pick it up, then click where it goes. Tap the deck to draw.
      </p>

      {/* ── Top row: stock · waste · gap · foundations ── */}
      <div className="flex items-start gap-2">
        {/* Stock */}
        <button
          type="button"
          onClick={drawStock}
          aria-label="Draw from stock"
          data-testid="sol-stock"
          className="flex h-16 w-11 items-center justify-center rounded-md border border-white/20 bg-black/20 text-xl text-white/60 shadow-inner transition-colors hover:bg-black/30"
        >
          {game.stock.length ? '↻' : '⟳'}
        </button>

        {/* Waste */}
        <button
          type="button"
          onClick={clickWaste}
          aria-label="Waste pile"
          className="h-16 w-11"
        >
          {wasteTop ? (
            <CardView card={wasteTop} selected={isSelCard(wasteTop)} />
          ) : (
            <EmptySlot />
          )}
        </button>

        <div className="w-6" />

        {/* Foundations */}
        {game.foundations.map((f, fi) => {
          const t = topOf(f)
          const ghostSuit = FOUNDATION_SUITS[fi]
          return (
            <button
              key={fi}
              type="button"
              onClick={() => tryFoundation(fi)}
              aria-label={`Foundation ${fi + 1}`}
              data-testid={`sol-foundation-${fi}`}
              className="h-16 w-11"
            >
              {t ? (
                <CardView card={t} />
              ) : (
                <div className="flex h-16 w-11 flex-col items-center justify-center gap-0.5 rounded-md border border-dashed border-white/20">
                  <span
                    className="text-lg leading-none"
                    style={{ color: isRed(ghostSuit) ? 'rgba(252,165,165,0.25)' : 'rgba(255,255,255,0.2)' }}
                  >
                    {SUIT_SYMBOL[ghostSuit]}
                  </span>
                  <span className="text-[9px] font-semibold leading-none text-white/20">A</span>
                </div>
              )}
            </button>
          )
        })}
      </div>

      {/* ── Tableau ── */}
      <div className="flex gap-2">
        {game.tableau.map((col, ci) => (
          <div
            key={ci}
            onClick={() => sel && tryTableau(ci)}
            className="flex min-h-32 w-11 flex-col"
          >
            {col.length === 0 ? (
              <EmptySlot />
            ) : (
              col.map((card, ri) => (
                <button
                  key={ri}
                  type="button"
                  data-testid={`sol-${ci}-${ri}`}
                  onClick={(e) => {
                    e.stopPropagation()
                    clickTableauCard(ci, ri)
                  }}
                  className={ri === 0 ? '' : '-mt-12'}
                >
                  <CardView card={card} selected={isSelCard(card)} />
                </button>
              ))
            )}
          </div>
        ))}
      </div>

      <ConfirmModal {...confirmProps} />
    </div>
  )
}
