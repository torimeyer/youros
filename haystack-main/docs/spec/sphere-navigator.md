---
created_at: 2026-04-03T19:03:55Z
status: spec
roundtable: gemini-2.5-pro (2026-04-03)
promoted_at: 2026-04-03T19:22:38Z
title: Sphere Navigator — cross-sphere linking for fcp-screen
---

# Sphere Navigator

A lightweight graph view in fcp-screen for drawing connections between
needles and spheres that embeddings miss. The primary action is linking
— broadening what models see when they traverse the topology.

## Problem

347 needles, 5 connected spheres, 212 isolated. Embeddings cluster by
semantic similarity, but conceptual links aren't always semantic.
"Discord bot calls Bash" and "daemon should handle tools natively" are
related by *architectural consequence*, not vocabulary. A human sees the
connection instantly but has no way to express it except `:link` commands
typed blind. The compile/refine cycle can't discover what the human
already knows.

## Core concept: link-first, navigate-second

This is not a full graph editor. It's a peek view with a linking verb.
The user sees the sphere topology, spots a gap ("these two ideas should
be connected"), draws the joint, and moves on. Navigation exists to
support linking, not as an end in itself.

The value isn't in the visualization — it's in the **joints that get
created**. Each human-drawn joint enriches future compile passes:
- Spheres merge or grow, reducing isolation
- Bridge scoring improves (more edges = better Jaccard overlap)
- Models traversing the graph discover paths that didn't exist before

## View: galaxy with peek

Single primary view. Spheres as labeled circles, sized by member count.
Isolated needles grouped into a "nebula" (see below). Hay clusters as
dashed outlines near their bridged spheres.

| Key         | Action                                        |
|-------------|-----------------------------------------------|
| hjkl/arrows | Move cursor between nodes                     |
| Enter       | Peek: side panel shows sphere members + hay   |
| /           | Search by keyword, jump cursor                |
| Tab         | Cycle between spheres                         |
| q           | Close navigator, back to conversation         |

### The 212 isolated needles problem (Gemini insight)

212 individual dots would destroy the galaxy view. Solution: embed their
titles, cluster by DBSCAN (no fixed k), render as a single "nebula"
entity in the galaxy view. Enter on the nebula shows the ~20-30
embedding-derived clusters. Enter on a cluster shows its member needles.

This means isolated needles get temporary structure from embeddings
without needing explicit joints — and when you link one out of the
nebula into a real sphere, it leaves the cloud.

### Sub-sphere clustering (Gemini insight)

Galaxy → 126-needle sphere is too big a jump. When entering a large
sphere (>10 members), run DBSCAN on member embeddings to show internal
"constellations" first. Enter on a constellation reveals its needles.
Progressive disclosure: galaxy → sphere → constellation → needle.

## Linking

The one edit operation that matters:

| Key | Action                                                |
|-----|-------------------------------------------------------|
| j   | Start joint: marks source needle. Navigate to target. |
| j   | Complete joint: creates depends-on edge, writes .ostk |
| Esc | Cancel in-progress joint                              |

Visual feedback: dashed line follows cursor while joint in progress.

That's it. No delete, no merge, no promote — those are `:close`,
`:unlink`, `:compile` in the conversation. The navigator is for the
one thing you can't do well in text: seeing two distant things and
connecting them.

## Layout: embeddings as spatial position

The same `embed_batch` + `pairwise_similarity` pipeline (Burn GPU,
potion-base-8M) positions nodes. Semantically similar nodes cluster
spatially, so when you spot two distant dots that *should* be near
each other, that's exactly the gap the navigator is for.

1. Embed all needle titles in one batch call (~350 embeddings, <1s GPU)
2. Pairwise similarity matrix (one matmul)
3. Force-directed layout (Fruchterman-Reingold)
4. **Persist positions** to `.ostk/layout.json` — preserve user's
   mental map across sessions. Only re-simulate for new nodes.

### Layout stability (Gemini insight)

- Seed initial positions from embeddings (consistent starting point)
- Simulation runs in background, renders only when energy drops below
  threshold — never show a "hot" jittering layout
- Pinned nodes: once the user has a mental map, positions stick
- Render budget: target 10-15 FPS, skip frames during convergence

## Temporal dimension (Gemini insight)

Static graph doesn't show momentum. Add a time filter:
- `<` / `>` to slide a time window
- Only show needles/hay created or active within the window
- Reveals which parts of the project are hot vs dormant
- Useful for understanding what a sprint touched

## Integration

Lives in fcp-screen as a tab (alongside conversation view). Toggle
with keybinding. Shares app state — needles/spheres already loaded.

### Dependencies

- `ratatui::widgets::canvas::Canvas` — 2D coordinate plane, Braille
  markers for resolution (2x4 per cell). Fallback to Block markers
  if performance is an issue. (Gemini: skip Sixel/Kitty for portability)
- `petgraph` — already in dep tree. Graph structures + BFS.
- `embeddings::embed_batch` / `pairwise_similarity` — drives layout
- Force-directed layout: ~100 lines against petgraph, or vendor `fdg`

### Data flow

```
read_needles() + read_hay()
  → embed_batch(titles)
  → pairwise_similarity()
  → force-directed layout → cached positions (.ostk/layout.json)
  → Canvas render (Braille markers)
  → j-key → ostk work link → .ostk/ writeback
```

## Phases

### Phase 1: Galaxy view + peek panel
- Canvas rendering of spheres as positioned circles
- Color by priority (P0 red, P1 yellow, P2 blue)
- Isolated needles as single nebula entity
- Enter to peek: right panel shows sphere members
- / to search

### Phase 2: Linking + sub-sphere drill
- j-key to draw joints between any two needles
- Dashed line visual feedback while joint in progress
- Writes to .ostk/ on completion, spheres recalculate
- Sub-sphere constellations for large spheres (DBSCAN)

### Phase 3: Embedding layout + persistence
- GPU batch embed, pairwise similarity, force-directed positioning
- Cached layout with pinned positions
- Background convergence, render on stability threshold
- Nebula expansion: enter to see embedding-clustered groups

### Phase 4: Temporal filter
- Time slider with < / > keys
- Filter view to time window
- Heat coloring by recency

## Acceptance criteria

- [ ] Galaxy view renders connected spheres as positioned circles in Canvas
- [ ] Enter on a sphere shows peek panel with members + hay
- [ ] / search jumps cursor to matching needle
- [ ] j-key creates a joint between two needles, writes to .ostk/
- [ ] Isolated needles grouped into nebula (embedding-clustered)
- [ ] Sub-sphere constellations for spheres with >10 members
- [ ] Layout positions persisted to .ostk/layout.json across sessions
- [ ] Embedding batch completes in <2s for 350 needles

## Open questions

1. Canvas resolution: Braille sufficient or need half-block?
2. Force-directed convergence iterations for <100ms?
3. DBSCAN epsilon parameter for sub-sphere / nebula clustering?
4. Saved named views? (Gemini suggests `:save-view architecture`)
5. Round-table: Gemini delivered, Sonnet/Qwen had tool permission
   issues, Mistral rate-limited. May re-run.
