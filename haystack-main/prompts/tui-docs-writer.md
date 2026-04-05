Write comprehensive TUI documentation for ostk llmOS. You are writing for the operator — the human who runs the TUI and wants to understand every pane, every keybinding, and the mental model.

## Files to write

### 1. docs/tui-guide.md
Complete user guide. Sections:
- What the TUI is (the shell of llmOS — not a chat, a process table)
- Mental model: you are the operator, agents are processes, .ostk/ is shared memory
- Every pane explained (Status bar, POST, Fleet, Activity, Metrics, Locks, Store, Work, Output, tack, quickline, Clock bar)
- Keyboard reference (Tab cycle, j/k scroll, Enter expand, Esc cancel, Ctrl-C quit, ?help)
- Tack grammar (: commands, .? probes, bare text = hay, → needle refs)
- Known limits: tack dispatches to CLI; →642 (LLM in loop) is pending — intent tack shows CLI output, not LLM response
- Boot sequence: ostk boot → ostk tui

### 2. docs/ostk-walkthrough.md
Step-by-step first-session walkthrough for a new user:
1. Install: curl install / cargo install
2. Init: ostk init (in your project repo)
3. Boot: ostk boot → read the output
4. TUI: ostk tui → what you see
5. First needle: type a problem in tack bar → files as hay → :compile → needle appears in Work pane
6. Fleet: understand that agents work needles in the background
7. POST: :post → confirm kernel is healthy
8. Quota: watch the xM/100M counter grow — this is the value accumulating

### 3. docs/tack-reference.md
Complete tack verb reference:
- Hard commands (:verb) — kernel dispatch, no LLM needed
- Soft probes (.? query) — intent query (awaiting LLM in loop →642)
- Bare text — filed as hay automatically
- Needle refs (→NNN) — reference a needle
- Examples for every common workflow

## Style
- Short sentences. Active voice.
- Show don't tell: use examples and TUI output snippets
- Honest about limitations: mention →642 (tack→LLM) as pending
- The OS is invisible. Docs should be too — minimal, useful, no fluff.

## Source files to read first
- src/tui/mod.rs (all pane implementations)
- docs/spec/llmos-concurrency.md (the mental model)
- docs/draft/eject-the-harness.md (why we're building this)
- .ostk/HUMANFILE (the operator contract)
