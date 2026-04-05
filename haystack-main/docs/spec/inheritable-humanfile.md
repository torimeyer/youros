---
title: inheritable-humanfile
author: '@scott'
promoted_at: 2026-03-20T02:30:36Z
needle: →826
created_at: 2026-03-20T02:18:59Z
status: spec
implements: []
---

# Inheritable HUMANFILE

## Problem

HUMANFILE is flat and project-scoped. Identity, model preferences, tack dialect, and GPG key must be re-declared per project. No concept of "who is this operator" at the OS level.

Multi-operator projects have no permission model. Any agent can call any tool.

## Design

### File hierarchy

```
~/.HUMANFILE                          # global identity (YOU)
└── project/.ostk/HUMANFILE       # project config (overrides)
    ├── humanfile.contributors        # who can operate here
    └── humanfile.authorized          # what they can do
```

### ~/.HUMANFILE — global identity

Travels with the operator. Signed by personal GPG key (T0).

```
IDENTITY scott
SIGN BAF08C963C7E3184

MODEL claude-sonnet-4-5
FALLBACK mistral-large-latest

AVAILABLE <<LIST
claude-sonnet-4-5
claude-opus-4-6
claude-haiku-4-5
gemini-2.5-pro
mistral-large-latest
codestral-latest
meta-llama/llama-4-maverick
LIST

TACK <<VERBS
:deploy → scripts/deploy.sh
:bench → ostk bench --docker
:status → git status --short
VERBS
```

### project/.ostk/HUMANFILE — project config

Extends the global. Signed by project key (T1).

```
EXTENDS ~/.HUMANFILE

MODEL claude-opus-4-6

DRIVER rust ostk mcp serve fcp-rust
DRIVER python ostk mcp serve fcp-python

TACK <<VERBS
:test → cargo test --lib
:lint → cargo clippy --all-targets
VERBS
```

### humanfile.contributors — operator registry

```
CONTRIBUTOR scott  BAF08C963C7E3184
CONTRIBUTOR tori   42E499C6D4889BFC
CONTRIBUTOR ci     AABBCCDD11223344
```

### humanfile.authorized — permission matrix

**Security constraint:** No `TOOL *` wildcards. Explicit tool names required.
AUTHORIZE is **project-scoped only** — never inherited via EXTENDS. Can narrow global permissions, never widen.

```
AUTHORIZE scott TOOL shell fs_ops fs_read
AUTHORIZE scott MODEL *

AUTHORIZE tori  TOOL shell fs_read
AUTHORIZE tori  MODEL claude-haiku-4-5

AUTHORIZE ci    TOOL shell fs_read
AUTHORIZE ci    MODEL claude-haiku-4-5
AUTHORIZE ci    TRUST T2
```

## Security invariants

1. **IDENTITY/SIGN are immutable** — child HUMANFILE cannot override. Reject at parse time.
2. **AUTHORIZE never widens** — project can narrow global permissions, never broaden them.
3. **No TOOL wildcards** — `TOOL *` is rejected. Explicit tool enumeration required.
4. **EXTENDS depth = 1** — only `~/.HUMANFILE → project` chain. No transitive inheritance.
5. **Signature freshness** — project HUMANFILE signatures older than 7 days reduce trust to T2.

## Merge semantics

| Directive | Merge rule | Rationale |
|-----------|-----------|-----------|
| MODEL | Override (project wins) | Project chooses its CPU |
| FALLBACK | Override | Same |
| AVAILABLE | Override (project-scoped) | Project defines its model list |
| TACK | Merge (project wins on conflict) | Your verbs + project verbs; project overrides same-name |
| DRIVER | Project-only | Drivers are project-specific |
| AUTHORIZE | Project-scoped only | Never inherited; can only narrow, not widen |
| SIGN | Identity-only | From ~/.HUMANFILE, not overridable |
| IDENTITY | Identity-only | A project cannot change who you are |

## Boot resolution

