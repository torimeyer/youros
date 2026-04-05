---
title: Filesystem Embeddings — Semantic Compression for the Squasher
version: 2
status: spec
created: 2026-03-15
supersedes: PR #4 (filesystem-embeddings v1, withdrawn)
depends_on:
- resource-limits
- context-degradation
evidence: Model2Vec benchmarks, potion-base-8M evaluation, leader algorithm literature, prior art survey (tokf, Claw, Drain3, SemDeDup, LLMLingua)
implements: []
---

# Filesystem Embeddings

> Every line of shell output is embedded before dedup. The squasher uses semantic similarity — not just edit distance — to collapse repeated meaning across the full output buffer.

## Summary

The current squasher pipeline (VTE strip + Levenshtein on consecutive lines) catches syntactic repetition: lines that look the same. It misses semantic repetition: lines that mean the same thing but appear far apart in the buffer. Cargo compiling 200 crates emits 200 structurally identical lines scattered across 10,000 lines of output. Levenshtein catches consecutive duplicates. Embeddings catch all of them.

This extends the free tier (100M tokens) by saving more tokens per command. The squasher already saves ~24% (observed: 67 -> 51 lines). Semantic dedup adds a second pass that collapses meaning-equivalent lines buffer-wide, targeting an additional 15-30% reduction on repetitive build/test output.

---

## Architecture

### Model

**Model2Vec `potion-base-8M`** — a distilled static embedding model.

| Property | Value |
|----------|-------|
| Size | 8 MB on disk |
| Latency | 0.125 ms/line (single-threaded, CPU) |
| Quality | 89% of MiniLM-L6 on STS benchmarks |
| Runtime | Pure Rust (no Python, no ONNX, no GPU) |
| Embedding dim | 256 |

No tokenizer model file. No runtime dependencies beyond the weight matrix. The entire model loads in ~2ms and lives in memory for the session lifetime.

### Algorithm

**Leader algorithm** — greedy single-pass clustering.

```
for each embedded line:
    compare against all cluster leaders (cosine similarity)
    if best_match >= threshold:
        assign to that cluster (discard line, keep leader)
    else:
        promote this line as a new cluster leader
```

Time complexity: O(n * k) where n = lines, k = clusters. For typical shell output (n=10000, k=200), this completes in <50ms. No iterative refinement. No distance matrix. Single pass, streaming-compatible.

### Threshold

**0.85 cosine similarity** (configurable). Empirically chosen:

| Threshold | Behavior |
|-----------|----------|
| 0.70 | Aggressive — collapses "Compiling foo" with "Downloading bar". Too lossy. |
| 0.85 | Sweet spot — collapses "Compiling foo v1.2" with "Compiling bar v3.4". Keeps structurally different lines. |
| 0.95 | Conservative — only catches near-identical lines. Marginal gain over Levenshtein. |

### Pipeline

The full squasher pipeline with embeddings enabled:

```
Raw PTY output
  → VTE strip (remove ANSI escapes, cursor movement, color codes)
  → Levenshtein dedup (consecutive identical/near-identical lines)
  → Tokenize (normalize {path}, {ver}, {hash}, {hex}, {num}, {date})
  → Embed (Model2Vec potion-base-8M, 256-dim vector per tokenized line)
  → Semantic cluster (leader algorithm, 0.85 cosine, buffer-wide)
  → Compressed output
```

Each stage is a filter. Each stage reduces input for the next. The pipeline is strictly sequential — no stage depends on future output.

---

## Key Insight: Tokenize Before Embed

The existing squasher tokenizer normalizes variable parts of shell output:

```
Compiling ostk v1.3.0 (/Users/scott/projects/ostk)
  → Compiling {path} {ver} ({path})

Compiling serde v1.0.210 (/Users/scott/.cargo/registry/src/...)
  → Compiling {path} {ver} ({path})
```

After tokenization, these two lines are **identical strings**. Their embeddings have cosine similarity = 1.0.

