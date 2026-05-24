import { describe, it, expect } from 'vitest'
import { isRoadmapToTasksRequest } from './roadmapChatCommand'

describe('isRoadmapToTasksRequest', () => {
  it.each([
    ['create tasks from this roadmap', true],
    ['Create Tasks From The Roadmap', true],
    ['create task from the roadmap', true],
    ['make tasks from the roadmap please', true],
    ['turn the roadmap into tasks', true],
    ['turn this roadmap into tasks', true],
    ['convert the roadmap to tasks', true],
    ['convert my roadmap into tasks now', true],
    ['break the roadmap down into tasks', true],
    ['break the roadmap into tasks', true],
    ['build tasks from my roadmap', true],
    ['generate tasks from that roadmap', true],
  ])('matches: %s', (text, expected) => {
    expect(isRoadmapToTasksRequest(text)).toBe(expected)
  })

  it.each([
    ['', false],
    ['hi there', false],
    ["what's on the roadmap?", false],
    ['show me the tasks', false],
    ['how many tasks are open?', false],
    ['read the roadmap for me', false],
    ['/tasks', false],
  ])('does not match: %s', (text, expected) => {
    expect(isRoadmapToTasksRequest(text)).toBe(expected)
  })

  it('ignores leading and trailing whitespace', () => {
    expect(isRoadmapToTasksRequest('   create tasks from this roadmap   ')).toBe(
      true,
    )
  })
})
