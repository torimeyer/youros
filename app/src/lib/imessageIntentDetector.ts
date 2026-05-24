/**
 * Detects whether a chat prompt is requesting an iMessage send to a contact.
 * Returns the phrase (name + message) after the verb for backend resolution,
 * or null when no intent is detected.
 */

// Pronouns / articles that are never contact names.
const NOT_A_NAME = /^(?:me|us|him|her|it|them|you|everyone|anyone|nobody|the|a|an|my|your|our|their|this|that|these|those)$/i

// Verbs indicating desire to send a message to a person.
const SEND_VERBS = 'tell|text|message|msg|dm|ping|buzz|notify'

// "tell X Y", "text X Y", etc. — captures everything after the verb.
const SEND_VERB_RE = new RegExp(
  `^\\s*(?:${SEND_VERBS})\\s+([\\w][\\w'\\-\\s]*)$`,
  'i'
)

// "let X know [that] Y"
const LET_KNOW_RE = /^\s*let\s+([\w][\w'\-\s]{1,50}?)\s+know\s+(?:that\s+)?(.{2,})\s*$/i

export interface IMessageIntent {
  /** Everything after the verb: e.g. "lil oatmeal goodnight" or "mom I'll be late" */
  phrase: string
}

/** Returns intent if the input looks like a send-to-contact request, else null. */
export function detectIMessageSendIntent(text: string): IMessageIntent | null {
  if (typeof text !== 'string') return null
  const trimmed = text.trim()
  if (!trimmed) return null

  // "let X know [that] Y"
  const letMatch = trimmed.match(LET_KNOW_RE)
  if (letMatch) {
    const name = letMatch[1].trim()
    const msg = letMatch[2].trim()
    const firstWord = name.split(/\s+/)[0]
    if (msg && !NOT_A_NAME.test(firstWord)) {
      return { phrase: `${name} ${msg}` }
    }
  }

  // "tell/text/message/etc. X Y"
  const sendMatch = trimmed.match(SEND_VERB_RE)
  if (sendMatch) {
    const afterVerb = sendMatch[1].trim()
    const words = afterVerb.split(/\s+/)
    // Need at least 2 words: one name word + one message word.
    if (words.length >= 2) {
      const firstWord = words[0].toLowerCase()
      if (!NOT_A_NAME.test(firstWord)) {
        return { phrase: afterVerb }
      }
    }
  }

  return null
}
