// Speaker prefixes recognized in agent transcripts.
// Each block starts when a line begins with one of these tokens followed by ": ".
const SPEAKER_PREFIXES = ['User', 'Assistant', 'Tool result', 'Tool', 'System'] as const
export type SpeakerRole = (typeof SPEAKER_PREFIXES)[number]

export interface TranscriptBlock {
  role: SpeakerRole
  body: string
  parentToolUseId?: string
  isSubagent?: boolean
  thinking?: string
}

const SUBAGENT_PREFIX_RE = /^\[subagent:\s*([^\]]+)\]\s*/
const THINKING_PREFIX_RE = /^\[thinking\]\s*/

function extractParentToolUseId(event: Record<string, unknown>): string | undefined {
  const topLevel = event.parent_tool_use_id
  if (typeof topLevel === 'string' && topLevel.trim().length > 0) {
    return topLevel.trim()
  }
  const message = event.message
  if (message && typeof message === 'object' && !Array.isArray(message)) {
    const msgId = (message as Record<string, unknown>).parent_tool_use_id
    if (typeof msgId === 'string' && msgId.trim().length > 0) {
      return msgId.trim()
    }
  }
  return undefined
}

function buildSpeakerBlock(role: SpeakerRole, rawBody: string): TranscriptBlock {
  const body = rawBody.trim()
  const subMatch = body.match(SUBAGENT_PREFIX_RE)
  if (!subMatch) {
    return { role, body }
  }
  const parentToolUseId = subMatch[1].trim()
  const remainder = body.slice(subMatch[0].length)
  const thinkingMatch = remainder.match(THINKING_PREFIX_RE)
  const block: TranscriptBlock = {
    role,
    body,
    parentToolUseId,
    isSubagent: true,
  }
  if (thinkingMatch) {
    block.thinking = remainder.slice(thinkingMatch[0].length).trim()
  }
  return block
}

// Parse a single Claude Code stream-json JSONL event into zero or more TranscriptBlocks.
// Preserves parent_tool_use_id -> parentToolUseId / isSubagent when --forward-subagent-text is active.
export function parseStreamJsonEvent(
  raw: string | Record<string, unknown>,
): TranscriptBlock[] {
  let event: Record<string, unknown>
  if (typeof raw === 'string') {
    const trimmed = raw.trim()
    if (!trimmed.startsWith('{')) return []
    try {
      const parsed: unknown = JSON.parse(trimmed)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return []
      }
      event = parsed as Record<string, unknown>
    } catch {
      return []
    }
  } else {
    event = raw
  }

  const msgType = event.type
  if (msgType !== 'assistant' && msgType !== 'user') {
    return []
  }
  if (msgType === 'user' && event.isMeta === true) {
    return []
  }

  const parentToolUseId = extractParentToolUseId(event)
  const isSubagent = Boolean(parentToolUseId)
  const subPrefix = isSubagent ? `[subagent: ${parentToolUseId}] ` : ''
  const subMeta: Partial<TranscriptBlock> = isSubagent
    ? { parentToolUseId, isSubagent: true }
    : {}

  const message =
    event.message && typeof event.message === 'object' && !Array.isArray(event.message)
      ? (event.message as Record<string, unknown>)
      : {}
  const msgContent = message.content

  const out: TranscriptBlock[] = []

  if (msgType === 'assistant' && Array.isArray(msgContent)) {
    const thinkingChunks: string[] = []
    const textChunks: string[] = []
    const toolChunks: string[] = []

    for (const item of msgContent) {
      if (!item || typeof item !== 'object' || Array.isArray(item)) continue
      const block = item as Record<string, unknown>
      const btype = block.type
      if (btype === 'thinking') {
        const t =
          typeof block.thinking === 'string'
            ? block.thinking
            : typeof block.text === 'string'
              ? block.text
              : ''
        if (t.trim()) thinkingChunks.push(t.trim())
      } else if (btype === 'text') {
        const t = typeof block.text === 'string' ? block.text : ''
        if (t.trim()) textChunks.push(t)
      } else if (btype === 'tool_use') {
        const name = typeof block.name === 'string' && block.name ? block.name : 'tool'
        toolChunks.push(`[tool: ${name}]`)
      }
    }

    if (thinkingChunks.length > 0 && isSubagent) {
      const joinedThinking = thinkingChunks.join('\n')
      out.push({
        role: 'Assistant',
        body: `${subPrefix}[thinking] ${joinedThinking}`,
        thinking: joinedThinking,
        ...subMeta,
      })
    }
    if (textChunks.length > 0) {
      out.push({
        role: 'Assistant',
        body: `${subPrefix}${textChunks.join('\n').trim()}`,
        ...subMeta,
      })
    }
    if (toolChunks.length > 0) {
      out.push({
        role: 'Assistant',
        body: `${subPrefix}${toolChunks.join(' ')}`,
        ...subMeta,
      })
    }
    return out
  }

  if (msgType === 'user') {
    if (typeof msgContent === 'string' && msgContent.trim()) {
      out.push({
        role: 'User',
        body: `${subPrefix}${msgContent.trim()}`,
        ...subMeta,
      })
      return out
    }

    if (Array.isArray(msgContent)) {
      const userTextChunks: string[] = []
      for (const item of msgContent) {
        if (!item || typeof item !== 'object' || Array.isArray(item)) continue
        const block = item as Record<string, unknown>
        if (block.type === 'tool_result') {
          const resultVal = block.content
          if (typeof resultVal === 'string' && resultVal.trim()) {
            out.push({
              role: 'Tool result',
              body: `${subPrefix}${resultVal.trim()}`,
              ...subMeta,
            })
          } else if (Array.isArray(resultVal)) {
            const pieces: string[] = []
            for (const sub of resultVal) {
              if (sub && typeof sub === 'object' && !Array.isArray(sub)) {
                const subRec = sub as Record<string, unknown>
                if (subRec.type === 'text' && typeof subRec.text === 'string' && subRec.text.trim()) {
                  pieces.push(subRec.text)
                }
              }
            }
            if (pieces.length > 0) {
              out.push({
                role: 'Tool result',
                body: `${subPrefix}${pieces.join('\n').trim()}`,
                ...subMeta,
              })
            }
          }
        } else if (block.type === 'text' && typeof block.text === 'string' && block.text.trim()) {
          userTextChunks.push(block.text.trim())
        }
      }
      if (userTextChunks.length > 0) {
        out.push({
          role: 'User',
          body: `${subPrefix}${userTextChunks.join('\n')}`,
          ...subMeta,
        })
      }
    }
  }

  return out
}

