import { useCallback, useEffect, useRef, useState } from 'react'
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

/* -- constants ------------------------------------------------------------ */
const SUIT_SYMBOL: Record<Suit, string> = {
  hearts: '♥', diamonds: '♦', clubs: '♣', spades: '♠',
}
const FOUNDATION_SUITS: Suit[] = ['spades', 'hearts', 'diamonds', 'clubs']
const rankLabel = (r: number) =>
  r === 1 ? 'A' : r === 11 ? 'J' : r === 12 ? 'Q' : r === 13 ? 'K' : String(r)
const isRed = (s: Suit) => s === 'hearts' || s === 'diamonds'

// Classic playing-card colors.
const COLORS = {
  red: '#dc2626',
  black: '#1a1c1e',
  parchment: '#f1f5f9',
  highlight: '#ffffff',
}

/* -- types ---------------------------------------------------------------- */
interface Game {
  stock: Card[]
  waste: Card[]
  foundations: Card[][]
  tableau: Card[][]
}

type Sel = { zone: 'waste' } | { zone: 'tableau'; col: number; row: number } | null

interface Dims {
  w: number
  h: number
  stackOffset: number
  cornerPx: number
  centerPx: number
}

/* -- pure helpers --------------------------------------------------------- */
function newGame(): Game {
  const deck = shuffle(buildDeck())
  const tableau: Card[][] = Array.from({ length: 7 }, () => [])
  let i = 0
  for (let col = 0; col < 7; col++)
    for (let n = 0; n <= col; n++)
      tableau[col].push({ ...deck[i++], faceUp: n === col })
  const stock = deck.slice(i).map((c) => ({ ...c, faceUp: false }))
  return { stock, waste: [], foundations: [[], [], [], []], tableau }
}

function topOf(pile: Card[]): Card | null {
  return pile.length ? pile[pile.length - 1] : null
}

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
    if (col.length && !col[col.length - 1].faceUp)
      col[col.length - 1] = { ...col[col.length - 1], faceUp: true }
  }
  return ng
}

// Card dimensions computed from container width and viewport height.
// Budget: 75vh = padding(32) + text(18) + gap(10) + CH + gap(14) + tableau(2.32*CH)
// -> 74 + 3.32*CH <= 75vh  ->  CH = (75vh-74)/3.32  ->  CW = CH/1.4
function computeDims(containerW: number, viewportH: number): Dims {
  const innerW = containerW - 32
  const byWidth = (innerW - 6 * 8) / 7
  const byHeight = (viewportH * 0.75 - 74) / (3.32 * 1.4)
  const w = Math.max(44, Math.min(200, Math.floor(Math.min(byWidth, byHeight))))
  const h = Math.round(w * 1.4)
  return {
    w,
    h,
    stackOffset: -Math.round(h * 0.78),
    cornerPx: Math.max(9, Math.round(w * 0.20)),
    centerPx: Math.max(16, Math.round(w * 0.38)),
  }
}

/* -- card visuals --------------------------------------------------------- */
function CardBack({ d }: { d: Dims }) {
  return (
    <div
      style={{
        width: d.w,
        height: d.h,
        flexShrink: 0,
        backgroundColor: '#1e40af', // Classic blue card back
        backgroundImage: [
          'linear-gradient(135deg, rgba(255,255,255,0.05) 25%, transparent 25%)',
          'linear-gradient(225deg, rgba(255,255,255,0.05) 25%, transparent 25%)',
          'linear-gradient(45deg, rgba(255,255,255,0.05) 25%, transparent 25%)',
          'linear-gradient(315deg, rgba(255,255,255,0.05) 25%, transparent 25%)',
        ].join(','),
        backgroundSize: '20px 20px',
      }}
      className="rounded border border-[#1e3a8a] shadow-[2px_2px_10px_rgba(0,0,0,0.5)]"
    />
  )
}

