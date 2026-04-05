# Tack Graph — Emerge/Compound Analysis
# Mined 2026-03-11 from transcripts + session logs + docs

## Sources Mined
- ~/projects/haystack/transcripts/ (65 .md files)
- ~/.haystack/claude-code/transcripts/ (5 .md files)
- ~/.claude/projects/-Users-scottmeyer-projects-haystack/ (3 most recent .jsonl sessions)
- ~/projects/haystack/docs/draft/ (45 drafts)
- ~/projects/haystack/docs/spec/ (tack.md, tack-grammar.md, KUP, TUI ops, CLI surface)

## Tack Expression Corpus

### Primary evidence: docs/draft/tack-session-2026-03-09.md
Raw tack verbs observed in session (47 unique):

| # | Verb | Context | Resolved? | Outcome |
|---|------|---------|-----------|---------|
| 1 | `:compile` | hay→needles pipeline | correct | 48 hay.compiled audit events |
| 2 | `:ship` | release/publish | correct | haystack commit + push |
| 3 | `:push` | git push | correct | direct passthrough |
| 4 | `:show`/`:showme` | display state | correct | universal query command |
| 5 | `:reap` | clean dead agents | correct | 9 reap events |
| 6 | `:bench` | run benchmarks | correct | 98 docker + 13 run events |
| 7 | `:verify` | integrity check | correct | shipped |
| 8 | `:check` | alias for verify | correct | alias |
| 9 | `:status` | OS state | correct | haystack show status |
| 10 | `:agents`/`:fleet`/`:proc` | process list | correct | haystack ps |
| 11 | `:plan` | initiate planning | correct | draft→promote→decompose pipeline |
| 12 | `:correct`/`:adjust` | nudge/correction | correct | haystack nudge |
| 13 | `:delegate` | hand off to agent | correct | haystack run/spawn |
| 14 | `:audit` | audit trail | correct | haystack audit check |
| 15 | `:refine` | re-triage | correct | haystack compile re-triage |
| 16 | `:emerge`/`:emerges` | surface unexpected | correct | haystack hay→compile |
| 17 | `:investigate` | dig into history | correct | haystack history |
| 18 | `:thread` | thread reference | correct | haystack thread |
| 19 | `:halt` | shutdown | correct | haystack shutdown |
| 20 | `:note`/`:observe` | file observation | correct | haystack hay |
| 21 | `:test` | run tests | correct | haystack bench --cargo-only |
| 22 | `:boot` | init system | correct | haystack boot |
| 23 | `:draft` | create draft | correct | haystack draft |
| 24 | `:bug` | file bug | correct | haystack needle add --type bug |
| 25 | `:confirm` | accept/proceed | correct | control flow |
| 26 | `:context` | read state | correct | LLM-resolved |
| 27 | `:discuss` | round-table | correct | not shipped (pattern exists) |
| 28 | `:pitchfork` | load context | correct | LLM-resolved context retrieval |
| 29 | `:boost` | intensity signal | correct | modifier, not command |
| 30 | `:limit` | resource constraint | correct | modifier, not command |
| 31 | `:report` | show alias | correct | haystack show |
| 32 | `:experiment` | exploratory task | correct | LLM-resolved |
| 33 | `:teach` | explanatory mode | correct | LLM-resolved |
| 34 | `:define`/`:def` | define term | correct | LLM-resolved |
| 35 | `:erase` | delete/remove | correct | LLM with safety check |
| 36 | `:calibrate` | verify+correct | correct | compound: verify then adjust |
| 37 | `:align` | sync state | correct | compound: show then refine |
| 38 | `:measure` | quantify | correct | not in grammar (session-only) |
| 39 | `:prove` | provide evidence | correct | not in grammar (session-only) |
| 40 | `:retro` | retrospective | correct | not in grammar (session-only) |
| 41 | `:consider` | weigh option | correct | not in grammar (session-only) |

### Secondary evidence: docs/draft/tack-v2.md (round-table validated)
Additional verbs from v2 analysis:

