---
promoted_at: 2026-04-01T17:10:52Z
status: spec
---
# Burn + CubeCL → ostk: GPU-Powered Squasher

**Date:** 2026-04-01
**Hardware:** M4 Max / 128GB unified / Metal 3
**Binary:** 15MB, embeddings feature-gated and inactive

---

## The Problem in Numbers

The squasher has processed 4,378 events totaling 42MB of tool output.
Current regex+Levenshtein pipeline achieves **61% compression** — impressive.

But **32% of all input (13.6MB, ~3.4M tokens) passes through barely touched** — zero or <5% compression across 2,302 events. These are lines the classifier marks `Unknown` and the Levenshtein dedup can't collapse because the duplicates aren't consecutive or aren't string-similar.

That's 3.4 million tokens that went to LLMs unnecessarily.

---

## What model2vec Actually Is (and Why It's Currently Dead)

potion-base-8M is a **static embedding model**:
- **30,522 vocab** (BGE tokenizer) × **256 dim** = 7.8M params
- No transformer layers. No attention. No FFN.
- Inference = `tokenize → gather embedding rows → mean pool → normalize`
- One matrix gather + one reduction. That's it.

It's currently dead code because:
1. Feature-gated behind `--features embeddings`
2. Requires downloading model files to `.ostk/.models/potion-base-8M/`
3. Nobody's going to do that

---

## What Burn Changes

### The Embedding Table Goes on the GPU

**Current (model2vec, CPU):**
```
for each line:
    tokenize(line)           # CPU
    gather rows from 30k×256 # CPU, sequential
    mean pool                # CPU
    cosine vs each cluster   # CPU, O(n×k)
```

**With Burn + Metal:**
```
batch ALL lines at once:
    tokenize(lines)                    # CPU (tokenizers crate)
    gather + mean pool → N×256 matrix  # ONE GPU dispatch
    sim = embeddings @ embeddings.T    # ONE matmul → N×N similarity matrix
```

For 500 lines, the GPU version does **2 dispatches** instead of 500 sequential embedding lookups + 500×k cosine comparisons. On M4 Max Metal 3, the matmul alone is ~0.1ms.

### burn-onnx Eliminates Model Download

```rust
// build.rs — compile model into binary at build time
use burn_onnx::ModelGen;
fn main() {
    ModelGen::new()
        .input("models/potion-base-8M.onnx")
        .out_dir("model/")
        .run_from_script();
}

// runtime — no download, no .ostk/.models/, always available
let model: PotionModel<Wgpu> = PotionModel::default();
```

The embeddings feature goes from "opt-in download" to "always on."

---

## The Real Win: What a Pairwise Similarity Matrix Unlocks

The current SemanticDedup uses a **leader algorithm**: process one line at a time, compare against cluster centroids, single threshold (0.85), merge or create. This only does dedup.

A full **N×N similarity matrix** (trivial with GPU matmul) enables four things regex cannot do:

### A. Hierarchical Clustering

Group lines at **multiple** thresholds simultaneously:
- **0.95**: Near-duplicate collapse (what SemanticDedup does today)
- **0.85**: Same-topic grouping ("all the linker errors together")
- **0.70**: Same-category ("all error output" vs "all progress output")

One matmul gives you all three tiers. The squasher can collapse at the appropriate level: strict dedup for hazards, aggressive grouping for noise.

### B. Signal Extraction (Learned Classification Without Regex)

Pre-embed a small set of **signal exemplars** once at startup:
```
"error:", "failed:", "FAIL", "panicked", "fatal"     → hazard signal
"warning:", "deprecated", "unused"                     → warning signal
"ok", "PASS", "success", "Finished", "Built"          → outcome signal
"Compiling", "Downloading", "Installing", "Progress"  → progress noise
```

For every line, compute cosine similarity against these ~20 exemplars. Lines close to hazard → keep. Lines close to outcome → promote. Lines far from ALL signals → noise, strip them.

**This replaces `classify_tier2` and `classify_tier3` entirely.** No regex. Works for any tool. The 28 grammar files become optional overrides rather than required definitions.

### C. Diversity-Preserving Compression

Current dedup keeps the **first** line in each cluster. With a similarity matrix, keep the **most informative**:

From a cluster of 20 similar error lines, pick the one that is:
- Most similar to the cluster center (representative)
- Most different from all other *kept* lines (diverse)

Result: maximum information density per token.

### D. Zero-Shot Tool Support

No grammar file for `terraform`? `kubectl`? `ansible`?

Doesn't matter. Embed the output, cluster it, and the natural clusters **are** the grammar. Progress lines cluster together. Errors cluster together. Diffs cluster together. The structure emerges from semantics without anyone writing a regex.

