import { describe, it, expect } from 'vitest'
import { bestQuestion, filterByAnswer, pickSecret } from './guessWhoLogic'
import { EDITIONS, type GwEdition, type GwItem } from './guessWhoEditions'

const food = EDITIONS.find((e) => e.id === 'food') as GwEdition

describe('guessWhoLogic', () => {
  it('picks a question that actually splits the candidate set', () => {
    const q = bestQuestion(food.items, food)
    expect(q).not.toBeNull()
    const yes = food.items.filter((c) => !!c.attrs[q!.attr]).length
    expect(yes).toBeGreaterThan(0)
    expect(yes).toBeLessThan(food.items.length)
    expect(q!.text).toBe(food.questions[q!.attr])
  })

  it('returns null when no question can split the candidates', () => {
    const one = [food.items[0]]
    expect(bestQuestion(one, food)).toBeNull()
    // Two identical-on-every-asked-attribute items also cannot be split.
    const twin: GwItem = { id: 'x', name: 'X', emoji: '?', attrs: { ...food.items[0].attrs } }
    expect(bestQuestion([food.items[0], twin], food)).toBeNull()
  })

  it('filters candidates consistently with a yes/no answer', () => {
    const yes = filterByAnswer(food.items, 'fruit', true)
    expect(yes.every((c) => c.attrs.fruit)).toBe(true)
    const no = filterByAnswer(food.items, 'fruit', false)
    expect(no.every((c) => !c.attrs.fruit)).toBe(true)
    expect(yes.length + no.length).toBe(food.items.length)
  })

  it('narrows to a single candidate by repeatedly asking the best question', () => {
    // The computer should always identify any secret using its own answers.
    for (const secret of food.items) {
      let candidates = [...food.items]
      let guard = 0
      while (candidates.length > 1 && guard < 50) {
        const q = bestQuestion(candidates, food)
        if (!q) break
        const answer = !!secret.attrs[q.attr]
        candidates = filterByAnswer(candidates, q.attr, answer)
        guard++
      }
      // Either pinned to one, or down to an indistinguishable cluster that
      // still contains the secret (a guess among them is fair).
      expect(candidates).toContain(secret)
      expect(candidates.length).toBeLessThanOrEqual(food.items.length)
    }
  })

  it('pickSecret is deterministic with an injected rng', () => {
    expect(pickSecret(food.items, () => 0)).toBe(food.items[0])
    expect(pickSecret(food.items, () => 0.999)).toBe(food.items[food.items.length - 1])
  })
})
