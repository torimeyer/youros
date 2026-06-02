// Pure logic for Guess Who. No React, no DOM, unit-testable headless.
import type { GwItem, GwEdition } from './guessWhoEditions'

export interface AiQuestion {
  attr: string
  text: string
}

/**
 * Pick the question that best splits the remaining candidates in half (maximum
 * information gain). Attributes where every candidate agrees are skipped (they
 * eliminate nothing). Returns null when no question can split the set further —
 * the caller should then make a guess. Ties break by attribute order for
 * deterministic, testable behavior.
 */
export function bestQuestion(candidates: GwItem[], edition: GwEdition): AiQuestion | null {
  const half = candidates.length / 2
  let best: { attr: string; diff: number } | null = null
  for (const attr of Object.keys(edition.questions)) {
    const yes = candidates.filter((c) => !!c.attrs[attr]).length
    if (yes === 0 || yes === candidates.length) continue // no split
    const diff = Math.abs(yes - half)
    if (best === null || diff < best.diff) best = { attr, diff }
  }
  return best === null ? null : { attr: best.attr, text: edition.questions[best.attr] }
}

/**
 * Keep only the candidates consistent with a yes/no answer to an attribute
 * question. answerYes=true keeps items where the attribute is true.
 */
export function filterByAnswer(candidates: GwItem[], attr: string, answerYes: boolean): GwItem[] {
  return candidates.filter((c) => !!c.attrs[attr] === answerYes)
}

/** Pick a starting item for either side. rng injected for tests. */
export function pickSecret(items: GwItem[], rng: () => number = Math.random): GwItem {
  return items[Math.floor(rng() * items.length)]
}
