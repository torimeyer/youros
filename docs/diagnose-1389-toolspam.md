# Diagnose: tool call inflation on saa 1389

## Counts (from transcript a7d09a4f, 274KB)

| Category | Count |
|---|---|
| **Total tool calls** | **36** (24 bash, 6 read, 6 fs_ops) |
| Redundant | 4 |
| Dead-end | 5 |
| Batchable (could save) | 4 |
| cd-prefix violations | 3 |

PLUS 2 wasted calls in the **parent session** (c4018ea0) before spawning.

---

## Top offenders (with transcript evidence)

### 1. Python discovery loop (5 calls, calls #19-23)
```
#19 cd api && python -m pytest ...            # FAIL: relative cd in worktree
#20 cd /abs && python3 -m pytest ...          # FAIL: system python3, no venv
#21 which python3.11 || which python3.12 ...  # dead-end
#22 ls api/.venv/bin/python* ...              # venv discovery
#23 cd /abs && /main-repo/api/.venv/bin/python -m pytest ...  # finally works
```
Cause: brief didn't include the test command. Agent had to discover venv from scratch.

### 2. Four frontend exploration greps instead of one read (calls #7, #11, #12 + read #13, #14)
```
#7  grep "email|gmail|source" Inbox.tsx | head -60
#11 grep "email|gmail|source" Inbox.test.tsx | head -40
#12 grep "sourceLabel|'email'|\"email\"" Inbox.tsx      ← re-grepped same file
#13 read Inbox.tsx
#14 read Inbox.tsx   ← read it AGAIN
```
Cause: brief already said "Inbox.tsx:35 maps source === 'email'". Agent should have done one `mcp__ostk__read(offset=30, limit=20)` and moved on. No guidance says "read file:line directly, skip discovery greps."

### 3. Dead-end file probe (call #8)
```
#8 cat test_tasks_source.py || echo FILE_NOT_FOUND   → FILE_NOT_FOUND
```
Brief listed `test_gmail_promote.py` and `test_inbox.py` as the relevant tests. `test_tasks_source.py` doesn't exist. Agent guessed a third file based on naming convention.

### 4. Six individual fs_ops writes instead of 2 batches (calls #16-18, #24-26)
```
#16 fs_ops write test_gmail_promote.py
#17 fs_ops write test_inbox.py
#18 fs_ops write test_tasks_source.py      ← this is the non-existent file!
...
#24 fs_ops write task_source_store.py
#25 fs_ops write gmail_triage.py
#26 fs_ops write Inbox.tsx
```
Could be 2 batch calls instead of 6.

### 5. Parent session: `ostk work show` doesn't exist (2 wasted parent calls)
```
ostk work show 1389   → ERROR: no such command
ostk work --help      → finds 'activate'
ostk work activate 1389  → correct
```
Parent burned 2 discovery calls just to fetch the needle. Correct verb: `ostk work activate N`.

---

## Root cause (3 sentences, evidence-backed)

**No pytest command in spawn brief** caused 5 discovery calls (#19-23). The correct invocation (`api/.venv/bin/python -m pytest`) is non-obvious in a worktree because system python3 doesn't have the venv, and `cd api` is a relative path that fails. **Brief gave file:line refs but no "read directly" discipline**, so the agent still ran 4 grep/read passes on Inbox.tsx despite the brief saying exactly where to look (line 35). **`ostk work show` doesn't exist**, but the parent ran it first instead of `ostk work activate`, adding 2 unnecessary discovery calls before the spawn even happened.

---

## Fix proposal

### Memory-level (written to memory/feedback_saa_brief_include_test_command.md)

Full entry written. Summary for MEMORY.md index (≤200 chars):
> "saa briefs must include exact test command and python path — without pytest invocation, agents burn 4+ calls on python discovery; use `api/.venv/bin/python -m pytest <file> -x -q`; also: use `ostk work activate N` not `ostk work show N`"

### Code-level diff suggestion (feedback_subagent_prompt_template.md)

Add to the end of the "Shell rules (agent must follow)" section:

```diff
+- **Backend tests in worktrees**: always use the MAIN repo venv: `/Users/torimeyer/claude/torios/api/.venv/bin/python -m pytest api/tests/<file>.py -x -q`. The worktree does not have its own .venv. Never use system `python3` or relative `cd api` — both fail in worktrees.
+- **File:line refs in brief = read directly**: if a brief cites `file.py:NN`, use `mcp__ostk__read(path=..., offset=NN-5, limit=30)`. Do NOT run a grep exploration phase first.
+- **Batch fs_ops writes**: when writing 3+ files in the same logical step, use `fs_ops(ops=[...])` batch mode. Never make N individual fs_ops calls when one batch call will do.
```

The spawn brief template (when writing `ostk work activate`-based briefs) should append:
```
## Running tests
- Backend: `cd <WT_ROOT>/api && /Users/torimeyer/claude/torios/api/.venv/bin/python -m pytest tests/<test_file>.py -x -q`
- Frontend: `cd <WT_ROOT> && bash scripts/run-vitest.sh <ComponentName>`
- TypeScript: `cd <WT_ROOT>/app && npx tsc -b --noEmit`
```

---

## Open questions

1. Should `ostk work activate` output be included verbatim in the spawn brief? It already has file:line references and AC. The parent currently paraphrases it, adding a human-error step. Verbatim paste would be 0-error.
2. Should the spawn template have a `## Running tests` section as a fill-in-the-blank so parent can't forget?
