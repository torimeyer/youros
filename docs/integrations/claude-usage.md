# Claude Code /usage — integration investigation (→1299)

## What /usage is

`/usage` is a **slash command within the interactive Claude Code session UI**, not a CLI subcommand. Typing `/usage` inside a live session shows subscription quota and billing cycle info. It is **not** accessible via:

- `claude usage` — opens an interactive session prompting for input
- `claude -p "/usage"` — produces no parseable output before timing out
- `claude --usage` — not a valid flag

## What we tried

```
$ claude --help        # no usage flag or usage subcommand
$ claude usage         # starts interactive session, asks "what do you want to do with usage?"
$ claude -p "/usage"   # hangs / no structured output
$ strings ~/.local/share/claude/versions/2.1.140 | grep -i quota   # no stable API URLs found
```

## What quota data IS available

| Source | What | How |
|---|---|---|
| `~/.claude/projects/*/**.jsonl` | Per-call token counts (input, output, cache) | Already read by `costs.py` |
| `ostk profile tokens --json` | Session-level aggregate tokens | `api/routers/usage.py` |
| Anthropic account API | Quota used, quota total, billing cycle end | **Not wired** (see below) |

## What quota data is NOT available (yet)

Anthropic's server-side subscription quota (how many messages left, when the billing cycle resets) lives behind an authenticated API call that requires the user's OAuth session token from `claude.ai`. Claude Code stores this token in the **macOS Keychain**, not in a readable file.

To wire it in the future:
1. Read the keychain entry: `security find-generic-password -s "claude.ai" -w`
2. Use the bearer token to call the Anthropic account API (endpoint TBD — not publicly documented)
3. Surface `quota_used`, `quota_total`, `billing_cycle_end` in `/api/usage`

Until then, `quota_available: false` is returned and `quota_note` explains why.

## Gemini

Gemini CLI (`gemini`) has no `usage`, `quota`, or `billing` subcommand as of v1.x. Usage is sourced entirely from the local audit log filtered to `gemini-*` model entries, same as Claude.

## What /api/usage returns today

```json
{
  "claude": {
    "auth_source": "subscription",
    "messages_today": 8,
    "tokens_today": 9600,
    "total_messages": 30,
    "recent_daily": [{"date": "2026-05-09", "messages": 4, "input_tokens": 20000, "output_tokens": 4000}, ...],
    "session_tokens": {"cache_hit_rate_pct": 67.9, ...},
    "quota_available": false,
    "quota_note": "..."
  },
  "gemini": {
    "auth_source": "gemini_cli",
    "messages_today": 2,
    "tokens_today": 2400,
    "total_messages": 5,
    "recent_daily": [...],
    "quota_available": false,
    "quota_note": "..."
  }
}
```
