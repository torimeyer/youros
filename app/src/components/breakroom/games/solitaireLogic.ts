// Pure logic for Klondike Solitaire. No React, no DOM, unit-testable headless.

export type Suit = 'hearts' | 'diamonds' | 'clubs' | 'spades'
export type Rank = 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13

export interface Card {
  suit: Suit
  rank: Rank
  faceUp: boolean
}

export function cardColor(suit: Suit): 'red' | 'black' {
  return suit === 'hearts' || suit === 'diamonds' ? 'red' : 'black'
}

// Tableau rule: descending rank, alternating color, onto a face-up card.
// An empty column (onto === null) accepts only a King.
export function canMoveToTableau(moving: Card, onto: Card | null): boolean {
  if (onto === null) return moving.rank === 13
  if (!onto.faceUp) return false
  return cardColor(moving.suit) !== cardColor(onto.suit) && moving.rank === onto.rank - 1
}

// Foundation rule: same suit, ascending from Ace. Empty accepts only an Ace.
export function canMoveToFoundation(card: Card, top: Card | null): boolean {
  if (top === null) return card.rank === 1
  return card.suit === top.suit && card.rank === top.rank + 1
}

// Win when all four foundation tops are Kings.
export function isWin(foundationTops: (Card | null)[]): boolean {
  return foundationTops.length === 4 && foundationTops.every((t) => t !== null && t.rank === 13)
}

export function buildDeck(): Card[] {
  const suits: Suit[] = ['hearts', 'diamonds', 'clubs', 'spades']
  const deck: Card[] = []
  for (const suit of suits)
    for (let r = 1; r <= 13; r++) deck.push({ suit, rank: r as Rank, faceUp: false })
  return deck
}

export function shuffle<T>(arr: readonly T[], rng: () => number = Math.random): T[] {
  const out = arr.slice()
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1))
    ;[out[i], out[j]] = [out[j], out[i]]
  }
  return out
}