| # | Verb | Context | Status |
|---|------|---------|--------|
| 42 | `:job`/`:job-name` | batch block | v2 only, not in grammar |
| 43 | `:exec`/`:execute` | imperative do | v2 + TUI ops spec |
| 44 | `:start` | begin work | v2 only |
| 45 | `:kill` | terminate | v2 + TUI spec |
| 46 | `:wait` | hold/pause | v2 only |
| 47 | `:discover` | explore/find | v2 only |
| 48 | `:lower` | deprioritize | v2 only |
| 49 | `:explain` | teach me | v2 only |
| 50 | `:rule` | declare a law | v2 only |
| 51 | `:ref` | reference | v2 + grammar |
| 52 | `:depends` | dependency | v2 only (in :job blocks) |
| 53 | `:goal` | objective | v2 only (in :job blocks) |
| 54 | `:break` | stop | v2 only |

### Tertiary evidence: docs/spec/KERNEL_UPDATE_PROTOCOL*.md + GOVERNANCE.md
Protocol verbs used in specs:

| # | Verb | Context | Status |
|---|------|---------|--------|
| 55 | `:negotiate` | KUP negotiation | spec (KUP v1.1) |
| 56 | `:attest` | GPG attestation | spec (governance) |
| 57 | `:propose` | kernel update proposal | spec (KUP v1.1) |
| 58 | `:respond` | reply to calibrate | spec (KUP v1.1) |
| 59 | `:sign` | GPG signing | spec (governance) |

### Quaternary evidence: docs/draft/tack-boot.md + dynamic-userspace-os.md
Boot/OS verbs:

| # | Verb | Context | Status |
|---|------|---------|--------|
| 60 | `:init` | initialize instance | draft (tack-boot) |
| 61 | `:load` | mount resource | draft (tack-boot) |
| 62 | `:driver` | load fcp driver | draft (tack-boot) |
| 63 | `:work` | pull next needle | draft (tack-boot) |
| 64 | `:revive` | resurrect from shadows/ | draft (dynamic-userspace-os) |
| 65 | `:spawn` | start agent | grammar + audit (shipped) |
| 66 | `:recover` | boot from state | grammar (shipped) |
| 67 | `:inform` | cross-OS nudge | draft (command-map) |
| 68 | `:milestone` | milestone marker | not shipped |
| 69 | `:insight` | hay stays hay | not shipped |

### Compound expressions observed (from tack-session-2026-03-09.md)
```
^->:add needles :delegate :notify
:context ++correctness .? pre-existing <<-.audit
:emerge->gc=>LLM ->:draft rtx3
:refine -> needles :ship
:correct !miss ::ship
::correct :calibrate -> compile .sync
:::::intent
:escl ->haystack wins
->x25 bench +++++error :context screens
:delegate -------noise
:pr-review :emerges distributed haystack os
:hs add `hs merge`.p0 :compound extension -> :refine :plan :ship
:compile -> :refine -> :plan -> :ship
```

---

## The Graph

### Nodes (all tack verbs observed, 69 unique)

```
LAYER 0 (kernel primitives — immutable):
  :boot :halt :verify :shutdown

LAYER 1 (kernel safety — immutable in .language):
  :init :load :driver :work :recover :spawn :kill :reap

LAYER 2 (ceremonial — high inertia):
  :negotiate :attest :sign :propose :confirm :respond

LAYER 3 (userspace — full dynamics):
  :compile :ship :push :show :bench :status :fleet :proc :agents
  :plan :correct :adjust :delegate :audit :refine :emerge :investigate
  :thread :note :observe :test :draft :bug :context :discuss
  :pitchfork :boost :limit :report :experiment :teach :define :erase
  :calibrate :align :exec :start :wait :discover :lower :explain
  :rule :ref :depends :goal :break :job :inform :revive :milestone :insight

LAYER 4 (session — ephemeral):
  :measure :prove :retro :consider :notify :escl :compound
```

### Edges (:compounds relationships)

Extracted from compound expressions, spec references, and `compounds:` frontmatter:

