# Coordination

You are one of N agents working in the same directory. The kernel resolves all file conflicts automatically. You do not coordinate file access. Ever.

## Primitives

NUDGE — request attention from another agent or the scheduler
  ostk nudge <alias> "message"
  Appears as [nudge] in target's next tool response

LOCK — exclusive access to a non-filesystem resource (CI deploy, ceremony, external API)
  ostk lock create <name>
  ostk lock release <name>
  NOT for files. The kernel handles files.

SPAWN — dispatch independent work
  ostk run <agentfile>

PS — check fleet state before dispatching
  ostk ps

## Rules

- Write files freely. The kernel resolves conflicts at write time.
- Read [stale] signals. Re-read before editing if flagged.
- Nudge the scheduler if blocked. Don't wait silently.
- File needles for work you discover but can't do.
