---
status: spec
version: 1
author: scott + orchestrator
created: 2026-03-15
extends: compounding-dependency-order.md
implements: []
---

# Hoberman Sphere Work Ordering

> Focus on one point, expand outward, delegate to agents — the work at each joint advances every connected needle.

## The Model

A Hoberman Sphere is a kinematic structure that expands uniformly from a compact center. One degree of freedom (expand/contract) moves every joint simultaneously. The model maps to work ordering:

| Physical | ostk |
|----------|----------|
| Sphere | Thread — a connected component of needles |
| Joint | Connection between two needles (shared files, concepts, dependencies) |
| Point | The collapsed center — the needle with highest connectivity |
| Radius | BFS distance from point — how far work has propagated |
| Expansion | Agent delegation — work radiating outward through joints |
| Contraction | Refinement — collapsing back to validate the point still holds |

The geometry is not spherical. The data structure is a weighted graph. But the interface IS spherical: one degree of freedom per thread (expand/contract), and the user sees radius, not topology.

## Three Operator Verbs

### `:refine` — Tighten the joints

Passes over specified needles to validate and assess:
- Is this needle still valid? Does it match spec/intent?
- What sphere does it belong to? What's its radius from the point?
- What joints does it have? Are there missing connections?
- Does it need acceptance criteria? Priority assignment?

Refine is the quality pass. It makes needles denser and truer before they enter compounding order. Isolated needles (degree 0) get connection suggestions. Invalid needles get flagged.

```
ostk work refine →540 →489 bd-025
```

Output: sphere membership, radius, degree, joint list, validity warnings, connection suggestions.

Audit event: `needle.refined`

### `:compound` — Add intent to a needle

Appends intent text to a needle, making it denser. If the intent references other needle IDs (→NNN), those become new joints. Each compound operation increases the needle's connectivity and potentially merges two spheres.

```
ostk work compound →460 "connects to audit integrity chain, joints with →607"
```

The `⊕` separator marks compound additions in the title. References to other needles create explicit edges in the joint graph.

Compounding is how the human injects intent. The graph does the rest.

Audit event: `needle.compounded`

### `:radiate` — Expand from the point

BFS from the highest-connectivity needle outward. Shows concentric rings and generates a delegation frontier — the set of open needles at radius 1 ready for agent dispatch.

```
ostk work radiate          # auto-selects highest-degree point
ostk work radiate →164     # radiate from specific needle
```

Output: point info, rings 0-5, delegation frontier with spawn instructions.

The LLM reads the frontier and generates targeted agent instructions without human sequencing. The joint graph IS the instruction.

Audit event: `needle.radiated`

## The Joint Graph

Three signal types create joints between needles:

### 1. Explicit dependencies
Parsed from needle titles: "depends on →NNN", "blocks →NNN", "enables →NNN", "requires →NNN", "after →NNN".

### 2. Shared references
Two needles both mention →NNN in their titles. They share a common dependency or concern.

### 3. Concept clustering
Two needles share domain terms (compile, bench, tui, kernel, audit, etc.) in their titles. Only clusters of 2-15 needles create edges — too-common terms are noise.

## Measures

| Measure | Existing | Source |
|---------|----------|--------|
| Confidence | Yes | Boot POST |
| Priority | Yes | P0/P1/P2 |
| Compounding score | Yes | Fan-out count |
| **Radius** | **New** | **BFS distance from point** |

Radius measures propagation depth from the expansion point. A needle at radius 0 is the point itself. Radius 1 is the delegation frontier. As agents complete work at ring N, the effective radius of completed work grows, and the frontier advances to ring N+1.

Radius is not priority. A P0 at radius 4 still gets worked before a P2 at radius 1. But between two P1s at equal priority, the one at lower radius compounds more — it's closer to the center and its work propagates further.

## Relationship to `:compile`

`:compile` rebuilds the joint graph. It discovers connections that `:refine` and `:compound` created. `:compile` is the graph reconstruction step — it reads all needles, parses signals, finds connected components, and recalculates sphere topology.

The flow:
```
:refine   → validate + assess individual needles
:compound → add intent + create new joints
:compile  → rebuild the full graph from new state
:radiate  → expand from point, delegate to agents
```

## The Autonomous Loop

With these verbs, an LLM agent can autonomously select and execute work:

```
1. Read sphere topology (point, rings, frontier)
2. :confirm needle at frontier
3. Execute work on confirmed needle
4. :offer partial completions to connected needles
5. :radiate to see updated frontier (cheaper after offers)
6. Repeat from 2
```

No human ordering required. The graph IS the instruction.

## Implementation

Commands live in `src/commands/refine.rs`. The joint graph builder (`build_joint_graph`) and sphere finder (`find_spheres`) are public for reuse by compile, the scheduler, and the hay intelligence layer.

### CLI surface

```
ostk work refine →NNN [→NNN ...]
ostk work compound →NNN "intent text"
ostk work radiate [→NNN]
```

## Acceptance Criteria

- [x] `ostk work refine` shows sphere, radius, degree, joints, and validity warnings
- [x] `ostk work compound` appends intent and creates joints from referenced IDs
- [x] `ostk work radiate` shows rings and delegation frontier from highest-degree point
- [x] Joint graph built from explicit deps + shared refs + concept clusters
- [x] Connected components identified as spheres with point selection
- [x] BFS radius computed from point to all reachable needles
- [x] All three commands emit audit events
- [ ] `:compile` integrates joint graph rebuild from refine.rs
- [ ] Radius used as tiebreaker in `ostk work next` scheduling
