import { describe, it, expect } from 'vitest'
import {
  hasSpeakerPrefixes,
  parseStreamJsonEvent,
  parseTranscript,
} from './transcript'

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

  it('extracts parentToolUseId and isSubagent from speaker-prefixed subagent blocks', () => {
    const content = [
      'User: run full audit',
      '',
      'Assistant: Spawning subagent check.',
      '',
      'Assistant: [subagent: toolu_01XYZ] [thinking] Checking schema constraints.',
      '',
      'Assistant: [subagent: toolu_01XYZ] All constraints verified.',
      '',
      'Tool result: [subagent: toolu_01XYZ] 0 violations',
      '',
      'Assistant: Audit complete.',
    ].join('\n')

    const blocks = parseTranscript(content)
    expect(blocks).toHaveLength(6)

    expect(blocks[0].isSubagent).toBeUndefined()
    expect(blocks[0].parentToolUseId).toBeUndefined()
    expect(blocks[1].isSubagent).toBeUndefined()

    expect(blocks[2].role).toBe('Assistant')
    expect(blocks[2].isSubagent).toBe(true)
    expect(blocks[2].parentToolUseId).toBe('toolu_01XYZ')
    expect(blocks[2].thinking).toBe('Checking schema constraints.')
    expect(blocks[2].body).toContain('[subagent: toolu_01XYZ]')
    expect(blocks[2].body).toContain('Checking schema constraints.')

    expect(blocks[3].role).toBe('Assistant')
    expect(blocks[3].isSubagent).toBe(true)
    expect(blocks[3].parentToolUseId).toBe('toolu_01XYZ')
    expect(blocks[3].body).toBe('[subagent: toolu_01XYZ] All constraints verified.')

    expect(blocks[4].role).toBe('Tool result')
    expect(blocks[4].isSubagent).toBe(true)
    expect(blocks[4].parentToolUseId).toBe('toolu_01XYZ')

    expect(blocks[5].isSubagent).toBeUndefined()
    expect(blocks[5].body).toBe('Audit complete.')
  })

  it('preserves embedded JSON lines inside speaker-prefixed blocks', () => {
    const content = [
      'Assistant: Payload sample:',
      '{"type": "assistant", "note": "inline json"}',
    ].join('\n')
    const blocks = parseTranscript(content)
    expect(blocks).toHaveLength(1)
    expect(blocks[0].role).toBe('Assistant')
    expect(blocks[0].body).toContain('"inline json"')
  })

  it('parses raw stream-json JSONL with top-level and message-level parent_tool_use_id', () => {
    const jsonl = [
      JSON.stringify({
        type: 'assistant',
        message: {
          role: 'assistant',
          content: [{ type: 'text', text: 'Launching subagent.' }],
        },
      }),
      JSON.stringify({
        type: 'assistant',
        parent_tool_use_id: 'toolu_sub_99',
        message: {
          role: 'assistant',
          content: [
            { type: 'thinking', thinking: 'Evaluating endpoints.' },
            { type: 'text', text: 'Endpoint /health is green.' },
            { type: 'tool_use', name: 'Read', id: 'tu_1' },
          ],
        },
      }),
      JSON.stringify({
        type: 'user',
        message: {
          role: 'user',
          parent_tool_use_id: 'toolu_sub_99',
          content: [
            {
              type: 'tool_result',
              tool_use_id: 'tu_1',
              content: 'status: ok',
            },
          ],
        },
      }),
      JSON.stringify({
        type: 'user',
        isMeta: true,
        parent_tool_use_id: 'toolu_sub_99',
        message: { role: 'user', content: '<system-reminder>skip</system-reminder>' },
      }),
    ].join('\n')

    expect(hasSpeakerPrefixes(jsonl)).toBe(true)
    const blocks = parseTranscript(jsonl)
    expect(blocks).toHaveLength(5)

    // Parent assistant block
    expect(blocks[0].role).toBe('Assistant')
    expect(blocks[0].body).toBe('Launching subagent.')
    expect(blocks[0].isSubagent).toBeUndefined()
    expect(blocks[0].parentToolUseId).toBeUndefined()

    // Forwarded subagent thinking, text, tool_use, and tool_result blocks
    for (const subBlock of blocks.slice(1)) {
      expect(subBlock.isSubagent).toBe(true)
      expect(subBlock.parentToolUseId).toBe('toolu_sub_99')
      expect(subBlock.body).toContain('[subagent: toolu_sub_99]')
    }
    expect(blocks[1].thinking).toBe('Evaluating endpoints.')
    expect(blocks[2].body).toContain('Endpoint /health is green.')
    expect(blocks[3].body).toContain('[tool: Read]')
    expect(blocks[4].role).toBe('Tool result')
    expect(blocks[4].body).toContain('status: ok')
  })
})

describe('parseStreamJsonEvent', () => {
  it('handles forwarded subagent user text messages', () => {
    const blocks = parseStreamJsonEvent({
      type: 'user',
      parent_tool_use_id: 'toolu_abc123',
      message: {
        role: 'user',
        content: [{ type: 'text', text: 'Subagent task prompt' }],
      },
    })
    expect(blocks).toHaveLength(1)
    expect(blocks[0].role).toBe('User')
    expect(blocks[0].isSubagent).toBe(true)
    expect(blocks[0].parentToolUseId).toBe('toolu_abc123')
    expect(blocks[0].body).toBe('[subagent: toolu_abc123] Subagent task prompt')
  })
})
