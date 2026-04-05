Implement →650: three-state ostk boot output.

Read src/commands/boot.rs and src/commands/post.rs first.

## State 1: no .ostk/ (fresh install)

If the current directory has no .ostk/, print:

```
welcome.

no .ostk/ found — this is your first time here.

the OS is invisible. it doesn't change your tools.
it just starts tracking what you build.

start fresh:         ostk init
import from a team:  ostk init --import <path-or-url>

your first 100M tokens saved: free.
→ ostk.ai
```

## State 2: .ostk/ present, POST passes

Print compact status:
```
ostk 1.0 · boot:0.87 ◉

needles: 264 open  agents: 0  hay: 1 pending
last session: 2h ago  audit: 1,344 events

ready.
```

## State 3: .ostk/ present, POST has failures

Print each check with ✓ or ✗. For failures, add repair command:
```
ostk 1.0 · boot:0.44 ◎

✓ .ostk/ present
✓ needles: 264 intact
✗ audit.jsonl: parse error on line 1,144
  → ostk repair audit
✓ .primefile: valid

1 issue found. run repairs above.
```

Repair command mapping:
- audit.jsonl error → ostk repair audit
- needles corrupt → ostk verify
- .primefile missing → ostk init --repair
- identity_counter missing → ostk init --repair
- conflict markers found → ostk verify

## Tests

Test each state produces correct output. Use tempdir for isolation.
cargo test boot after implementation.
Close →650 when all three states pass.
