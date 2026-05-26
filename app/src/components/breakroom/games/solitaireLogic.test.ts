import { describe, it, expect } from 'vitest'
import {
  canMoveToTableau,
  canMoveToFoundation,
  isWin,
  buildDeck,
  cardColor,
  type Card,
} from './solitaireLogic'

const c = (suit: Card['suit'], rank: number, faceUp = true): Card => ({
  suit,
  rank: rank as Card['rank'],
  faceUp,
})

describe('cardColor', () => {
  it('reds and blacks', () => {
    expect(cardColor('hearts')).toBe('red')
    expect(cardColor('diamonds')).toBe('red')
    expect(cardColor('clubs')).toBe('black')
    expect(cardColor('spades')).toBe('black')
  })
})

describe('canMoveToTableau', () => {
  it('accepts opposite-color, one-lower onto a face-up card', () => {
    expect(canMoveToTableau(c('hearts', 6), c('spades', 7))).toBe(true)
  })
  it('rejects same color', () => {
    expect(canMoveToTableau(c('hearts', 6), c('diamonds', 7))).toBe(false)
  })
  it('rejects wrong rank', () => {
    expect(canMoveToTableau(c('hearts', 5), c('spades', 7))).toBe(false)
  })
  it('rejects onto a face-down card', () => {
    expect(canMoveToTableau(c('hearts', 6), c('spades', 7, false))).toBe(false)
  })
  it('only a King may move onto an empty column', () => {
    expect(canMoveToTableau(c('spades', 13), null)).toBe(true)
    expect(canMoveToTableau(c('spades', 12), null)).toBe(false)
  })
})

describe('canMoveToFoundation', () => {
  it('accepts an Ace onto an empty foundation', () => {
    expect(canMoveToFoundation(c('hearts', 1), null)).toBe(true)
  })
  it('rejects a non-Ace onto an empty foundation', () => {
    expect(canMoveToFoundation(c('hearts', 2), null)).toBe(false)
  })
  it('accepts same-suit ascending', () => {
    expect(canMoveToFoundation(c('hearts', 2), c('hearts', 1))).toBe(true)
  })
  it('rejects different suit', () => {
    expect(canMoveToFoundation(c('spades', 2), c('hearts', 1))).toBe(false)
  })
  it('rejects non-sequential', () => {
    expect(canMoveToFoundation(c('hearts', 3), c('hearts', 1))).toBe(false)
  })
})

describe('buildDeck / isWin', () => {
  it('builds 52 unique cards', () => {
    const d = buildDeck()
    expect(d).toHaveLength(52)
    expect(new Set(d.map((x) => x.suit + '-' + x.rank)).size).toBe(52)
  })
  it('wins when all four foundations top out at King', () => {
    const kings = [c('hearts', 13), c('diamonds', 13), c('clubs', 13), c('spades', 13)]
    expect(isWin(kings)).toBe(true)
    expect(isWin([c('hearts', 13), null, c('clubs', 13), c('spades', 13)])).toBe(false)
  })
})
