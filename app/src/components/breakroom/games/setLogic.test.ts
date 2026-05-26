import { describe, it, expect } from 'vitest'
import {
  isValidSet,
  buildDeck,
  shuffle,
  findAnySet,
  tableauNeedsExpansion,
  type SetCard,
} from './setLogic'

const card = (
  number: 1 | 2 | 3,
  color: SetCard['color'],
  shading: SetCard['shading'],
  shape: SetCard['shape'],
): SetCard => ({ number, color, shading, shape })

describe('isValidSet', () => {
  it('accepts all-different on every attribute', () => {
    expect(
      isValidSet(
        card(1, 'red', 'solid', 'oval'),
        card(2, 'green', 'solid', 'oval'),
        card(3, 'purple', 'solid', 'oval'),
      ),
    ).toBe(true)
  })

  it('accepts all-same on three attributes, all-different on one', () => {
    expect(
      isValidSet(
        card(1, 'red', 'solid', 'oval'),
        card(1, 'red', 'solid', 'squiggle'),
        card(1, 'red', 'solid', 'diamond'),
      ),
    ).toBe(true)
  })

  it('rejects a triple where one attribute is two-same-one-different', () => {
    // numbers 1,2,2 -> neither all-same nor all-different
    expect(
      isValidSet(
        card(1, 'red', 'solid', 'oval'),
        card(2, 'green', 'solid', 'oval'),
        card(2, 'purple', 'solid', 'oval'),
      ),
    ).toBe(false)
  })
})

describe('buildDeck', () => {
  it('builds 81 unique cards', () => {
    const deck = buildDeck()
    expect(deck).toHaveLength(81)
    const keys = new Set(deck.map((c) => `${c.number}-${c.color}-${c.shading}-${c.shape}`))
    expect(keys.size).toBe(81)
  })
})

describe('shuffle', () => {
  it('is a permutation and is deterministic for a fixed rng', () => {
    const deck = buildDeck()
    const rng = () => 0.42
    expect(shuffle(deck, rng)).toEqual(shuffle(deck, rng))
    expect(shuffle(deck, rng)).toHaveLength(81)
  })
})

describe('findAnySet / tableauNeedsExpansion', () => {
  it('finds a valid set when one exists and the indices form a set', () => {
    const cards = [
      card(1, 'red', 'solid', 'oval'),
      card(2, 'green', 'striped', 'squiggle'),
      card(3, 'purple', 'open', 'diamond'),
      card(1, 'red', 'open', 'diamond'),
    ]
    const found = findAnySet(cards)
    expect(found).not.toBeNull()
    const [i, j, k] = found!
    expect(isValidSet(cards[i], cards[j], cards[k])).toBe(true)
    expect(tableauNeedsExpansion(cards)).toBe(false)
  })

  it('returns null and flags expansion when no set is present', () => {
    const cards = [
      card(1, 'red', 'solid', 'oval'),
      card(1, 'red', 'solid', 'squiggle'),
      card(2, 'red', 'solid', 'oval'),
    ]
    expect(findAnySet(cards)).toBeNull()
    expect(tableauNeedsExpansion(cards)).toBe(true)
  })
})