function CardFace({
  card,
  selected,
  d,
}: {
  card: Card
  selected?: boolean
  d: Dims
}) {
  const red = isRed(card.suit)
  const rank = rankLabel(card.rank)
  const suit = SUIT_SYMBOL[card.suit]
  
  // Face cards (A/J/Q/K) get a simple centered emblem.
  const isFaceCard = card.rank > 10 || card.rank === 1
  
  return (
    <div
      style={{
        width: d.w,
        height: d.h,
        flexShrink: 0,
        color: red ? COLORS.red : COLORS.black,
        background: `linear-gradient(165deg, ${COLORS.highlight} 0%, ${COLORS.parchment} 100%)`,
        position: 'relative',
        overflow: 'hidden'
      }}
      className={`select-none rounded border transition-all duration-200 ${
        selected ? 'border-yellow-400 ring-2 ring-yellow-400/50 shadow-2xl -translate-y-2' : 'border-[#bcbbb0]'
      }`}
    >
      {/* Cinematic shadow overlay */}
      <div className="absolute inset-0 bg-gradient-to-br from-transparent to-black/10 pointer-events-none" />
      
      <div
        className="absolute left-1 top-1 flex flex-col items-center"
        style={{ lineHeight: 1 }}
      >
        <span style={{ fontSize: d.cornerPx, fontWeight: 800, lineHeight: 1 }}>{rank}</span>
        <span style={{ fontSize: d.cornerPx * 0.9, lineHeight: 1 }}>{suit}</span>
      </div>

      <div className="flex h-full items-center justify-center opacity-80">
        {isFaceCard ? (
          <div className="flex flex-col items-center gap-1">
             <span style={{ fontSize: d.centerPx * 1.2, lineHeight: 1 }}>{suit}</span>
             <div className="h-0.5 w-8 bg-current opacity-20" />
          </div>
        ) : (
          <span style={{ fontSize: d.centerPx, lineHeight: 1 }}>{suit}</span>
        )}
      </div>

      <div
        className="absolute bottom-1 right-1 flex flex-col items-center"
        style={{ transform: 'rotate(180deg)', lineHeight: 1 }}
      >
        <span style={{ fontSize: d.cornerPx, fontWeight: 800, lineHeight: 1 }}>{rank}</span>
        <span style={{ fontSize: d.cornerPx * 0.9, lineHeight: 1 }}>{suit}</span>
      </div>
    </div>
  )
}

function CardView({ card, selected, d }: { card: Card; selected?: boolean; d: Dims }) {
  return card.faceUp ? <CardFace card={card} selected={selected} d={d} /> : <CardBack d={d} />
}

function EmptySlot({ ghost, d }: { ghost?: string; d: Dims }) {
  return (
    <div
      style={{ width: d.w, height: d.h, flexShrink: 0 }}
      className="flex flex-col items-center justify-center gap-0.5 rounded border border-[#bcbbb0]/30 bg-black/10"
    >
      {ghost && (
        <span style={{ fontSize: d.centerPx, lineHeight: 1, color: 'rgba(255,255,255,0.1)' }}>
          {ghost}
        </span>
      )}
    </div>
  )
}