function hasLeadingSpeakerPrefix(content: string): boolean {
  const firstLines = content.split('\n').slice(0, 20)
  return firstLines.some((line) =>
    SPEAKER_PREFIXES.some((prefix) => line.startsWith(`${prefix}: `)),
  )
}

function looksLikeStreamJson(content: string): boolean {
  const lines = content.split('\n')
  for (const rawLine of lines.slice(0, 20)) {
    const trimmed = rawLine.trim()
    if (!trimmed) continue
    if (!trimmed.startsWith('{')) return false
    try {
      const parsed: unknown = JSON.parse(trimmed)
      return Boolean(
        parsed &&
          typeof parsed === 'object' &&
          !Array.isArray(parsed) &&
          typeof (parsed as Record<string, unknown>).type === 'string',
      )
    } catch {
      return false
    }
  }
  return false
}

// Returns true when the content has at least one recognized speaker prefix or stream-json event.
export function hasSpeakerPrefixes(content: string): boolean {
  if (!content) return false
  return hasLeadingSpeakerPrefix(content) || looksLikeStreamJson(content)
}

// Split a transcript string into speaker blocks.
// Supports both speaker-prefixed transcripts ("Speaker: ...") and raw stream-json JSONL lines.
export function parseTranscript(content: string): TranscriptBlock[] {
  if (!content) return []

  if (!hasLeadingSpeakerPrefix(content) && looksLikeStreamJson(content)) {
    const blocks: TranscriptBlock[] = []
    for (const line of content.split('\n')) {
      if (!line.trim()) continue
      blocks.push(...parseStreamJsonEvent(line))
    }
    return blocks
  }

  const lines = content.split('\n')
  const blocks: TranscriptBlock[] = []
  let currentRole: SpeakerRole | null = null
  let currentLines: string[] = []

  const flush = () => {
    if (currentRole !== null) {
      blocks.push(buildSpeakerBlock(currentRole, currentLines.join('\n')))
    }
    currentLines = []
  }

  for (const line of lines) {
    const matchedPrefix = SPEAKER_PREFIXES.find((prefix) =>
      line.startsWith(`${prefix}: `),
    )
    if (matchedPrefix) {
      flush()
      currentRole = matchedPrefix
      // Everything after "Role: " is the first line of the body.
      currentLines.push(line.slice(matchedPrefix.length + 2))
    } else {
      currentLines.push(line)
    }
  }
  flush()

  return blocks
}
