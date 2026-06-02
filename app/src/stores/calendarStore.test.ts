import { describe, it, expect, beforeEach } from 'vitest'
import { useCalendarStore } from './calendarStore'

const EVENT = {
  id: 'e-1',
  summary: 'Standup',
  start: { dateTime: '2026-05-10T09:00:00Z' },
  end: { dateTime: '2026-05-10T09:15:00Z' },
}

describe('useCalendarStore', () => {
  beforeEach(() => {
    useCalendarStore.setState({ events: [], wsConnected: false })
  })

  it('setEvents replaces the events list', () => {
    useCalendarStore.getState().setEvents([EVENT])
    expect(useCalendarStore.getState().events).toHaveLength(1)
    expect(useCalendarStore.getState().events[0].id).toBe('e-1')
  })

  it('content-equal setEvents keeps the same reference (→1730)', () => {
    useCalendarStore.getState().setEvents([EVENT])
    const first = useCalendarStore.getState().events
    // Shallow copy: the nested start/end objects keep the same references,
    // so the element is shallow-equal and the guard must collapse it.
    useCalendarStore.getState().setEvents([{ ...EVENT }])
    expect(useCalendarStore.getState().events).toBe(first)
  })

  it('a recreated nested object errs toward changed and swaps', () => {
    useCalendarStore.getState().setEvents([EVENT])
    const first = useCalendarStore.getState().events
    // New `start` object reference -> shallowEqualObject sees a changed field.
    useCalendarStore
      .getState()
      .setEvents([{ ...EVENT, start: { ...EVENT.start } }])
    expect(useCalendarStore.getState().events).not.toBe(first)
  })

  it('a real change swaps the reference', () => {
    useCalendarStore.getState().setEvents([EVENT])
    const first = useCalendarStore.getState().events
    useCalendarStore.getState().setEvents([{ ...EVENT, summary: 'Changed' }])
    expect(useCalendarStore.getState().events).not.toBe(first)
  })
})
