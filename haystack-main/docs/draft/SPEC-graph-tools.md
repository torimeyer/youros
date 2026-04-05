# Graph-Aware Tools — Spec

## Problem

The joint graph (spheres, joints, BFS radii) exists in Rust but is invisible to agents during work. An agent filing a needle can say `depends on →1032` only if it *knows* →1032 exists and what it represents. Without a neighborhood index, the dependency language is useless.

## New Verbs

### `:sphere →NNN`
Show the sphere digest for a needle — its connected component, joints, BFS rings.

**Signature**: `(id) → (digest)`
**Resolution**: `ostk work sphere`

**Output format** (compact, ~200 tokens for a 12-member sphere):
```
SPHERE 3 (point: →846, 12 members, 8 joints)
  →846 [P0|open]  8 control-solution benchmarks broken patches
  →1032 [P1|closed] unify prompt
  →1033 [P1|closed] all 40 Dockerfiles build
  →1036 [P1|closed] fire-and-forget
  →1043 [P1|open]  bench claude-haiku-4-5 — 40×3 arms

  JOINTS:
    →1033 --blocks--> →1036
    →846  --concept:bench--> →1043

  HAY (3 straws):
    "CPU driver cost reporting missing for Anthropic/Google"
    "vibe TOML whitespace mangling codestral model name"
    "devstral-small-latest OR path stripped"
```

The sphere digest is the "index" — small enough for an agent to hold in context, rich enough to reason about dependencies.

### `:near "concept"`
Find needles and hay in concept space. Returns the neighborhood without requiring a specific needle ID.

**Signature**: `(query) → (matches)`
**Resolution**: `ostk work near`

**Output**: Lists open needles matching the concept term, grouped by sphere, plus any hay clusters that bridge to those spheres.

### `:link →A →B type`
Explicitly create a typed joint between two needles.

**Signature**: `(source, target, type) → ()`
**Resolution**: `ostk work link`

**Types**: `depends_on`, `blocks`, `after`, `enables`, `requires`

**Effect**: Persists to `depends_on[]`/`blocks[]` fields in issues.jsonl. Bidirectional: `→A depends_on →B` also sets `→B blocks →A`.

### `:unlink →A →B`
Remove a joint between two needles.

**Signature**: `(source, target) → ()`
**Resolution**: `ostk work unlink`

### `:activate →NNN`
Surface the full sphere context for a needle — ready to work it. This is `:sphere` + hay clusters + BFS frontier + delegation targets. The "pre-flight briefing" before an agent starts a needle.

**Signature**: `(id) → (context)`
**Resolution**: `ostk work activate`

**Output**: Sphere digest + hay cluster details + frontier analysis + suggested next actions.

## Updated Concept Vocabulary

Current CONCEPT_TERMS needs expansion for needle-bench and graph work:

**Add**: `"sphere"`, `"joint"`, `"activate"`, `"link"`, `"graph"`, `"model"`, `"arm"`, `"prompt"`, `"cost"`, `"token"`, `"container"`, `"build"`, `"approval"`, `"policy"`, `"humanfile"`, `"embed"`, `"cluster"`, `"web"`, `"discord"`, `"deploy"`

## Hoberman Dynamics

The sphere/joint/point vocabulary already encodes the Hoberman metaphor:
- **Sphere** = connected component (expands/contracts)
- **Joint** = typed edge (the link that preserves topology)
- **Point** = hub node (the center of the Hoberman sphere)
- **Radiate** = expand from point (open the Hoberman)
- **Compile** = contract hay into needles (close the Hoberman)
- **Activate** = load the sphere at its current expansion level

The new verbs make this explicit to agents.
