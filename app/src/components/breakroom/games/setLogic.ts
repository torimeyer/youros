// Pure logic for the Set card game. No React, no DOM, unit-testable headless.
// A "set" is 3 cards where, for EACH of the 4 attributes, the values are
// either all the same or all different.

export type SetNumber = 1 | 2 | 3
export type SetColor = 'red' | 'green' | 'purple'
export type SetShading = 'solid' | 'striped' | 'open'
export type SetShape = 'oval' | 'squiggle' | 'diamond'

export interface SetCard {
  number: SetNumber
  color: SetColor
  shading: SetShading
  shape: SetShape
}

const NUMBERS: SetNumber[] = [1, 2, 3]
const COLORS: SetColor[] = ['red', 'green', 'purple']
const SHADINGS: SetShading[] = ['solid', 'striped', 'open']
const SHAPES: SetShape[] = ['oval', 'squiggle', 'diamond']

/** A triple is a set if each attribute is all-same or all-different. */
export function isValidSet(a: SetCard, b: SetCard, c: SetCard): boolean {
  const keys: (keyof SetCard)[] = ['number', 'color', 'shading', 'shape']
  for (const k of keys) {
    const allSame = a[k] === b[k] && b[k] === c[k]
    const allDiff = a[k] !== b[k] && b[k] !== c[k] && a[k] !== c[k]
    if (!allSame && !allDiff) return false
  }
  return true
}

/** The full 81-card Set deck. */
export function buildDeck(): SetCard[] {
  const deck: SetCard[] = []
  for (const number of NUMBERS)
    for (const color of COLORS)
      for (const shading of SHADINGS)
        for (const shape of SHAPES) deck.push({ number, color, shading, shape })
  return deck
}

/** Deterministic Fisher-Yates when given an rng; Math.random by default. */
export function shuffle<T>(arr: readonly T[], rng: () => number = Math.random): T[] {
  const out = arr.slice()
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1))
    ;[out[i], out[j]] = [out[j], out[i]]
  }
  return out
}

/** Indices of the first valid set in `cards`, or null if none exists. */
export function findAnySet(cards: readonly SetCard[]): [number, number, number] | null {
  for (let i = 0; i < cards.length; i++)
    for (let j = i + 1; j < cards.length; j++)
      for (let k = j + 1; k < cards.length; k++)
        if (isValidSet(cards[i], cards[j], cards[k])) return [i, j, k]
  return null
}

/** Classic Set rule: when the tableau contains no set, more cards are dealt. */
export function tableauNeedsExpansion(cards: readonly SetCard[]): boolean {
  return findAnySet(cards) === null
}
