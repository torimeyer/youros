import { describe, it, expect, beforeEach } from 'vitest'
import {
  recordSettingsWriteStart,
  recordSettingsWriteSettled,
  fetchedSettingsValueIsStale,
  resetSettingsWriteBarrier,
} from './settingsWriteBarrier'

describe('settingsWriteBarrier (→2777)', () => {
  beforeEach(() => resetSettingsWriteBarrier())

  it('nothing is stale when no writes were recorded', () => {
    expect(fetchedSettingsValueIsStale('os_name', 0)).toBe(false)
  })

  it('an unsettled write makes the key stale for any fetch', () => {
    recordSettingsWriteStart(['os_name'])
    expect(fetchedSettingsValueIsStale('os_name', Date.now() + 60_000)).toBe(true)
  })

  it('a settled write blocks fetches that started at or before settling, not later ones', () => {
    recordSettingsWriteStart(['os_name'])
    const beforeSettle = Date.now()
    recordSettingsWriteSettled(['os_name'])
    expect(fetchedSettingsValueIsStale('os_name', beforeSettle)).toBe(true)
    expect(fetchedSettingsValueIsStale('os_name', Date.now() + 10_000)).toBe(false)
  })

  it('overlapping writes keep the key stale until the last one settles', () => {
    recordSettingsWriteStart(['os_name'])
    recordSettingsWriteStart(['os_name'])
    recordSettingsWriteSettled(['os_name'])
    expect(fetchedSettingsValueIsStale('os_name', Date.now() + 10_000)).toBe(true)
    recordSettingsWriteSettled(['os_name'])
    expect(fetchedSettingsValueIsStale('os_name', Date.now() + 10_000)).toBe(false)
  })

  it('keys are independent', () => {
    recordSettingsWriteStart(['os_name'])
    expect(fetchedSettingsValueIsStale('accent_color', 0)).toBe(false)
  })
})
