# yourOS Rules: Plain-Language Guide

These are the automatic checks that yourOS runs to protect your work. Think of them as a set of house rules the system enforces on your behalf — and on torios's behalf — so mistakes don't pile up silently.

Each entry has three lines:
- **What it does** — the plain English version
- **Why it exists** — the real reason it was added
- **What happens when it fires** — what you or torios will see

Rules marked **ON** are active right now. Rules marked **OFF** exist and can be turned on.

---

## Rules that are ON right now

### Task title quality check
**Status:** ON

**What it does:** Blocks creating a task if the title is too short, too vague, or starts with a throwaway phrase.

**Why it exists:** Vague titles like "fix stuff" or "misc" make the backlog unreadable. When a task has a real title, you can find it later, understand it at a glance, and assign it without guessing what it means.

**What happens when it fires:** torios gets a message saying the title is too short or looks like a placeholder, with a prompt to write something specific. The task is not created until the title is fixed.

> P0 and P1 tasks also require acceptance criteria — a description of what "done" looks like — before they can be created.

---

### Worktree safety guard
**Status:** ON

**What it does:** When an AI worker is running in its own isolated workspace, this check makes sure it saves changes to its own branch, not back to the main codebase.

**Why it exists:** A bug in 2026 caused a worker to accidentally save its changes directly to the main codebase instead of its isolated branch. That kind of mistake is hard to catch and hard to undo. This rule makes it structurally impossible.

**What happens when it fires:** The save operation is blocked. torios sees a message explaining that the command was missing a workspace address, so it can retry with the correct one.

---

### Don't commit to main from a worker branch
**Status:** ON

**What it does:** Blocks a worker from saving its changes directly to the main codebase when it should be saving to its own branch.

**Why it exists:** A specific incident (task →1346) where a worker committed directly to main instead of its isolated branch. The correct branch was right there; the worker just didn't use it. This rule closes that gap.

**What happens when it fires:** The save is blocked. The worker is told it is on the main branch and needs to switch to its own branch first.

---

### Don't close a task without proof it's done
**Status:** ON

**What it does:** Blocks closing a task unless at least one saved change on the main codebase actually references that task's ID.

**Why it exists:** Two tasks (→1365 and →1374) were marked complete overnight with no actual work behind them. The system accepted the close with zero verification. This rule requires real evidence — a saved change with the task number in the description — before a close goes through.

**What happens when it fires:** The close is blocked. torios sees a message saying no matching change was found and is given instructions for how to bypass the check in legitimate edge cases (work that landed without the ID in the title, or administrative closes).

---

### Don't delete an AI worker that's still running
**Status:** ON

**What it does:** Before the system removes a stale or quiet AI worker, it checks whether the worker's process is actually still alive.

**Why it exists:** A missed status ping alone is not proof a worker is dead. This rule adds a direct process check — if the worker's process is still running, it is not deleted no matter how quiet it has been.

**What happens when it fires:** The worker is not deleted. The reaper moves on. You won't notice it directly unless you're watching the agents list.

---

## Rules that exist but are OFF

These are turned off by default. Each one can be enabled if you want the behavior.

---

### Force agent spawning for certain commands
**Status:** OFF

**What it does:** When a message starts with "saa", "diagnose", or "fix", blocks torios from doing the work inline and requires it to start a dedicated AI worker instead.

**Why it exists:** Inline work bypasses the audit trail, parallel execution, and the Agents page visibility that makes tracking possible. This rule would enforce the "everything through agents" pattern as a hard rule rather than a soft expectation.

**What happens when it fires:** The inline action is blocked. torios must start a worker before proceeding.

---

### Protect against common command mistakes
**Status:** OFF

**What it does:** Catches several easy mistakes in terminal commands before they cause problems: running a web request without a timeout, starting a development server the wrong way, running a specific test runner directly instead of through the project's wrapper scripts, and accidentally assigning to protected system variables.

**Why it exists:** Each of these mistakes has caused a real incident — a hanging command with no timeout, a development server started outside of the correct lifecycle manager, orphaned background processes. The rule prevents the mistake rather than diagnosing it afterward.

**What happens when it fires:** The command is blocked with a specific message explaining what to use instead. For example: "use scripts/dev-backend.sh instead of npm run dev."

---

### Warn when too many unsaved changes pile up
**Status:** OFF

**What it does:** Checks how many files have changes that haven't been saved to version control yet. If there are five or more, it surfaces a reminder to save before stacking more work.

**Why it exists:** More than five unsaved files means more to untangle if something goes wrong. Frequent saves keep the history clean and recovery easy. The threshold of five came from a review session in April 2026.

**What happens when it fires:** A non-blocking reminder appears at the top of the turn. Work is not stopped. It's just a nudge to commit before continuing.

---

### Auto-close tasks after certain saves
**Status:** OFF

**What it does:** After each save to version control, scans the description for a task reference (→NNN). If the save type is a fix, new feature, performance improvement, or refactor, the referenced task is automatically marked complete.

**Why it exists:** Closing tasks manually after a commit is easy to forget. Automation removes the step entirely for the most common case.

**What happens when it fires:** The matching task is closed automatically. Documentation saves, test saves, and maintenance saves do not trigger this — only fix, feat, perf, and refactor.

---

### Nudge toward using yourOS's own tools
**Status:** OFF

**What it does:** When the system detects that torios is using generic AI tools instead of yourOS's own tool set, it reminds torios to use the yourOS versions instead. In advisory mode, it's a hint. In enforce mode, it's a block.

