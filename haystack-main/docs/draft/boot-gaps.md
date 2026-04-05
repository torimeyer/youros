---
title: Missing Pieces — Round Table Output (Gemini + Opus)
status: spec
version: 1
created: 2026-03-10
source: gemini-2.5-pro round table on tack-boot gaps
---

# Gaps — Implementation Precision

## Gap 1: @import os / registry-import.jsonl

ALREADY IN KERNEL: `src/kernel/registry.rs` reads `.haystack/registry-import.jsonl`.

Format: JSON entries with `path`, `name`, `last_boot` of external resources.
Driver loads: reads `driver.jsonl` at specified path, maps file-type associations.
Verb extension: drivers register intents (Command/Query/Escalate) into .language.
Flat imports: no circular deps at kernel level.
Recursive deps: drivers advertise via their own `registry-import.jsonl`.

```jsonl
{"path":"/usr/local/lib/fcp-k8s","name":"fcp-k8s","last_boot":"2026-03-10"}
{"path":"/usr/local/lib/fcp-helm","name":"fcp-helm","last_boot":"2026-03-10"}
```

Needles: complete `registry-import.jsonl` loading in boot sequence → →594

## Gap 2: HUMANFILE key-fingerprint lookup

Lookup table: `~/.haystack/humans.jsonl`
Format: `{"fingerprint":"955AF54E","humanfile":"~/projects/haystack/.haystack/HUMANFILE"}`
First-time user: bootstrap sequence → select GPG key → seed HUMANFILE from CLAUDE.md
Multi-user: multiple fingerprints → isolated HUMANFILE stores (strict privacy)
Many-to-one: different fingerprints → distinct HUMANFILE paths, same kernel

Login flow:
```
login.gpg: 955AF54E
  → lookup ~/.haystack/humans.jsonl
  → found? load HUMANFILE
  → not found? bootstrap (create entry, seed from CLAUDE.md)
```

Needle: implement humans.jsonl lookup in haystack boot (new)

## Gap 3: tack.t test framework

Runner: `haystack bench --tack`
Format (line-delimited):
```
.tack  :compile
.cmd   haystack compile
.test  exit:0

.tack  :pitchfork HUMANFILE tack
.cmd   haystack show --semantic HUMANFILE tack
.test  output contains "HUMANFILE"

.tack  :correct X
.cmd   haystack nudge --correction X
.test  .language momentum-- for last verb
```

Pass = textual command match + non-zero momentum update to .language
Fail = mismatch OR zero momentum (resolution didn't advance agent context)
Boot-critical subset: tagged with `.boot-critical true`, runs during POST

Runner integration:
```
haystack bench --tack          # run all .t files
haystack bench --tack --post   # run only boot-critical subset (for POST)
haystack post                  # runs POST sequence including tack.t --post
```

Needle: →600 (tack.t framework)

## Gap 4: POST — Power-On Self Test (precise)

Seven sequential checks. Any fail = boot halted, no login.gpg: shown.

```
POST check 1: .primefile signature
  gpg --verify .haystack/.primefile.asc .haystack/.primefile
  Expected: GOODSIG 99B076C9 AND GOODSIG 955AF54E (both keys)
  Fail: "POST ERROR [1]: .primefile signature invalid"

POST check 2: .language schema
  JSON schema validation on .haystack/.language
  Schema: verb|tier|layer|last_gen|half_life|momentum|resolution
  Fail: "POST ERROR [2]: .language schema violation at line N"

POST check 3: gen_table consistency
  For each path in gen_table: shadow file count matches recorded generation
  Fail: "POST ERROR [3]: gen_table inconsistency at path X"

POST check 4: audit.jsonl hash chain
  Each entry has prev_hash, current hash verifiable
  chain[n].prev_hash == sha256(chain[n-1])
  Fail: "POST ERROR [4]: audit.jsonl chain broken at event N"

POST check 5: tack.t boot-critical tests
  haystack bench --tack --post
  All .boot-critical tests pass
  Fail: "POST ERROR [5]: tack.t failure: <test_name>"

POST check 6: fcp-* driver load + confidence
  Load all entries from registry-import.jsonl
  Each driver: fcp_probe() returns confidence >= 0.3 (minimum)
  Fail: "POST ERROR [6]: driver <name> confidence below threshold"

POST check 7: boot.md staleness
  boot.md mtime <= last audit.jsonl entry timestamp?
  If boot.md is OLDER than last audit event: stale
  Fail: "POST ERROR [7]: boot.md stale — run haystack refine"

ALL PASS:
  login.gpg: _
```

Needle: →601 (POST sequence)

## Meta-tack layers (emerging)

Human intent flows to machine code through layers. Not all layers exist yet.

```
Layer 0: Human intent (raw natural language or dense tack)
Layer 1: Tack (compressed intent — current)
Layer 2: Meta-tack (composed operations — emerging)
  :pipeline A → B → C        (A output is B input context)
  :orchestrate N :task X      (spawn N agents)
  :compose fcp-rust | fcp-k8s (pipe through drivers)
  :if POST passes → :boot     (conditional)
  :until confidence > 0.9 → :bench boot-post (loop)
Layer 3: Haystack CLI (resolved commands — current)
Layer 4: Kernel primitives (current)
Layer 5: Machine code (the binary)
```

Meta-tack is decomposable: every expression reduces to simple tack verbs.
Emergence from agreed simple things — the meta-layer is sugar over primitives.

Needle: →603 (tack: layered arguments + fcp pipe operator)