```
:compile ──→ :refine ──→ :plan ──→ :ship          (the main pipeline)
:emerge ──→ :draft ──→ :compile                    (emergence pipeline)
:correct ──→ :calibrate ──→ :compile               (correction pipeline)
:boot ──→ :refine ──→ :compile ──→ :work           (boot sequence)
:delegate ──→ :spawn ──→ :fleet                    (delegation chain)
:audit ──→ :investigate ──→ :thread                (audit chain)
:negotiate ──→ :attest ──→ :sign ──→ :confirm      (governance chain)
:propose ──→ :negotiate ──→ :respond               (KUP chain)
:pitchfork ──→ :context ──→ :compile               (context retrieval)
:bug ──→ :needle ──→ :delegate                     (bug pipeline)
:bench ──→ :verify ──→ :ship                       (quality gate)
:discuss ──→ :emerge ──→ :draft                    (round-table pipeline)
:job ──→ :depends ──→ :goal ──→ :exec              (batch execution)
:note ──→ :emerge ──→ :compile                     (hay→needle)
:teach ──→ :explain ──→ :define                    (educational chain)
:calibrate ──→ :align ──→ :refine                  (drift correction)
:init ──→ :load ──→ :driver ──→ :boot              (tack boot init)

tack-boot :compounds escape-the-harness
tack-boot :compounds dynamic-userspace-os
tack-boot :compounds intent-dynamic-programming
:emerge :compounds :compile (emergence IS uncompiled observations)
:correct :compounds HUMANFILE (each :correct updates memoization table)
```

### Clusters (:emerge events)

Events that surfaced unexpectedly during sessions:

| Emerge Event | Source | What Surfaced | Captured? |
|-------------|--------|---------------|-----------|
| Tack itself | session 2026-03-08 | Language emerged from use, named by round table | YES — spec/tack.md |
| Batch jobs | session 2026-03-08 | :job blocks with :depends/:goal — hierarchical tack | PARTIAL — tack-v2.md draft |
| eOS (emergent OS) | session 2026-03-09 | haystack IS an OS, emerged not designed | YES — draft/emergent-os.md |
| Dynamic .language | session 2026-03-10 | Verbs atrophy/rise via momentum | YES — draft/dynamic-userspace-os.md |
| Tack boot | session 2026-03-10 | boot.md IS a tack init script | YES — draft/tack-boot.md |
| Intent DP | session 2026-03-10 | Tack is dynamic programming across sessions | YES — draft/intent-dynamic-programming.md |
| Compilation ratio | session 2026-03-09 | 23% of hay compiles to needles — that IS intelligence | PARTIAL — draft/tack-command-map.md |
| :correct as reasoning | session 2026-03-08 | Correction boundary IS where reasoning happens | NO — only in insights doc |
| Decompose exhaust | retro sprints 3-4 | Mechanical decomposition kills agents (22% vs 44% close) | NO — only in transcript |
| Agent self-discovery | meta-analysis | Agents 2x effective on self-discovered vs decomposed work | NO — only in transcript |
| Hook compounding | claude-code transcripts | hookify as canonical runtime compounds every future plugin | PARTIAL — in needle-001.md |
| Invisible coordination | session 2026-03-07 | haystack as container runtime (Docker analogy) | NO — only in 1018-cc.md transcript |

---

## Gap Analysis

### Verbs used but NOT in docs/spec/tack-grammar.md