Without tokenization, the raw lines would embed to ~0.92 similarity — close but not identical, and potentially below a conservative threshold. The tokenizer and embeddings are **complementary, not competing**:

- The tokenizer handles **structural normalization** (paths, versions, hashes)
- Embeddings handle **semantic equivalence** (lines with different structure but same meaning)
- Together they achieve higher compression than either alone

This is why the pipeline order matters: tokenize first, embed second.

---

## Three-Tier Degradation

The squasher operates at the highest available tier and degrades gracefully.

### Tier 1: Full (Embeddings + Levenshtein)

- Feature flag `embeddings` enabled at compile time
- Model file available at runtime
- Pipeline: VTE strip -> Levenshtein -> Tokenize -> Embed -> Semantic cluster
- Maximum compression

### Tier 2: Template (Levenshtein only)

- Default build, no feature flag required
- Always works, zero additional dependencies
- Pipeline: VTE strip -> Levenshtein -> Tokenize
- Current production behavior

### Tier 3: Minimal (VTE strip only)

- Emergency fallback if tokenizer panics or is disabled
- Pipeline: VTE strip only
- Guaranteed to work on any output

Tier selection is automatic. If the embeddings feature is compiled in but the model file is missing or corrupt, the squasher logs a warning and falls back to Tier 2. If the tokenizer fails on a specific line, that line passes through with VTE strip only (Tier 3 for that line, not the whole buffer).

---

## Feature Flag

```
# Default build — Tier 2, no embedding dependencies
cargo build

# With embeddings — Tier 1, pulls in model2vec crate + model weights
cargo build --features embeddings
```

The `embeddings` feature flag gates:
- The `model2vec` crate dependency
- The model weight file (bundled or downloaded on first use)
- The semantic clustering pass in the squasher pipeline

Zero impact on default build size, compile time, or runtime behavior. The feature flag is the only entry point — no runtime detection, no dynamic loading.

---

## Configuration

```toml
# ostk.toml

[squasher.embeddings]
enabled = true                  # master switch (requires feature flag at compile time)
model = "potion-base-8M"       # model identifier
cosine_threshold = 0.85         # similarity threshold for clustering
```

| Key | Default | Range | Effect |
|-----|---------|-------|--------|
| `enabled` | `true` | bool | If `false`, skip embedding pass even if compiled in |
| `model` | `"potion-base-8M"` | string | Model identifier for future model swaps |
| `cosine_threshold` | `0.85` | 0.0-1.0 | Lower = more aggressive dedup, higher = more conservative |

Configuration is read once at squasher initialization. Changes require process restart (no hot reload — the squasher is a stateless filter, not a long-running service).

---

## 5 Laws Compliance

| Law | Requirement | How this spec complies |
|-----|-------------|----------------------|
| **Law 1: Write path invisible** | No new tools visible to the agent | Embeddings are computed on the output path (PTY -> squasher -> context). No new MCP tools. No new commands. The agent never knows embeddings exist. |
| **Law 2: Agents ephemeral** | Nothing persisted per-agent | Embedding vectors live in memory for the duration of one squash call. No vector database. No cached embeddings across sessions. The model weights are shared read-only state, not agent state. |
| **Law 3: Coordinate through filesystem** | No message passing between agents | The squasher reads PTY output from the filesystem spool. Compressed output is written back. No IPC, no shared memory, no message queues. |
| **Law 4: OCC** | Optimistic concurrency control | N/A for v1.5. The squasher operates on a single agent's output buffer. No cross-agent dedup. If v1.6 introduces cross-agent semantic dedup, OCC applies to the shared spool. |
| **Law 5: Invisible infrastructure** | Feature flag, graceful degradation | Gated behind `--features embeddings`. Three-tier degradation ensures the system never fails due to embeddings. The agent, the operator, and the kernel all function identically whether embeddings are compiled in or not. |

---

## Metrics

Semantic savings are tracked separately from Levenshtein savings in `metrics.jsonl`:

