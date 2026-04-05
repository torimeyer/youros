# v0.2.0 Silent Arm Regression Analysis

status: draft
date: 2026-03-08

## Summary

The reported regression of v0.2.0 silent arm from 8/10 to 5/10 is **incorrect**. The actual evaluation data shows:

| Arm | v0.1.0 (v19) | v0.2.0 (v20) | Delta |
|-----|-------------|-------------|-------|
| Silent | 8/10 | **7/10** | -1 |
| Injected | 7/10 | 7/10 | 0 |

One instance regressed: **seaborn (mwaskom__seaborn-3069)**. All other instances produced identical pass/fail outcomes between versions.

## Root Cause: Not the Shim

The v0.1.0 -> v0.2.0 diff contains **zero changes** to the shim code path:

- `src/main.rs`: `try_shell_dash_c()`, `run_dash_c_compressed()`, `shim_interstitial()` -- **identical** between versions
- `src/squasher/mod.rs`, `src/squasher/dedup.rs`, `src/squasher/vte_strip.rs` -- **zero diff**
- `src/kernel/pty.rs` -- **zero diff**
- `Cargo.toml` / `Cargo.lock` -- **zero diff** (no dependency changes)

The only changes in v0.2.0 are:
1. New CLI commands (`hay`, `thread`) -- unreachable from the shim path
2. Bead -> needle rename in `src/lib.rs` and `src/commands/*.rs` -- unreachable from the shim path
3. `.ostk/` metadata files -- not compiled into the binary

The shim binary for v0.2.0 executes the **exact same code path** as v0.1.0 for `bash -c "command"` invocations.

## The Seaborn Instance

### What happened

Both versions face identical shim friction (interstitial on first call, cat/sed/head shim errors). The agent recovers from these in both versions. The difference is in the **fix strategy** the LLM chose:

- **v0.1.0 (passed)**: Used `get_major_locator()()` to get tick positions, then set limits to (min - 0.5, max + 0.5). Correct.
- **v0.2.0 (failed)**: Used `get_data_interval()` to get data range, then added +/- 0.5. This returns the matplotlib auto-scaled range, not the categorical tick positions, producing limits of (-0.055, 2.945) instead of (-0.5, 2.5).

The test expects `ax.get_xlim() == (-0.5, 2.5)`. The v0.2.0 patch fails this assertion.

### Why the agent chose differently

This is LLM non-determinism, not shim influence. The v0.2.0 agent happened to find and follow a code pattern using `get_data_interval()` (visible at line 1658 of its observation of the codebase). The v0.1.0 agent took a different path through the code and used `get_major_locator()` instead.

Shim error counts are comparable between versions:
- v0.1.0 seaborn: 22 shim errors
- v0.2.0 seaborn: 26 shim errors

The 4 extra errors in v0.2.0 did not cause the wrong fix strategy -- they just added 4 more wasted retries, each trivially recovered.

## Shim Error Budget (All Instances)

| Instance | v0.1.0 errors | v0.2.0 errors |
|----------|--------------|--------------|
| seaborn | 22 | 26 |
| pytest | 18 | 14 |
| matplotlib | 18 | 14 |
| xarray | 18 | 16 |
| scikit-learn | 14 | 18 |
| astropy | 14 | 12 |
| pylint | 14 | 14 |
| flask | 12 | 12 |
| django | 12 | 12 |
| requests | 10 | 10 |
| **Total** | **152** | **148** |

Error counts fluctuate naturally. The shim code is identical; variation comes from LLM choosing different commands (sometimes more sed, sometimes more grep).

## Fragility Analysis

Seaborn was already fragile:
- v19 silent: PASS (lucky -- chose the right approach)
- v19 injected: **FAIL** (same shim, different LLM path)
- v20 silent: **FAIL** (different LLM path)
- v20 injected: **FAIL** (different LLM path)

The seaborn instance passes 1 out of 4 arms, confirming it's a coin flip, not a shim regression.

## Known Shim Issues (Both Versions)

These issues exist in BOTH v0.1.0 and v0.2.0 equally:

1. **Interstitial eats first command**: Returns exit code 2, no output. Agent recovers by retrying.
2. **cat shim rejects file paths**: `cat file.py` -> `error: unrecognized subcommand 'file.py'`. Agent recovers by using other tools.
3. **sed shim rejects -n flag**: `sed -n '...' file` -> `error: unexpected argument '-n'`. Agent recovers.
4. **head shim rejects -n flag**: `head -n N file` -> `error: unexpected argument '-n'`. Agent recovers.

These are not regressions -- they're pre-existing bugs in the shim passthrough for cat/sed/head symlinks that affect both versions identically.

## Conclusion

No shim regression exists in v0.2.0. The 8/10 -> 7/10 drop is LLM non-determinism on a single fragile instance (seaborn). The binary diff contains zero changes to any code in the shim execution path.

The real risk is not version regression but the ~15 shim errors per instance that waste agent steps. Fixing cat/sed/head argument passthrough would reduce noise for all instances.
