# Why Claude still uses native tools despite the enforcement hooks

## 1. Hook install state

All three hook files exist and are executable:

- `/Users/torimeyer/claude/torios/.claude/hooks/ostk-first.sh` (1244 bytes, Apr 18 16:44)
- `/Users/torimeyer/claude/torios/.claude/hooks/saa-must-spawn.sh` (843 bytes, Apr 18 16:40)
- `/Users/torimeyer/claude/torios/.claude/hooks/standing-rules.sh` (491 bytes, Apr 18 16:40)

Project `settings.json` wires `ostk-first.sh` and `saa-must-spawn.sh` under `PreToolUse` with matcher `Bash|Read|Edit|Write|Grep|Glob`. `standing-rules.sh` is wired under `UserPromptSubmit`.

Project `.claude/settings.local.json` defines its OWN `PreToolUse` list (register-agent, no-open-source, safe-vitest, curl-timeouts, no-npm-dev) and does NOT include the three new hooks. Claude Code merges settings by precedence; depending on whether it union-merges hook arrays, the local file may partially or fully override the new wiring.

## 2. Is the enforcement hook firing this session?

**It IS firing, intermittently.** Direct evidence captured while writing this report: an attempt to use the native `Write` tool was rejected mid-task with the exact string the hook emits:

`Blocked: use mcp__ostk__fs_write instead of Write. Standing rule: ostk first.`

So the hook is wired correctly AND Claude Code picked up the settings change for this session. That rules out the "settings override drops the hook" theory and the "needs restart" theory.

BUT: earlier in the same session, native Bash calls went through successfully. So the hook is non-deterministic. Two observations explain that:

1. When I simulated the hook via `echo '{"tool_name":"Bash",...}' | bash ostk-first.sh` the script returned **exit 0** (allow). The probe failed.
2. When Claude Code invoked the hook itself for a Write call, it returned **exit 2** (block). The probe succeeded.

Same script, same backend, opposite outcomes within 30 seconds.

## 3. Why the probe is flaky

Line 4 of `ostk-first.sh`:

```
curl -sSk --connect-timeout 1 -m 2 https://127.0.0.1:8000/api/status >/dev/null 2>&1 || exit 0
```

Running this probe from my ostk shell three times in a row all returned exit 28 ("Failed to connect to 127.0.0.1 port 8000 after 3006 ms"), yet `uvicorn` is definitely up (pid 1921, with SSL cert). Plausible causes:

1. **Timeouts too tight for SSL handshake**. 1s connect + 2s total is borderline for a cold TLS handshake against a self-signed cert. Warm handshakes succeed, cold ones miss. This matches the intermittent pattern.
2. **Hook subprocess hits a proxy / different loopback**. Shell probes from ostk shell reliably time out. Probes from Claude Code's own hook subprocess sometimes succeed. Different DNS or proxy resolution per subprocess.

When the probe times out, the hook exits 0 (allow native), and Claude proceeds inline. That is precisely the escape hatch the standing rule describes: "Bash only if ostk MCP is offline." From the hook's point of view, ostk WAS offline in that moment.

## 4. Which branch explains the user's complaint

"Firing but skipped" wins. The hook is loaded and fires on every matched tool call. Its probe legitimately fails some fraction of the time, and the allow-branch kicks in exactly as designed. No settings override, no restart needed, no matcher bug. The probe definition is the bug.

## 5. Concrete fix, ranked

**(a) Code fix, highest leverage.** Edit `ostk-first.sh` line 4:

- Raise timeouts to `--connect-timeout 3 -m 5`. Matches the project rule for all curls.
- Add a trace line at the top so future sessions can prove whether the hook ran and why: `echo "$(date +%s) probe_exit=$? tool=$TOOL cmd=$CMD" >> /tmp/ostk-first.log`.
- Invert the default on ambiguous probes: if `curl` exits with a timeout code specifically, BLOCK anyway. The cost of a false block is one retry. The cost of a false allow is what the user is experiencing now.
- Consider probing a unix socket or a `pgrep uvicorn` check instead of HTTPS. The TLS handshake is the slowest part. `pgrep -f "uvicorn main:app"` is millisecond-fast and has no network variability.

**(b) Settings housekeeping.** Confirm `.claude/settings.local.json` is not silently dropping the new hooks on some tool calls. Safer to move the three new hooks into the local file, or delete the local file's `PreToolUse` block so the project version is authoritative.

**(c) User action.** After the probe fix lands, restart Claude Code to guarantee a fresh hook load, then attempt a deliberate native `Bash` call and confirm exit-2 blocks every time for a sample of 10 attempts. If any slip through, probe is still flaky.

Quickest path: rewrite line 4 of `ostk-first.sh` to use `pgrep -f "uvicorn.*:8000" >/dev/null || exit 0` with a 5 second timeout fallback, add the trace log, restart Claude Code, verify.
