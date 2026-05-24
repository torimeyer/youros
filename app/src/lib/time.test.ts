import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  formatRelative,
  formatDate,
  formatTime,
  formatDateTime,
  formatTimestamp,
  formatSmartDate,
} from './time'

const FIXED_NOW = new Date('2026-05-22T14:30:00.000Z')

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(FIXED_NOW)
})

afterEach(() => {
  vi.useRealTimers()
})

describe('formatRelative', () => {
  it('returns "just now" for timestamps under 60 seconds ago', () => {
    const iso = new Date(FIXED_NOW.getTime() - 30_000).toISOString()
    expect(formatRelative(iso)).toBe('just now')
  })

  it('returns minutes for timestamps under an hour ago', () => {
    const iso = new Date(FIXED_NOW.getTime() - 5 * 60_000).toISOString()
    expect(formatRelative(iso)).toBe('5m ago')
  })

  it('returns hours for timestamps under a day ago', () => {
    const iso = new Date(FIXED_NOW.getTime() - 3 * 3600_000).toISOString()
    expect(formatRelative(iso)).toBe('3h ago')
  })

  it('returns "yesterday" for timestamps 1 day ago', () => {
    const iso = new Date(FIXED_NOW.getTime() - 26 * 3600_000).toISOString()
    expect(formatRelative(iso)).toBe('yesterday')
  })

  it('returns days for timestamps multiple days ago', () => {
    const iso = new Date(FIXED_NOW.getTime() - 5 * 86400_000).toISOString()
    expect(formatRelative(iso)).toBe('5d ago')
  })

  it('returns empty string for null', () => {
    expect(formatRelative(null)).toBe('')
  })

  it('returns empty string for undefined', () => {
    expect(formatRelative(undefined)).toBe('')
  })
})

describe('formatDate', () => {
  it('formats a date as "Month Day"', () => {
    const result = formatDate('2026-05-13T10:00:00Z')
    expect(result).toMatch(/May\s+13/)
  })

  it('formats a different month correctly', () => {
    const result = formatDate('2026-01-15T15:00:00Z')
    expect(result).toMatch(/Jan\s+15/)
  })

  it('returns empty string for null', () => {
    expect(formatDate(null)).toBe('')
  })

  it('returns empty string for invalid input', () => {
    expect(formatDate('not-a-date')).toBe('')
  })
})

describe('formatTime', () => {
  it('formats an ISO string as a time', () => {
    const result = formatTime('2026-05-22T14:30:00Z')
    expect(result).toMatch(/\d{1,2}:\d{2}/)
  })

  it('accepts a Date object', () => {
    const d = new Date('2026-05-22T09:05:00Z')
    const result = formatTime(d)
    expect(result).toMatch(/\d{1,2}:05/)
  })

  it('returns empty string for null', () => {
    expect(formatTime(null)).toBe('')
  })

  it('returns empty string for undefined', () => {
    expect(formatTime(undefined)).toBe('')
  })
})

describe('formatDateTime', () => {
  it('includes both month and time parts', () => {
    const result = formatDateTime('2026-05-13T14:30:00Z')
    expect(result).toMatch(/May/)
    expect(result).toMatch(/\d{1,2}:\d{2}/)
  })

  it('handles a different date correctly', () => {
    const result = formatDateTime('2026-01-07T08:00:00Z')
    expect(result).toMatch(/Jan/)
    expect(result).toMatch(/\d{1,2}:\d{2}/)
  })

  it('returns empty string for null', () => {
    expect(formatDateTime(null)).toBe('')
  })

  it('returns empty string for undefined', () => {
    expect(formatDateTime(undefined)).toBe('')
  })
})

describe('formatTimestamp', () => {
  it('includes month, day, and HH:MM:SS', () => {
    const result = formatTimestamp('2026-05-13T14:30:09Z')
    expect(result).toMatch(/May/)
    expect(result).toMatch(/13/)
    expect(result).toMatch(/\d{2}:\d{2}:\d{2}/)
  })

  it('works for a different timestamp', () => {
    const result = formatTimestamp('2026-01-15T15:00:00Z')
    expect(result).toMatch(/Jan/)
    expect(result).toMatch(/\d{2}:\d{2}:\d{2}/)
  })

  it('returns the raw string for invalid input', () => {
    expect(formatTimestamp('bad')).toBe('bad')
  })

  it('returns empty string for null', () => {
    expect(formatTimestamp(null)).toBe('')
  })
})

describe('formatSmartDate', () => {
  it('returns a time string for a timestamp earlier today', () => {
    const earlier = new Date(FIXED_NOW.getTime() - 2 * 3600_000).toISOString()
    const result = formatSmartDate(earlier)
    expect(result).toMatch(/\d{1,2}:\d{2}/)
  })

  it('returns "Yesterday" for a timestamp from yesterday', () => {
    const yesterday = new Date(FIXED_NOW)
    yesterday.setDate(yesterday.getDate() - 1)
    const result = formatSmartDate(yesterday.toISOString())
    expect(result).toBe('Yesterday')
  })

  it('returns "Month Day" for older timestamps', () => {
    const result = formatSmartDate('2026-05-10T10:00:00Z')
    expect(result).toMatch(/May\s+10/)
  })

  it('returns empty string for null', () => {
    expect(formatSmartDate(null)).toBe('')
  })
})