**Why it exists:** yourOS's tools track file history, detect conflicting edits, and write an audit log. Generic tools bypass all of that. Using the wrong tool means lost history and potential conflicts that are hard to detect.

**What happens when it fires:** In advisory mode (the default), a hint appears and the generic tool still runs. In enforce mode, the generic tool is blocked outright.

---

### Route AI worker tasks through the real isolation system
**Status:** OFF

**What it does:** When torios starts a new AI worker that will make changes, this rule intercepts the request and routes it through yourOS's backend instead of using the AI's native worker system. Read-only workers pass through unchanged.

**Why it exists:** yourOS's backend creates a proper isolated workspace for each worker. The AI's native system doesn't. Without this, workers making changes could step on each other or on the main codebase.

**What happens when it fires:** The worker request is silently redirected. From torios's perspective, the worker starts normally. The difference is that it gets a real isolated workspace.

---

### Re-read your rules on every turn
**Status:** OFF

**What it does:** At the start of every message, reads a file of behavioral rules and puts them at the top of torios's context so they stay active throughout a long session.

**Why it exists:** AI context windows have limits. In a long session, instructions from the beginning of a conversation can fade or be overwritten. This rule keeps the most important behavioral rules visible and enforced throughout.

**What happens when it fires:** Rules from the HUMANFILE appear at the top of every turn's context. You won't see them directly — they shape torios's behavior invisibly.

---

### Inject safety reminders into every turn
**Status:** OFF

**What it does:** Adds one or more safety enforcement blocks to every turn: a "receipts" check (no claiming something is done without real evidence), a "dead agent" check (no declaring a worker stalled without checking 3+ signals), and a "silent failure" check (no guessing at failure causes without checking whether the system is properly logged in first).

**Why it exists:** These specific reminders address real recurring failures where torios claimed work was done without verifying, or declared an agent dead based on too little evidence. Injecting the reminders into every turn means they can't be forgotten as context grows.

**What happens when it fires:** The reminders appear in the system context on every turn. You won't see them. They shape torios's behavior.

---

### Keep task statuses accurate
**Status:** OFF

**What it does:** Enforces that "in progress" tasks are being actively worked on right now, "pending" means waiting on you, and "complete" means the work is actually verified — not just finished.

**Why it exists:** Task states that don't match reality make the Agents page and task list misleading. A task showing as "in progress" while nothing is happening creates false confidence.

**What happens when it fires:** torios is nudged to update the task state when the current state doesn't match the actual situation.

---

### Block the watcher from being used as a file reader
**Status:** OFF

**What it does:** Blocks the Monitor tool (which is for watching live event streams) from being used to read a file once and return.

**Why it exists:** Monitor is designed for streaming: watching a log file grow, polling for changes, tailing an active process. Using it to read a static file is the wrong tool — it either hangs waiting for more output or returns partial results. This rule redirects to the right tool.

**What happens when it fires:** The Monitor call is blocked with a message saying to use a file read instead.

---

### Alert when a worker commits structure without substance
**Status:** OFF

**What it does:** When a background watcher detects that a worker saved changes that are purely structural setup (folders, empty files, boilerplate) with no real functionality, it queues a warning. This rule surfaces that warning in the next turn.

**Why it exists:** "Scaffold commits" look like progress but don't represent real delivered work. Surfacing them lets you know a worker may have stopped short, or may be about to ask for direction when it should be continuing.

**What happens when it fires:** A warning appears at the top of the next turn listing which worker made a scaffold commit and when. Warnings older than 30 minutes are silently dropped.

---

### Show open tasks when you ask to keep going
**Status:** OFF

**What it does:** When you say "keep going", "continue", "next", or similar and no workers are currently running, surfaces the list of open tasks so torios knows what to pick up.

**Why it exists:** Without this, "keep going" during an idle moment might produce nothing, or torios might pick up a task based on incomplete context. This rule makes the next action explicit.

**What happens when it fires:** The open task list appears in torios's context, with a directive to pick up the next item.

---

### Measure before fixing performance problems
**Status:** OFF (non-blocking advisory only)

**What it does:** When a message contains words like "slow", "hang", "timeout", or "blocking", reminds torios to measure the current performance before making changes.

**Why it exists:** Editing code to fix a slowdown you haven't measured often moves the wrong thing. A measurement first establishes a baseline and points to the actual bottleneck.

**What happens when it fires:** A hint appears suggesting a timing measurement step. It never blocks anything — it's purely advisory.

---

### Tell you which type of block stopped something
**Status:** OFF

**What it does:** When a tool is blocked, figures out whether the block came from one of yourOS's own rules or from a setting in Claude's configuration, and tells you clearly which one it was.

**Why it exists:** Claude Code shows the same generic "blocked" message whether yourOS's rules stopped something or a settings-level permission rule stopped it. Without this, diagnosing why something was blocked requires guessing. This rule removes the ambiguity.

**What happens when it fires:** If the block came from a settings rule (not a yourOS rule), a clear message says so and tells you where to look.

---

### Keep AI workers paired with watchers in focused mode
**Status:** OFF

**What it does:** When focused work mode is active (a specific file exists in your home folder), every time torios starts an AI worker it must also start a Monitor watcher in the same turn.

**Why it exists:** In focused mode, silence reads as "something died." Without a watcher, a long-running worker goes quiet with no progress signals. Pairing a watcher with every worker means there's always a visible status update coming through.

**What happens when it fires:** If torios tries to start a worker without a watcher in the same turn, the worker start is blocked. A message says to add a Monitor call before proceeding.

---

*Last updated: May 2026.*
