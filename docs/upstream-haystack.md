# Upstream ostk Source — v6.0.0/v6.0.5 Location

## Summary

The installed `ostk` binary (`~/.local/bin/ostk`) is **v6.0.5**, built from the private repo
`https://github.com/os-tack/haystack`. The local `haystack-main/` directories are frozen at
**v2.3.0** and have no relation to the running binary. This mismatch blocks durable source fixes
for →1214, →1215, and →1217.

---

## Versions at each path

| Path | Cargo.toml version | Binary version |
|------|--------------------|----------------|
| `~/.local/bin/ostk` (live) | n/a | **6.0.5** |
| `~/claude/torios/haystack-main/` | 2.3.0 | 2.3.0 |
| `~/Downloads/haystack-main/` | 2.3.0 | n/a |
| `~/.youros/sync_repo/haystack-main/` | no Cargo.toml | partial src only |

---

## Where v6.0.0 source actually lives

**GitHub**: `https://github.com/os-tack/haystack` (private repo, org: os-tack)

Releases are mirrored to a second public repo: `https://github.com/os-tack/ostk.ai`

Both repos publish the compiled binary archives (e.g. `ostk-6.0.0-aarch64-apple-darwin.tar.gz`).
Neither repo exposes raw `.rs` source files publicly.

The install path is:
```
curl -fsSL https://ostk.ai/install | sh
```
or via the yourOS `install.sh` which downloads from `os-tack/ostk.ai` releases.

**tori's GitHub account** does not currently have collaborator access to `os-tack/haystack`
(confirmed via `gh api repos/os-tack/haystack/collaborators/torimeyer` → 404).

---

## Partial source in sync_repo

`~/.youros/sync_repo/haystack-main/src/` contains a partial sync of upstream source:
- `src/commands/`
- `src/kernel/` (policy.rs, helpers.rs)
- `src/serve/`

This has no Cargo.toml and no version marker. It is a partial read-only mirror, not a build-able
workspace. Version unknown — likely current upstream but unconfirmed.

---

## Recommendation

To make durable upstream fixes (→1214 gate self-filter, →1215 mcp__ostk__bash env+parser,
→1217 transport buffer):

**Option A — Request access (fastest path to real fixes)**
Ask Scott (os-tack) to add tori as a collaborator on `os-tack/haystack`.
Then: `git clone https://github.com/os-tack/haystack && git checkout -b fix-1214-gate-self-filter`.
Fixes land in the next ostk release; `ostk update` propagates them automatically.

**Option B — File issues on os-tack/haystack**
Use the partial source in `~/.youros/sync_repo/haystack-main/src/` to identify the exact file/line
for each bug, then open a GH issue on `os-tack/haystack` with a patch attached.
Turnaround depends on upstream release cadence (recently: near-daily releases).

**Option C — Vendor current binary + wrapper shims (workaround only)**
Keep the installed binary as-is and apply workarounds inside the yourOS Python layer.
Does not fix the root cause; tech debt that blocks clean operation.

**Recommended**: Option A. The repo is private-but-accessible to org members. One access grant
unblocks all three dependent Tasks.

---

## How to stay current

```sh
# Update to latest ostk release
ostk update

# Or re-run the install script
curl -fsSL https://ostk.ai/install | sh
```

Latest confirmed release: v6.0.5 (2026-05-12)
