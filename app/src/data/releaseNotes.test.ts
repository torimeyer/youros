import { describe, it, expect } from 'vitest'
import releaseNotes from './releaseNotes'

describe('releaseNotes data', () => {
  it('has at least one release group', () => {
    expect(releaseNotes.length).toBeGreaterThan(0)
  })

  it('each group has a valid date string (YYYY-MM-DD)', () => {
    const datePattern = /^\d{4}-\d{2}-\d{2}$/
    for (const group of releaseNotes) {
      expect(group.date).toMatch(datePattern)
    }
  })

  it('each group has a non-empty label', () => {
    for (const group of releaseNotes) {
      expect(group.label.length).toBeGreaterThan(0)
    }
  })

  it('each group has at least one entry', () => {
    for (const group of releaseNotes) {
      expect(group.entries.length).toBeGreaterThan(0)
    }
  })

  it('each entry has a non-empty title and description', () => {
    for (const group of releaseNotes) {
      for (const entry of group.entries) {
        expect(entry.title.length).toBeGreaterThan(0)
        expect(entry.description.length).toBeGreaterThan(0)
      }
    }
  })

  it('groups are sorted newest first', () => {
    for (let i = 1; i < releaseNotes.length; i++) {
      expect(releaseNotes[i - 1].date >= releaseNotes[i].date).toBe(true)
    }
  })

  it('no descriptions contain em-dashes', () => {
    for (const group of releaseNotes) {
      for (const entry of group.entries) {
        expect(entry.description).not.toContain('\u2014')
        expect(entry.description).not.toContain('\u2013')
      }
    }
  })

  it('the April 6, 2026 release has 16 entries', () => {
    const april6 = releaseNotes.find((g) => g.date === '2026-04-06')
    expect(april6).toBeTruthy()
    expect(april6!.entries.length).toBe(16)
  })
})
