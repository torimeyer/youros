import { describe, it, expect } from 'vitest'
import { isSessionTask } from './tasks'

describe('isSessionTask', () => {
  it('returns true for tasks with session-task: description prefix', () => {
    expect(isSessionTask({ title: 'anything', description: 'session-task: some data' })).toBe(true)
  })

  it('returns true for tasks with old Auto-filed sentinel in description', () => {
    expect(isSessionTask({ title: 'anything', description: 'Auto-filed by SessionStart hook on 2026-04-14' })).toBe(true)
  })

  it('returns true for tasks matching the old Claude Code session title format', () => {
    expect(isSessionTask({ title: 'Claude Code session claude-code-5a57fc8e-5' })).toBe(true)
    expect(isSessionTask({ title: 'claude code session claude-code-abc123' })).toBe(true)
  })

  it('returns false for regular tasks', () => {
    expect(isSessionTask({ title: 'Fix the login flow', description: 'Users cannot log in on mobile' })).toBe(false)
    expect(isSessionTask({ title: 'Update cost tracking UI' })).toBe(false)
  })

  it('returns false when description is undefined', () => {
    expect(isSessionTask({ title: 'Normal task' })).toBe(false)
  })
})
