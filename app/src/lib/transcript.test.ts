import { describe, it, expect } from 'vitest'
import { hasSpeakerPrefixes, parseTranscript } from './transcript'

describe('hasSpeakerPrefixes', () => {
  it('returns true when content has User: prefix', () => {
    expect(hasSpeakerPrefixes('User: hello')).toBe(true)
  })

  it('returns true when content has Assistant: prefix', () => {
    expect(hasSpeakerPrefixes('Assistant: hey')).toBe(true)
  })

  it('returns false when content has no speaker prefixes', () => {
    expect(hasSpeakerPrefixes('Just some plain text\nwith no speaker labels')).toBe(false)
  })

  it('returns false for empty string', () => {
    expect(hasSpeakerPrefixes('')).toBe(false)
  })
})

describe('parseTranscript', () => {
  it('splits User and Assistant into two blocks', () => {
    const blocks = parseTranscript('User: hi\n\nAssistant: hey')
    expect(blocks).toHaveLength(2)
    expect(blocks[0].role).toBe('User')
    expect(blocks[0].body).toBe('hi')
    expect(blocks[1].role).toBe('Assistant')
    expect(blocks[1].body).toBe('hey')
  })

  it('preserves multi-line body text within a block', () => {
    const blocks = parseTranscript('Assistant: Line one\nLine two\nLine three')
    expect(blocks).toHaveLength(1)
    expect(blocks[0].body).toBe('Line one\nLine two\nLine three')
  })

  it('handles Tool: prefix as a block', () => {
    const blocks = parseTranscript('Tool: read_file\nsome output here')
    expect(blocks).toHaveLength(1)
    expect(blocks[0].role).toBe('Tool')
    expect(blocks[0].body).toContain('read_file')
  })

  it('handles Tool result: prefix as a block', () => {
    const blocks = parseTranscript('Tool result: success')
    expect(blocks).toHaveLength(1)
    expect(blocks[0].role).toBe('Tool result')
  })

  it('handles System: prefix as a block', () => {
    const blocks = parseTranscript('System: you are a helpful agent')
    expect(blocks).toHaveLength(1)
    expect(blocks[0].role).toBe('System')
  })

  it('handles a full multi-turn conversation', () => {
    const content = [
      'User: what is 2+2',
      '',
      'Assistant: 4',
      '',
      'User: are you sure',
      '',
      'Assistant: yes',
    ].join('\n')
    const blocks = parseTranscript(content)
    expect(blocks).toHaveLength(4)
    expect(blocks[0].role).toBe('User')
    expect(blocks[1].role).toBe('Assistant')
    expect(blocks[2].role).toBe('User')
    expect(blocks[3].role).toBe('Assistant')
  })

  it('returns empty array for empty string', () => {
    expect(parseTranscript('')).toHaveLength(0)
  })

  it('returns no blocks when content has no speaker prefixes', () => {
    // No prefix match means currentRole stays null and flush() never emits a block.
    const blocks = parseTranscript('just plain text without any prefixes')
    expect(blocks).toHaveLength(0)
  })
})
