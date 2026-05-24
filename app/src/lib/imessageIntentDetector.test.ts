import { describe, it, expect } from 'vitest'
import { detectIMessageSendIntent } from './imessageIntentDetector'

describe('detectIMessageSendIntent', () => {
  // --- positive cases ---
  it('detects "tell X Y" pattern', () => {
    const result = detectIMessageSendIntent('tell lil oatmeal goodnight')
    expect(result).not.toBeNull()
    expect(result!.phrase).toBe('lil oatmeal goodnight')
  })

  it('detects "text X Y" pattern', () => {
    const result = detectIMessageSendIntent('text mom I will be home late')
    expect(result).not.toBeNull()
    expect(result!.phrase).toBe('mom I will be home late')
  })

  it('detects "message X Y" pattern', () => {
    const result = detectIMessageSendIntent('message dad happy birthday')
    expect(result).not.toBeNull()
    expect(result!.phrase).toBe('dad happy birthday')
  })

  it('detects "dm X Y" pattern', () => {
    const result = detectIMessageSendIntent('dm sarah are you free tonight')
    expect(result).not.toBeNull()
    expect(result!.phrase).toBe('sarah are you free tonight')
  })

  it('detects "let X know Y" pattern', () => {
    const result = detectIMessageSendIntent("let mom know I'm running late")
    expect(result).not.toBeNull()
    expect(result!.phrase).toContain('mom')
    expect(result!.phrase).toContain("I'm running late")
  })

  it('detects "let X know that Y" pattern', () => {
    const result = detectIMessageSendIntent('let alex know that dinner is ready')
    expect(result).not.toBeNull()
    expect(result!.phrase).toContain('alex')
    expect(result!.phrase).toContain('dinner is ready')
  })

  it('handles single-word contact name', () => {
    const result = detectIMessageSendIntent('tell scott the build is done')
    expect(result).not.toBeNull()
    expect(result!.phrase).toBe('scott the build is done')
  })

  it('handles multi-word contact name', () => {
    const result = detectIMessageSendIntent('tell big oatmeal good morning')
    expect(result).not.toBeNull()
    expect(result!.phrase).toBe('big oatmeal good morning')
  })

  // --- negative cases ---
  it('rejects "tell me about X" (pronoun guard)', () => {
    expect(detectIMessageSendIntent('tell me about quantum physics')).toBeNull()
  })

  it('rejects "text them Y" (pronoun guard)', () => {
    expect(detectIMessageSendIntent('text them the address')).toBeNull()
  })

  it('rejects bare single word after verb (no message)', () => {
    expect(detectIMessageSendIntent('tell sarah')).toBeNull()
  })

  it('returns null for empty string', () => {
    expect(detectIMessageSendIntent('')).toBeNull()
  })

  it('returns null for non-intent text', () => {
    expect(detectIMessageSendIntent('what is the weather today')).toBeNull()
  })

  it('returns null for null-ish input', () => {
    // @ts-expect-error testing runtime guard
    expect(detectIMessageSendIntent(null)).toBeNull()
  })

  it('returns null when only one word total (no message)', () => {
    expect(detectIMessageSendIntent('text')).toBeNull()
  })
})