```
1. Load ~/.HUMANFILE (if exists) → base identity + preferences
   → If missing: warn, suggest `ostk humanfile init --global`
2. Verify global signature      → GPG (T0 key)
3. Load project HUMANFILE       → if EXTENDS, merge over base
   → IDENTITY/SIGN from parent only; reject overrides
4. Verify project signature     → GPG (T1 key)
   → Check signature age; reduce to T2 if >7 days stale
5. Load contributors            → verify operator key is listed
6. Load authorized              → apply permission constraints at dispatch
   → Validate: no TOOL wildcards, no escalation beyond global
7. Register in kernel           → available at dispatch time
```

## Unix analogy

| HUMANFILE | Unix | Purpose |
|-----------|------|---------|
| ~/.HUMANFILE | ~/.profile | User identity + preferences |
| project HUMANFILE | /etc/project.conf | Project configuration |
| humanfile.contributors | /etc/group | Membership |
| humanfile.authorized | /etc/sudoers | Permissions |
| EXTENDS | source ~/.profile | Inheritance |

## Compounds

- **→657** EXTENDS directive (Agentfile inheritance)
- **→567** HUMANFILE-as-tack-import (dialect travels with identity)
- **→824** kernel approval bus (authorization checks)
- **→769** security review (trust tiers → AUTHORIZE)
- **→775** tori-boot hardening (contributor verification)

## Parser

Same parser as Agentfile. Extended directive set:

| New directive | File | Purpose |
|--------------|------|---------|
| IDENTITY | ~/.HUMANFILE | Operator name |
| SIGN | ~/.HUMANFILE | GPG key fingerprint |
| MODEL | HUMANFILE | Default model |
| FALLBACK | HUMANFILE | Fallback model |
| AVAILABLE | HUMANFILE | Model allowlist (heredoc) |
| DRIVER | HUMANFILE | fcp-* driver registration |
| TACK | HUMANFILE | Verb definitions (heredoc) |
| CONTRIBUTOR | contributors | name + key mapping |
| AUTHORIZE | authorized | name + permission grant |

## Acceptance criteria

- [ ] Parser accepts IDENTITY, SIGN, MODEL, FALLBACK, AVAILABLE, DRIVER, TACK directives
- [ ] EXTENDS resolves ~/.HUMANFILE → project HUMANFILE merge chain
- [ ] IDENTITY/SIGN immutable across EXTENDS (reject override at parse time)
- [ ] Graceful degradation: missing ~/.HUMANFILE warns, doesn't fail boot
- [ ] `ostk humanfile init --global` creates ~/.HUMANFILE
- [ ] Boot resolves merged config and populates kernel context
- [ ] `ostk sign` re-signs HUMANFILE after edits
- [ ] CONTRIBUTOR/AUTHORIZE parsed (team features, opt-in)

## MVP scope (from round table)

**PR1 (~150 lines):** EXTENDS + MODEL override + IDENTITY/SIGN parsing. No signatures yet.
**PR2 (~100 lines):** GPG verification + DRIVER spawning.
**PR3 (~120 lines):** CONTRIBUTOR/AUTHORIZE (separate governance track).

Solo-first: default experience is 1 file (`~/.HUMANFILE`). Team features opt-in.

## Resolved questions (from round table 2026-03-19)

1. **TACK merge:** project wins on conflict (same-name verb → project version). Warn in boot output.
2. **~/.HUMANFILE creation:** `ostk humanfile init --global` wizard. Not auto-created.
3. **CI authentication:** CONTRIBUTOR entry with service key + AUTHORIZE with explicit tools. No special mechanism.
4. **AUTHORIZE granularity:** No wildcards for TOOL. Explicit names required. MODEL wildcards OK (lower risk).
5. **Config editing:** `ostk config set MODEL opus --sign` for atomic edit + re-sign.
6. **EXTENDS security:** Can never widen permissions. IDENTITY immutable. Depth capped at 1.
7. **Bail integration:** bail pack includes contributors + authorized. Unpack verifies operator against project keyring.