**This attacks the 28-grammar ceiling.** Every tool in the world gets semantic compression for free.

---

## Quantified Impact

| Metric | Current | With GPU Embeddings |
|--------|---------|-------------------|
| Compression ratio | 61% savings | Est. 75-85% savings |
| Events with <5% compression | 52% (2,302 events) | Est. <15% |
| Tokens saved per session | ~2.5M | Est. +1.4-2M additional |
| Unknown classification fallthrough | Lines pass to dedup-only | Semantic signal extraction catches them |
| Tool coverage (grammar files) | 28 tools | Every tool (zero-shot) |
| Model availability | Dead (requires download) | Always on (compiled in) |
| Embedding latency | CPU sequential | GPU batch, sub-ms |

Conservative estimate: **33-50% further reduction** in total token spend on top of existing 61%.

---

## Implementation Path

### Phase 1: GPU Embeddings (replace model2vec)

```
Cargo.toml:
  [dependencies.burn]
  version = "0.21"
  features = ["wgpu", "metal", "ndarray"]  # Metal GPU + CPU fallback
  optional = true

  [build-dependencies]
  burn-onnx = "0.21"
```

1. Export potion-base-8M to ONNX (trivial — model2vec has export)
2. burn-onnx `build.rs` compiles it to native Rust
3. Replace `src/squasher/embeddings.rs` engine with Burn inference
4. Batch embedding: all lines at once instead of one-at-a-time
5. GPU pairwise similarity via matmul

### Phase 2: Signal Extraction (replace regex classifiers)

1. Pre-embed signal exemplars (hardcoded set of ~20 strings)
2. Classify every line by nearest signal cluster
3. Keep hazard/outcome lines, strip noise lines, dedup unknown
4. Falls back to regex tiers for grammar-matched tools (compound, don't replace)

### Phase 3: Local LLM Tier (burn-lm)

1. Integrate burn-lm's `InferenceServer` trait as kernel service
2. Llama 3.2 3B on Metal for: context summarization, hay→needle compilation
3. Reserve Claude/Gemini/DeepSeek for hard reasoning
4. This machine has 128GB unified memory — 3B model is trivial

---

## Risks

| Concern | Assessment |
|---------|-----------|
| Compile time | Burn is heavy. Feature-gate as `embeddings-burn`. CI builds without it. |
| Binary size | ~30MB for f32 weights. Ship as `.burnpack` sidecar, not embedded. Or use f16 (15MB). |
| Metal stability | wgpu Metal path is mature. CubeCL MSL is newer but Burn's wgpu backend uses wgpu→Metal which is battle-tested. |
| Fallback | `ndarray` CPU backend means it works everywhere. GPU is an acceleration, not a requirement. |
| Complexity | Phase 1 is a drop-in replacement for model2vec. Same API surface, faster backend. |

---

## The Compound

Regex grammars + Levenshtein dedup + GPU embeddings **compound**:
- Grammars catch known patterns with zero latency
- Levenshtein catches consecutive duplicates cheaply
- Embeddings catch everything else — scattered duplicates, semantic noise, unknown tools

Each tier handles what the others can't. The 61% becomes 80%+.
That 13.6MB of uncompressed pass-through shrinks to ~3MB.
~2.5 million tokens stop going to LLMs that didn't need to see them.

---

## Acceptance Criteria

### Phase 1: GPU Embeddings (replace model2vec)
- [ ] Export potion-base-8M to ONNX and add to `models/`
- [ ] `build.rs` compiles model via burn-onnx → native Rust
- [ ] `src/squasher/embeddings.rs` uses Burn backend (Metal GPU, ndarray CPU fallback)
- [ ] Batch embedding: all squasher lines embedded in one GPU dispatch
- [ ] Pairwise cosine similarity via matmul (not per-line leader algorithm)
- [ ] No model download step — embeddings always available when feature enabled
- [ ] `cargo test` passes with `--features embeddings-burn`
- [ ] Compression ratio on existing metrics.jsonl events improves measurably (>5% gain)

### Phase 2: Signal Extraction
- [ ] Pre-embedded signal exemplars (hazard, warning, outcome, noise) loaded at startup
- [ ] Lines classified by cosine distance to signal clusters
- [ ] `classify_tier2` / `classify_tier3` bypassed when embedding classification available
- [ ] Zero-shot compression works for tools without grammar files
- [ ] Events with <5% compression drops below 25% (from current 52%)

### Phase 3: Local LLM (burn-lm)
- [ ] burn-lm `InferenceServer` integrated as kernel service
- [ ] Llama 3.2 3B runs on Metal for context summarization
- [ ] Routine summarization routed to local model, hard reasoning to cloud LLMs