```jsonl
{"event":"squash","original":240,"compressed":180,"saved":60,"ts":"2026-03-15T10:00:00Z"}
{"event":"squash_semantic","original":180,"compressed":120,"saved":60,"ts":"2026-03-15T10:00:00Z"}
```

| Field | `"event": "squash"` | `"event": "squash_semantic"` |
|-------|---------------------|------------------------------|
| Meaning | Levenshtein + tokenizer savings | Embedding cluster savings |
| `original` | Lines before Levenshtein | Lines after Levenshtein (input to embeddings) |
| `compressed` | Lines after Levenshtein | Lines after semantic clustering |
| `saved` | `original - compressed` | `original - compressed` |

The two events are emitted together for every squash operation when Tier 1 is active. When Tier 2 is active, only the `"squash"` event is emitted (current behavior, no change).

This separation lets the operator measure the marginal value of embeddings independently: is the `embeddings` feature flag worth the 8MB model weight?

---

## Future Consumers (v1.6+)

The embedding infrastructure built for the squasher has two planned future consumers:

### Digest Ranking

Rank stale files by semantic relevance to the current task. When the digest compiler selects which files to summarize, it can use the same embedding model to compute similarity between the agent's current needle description and each candidate file's content. Files semantically closer to the active work get higher-fidelity digests.

### Hot PR Tier 4

Detect semantically contradictory non-overlapping edits. Two agents editing different files might introduce semantic conflicts that diff-based merge cannot detect (e.g., agent-1 adds a cache in `server.rs`, agent-2 adds a "never cache" policy in `config.rs`). Embedding the changed regions and computing cross-agent similarity surfaces these conflicts before merge.

Both consumers reuse the same model, the same embedding function, and the same cosine similarity primitive. The squasher is the proving ground.

---

## Prior Art

| System | What it does | How this differs |
|--------|-------------|-----------------|
| **tokf** | Token-frequency-based log template extraction | Pattern matching, no semantic understanding. Cannot detect meaning equivalence across different templates. |
| **Claw Compactor** | LLM context compression via summarization | Uses an LLM to summarize — expensive, non-deterministic, adds latency. We use a static embedding model: deterministic, 0.125ms/line, no API calls. |
| **Drain3** | Online log parsing via fixed-depth tree | Extracts templates from structured logs. Does not embed or cluster by meaning. Cannot handle unstructured shell output (compiler warnings, test failures, stack traces). |
| **NVIDIA SemDeDup** | Semantic deduplication of training datasets | Operates on documents, not lines. Requires GPU-scale compute. Designed for offline batch processing of TB-scale datasets, not real-time streaming of KB-scale shell output. |
| **LLMLingua** | Prompt compression via perplexity-based token pruning | Removes low-information tokens from prompts. Complementary but different: we remove redundant lines, not redundant tokens within a line. LLMLingua could be a future Tier 1.5 addition. |

**None of these systems perform embedding-based semantic deduplication of shell output in real time.** The combination of tokenize-before-embed (structural normalization feeding into semantic clustering) and the leader algorithm (single-pass, streaming-compatible) on PTY output is novel.

---

## Acceptance Criteria

- [ ] `potion-base-8M` model loads in <5ms, embeds at >8000 lines/sec
- [ ] Leader algorithm clusters 10,000 lines in <50ms
- [ ] Tier 1 achieves measurably higher compression than Tier 2 on `cargo build` output
- [ ] Tier degradation is automatic: missing model -> Tier 2, no compile flag -> Tier 2
- [ ] `squash_semantic` events emitted to `metrics.jsonl` with correct counts
- [ ] `cosine_threshold` configurable via `ostk.toml`
- [ ] Feature flag `embeddings` compiles cleanly with zero impact on default build
- [ ] Tokenize-before-embed produces cosine=1.0 for structurally identical lines with different variable parts
- [ ] No embedding state persists across squash calls (Law 2)
- [ ] No new MCP tools or agent-visible interfaces (Law 1)