/* -- main component ------------------------------------------------------- */
export default function Solitaire() {
  const { confirm, confirmProps } = useConfirm()
  const [game, setGame] = useState<Game>(newGame)
  const [sel, setSel] = useState<Sel>(null)
  const boardRef = useRef<HTMLDivElement>(null)
  const [dims, setDims] = useState<Dims>(() => computeDims(800, 900))
  const dragSrc = useRef<Sel>(null)

  // Responsive sizing via ResizeObserver
  useEffect(() => {
    const compute = () => {
      if (!boardRef.current) return
      setDims(computeDims(boardRef.current.clientWidth, window.innerHeight))
    }
    compute()
    let ro: ResizeObserver | null = null
    if (typeof ResizeObserver !== 'undefined' && boardRef.current) {
      ro = new ResizeObserver(compute)
      ro.observe(boardRef.current)
    }
    window.addEventListener('resize', compute)
    return () => {
      if (ro) ro.disconnect()
      window.removeEventListener('resize', compute)
    }
  }, [])

  const reset = useCallback(() => {
    setGame(newGame())
    setSel(null)
  }, [])

  useEffect(() => {
    if (isWin(game.foundations.map(topOf))) {
      void (async () => {
        const again = await confirm({
          title: 'You won!',
          message: 'You finished the game. Play again?',
          confirmLabel: 'Play again',
          cancelLabel: 'Quit',
        })
        if (again) reset()
      })()
    }
  }, [game, confirm, reset])

  /* -- core move functions ----------------------------------------------- */
  function applyFoundation(fi: number, src: Sel) {
    if (!src) return
    setGame((g) => {
      const mv = selCards(g, src)
      if (mv.length !== 1 || !canMoveToFoundation(mv[0], topOf(g.foundations[fi]))) return g
      const ng = removeSelected(g, src)
      ng.foundations[fi] = [...ng.foundations[fi], { ...mv[0], faceUp: true }]
      return ng
    })
    setSel(null)
  }

  function applyTableau(destCol: number, src: Sel) {
    if (!src) return
    setGame((g) => {
      const mv = selCards(g, src)
      if (!mv.length) return g
      if (src.zone === 'tableau' && src.col === destCol) return g
      if (!canMoveToTableau(mv[0], topOf(g.tableau[destCol]))) return g
      const ng = removeSelected(g, src)
      ng.tableau[destCol] = [
        ...ng.tableau[destCol],
        ...mv.map((c) => ({ ...c, faceUp: true })),
      ]
      return ng
    })
    setSel(null)
  }

  // Double-click: auto-move to best foundation, then best tableau column.
  function autoMove(src: Sel) {
    if (!src) return
    setSel(null)
    setGame((g) => {
      const mv = selCards(g, src)
      if (!mv.length || !mv[0].faceUp) return g
      // Try foundations first (single card only)
      if (mv.length === 1) {
        for (let fi = 0; fi < 4; fi++) {
          if (canMoveToFoundation(mv[0], topOf(g.foundations[fi]))) {
            const ng = removeSelected(g, src)
            ng.foundations[fi] = [...ng.foundations[fi], { ...mv[0], faceUp: true }]
            return ng
          }
        }
      }
      // Try tableau columns
      const srcCol = src.zone === 'tableau' ? src.col : -1
      for (let dc = 0; dc < 7; dc++) {
        if (dc === srcCol) continue
        if (canMoveToTableau(mv[0], topOf(g.tableau[dc]))) {
          const ng = removeSelected(g, src)
          ng.tableau[dc] = [...ng.tableau[dc], ...mv.map((c) => ({ ...c, faceUp: true }))]
          return ng
        }
      }
      return g
    })
  }

  /* -- click handlers ---------------------------------------------------- */
  function drawStock() {
    setSel(null)
    setGame((g) => {
      if (!g.stock.length)
        return {
          ...g,
          stock: [...g.waste].reverse().map((c) => ({ ...c, faceUp: false })),
          waste: [],
        }
      const stock = g.stock.slice()
      const card = { ...stock.pop()!, faceUp: true }
      return { ...g, stock, waste: [...g.waste, card] }
    })
  }

  function clickWaste() {
    if (sel?.zone === 'waste') {
      setSel(null)
      return
    }
    setSel(game.waste.length ? { zone: 'waste' } : null)
  }

  function clickTableauCard(col: number, row: number) {
    if (!sel) {
      if (game.tableau[col][row].faceUp) setSel({ zone: 'tableau', col, row })
      return
    }
    if (sel.zone === 'tableau' && sel.col === col && sel.row === row) {
      setSel(null)
      return
    }
    applyTableau(col, sel)
  }

  /* -- drag handlers ----------------------------------------------------- */
  function handleDragStart(e: React.DragEvent, src: Sel) {
    if (!src) { e.preventDefault(); return }
    const cards = selCards(game, src)
    if (!cards.length || !cards[0].faceUp) { e.preventDefault(); return }
    dragSrc.current = src
    setSel(null)
    e.dataTransfer.effectAllowed = 'move'
  }

  function handleDragOver(e: React.DragEvent) {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }

  function handleDrop(e: React.DragEvent, action: (src: Sel) => void) {
    e.preventDefault()
    e.stopPropagation()
    const src = dragSrc.current
    if (src) {
      dragSrc.current = null
      action(src)
    }
  }

  /* -- render ------------------------------------------------------------ */
  const selSet = selCards(game, sel)
  const isSelCard = (c: Card) => sel != null && selSet.includes(c)
  const wasteTop = topOf(game.waste)
  const gap = Math.max(4, Math.round(dims.w * 0.07))

  return (
    <div
      ref={boardRef}
      className="flex w-full flex-col rounded shadow-2xl overflow-hidden"
      style={{
        background: '#15803d', // Classic green felt
        padding: 24,
        gap: 20,
      }}
    >
      <div className="flex flex-col gap-1 border-b border-white/10 pb-4">
        <h2 className="text-2xl font-bold tracking-tight text-white">Solitaire</h2>
        <p className="text-xs font-medium text-white/70">Move every card to the four piles up top.</p>
      </div>

      <p className="text-[10px] uppercase tracking-widest text-white/50">
        Stack down by alternating color. Double-click to send a card up top.
      </p>

      {/* top row: stock, waste, spacer, 4 foundations */}
      <div className="flex items-start" style={{ gap }}>
        {/* Stock */}
        <button
          type="button"
          onClick={drawStock}
          aria-label="Draw from stock"
          data-testid="sol-stock"
          className="flex items-center justify-center rounded border border-white/5 bg-black/40 text-white/20 shadow-inner transition-colors hover:bg-black/60 flex-shrink-0"
          style={{ width: dims.w, height: dims.h, fontSize: dims.centerPx }}
        >
          {game.stock.length ? '↻' : '⟳'}
        </button>

        {/* Waste */}
        <button
          type="button"
          onClick={clickWaste}
          onDoubleClick={() => {
            if (game.waste.length) autoMove({ zone: 'waste' })
          }}
          aria-label="Waste pile"
          style={{
            width: dims.w,
            height: dims.h,
            flexShrink: 0,
            padding: 0,
            background: 'none',
            border: 'none',
          }}
          draggable={!!wasteTop}
          onDragStart={(e) => wasteTop && handleDragStart(e, { zone: 'waste' })}
          onDragEnd={() => { dragSrc.current = null }}
        >
          {wasteTop ? (
            <CardView card={wasteTop} selected={isSelCard(wasteTop)} d={dims} />
          ) : (
            <EmptySlot d={dims} />
          )}
        </button>

        <div style={{ width: Math.round(dims.w * 0.4), flexShrink: 0 }} />

        {/* Foundations */}
        {game.foundations.map((f, fi) => {
          const t = topOf(f)
          const gs = FOUNDATION_SUITS[fi]
          return (
            <button
              key={fi}
              type="button"
              onClick={() => sel && applyFoundation(fi, sel)}
              aria-label={`Foundation ${fi + 1}`}
              data-testid={`sol-foundation-${fi}`}
              style={{
                width: dims.w,
                height: dims.h,
                flexShrink: 0,
                padding: 0,
                background: 'none',
                border: 'none',
              }}
              onDragOver={handleDragOver}
              onDrop={(e) => handleDrop(e, (src) => applyFoundation(fi, src))}
            >
              {t ? (
                <CardView card={t} d={dims} />
              ) : (
                <div
                  style={{ width: dims.w, height: dims.h }}
                  className="flex flex-col items-center justify-center gap-0.5 rounded border border-[#bcbbb0]/10 bg-black/20"
                >
                  <span
                    style={{
                      fontSize: dims.centerPx,
                      lineHeight: 1,
                      color: isRed(gs)
                        ? 'rgba(157,36,36,0.2)'
                        : 'rgba(255,255,255,0.05)',
                    }}
                  >
                    {SUIT_SYMBOL[gs]}
                  </span>
                  <span
                    style={{ fontSize: dims.cornerPx, lineHeight: 1 }}
                    className="font-bold text-white/5"
                  >
                    A
                  </span>
                </div>
              )}
            </button>
          )
        })}
      </div>

      {/* Tableau */}
      <div className="flex" style={{ gap }}>
        {game.tableau.map((col, ci) => (
          <div
            key={ci}
            style={{ width: dims.w, minHeight: dims.h, flexShrink: 0 }}
            className="flex flex-col"
            onClick={() => sel && applyTableau(ci, sel)}
            onDragOver={handleDragOver}
            onDrop={(e) => {
              e.stopPropagation()
              handleDrop(e, (src) => applyTableau(ci, src))
            }}
          >
            {col.length === 0 ? (
              <EmptySlot d={dims} />
            ) : (
              col.map((card, ri) => (
                <button
                  key={ri}
                  type="button"
                  data-testid={`sol-${ci}-${ri}`}
                  style={ri === 0 ? {} : { marginTop: dims.stackOffset }}
                  className="p-0 border-0 bg-transparent"
                  onClick={(e) => {
                    e.stopPropagation()
                    clickTableauCard(ci, ri)
                  }}
                  onDoubleClick={(e) => {
                    e.stopPropagation()
                    if (card.faceUp) autoMove({ zone: 'tableau', col: ci, row: ri })
                  }}
                  draggable={card.faceUp}
                  onDragStart={(e) => {
                    e.stopPropagation()
                    handleDragStart(e, { zone: 'tableau', col: ci, row: ri })
                  }}
                  onDragEnd={() => { dragSrc.current = null }}
                  onDragOver={(e) => {
                    e.stopPropagation()
                    handleDragOver(e)
                  }}
                  onDrop={(e) => {
                    e.stopPropagation()
                    handleDrop(e, (src) => applyTableau(ci, src))
                  }}
                >
                  <CardView card={card} selected={isSelCard(card)} d={dims} />
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
