// Speaker prefixes recognized in agent transcripts.
// Each block starts when a line begins with one of these tokens followed by ": ".
const SPEAKER_PREFIXES = ['User', 'Assistant', 'Tool result', 'Tool', 'System'] as const
export type SpeakerRole = (typeof SPEAKER_PREFIXES)[number]

export interface TranscriptBlock {
  role: SpeakerRole
  body: string
}

// Returns true when the content has at least one recognized speaker prefix.
export function hasSpeakerPrefixes(content: string): boolean {
  const firstLines = content.split('\n').slice(0, 20)
  return firstLines.some((line) =>
    SPEAKER_PREFIXES.some((prefix) => line.startsWith(`${prefix}: `)),
  )
}

// Split a transcript string into speaker blocks.
// Lines beginning with "Speaker: " start a new block.
// Consecutive non-prefix lines are appended to the current block body.
export function parseTranscript(content: string): TranscriptBlock[] {
  const lines = content.split('\n')
  const blocks: TranscriptBlock[] = []
  let currentRole: SpeakerRole | null = null
  let currentLines: string[] = []

  const flush = () => {
    if (currentRole !== null) {
      blocks.push({ role: currentRole, body: currentLines.join('\n').trim() })
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
