/**
 * Chat-side matcher and handler for "create tasks from this roadmap".
 *
 * The torichat panel calls {@link isRoadmapToTasksRequest} before it
 * hands a message off to the normal WebSocket AI routing. When the
 * matcher returns true the panel calls {@link runRoadmapToTasks}
 * instead, which hits the backend endpoint and posts the reply as an
 * assistant bubble. Keeping this pure and in its own module lets us
 * unit test the matcher without booting the whole ChatPanel.
 */

const PATTERNS: RegExp[] = [
  /\bcreate\s+tasks?\s+from\s+(?:this|the|that|my)?\s*roadmap\b/i,
  /\bmake\s+tasks?\s+from\s+(?:this|the|that|my)?\s*roadmap\b/i,
  /\bturn\s+(?:this|the|that|my)?\s*roadmap\s+into\s+tasks?\b/i,
  /\bconvert\s+(?:this|the|that|my)?\s*roadmap\s+(?:in)?to\s+tasks?\b/i,
  /\bbreak\s+(?:this|the|that|my)?\s*roadmap\s+(?:down\s+)?into\s+tasks?\b/i,
  /\bbuild\s+tasks?\s+from\s+(?:this|the|that|my)?\s*roadmap\b/i,
  /\bgenerate\s+tasks?\s+from\s+(?:this|the|that|my)?\s*roadmap\b/i,
]

/** True when the user is asking to convert a roadmap into tasks. */
export function isRoadmapToTasksRequest(text: string): boolean {
  if (typeof text !== 'string') return false
  const trimmed = text.trim()
  if (!trimmed) return false
  return PATTERNS.some((p) => p.test(trimmed))
}

export interface RoadmapToTasksResponse {
  status: 'ok' | 'no_roadmap' | 'empty' | string
  reply: string
  created: Array<{ id?: string | null; title: string }>
  roadmap_path: string
}