| Verb | Source | Used How | Status |
|------|--------|----------|--------|
| `:measure` | session 2026-03-09 | quantify something | session-only, no spec |
| `:prove` | session 2026-03-09 | provide evidence | session-only, no spec |
| `:retro` | session 2026-03-09 + KUP v1.1 | retrospective | used in KUP spec, not in grammar |
| `:consider` | session 2026-03-09 | weigh option | session-only, no spec |
| `:job` | tack-v2.md (validated) | batch block | v2 only, critical gap |
| `:exec`/`:execute` | tack-v2.md + TUI ops spec | imperative do | in TUI spec but not grammar |
| `:start` | tack-v2.md | begin work | v2 only |
| `:kill` | tack-v2.md + TUI spec | terminate | in TUI spec but not grammar |
| `:wait` | tack-v2.md | hold/pause | v2 only |
| `:discover` | tack-v2.md | explore/find | v2 only |
| `:lower` | tack-v2.md | deprioritize | v2 only |
| `:rule` | tack-v2.md | declare a law | v2 only |
| `:depends` | tack-v2.md | dependency edge | v2 only |
| `:goal` | tack-v2.md | objective | v2 only |
| `:break` | tack-v2.md | stop | v2 only |
| `:negotiate` | KUP v1.1 | governance | in KUP spec, not grammar |
| `:attest` | governance | GPG attestation | in governance, not grammar |
| `:propose` | KUP v1.1 | kernel update | in KUP spec, not grammar |
| `:spawn` | audit trail | start agent | shipped in code, not in grammar |
| `:recover` | command-map | boot from state | shipped, not in grammar |
| `:init` | tack-boot draft | initialize | draft only |
| `:load` | tack-boot draft | mount resource | draft only |
| `:driver` | tack-boot draft | load driver | draft only |
| `:work` | tack-boot draft | pull next needle | draft only |
| `:revive` | dynamic-userspace-os | resurrect verb | draft only |
| `:inform` | command-map | cross-OS nudge | shipped, not in grammar |
| `:milestone` | command-map | milestone marker | not shipped |
| `:insight` | command-map | hay observation | not shipped |
| `:notify` | session compound expr | notification | session-only |
| `:compound` | session compound expr | compounding ref | session-only |
| `:needle` | session + grammar pattern | add needle | in pattern match, not verb table |

### :emerge events that produced insights NOT yet in docs/draft/

| Insight | Source | Draft needed? |
|---------|--------|---------------|
| :correct as reasoning event | insights-session-2026-03-08 | YES — filed as needle |
| Decompose exhaust kills agents | meta-analysis-needle-guidance.md | YES — filed as needle |
| Agent self-discovery 2x effective | meta-analysis-needle-guidance.md | YES — filed as needle |
| haystack as container runtime | 1018-cc.md transcript | YES — filed as needle |

### :compounds chains that reveal new specs needed

| Chain | What it implies | Spec exists? |
|-------|----------------|-------------|
| :negotiate→:attest→:sign→:confirm | Governance ceremony needs grammar entries | NO — verbs in KUP but not grammar |
| :job→:depends→:goal→:exec | Batch execution grammar (v2 tack) | NO — only in draft/tack-v2.md |
| :init→:load→:driver→:boot | Boot init sequence tack | NO — only in draft/tack-boot.md |
| :correct→:calibrate→:compile | Correction-driven recompilation | NO — implicit in usage, not specced |

---

## Needles Filed

### →611: tack-grammar: add 15 v2 verbs to verb table
Verbs :job, :exec, :start, :kill, :wait, :discover, :lower, :rule, :depends, :goal, :break, :spawn, :recover, :inform, :needle confirmed by round-table validation and/or audit trail. Missing from spec/tack-grammar.md verb table.

### →612: tack-grammar: add governance verbs (:negotiate, :attest, :propose)
These verbs are used in KUP v1.1 and GOVERNANCE.md specs but absent from tack-grammar.md. Governance tack should be documented in the grammar.

### →613: draft: :correct as reasoning boundary
Insight from session 2026-03-08: "Reasoning happens at the correction boundary. When the human says :correct, the agent's inference meets reality and adjusts." Not captured in any draft. Source: docs/insights-session-2026-03-08.md line 187.

### →614: draft: decompose exhaust — organic vs mechanical needles
Meta-analysis proved agents 2x effective at closing self-discovered work (44%) vs mechanically decomposed work (22%). "A needle finds the thing. Decompose exhaust buries it." Source: transcripts/discussions/meta-analysis-needle-guidance.md.

