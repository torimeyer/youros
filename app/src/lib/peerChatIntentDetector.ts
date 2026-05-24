/**
 * Detects whether a chat prompt is requesting an LLM-to-LLM (peer-chat)
 * conversation. The ChatPanel calls {@link isPeerChatIntent} on submit; when
 * it returns true the panel shows the turns picker before dispatching.
 */

const LLM_NAMES = '(?:claude|gemini|gpt|openai|anthropic|sonnet|opus|haiku|mistral|llama|grok)'

const PATTERNS: RegExp[] = [
  // "chat with gemini about X", "talk to claude about Y"
  new RegExp(`\\b(?:chat|talk)\\s+(?:with|to)\\s+${LLM_NAMES}\\b`, 'i'),
  // "have claude and gemini discuss X", "have gemini chat with claude"
  new RegExp(`\\bhave\\s+${LLM_NAMES}\\s+(?:and\\s+\\w+\\s+)?(?:chat|talk|discuss|debate|speak)\\b`, 'i'),
  // "have [other] and claude discuss X"
  new RegExp(`\\bhave\\s+\\w+\\s+and\\s+${LLM_NAMES}\\s+(?:chat|talk|discuss|debate|speak)\\b`, 'i'),
  // "let gemini talk to claude", "let claude speak with gemini"
  new RegExp(`\\blet\\s+${LLM_NAMES}\\s+(?:chat|talk|discuss|debate|speak)\\b`, 'i'),
  // "make claude and gemini debate X"
  new RegExp(`\\bmake\\s+${LLM_NAMES}\\s+(?:and\\s+\\w+\\s+)?(?:chat|talk|discuss|debate|speak)\\b`, 'i'),
]

/** True when the user is requesting an LLM-to-LLM peer-chat session. */
export function isPeerChatIntent(text: string): boolean {
  if (typeof text !== 'string') return false
  const trimmed = text.trim()
  if (!trimmed) return false
  return PATTERNS.some((p) => p.test(trimmed))
}
