// Regression locks for the three Solitaire behaviors reworked onto the
// classic card design (→2898): double-click auto-move, drag and drop for
// single cards and stacks, and board sizing that fills the window.
//
// The deal is made deterministic by replacing shuffle() with a crafted deck.
// Deal order (see newGame in Solitaire.tsx): column c receives deck[i++] for
// rows 0..c, only the top row face-up; stock is deck[28..51] and drawStock
// pops from the END, so deck[51] is the first card drawn to the waste.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, within, cleanup } from '@testing-library/react'
import Solitaire from './Solitaire'

vi.mock('./solitaireLogic', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./solitaireLogic')>()
  type Card = import('./solitaireLogic').Card
  type Suit = import('./solitaireLogic').Suit
  type Rank = import('./solitaireLogic').Rank
  const C = (suit: Suit, rank: number): Card => ({ suit, rank: rank as Rank, faceUp: false })
  // Pinned positions in the dealt deck:
  //   deck[0]  -> column 0 top (face-up): A♠   (double-click -> foundation)
  //   deck[1]  -> column 1 bottom (face-down): 9♦ (flips when 6♥ leaves)
  //   deck[2]  -> column 1 top (face-up): 6♥   (moves onto 7♠)
  //   deck[5]  -> column 2 top (face-up): 7♠   (accepts 6♥; stack drags onto 8♥)
  //   deck[9]  -> column 3 top (face-up): 8♥   (accepts the 7♠+6♥ stack)
  //   deck[14] -> column 4 top (face-up): K♦   (no legal move anywhere)
  //   deck[51] -> first stock draw: A♥        (waste -> foundation drag)
  const pinned: Record<number, Card> = {
    0: C('spades', 1),
    1: C('diamonds', 9),
    2: C('hearts', 6),
    5: C('spades', 7),
    9: C('hearts', 8),
    14: C('diamonds', 13),
    51: C('hearts', 1),
  }
  const craftedDeck = (): Card[] => {
    const used = new Set(Object.values(pinned).map((c) => `${c.suit}-${c.rank}`))
    const rest = actual.buildDeck().filter((c) => !used.has(`${c.suit}-${c.rank}`))
    let k = 0
    return Array.from({ length: 52 }, (_, i) => pinned[i] ?? rest[k++])
  }
  return { ...actual, shuffle: () => craftedDeck() }
})

// Minimal stand-in for DataTransfer, which jsdom does not implement.
const dt = () => ({ dataTransfer: { effectAllowed: 'none', dropEffect: 'none' } })

afterEach(cleanup)

describe('Solitaire — double-click auto-move', () => {
  it('sends an eligible card to a foundation on double-click', () => {
    render(<Solitaire />)
    // A♠ sits face-up on top of column 0. An empty foundation ghost shows a
    // single decorative "A"; a real card face shows "A" in both corners.
    expect(within(screen.getByTestId('sol-foundation-0')).getAllByText('A')).toHaveLength(1)
    fireEvent.dblClick(screen.getByTestId('sol-0-0'))
    expect(screen.queryByTestId('sol-0-0')).toBeNull()
    expect(within(screen.getByTestId('sol-foundation-0')).getAllByText('A')).toHaveLength(2)
  })

  it('moves to a matching tableau column when no foundation fits, flipping the exposed card', () => {
    render(<Solitaire />)
    fireEvent.dblClick(screen.getByTestId('sol-1-1')) // 6♥ -> onto 7♠ in column 2
    expect(screen.getByTestId('sol-2-3')).toBeInTheDocument()
    expect(screen.queryByTestId('sol-1-1')).toBeNull()
    // The face-down 9♦ under the departed 6♥ flips face-up (rank now visible).
    expect(within(screen.getByTestId('sol-1-0')).getAllByText('9').length).toBeGreaterThan(0)
  })

  it('does nothing when the card has no legal move', () => {
    render(<Solitaire />)
    fireEvent.dblClick(screen.getByTestId('sol-4-4')) // K♦: no empty column, no foundation
    expect(screen.getByTestId('sol-4-4')).toBeInTheDocument()
    expect(within(screen.getByTestId('sol-4-4')).getAllByText('K').length).toBeGreaterThan(0)
  })
})

