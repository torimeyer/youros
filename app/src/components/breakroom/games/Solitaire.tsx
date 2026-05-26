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

function CardView({ card, selected }: { card: Card; selected?: boolean }) {
  if (!card.faceUp) {
    return <div className="h-16 w-11 rounded-md border border-slate-400 bg-indigo-900/50" />
  }
  return (
    <div
      className={`flex h-16 w-11 flex-col justify-between rounded-md border bg-white p-1 text-sm font-semibold ${
        selected ? 'accent-border ring-2' : 'border-slate-300'
      } ${isRed(card.suit) ? 'text-red-600' : 'text-slate-900'}`}
    >
      <span className="leading-none">{rankLabel(card.rank)}</span>
      <span className="self-end leading-none">{SUIT_SYMBOL[card.suit]}</span>
    </div>
  )
}

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
        return { ...g, stock: [...g.waste].reverse().map((c) => ({ ...c, faceUp: false })), waste: [] }
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
    <div className="flex flex-col gap-4">
      <p className="text-sm text-slate-600 dark:text-slate-400">
        Click a card to pick it up, then click where it goes. Tap the deck to draw.
      </p>
      <div className="flex items-start gap-2">
        <button
          type="button"
          onClick={drawStock}
          aria-label="Draw from stock"
          data-testid="sol-stock"
          className="h-16 w-11 rounded-md border border-slate-400 bg-slate-200 text-base text-slate-500 dark:bg-slate-800"
        >
          {game.stock.length ? '↻' : '⟳'}
        </button>
        <button type="button" onClick={clickWaste} aria-label="Waste pile" className="h-16 w-11">
          {wasteTop ? (
            <CardView card={wasteTop} selected={isSelCard(wasteTop)} />
          ) : (
            <div className="h-16 w-11 rounded-md border border-dashed border-slate-300" />
          )}
        </button>
        <div className="w-6" />
        {game.foundations.map((f, fi) => {
          const t = topOf(f)
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
                <div className="flex h-16 w-11 items-center justify-center rounded-md border border-dashed border-slate-300 text-slate-400">
                  A
                </div>
              )}
            </button>
          )
        })}
      </div>
      <div className="flex gap-2">
        {game.tableau.map((col, ci) => (
          <div
            key={ci}
            onClick={() => sel && tryTableau(ci)}
            className="flex min-h-32 w-11 flex-col"
          >
            {col.length === 0 ? (
              <div className="h-16 w-11 rounded-md border border-dashed border-slate-300" />
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
