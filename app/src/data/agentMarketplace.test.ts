import { describe, it, expect } from 'vitest'
import { AGENT_MARKETPLACE, PERSONA_ICONS } from './agentMarketplace'

describe('AGENT_MARKETPLACE data', () => {
  it('has exactly 11 categories', () => {
    expect(AGENT_MARKETPLACE).toHaveLength(11)
  })

  it('has the expected category ids in order', () => {
    const ids = AGENT_MARKETPLACE.map((c) => c.id)
    expect(ids).toEqual([
      'everyone',
      'pm',
      'engineer',
      'sales',
      'writer',
      'home',
      'student',
      'marketing',
      'founder',
      'support',
      'designer',
    ])
  })

  it('includes all Wave 8 specialty templates', () => {
    const allNames = AGENT_MARKETPLACE.flatMap((c) => c.templates.map((t) => t.name))
    const wave8 = ['Campaign Brief', 'Budget Builder', 'Investor Update', 'Customer Reply', 'Design Critique']
    for (const name of wave8) {
      expect(allNames).toContain(name)
    }
  })

  it('includes the Roadmap template in the PM category', () => {
    const pm = AGENT_MARKETPLACE.find((c) => c.id === 'pm')
    expect(pm).toBeTruthy()
    const names = pm!.templates.map((t) => t.name)
    expect(names).toContain('Roadmap')
  })

  it('every category has a non-empty templates array', () => {
    for (const cat of AGENT_MARKETPLACE) {
      expect(Array.isArray(cat.templates)).toBe(true)
      expect(cat.templates.length).toBeGreaterThan(0)
    }
  })

  it('every category has a non-empty category name and tagline', () => {
    for (const cat of AGENT_MARKETPLACE) {
      expect(typeof cat.category).toBe('string')
      expect(cat.category.length).toBeGreaterThan(0)
      expect(typeof cat.tagline).toBe('string')
      expect(cat.tagline.length).toBeGreaterThan(0)
    }
  })

  it('every template has name, description, icon, model, and budget', () => {
    for (const cat of AGENT_MARKETPLACE) {
      for (const tpl of cat.templates) {
        expect(typeof tpl.name).toBe('string')
        expect(tpl.name.length).toBeGreaterThan(0)

        expect(typeof tpl.description).toBe('string')
        expect(tpl.description.length).toBeGreaterThan(0)

        expect(typeof tpl.icon).toBe('string')
        expect(tpl.icon.length).toBeGreaterThan(0)

        expect(typeof tpl.model).toBe('string')
        expect(tpl.model.length).toBeGreaterThan(0)

        expect(typeof tpl.budget).toBe('number')
        expect(Number.isFinite(tpl.budget)).toBe(true)
      }
    }
  })

  it('PERSONA_ICONS has an entry for every category id', () => {
    for (const cat of AGENT_MARKETPLACE) {
      expect(PERSONA_ICONS[cat.id]).toBeTruthy()
      expect(typeof PERSONA_ICONS[cat.id]).toBe('string')
    }
  })
})