describe('Solitaire — drag and drop', () => {
  it('moves a single card between tableau columns', () => {
    render(<Solitaire />)
    fireEvent.dragStart(screen.getByTestId('sol-1-1'), dt()) // 6♥
    fireEvent.drop(screen.getByTestId('sol-2-2'), dt()) // onto 7♠
    expect(screen.getByTestId('sol-2-3')).toBeInTheDocument()
    expect(screen.queryByTestId('sol-1-1')).toBeNull()
  })

  it('moves a whole face-up stack together', () => {
    render(<Solitaire />)
    // Build a two-card run: 6♥ onto 7♠ in column 2.
    fireEvent.dblClick(screen.getByTestId('sol-1-1'))
    expect(screen.getByTestId('sol-2-3')).toBeInTheDocument()
    // Drag the 7♠ (with 6♥ stacked on it) onto 8♥ in column 3.
    fireEvent.dragStart(screen.getByTestId('sol-2-2'), dt())
    fireEvent.drop(screen.getByTestId('sol-3-3'), dt())
    expect(screen.getByTestId('sol-3-4')).toBeInTheDocument() // 7♠ arrived
    expect(screen.getByTestId('sol-3-5')).toBeInTheDocument() // 6♥ came along
    expect(screen.queryByTestId('sol-2-2')).toBeNull()
    expect(screen.queryByTestId('sol-2-3')).toBeNull()
  })

  it('moves the waste card onto a foundation by drag and drop', () => {
    render(<Solitaire />)
    fireEvent.click(screen.getByTestId('sol-stock')) // draw A♥ to the waste
    const waste = screen.getByLabelText('Waste pile')
    expect(within(waste).getAllByText('A').length).toBeGreaterThan(0)
    fireEvent.dragStart(waste, dt())
    fireEvent.drop(screen.getByTestId('sol-foundation-1'), dt())
    expect(within(screen.getByTestId('sol-foundation-1')).getAllByText('A')).toHaveLength(2)
    expect(within(waste).queryByText('A')).toBeNull()
  })

  it('rejects an illegal drop and leaves both piles unchanged', () => {
    render(<Solitaire />)
    fireEvent.dragStart(screen.getByTestId('sol-1-1'), dt()) // 6♥
    fireEvent.drop(screen.getByTestId('sol-3-3'), dt()) // onto 8♥: wrong rank
    expect(screen.getByTestId('sol-1-1')).toBeInTheDocument()
    expect(screen.queryByTestId('sol-3-4')).toBeNull()
  })
})

describe('Solitaire — board fills the window', () => {
  const origClientWidth = Object.getOwnPropertyDescriptor(Element.prototype, 'clientWidth')
  const origInnerHeight = Object.getOwnPropertyDescriptor(window, 'innerHeight')

  beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
      configurable: true,
      get: () => 1200,
    })
    Object.defineProperty(window, 'innerHeight', {
      configurable: true,
      writable: true,
      value: 768,
    })
  })

  afterEach(() => {
    delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth
    if (origClientWidth) Object.defineProperty(Element.prototype, 'clientWidth', origClientWidth)
    if (origInnerHeight) Object.defineProperty(window, 'innerHeight', origInnerHeight)
  })

  it('sizes cards from the container width and window height, and grows on resize', () => {
    render(<Solitaire />)
    // computeDims(1200, 768): height budget wins -> card width 108px.
    expect(screen.getByTestId('sol-stock').style.width).toBe('108px')
    // Taller window: height budget loosens, container width caps at 160px.
    Object.defineProperty(window, 'innerHeight', {
      configurable: true,
      writable: true,
      value: 1200,
    })
    fireEvent(window, new Event('resize'))
    expect(screen.getByTestId('sol-stock').style.width).toBe('160px')
  })
})