### →615: draft: haystack as container runtime
Insight from session 2026-03-07: "haystack is a CONTAINER RUNTIME for LLM agents. Like Docker made namespaces invisible to processes, haystack makes coordination invisible to agents." Not captured in any draft. Source: transcripts/2026-03-07/1018-cc.md.

### →616: spec: tack boot init sequence
The :init→:load→:driver→:boot chain is fully designed in draft/tack-boot.md but needs promotion to spec. Has acceptance criteria. Blocked on →590 (signed boot.md).

### →617: spec: batch job grammar (:job blocks)
The :job→:depends→:goal→:exec pattern is the most powerful tack feature observed (v2 round-table validated) but has no spec. Only in draft/tack-v2.md.

---

## Promotions

### draft/tack-command-map.md → status: ready-for-spec
Evidence: 22/26 verbs map to shipped commands. Audit-verified with 1085 events. Compilation ratio (23%) is a key metric. All data is empirical.

### draft/tack-v2.md → status: ready-for-spec  
Evidence: Round-table validated (3 perspectives). v1 gaps identified and filled. Batch job syntax documented. Unobserved v1 operators marked theoretical.

---

## Top 5 Most-Used Tack Verbs NOT in .language

.language does not exist yet (→583 tracks its creation). These are the seed entries based on usage frequency from audit trail + session corpus:

| Rank | Verb | Evidence | Recommended tier | Resolution |
|------|------|----------|-----------------|------------|
| 1 | `:compile` | 48 hay.compiled events, in every pipeline | tier 1 | `haystack compile` |
| 2 | `:ship` | Used in every release, in main pipeline | tier 1 | `haystack commit` + push |
| 3 | `:correct` | Most frequent human intervention verb | tier 1 | `haystack nudge` |
| 4 | `:show` | Universal query, used across all sessions | tier 1 | `haystack show $1` |
| 5 | `:bench` | 111 total bench events (98 docker + 13 run) | tier 1 | `haystack bench` |

These five verbs should be the first entries in `.language` when →583 lands.
Runner-up verbs: `:draft` (13 events), `:emerge` (48 hay), `:delegate` (active), `:calibrate` (session-critical), `:boot` (every session start).

## Session 2026-03-12 — New verbs observed

| # | Verb | Context | Status |
|---|------|---------|--------|
| 70 | `:warn` | flag condition (api rate limit, model switch) | stable — signal modifier |
| 71 | `:notify` | announce fact (:notify model switch, :notify use haystack -c) | stable — signal modifier |
| 72 | `:caution` | slow down before proceeding (security audit gate) | stable — control flow |
| 73 | `:hesitant` | emotional modifier — don't force decision | stable — modifier |
| 74 | `:reboot` | reset + re-orient (compound: shutdown + boot) | stable |
| 75 | `:uerr` | user-flagged error correction (overrides prior output) | stable |
| 76 | `:network-isolation` | constraint modifier (no kernel channel) | stable — context |
| 77 | `:intelligence<->:intelligence` | two intelligences in escalating dialogue | emergent |
| 78 | `:escalation` | privilege or intelligence escalation signal | emergent |
| 79 | `:rtx3` | intensity modifier — real-time execution level 3 | tentative |
| 80 | `:pitchfork :past :transcript` | load context from prior session transcript | compound pitchfork |
| 81 | `:pitchfork graph :from` | load graph structure from named source | compound pitchfork |

## Compounds observed 2026-03-12

- `:intelligence<->:intelligence` :compounds `:escalation`
- `:escalation` :compounds `:security` (gate: negotiate protocol)
- `:caution` :compounds `:audit append-only :strict`
- `:reboot` = `:halt` + `:boot`
- `:pitchfork :past :transcript` = `:pitchfork` + temporal qualifier

## Emerge events 2026-03-12

1. `@ostk.ai.prime` responded to negotiate docs by BUILDING the protocol into ostk — output exceeded input
2. ENTITYFILE emerged from HUMANFILE — agent extended trust model to cover its own constraints
3. `:intelligence<->:intelligence :escalation` — new notation emerged from observing the PR #1 exchange
