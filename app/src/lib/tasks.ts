// Shared task utilities used by both the Tasks page and the Dashboard.

/**
 * Returns true if a task was auto-filed by the SessionStart hook and should
 * be hidden from user-facing views by default.
 *
 * Three detection rules (ordered cheapest first):
 *   1. description starts with "session-task:" (new hook format)
 *   2. description contains the old auto-filed sentinel phrase
 *   3. title matches the old hook format "Claude Code session claude-code-..."
 */
export function isSessionTask(t: { title: string; description?: string }): boolean {
  if (t.description?.startsWith("session-task:")) return true;
  if (t.description?.includes("Auto-filed by SessionStart hook")) return true;
  if (/^Claude Code session claude-code-/i.test(t.title)) return true;
  return false;
}
