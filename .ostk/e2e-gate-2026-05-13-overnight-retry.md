# Overnight e2e gate retry — 2026-05-13

## What landed since v3.15.0
- e00e555 feat(→1241): action doc + per-template buttons on Recent Agents
- a71fb97 / 7732744 / 282775f test(→1264): skip markers tracking →1269 upstream
- 9f82fd1 fix(→1266 →1271): template-spawned agents stay visible in Active tab

## Gate results (via →1273)
| phase | result | source |
|-------|--------|--------|
| backend pytest | 4657 passed, 5 skipped (2m32s) | 1273-e2e-gate-retry transcript |
| frontend vitest | 2284 passed, 1 skipped (138 files) | 1273-e2e-gate-retry transcript |
| TypeScript (`tsc -b`) | clean, no errors | 1273-e2e-gate-retry transcript |
| e2e_smoke (137 phases) | wedge — subprocess silent 301s, killed | both attempts hit same wedge |

## Open follow-ups
- →1272: `test_list_agents_warm_cache_is_fast` cold scan exceeds 30s. Skip-marked.
- →1273: this retry needle, closing.
- →1274: e2e gate agents wedge on long pytest/smoke runs. Use spawn+interact+tee for next retry.
- →1275: e2e_smoke retry via spawn+interact pattern (pending).
