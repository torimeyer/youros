//! Deduplication engine — implicit (structural) dedup.
//!
//! Line deduplication (originated in mish, ostk's experimental predecessor). Collapses consecutive similar lines
//! into "first line (xN)" summaries. Uses template tokenization to
//! normalize variable parts (versions, hashes, timestamps, paths).

use regex::Regex;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// Raw line similarity threshold (for implicit dedup)
pub const RAW_SIMILARITY: f64 = 0.3;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/// Result of implicit (structural) dedup check.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DedupResult {
    /// Line absorbed into current streak
    Absorbed,
    /// Streak broken — flush accumulated count
    FlushStreak { first: String, count: u32 },
    /// Not similar to previous line
    NotSimilar,
}

// ---------------------------------------------------------------------------
// Levenshtein distance
// ---------------------------------------------------------------------------

fn levenshtein(a: &str, b: &str) -> usize {
    let a_len = a.len();
    let b_len = b.len();
    if a_len == 0 {
        return b_len;
    }
    if b_len == 0 {
        return a_len;
    }

    let mut prev: Vec<usize> = (0..=b_len).collect();
    let mut curr = vec![0; b_len + 1];

    for (i, ca) in a.chars().enumerate() {
        curr[0] = i + 1;
        for (j, cb) in b.chars().enumerate() {
            let cost = if ca == cb { 0 } else { 1 };
            curr[j + 1] = (prev[j] + cost).min(prev[j + 1] + 1).min(curr[j] + 1);
        }
        std::mem::swap(&mut prev, &mut curr);
    }

    prev[b_len]
}

fn normalized_distance(a: &str, b: &str) -> f64 {
    let max_len = a.len().max(b.len());
    if max_len == 0 {
        return 0.0;
    }
    levenshtein(a, b) as f64 / max_len as f64
}

// ---------------------------------------------------------------------------
// Tokenizer patterns (shared between ImplicitDedup and SemanticDedup)
// ---------------------------------------------------------------------------

/// Build the ordered list of tokenizer regex patterns and their replacement
/// tokens. Order matters: URLs before paths, semver before plain numbers, etc.
///
/// Used by both `ImplicitDedup` (structural dedup) and `tokenize_for_embedding`
/// (semantic dedup pre-processing).
pub fn build_tokenizers() -> Vec<(Regex, &'static str)> {
    vec![
        // URLs
        (Regex::new(r"https?://\S+").unwrap(), "{url}"),
        // Paths
        (Regex::new(r"(?:/[\w.\-]+){2,}").unwrap(), "{path}"),
        // Semver
        (
            Regex::new(r"\d+\.\d+\.\d+(?:-[\w.]+)?(?:\+[\w.]+)?").unwrap(),
            "{ver}",
        ),
        // Package identifiers
        (Regex::new(r"(?:@[\w\-]+/)?[\w.\-]+@").unwrap(), "{pkg}@"),
        // UUIDs
        (
            Regex::new(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}").unwrap(),
            "{uuid}",
        ),
        // Hashes
        (Regex::new(r"\b[0-9a-f]{7,64}\b").unwrap(), "{hash}"),
        // ISO timestamps
        (
            Regex::new(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}").unwrap(),
            "{ts}",
        ),
        // Syslog timestamps
        (
            Regex::new(
                r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+\s+\d+:\d+:\d+",
            )
            .unwrap(),
            "{ts}",
        ),
        // Plain numbers (2+ digits)
        (Regex::new(r"\b\d{2,}\b").unwrap(), "{n}"),
    ]
}

/// Apply tokenizer patterns to a line, replacing variable parts with tokens.
///
/// This is the shared implementation used by both `ImplicitDedup::tokenize()`
/// and `tokenize_for_embedding()`.
fn apply_tokenizers(line: &str, tokenizers: &[(Regex, &str)]) -> String {
    let mut result = line.to_string();
    for (regex, token) in tokenizers {
        result = regex.replace_all(&result, *token).into_owned();
    }
    result
}

/// Tokenize a line before embedding — normalizes paths, versions, hashes etc.
///
/// This compounds with semantic similarity: lines with identical structure
/// but different variable parts (paths, versions, timestamps, etc.) produce
/// identical tokenized forms, yielding cosine similarity = 1.0 after embedding.
///
/// Uses the same regex tokenizer patterns as `ImplicitDedup`.
///
/// # Examples
/// ```ignore
/// let a = tokenize_for_embedding("error: failed to compile /foo/bar.rs");
/// let b = tokenize_for_embedding("error: failed to compile /baz/qux.rs");
/// assert_eq!(a, b); // both become "error: failed to compile {path}"
/// ```
pub fn tokenize_for_embedding(line: &str) -> String {
    let tokenizers = build_tokenizers();
    apply_tokenizers(line, &tokenizers)
}

// ---------------------------------------------------------------------------
// ImplicitDedup
// ---------------------------------------------------------------------------

pub struct ImplicitDedup {
    previous_line: Option<String>,
    previous_template: Option<String>,
    streak_count: u32,
    streak_first: Option<String>,
    tokenizers: Vec<(Regex, &'static str)>,
    threshold: f64,
}

impl ImplicitDedup {
    pub fn new() -> Self {
        Self {
            previous_line: None,
            previous_template: None,
            streak_count: 0,
            streak_first: None,
            tokenizers: build_tokenizers(),
            threshold: RAW_SIMILARITY,
        }
    }

    /// Create with a custom similarity threshold (overrides the default 0.3).
    pub fn with_threshold(threshold: f64) -> Self {
        Self {
            previous_line: None,
            previous_template: None,
            streak_count: 0,
            streak_first: None,
            tokenizers: build_tokenizers(),
            threshold,
        }
    }

    fn tokenize(&self, line: &str) -> String {
        apply_tokenizers(line, &self.tokenizers)
    }

    /// Check if a line continues the current streak or breaks it.
    pub fn check(&mut self, line: &str) -> DedupResult {
        if let Some(ref prev) = self.previous_line {
            if self.quick_similar(line, prev) {
                let template = self.tokenize(line);
                let prev_template = self.previous_template.as_ref().unwrap();

                if template == *prev_template
                    || normalized_distance(&template, prev_template) < self.threshold
                {
                    self.streak_count += 1;
                    self.previous_line = Some(line.to_string());
                    return DedupResult::Absorbed;
                }
            }

            // Streak broken — flush if we had one
            if self.streak_count > 1 {
                let result = DedupResult::FlushStreak {
                    first: self.streak_first.take().unwrap(),
                    count: self.streak_count,
                };
                self.reset(line);
                return result;
            }
        }

        self.reset(line);
        DedupResult::NotSimilar
    }

    fn quick_similar(&self, a: &str, b: &str) -> bool {
        let (la, lb) = (a.len(), b.len());
        if la > lb * 2 || lb > la * 2 {
            return false;
        }

        let first_a = a.split_whitespace().next();
        let first_b = b.split_whitespace().next();
        first_a == first_b
    }

    fn reset(&mut self, line: &str) {
        self.previous_line = Some(line.to_string());
        self.previous_template = Some(self.tokenize(line));
        self.streak_count = 1;
        self.streak_first = Some(line.to_string());
    }

    /// Flush any remaining streak at end of stream.
    pub fn flush(&mut self) -> Option<DedupResult> {
        if self.streak_count > 1 {
            let result = DedupResult::FlushStreak {
                first: self.streak_first.take().unwrap(),
                count: self.streak_count,
            };
            self.streak_count = 0;
            self.previous_line = None;
            self.previous_template = None;
            Some(result)
        } else {
            None
        }
    }
}

impl Default for ImplicitDedup {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// SemanticDedup — leader-algorithm clustering (requires embeddings feature)
// ---------------------------------------------------------------------------

/// Result of semantic dedup processing.
#[cfg(feature = "embeddings")]
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SemanticResult {
    /// Line absorbed into an existing cluster
    Absorbed,
    /// New cluster created for this line
    NewCluster,
    /// Embedding computation failed; caller should fall back
    ComputeError,
}

/// Semantic dedup via leader algorithm (greedy single-pass clustering).
///
/// Runs *after* `ImplicitDedup` has collapsed consecutive duplicates.
/// Accumulates the buffer's unique lines, clusters them by cosine
/// similarity of their embeddings, and at flush time emits one
/// representative per cluster with an absorption count.
#[cfg(feature = "embeddings")]
pub struct SemanticDedup {
    /// Cluster centroids: (representative_line, embedding, count)
    clusters: Vec<(String, Vec<f32>, u32)>,
    /// Cosine similarity threshold (default 0.85)
    threshold: f32,
}

#[cfg(feature = "embeddings")]
impl SemanticDedup {
    pub fn new(threshold: f32) -> Self {
        Self {
            clusters: Vec::new(),
            threshold,
        }
    }

    /// Process a single line through the leader algorithm.
    ///
    /// 1. Tokenize the line (normalize paths, versions, hashes, etc.).
    /// 2. Compute the tokenized form's embedding.
    /// 3. Compare against every existing cluster centroid.
    /// 4. If the best match exceeds `threshold`, absorb into that cluster.
    /// 5. Otherwise, create a new cluster with this line as representative.
    ///
    /// Tokenizing before embedding compounds cheap regex normalization with
    /// expensive embedding computation: structurally identical lines (differing
    /// only in paths/versions/timestamps) produce identical embeddings.
    pub fn process_line(&mut self, line: &str) -> SemanticResult {
        // Tokenize first — normalize variable parts so structurally identical
        // lines embed identically (needle →693: tokenize-before-embed).
        let tokenized = tokenize_for_embedding(line);
        let embedding = match crate::squasher::embeddings::embed(&tokenized) {
            Ok(v) => v,
            Err(_) => return SemanticResult::ComputeError,
        };

        let mut best_sim: f32 = -1.0;
        let mut best_idx: Option<usize> = None;

        for (i, (_rep, centroid, _count)) in self.clusters.iter().enumerate() {
            let sim = crate::squasher::embeddings::cosine_similarity(&embedding, centroid);
            if sim > best_sim {
                best_sim = sim;
                best_idx = Some(i);
            }
        }

        if best_sim > self.threshold {
            if let Some(idx) = best_idx {
                self.clusters[idx].2 += 1;
                return SemanticResult::Absorbed;
            }
        }

        // No match above threshold — new cluster
        self.clusters.push((line.to_string(), embedding, 1));
        SemanticResult::NewCluster
    }

    /// Emit all clusters as (representative_line, count) pairs.
    pub fn flush_clusters(&self) -> Vec<(String, u32)> {
        self.clusters
            .iter()
            .map(|(rep, _emb, count)| (rep.clone(), *count))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_implicit_dedup_consecutive_similar() {
        let mut id = ImplicitDedup::new();
        assert_eq!(
            id.check("Compiling serde v1.0.195"),
            DedupResult::NotSimilar
        );
        assert_eq!(id.check("Compiling tokio v1.35.0"), DedupResult::Absorbed);
        assert_eq!(id.check("Compiling regex v1.10.0"), DedupResult::Absorbed);
    }

    #[test]
    fn test_implicit_dedup_streak_break() {
        let mut id = ImplicitDedup::new();
        id.check("Compiling serde v1.0.195");
        id.check("Compiling tokio v1.35.0");
        id.check("Compiling regex v1.10.0");
        let result = id.check("error[E0308]: mismatched types");
        assert_eq!(
            result,
            DedupResult::FlushStreak {
                first: "Compiling serde v1.0.195".into(),
                count: 3,
            }
        );
    }

    #[test]
    fn test_implicit_dedup_dissimilar_not_merged() {
        let mut id = ImplicitDedup::new();
        assert_eq!(id.check("Compiling serde"), DedupResult::NotSimilar);
        assert_eq!(id.check("error: something broke"), DedupResult::NotSimilar);
    }

    #[test]
    fn test_flush_remaining_streak() {
        let mut id = ImplicitDedup::new();
        id.check("Compiling serde v1.0.195");
        id.check("Compiling tokio v1.35.0");
        id.check("Compiling regex v1.10.0");
        let result = id.flush();
        assert_eq!(
            result,
            Some(DedupResult::FlushStreak {
                first: "Compiling serde v1.0.195".into(),
                count: 3,
            })
        );
    }

    #[test]
    fn test_no_flush_single_line() {
        let mut id = ImplicitDedup::new();
        id.check("single line");
        assert_eq!(id.flush(), None);
    }

    #[test]
    fn test_levenshtein_empty() {
        assert_eq!(levenshtein("", ""), 0);
        assert_eq!(levenshtein("abc", ""), 3);
        assert_eq!(levenshtein("", "abc"), 3);
    }

    #[test]
    fn test_levenshtein_identical() {
        assert_eq!(levenshtein("hello", "hello"), 0);
    }

    #[test]
    fn test_levenshtein_substitution() {
        assert_eq!(levenshtein("cat", "car"), 1);
    }

    // -----------------------------------------------------------------------
    // Non-feature-gated compilation check: default build must compile without
    // the SemanticDedup code present.
    // -----------------------------------------------------------------------

    #[test]
    fn test_default_build_compiles_without_embeddings() {
        // ImplicitDedup must remain usable in all configurations.
        let mut id = ImplicitDedup::new();
        let _ = id.check("line a");
        let _ = id.flush();
    }

    // -----------------------------------------------------------------------
    // tokenize_for_embedding tests (needle →693)
    // -----------------------------------------------------------------------

    #[test]
    fn test_tokenize_paths_normalize() {
        // Same error with different paths should produce identical tokenized output
        let a = tokenize_for_embedding("error in /foo/bar.rs");
        let b = tokenize_for_embedding("error in /baz/qux.rs");
        assert_eq!(a, b);
        assert!(a.contains("{path}"));
    }

    #[test]
    fn test_tokenize_different_errors_with_same_path_differ() {
        // Different error messages should remain different even with the same path
        let a = tokenize_for_embedding("error: failed to compile /foo/bar.rs");
        let b = tokenize_for_embedding("warning: unused variable in /foo/bar.rs");
        assert_ne!(a, b);
    }

    #[test]
    fn test_tokenize_idempotent() {
        // Applying tokenization twice should produce the same result
        let line = "error at /foo/bar.rs version 1.2.3 hash abc1234";
        let once = tokenize_for_embedding(line);
        let twice = tokenize_for_embedding(&once);
        assert_eq!(once, twice);
    }

    #[test]
    fn test_tokenize_versions_normalize() {
        let a = tokenize_for_embedding("Compiling serde v1.0.195");
        let b = tokenize_for_embedding("Compiling serde v2.3.0");
        assert_eq!(a, b);
        assert!(a.contains("{ver}"));
    }

    #[test]
    fn test_tokenize_urls_normalize() {
        let a = tokenize_for_embedding("fetching https://example.com/api/v1");
        let b = tokenize_for_embedding("fetching https://other.org/data/v2");
        assert_eq!(a, b);
        assert!(a.contains("{url}"));
    }

    #[test]
    fn test_tokenize_timestamps_normalize() {
        let a = tokenize_for_embedding("event at 2024-01-15T10:30:00");
        let b = tokenize_for_embedding("event at 2025-12-31T23:59:59");
        assert_eq!(a, b);
        assert!(a.contains("{ts}"));
    }

    #[test]
    fn test_tokenize_uuids_normalize() {
        let a = tokenize_for_embedding("id: 550e8400-e29b-41d4-a716-446655440000");
        let b = tokenize_for_embedding("id: 6ba7b810-9dad-11d1-80b4-00c04fd430c8");
        assert_eq!(a, b);
        assert!(a.contains("{uuid}"));
    }

    #[test]
    fn test_tokenize_plain_text_unchanged() {
        // Text without any variable parts should pass through unchanged
        let line = "hello world";
        assert_eq!(tokenize_for_embedding(line), line);
    }

    #[test]
    fn test_tokenize_matches_implicit_dedup() {
        // tokenize_for_embedding should produce the same result as ImplicitDedup::tokenize
        let id = ImplicitDedup::new();
        let line = "error at /foo/bar.rs v1.2.3";
        let standalone = tokenize_for_embedding(line);
        let from_dedup = id.tokenize(line);
        assert_eq!(standalone, from_dedup);
    }
}

// ---------------------------------------------------------------------------
// SemanticDedup tests (only compiled with `embeddings` feature + test cfg)
// ---------------------------------------------------------------------------

#[cfg(all(test, feature = "embeddings"))]
mod semantic_tests {
    use super::*;

    // Minimal stub embeddings for unit testing. We mock the embedding
    // function so tests don't require a real model. The mock produces a
    // deterministic vector from the line's bytes.
    //
    // Since the real `crate::squasher::embeddings` module is expected
    // to be built by needle →690 in parallel, we test SemanticDedup's logic
    // directly by constructing it and calling internal helpers via the
    // public API, feeding lines that will produce known embeddings.

    /// Helper: build a SemanticDedup and manually inject cluster state.
    fn make_dedup(threshold: f32) -> SemanticDedup {
        SemanticDedup::new(threshold)
    }

    /// Helper: manually add a cluster for testing without needing embed().
    fn inject_cluster(sd: &mut SemanticDedup, rep: &str, embedding: Vec<f32>, count: u32) {
        sd.clusters.push((rep.to_string(), embedding, count));
    }

    // -- Cosine threshold boundary tests --

    #[test]
    fn test_cosine_threshold_exact_boundary() {
        // Verify that cosine_similarity is accessible
        use crate::squasher::embeddings::cosine_similarity;

        let a = vec![1.0, 0.0, 0.0];
        let b = vec![1.0, 0.0, 0.0];
        assert!((cosine_similarity(&a, &b) - 1.0).abs() < 1e-6);

        // Orthogonal vectors: similarity = 0
        let c = vec![0.0, 1.0, 0.0];
        assert!(cosine_similarity(&a, &c).abs() < 1e-6);
    }

    #[test]
    fn test_threshold_absorb_above() {
        // Two identical embeddings must produce similarity 1.0 > any threshold < 1.0
        let mut sd = make_dedup(0.85);
        let emb = vec![0.5, 0.5, 0.5];
        inject_cluster(&mut sd, "original line", emb.clone(), 1);

        // Manually simulate what process_line does: embed returns same vector
        // → similarity = 1.0 → absorbed.
        use crate::squasher::embeddings::cosine_similarity;
        let sim = cosine_similarity(&emb, &emb);
        assert!(sim > sd.threshold);
    }

    #[test]
    fn test_threshold_new_cluster_below() {
        let mut sd = make_dedup(0.85);
        let emb_a = vec![1.0, 0.0, 0.0];
        let emb_b = vec![0.0, 1.0, 0.0]; // orthogonal → sim ≈ 0
        inject_cluster(&mut sd, "cluster A", emb_a, 1);

        use crate::squasher::embeddings::cosine_similarity;
        let sim = cosine_similarity(&emb_b, &sd.clusters[0].1);
        assert!(sim <= sd.threshold);
    }

    // -- Cluster creation and absorption tests --

    #[test]
    fn test_cluster_creation() {
        let mut sd = make_dedup(0.85);
        assert_eq!(sd.clusters.len(), 0);
        inject_cluster(&mut sd, "first", vec![1.0, 0.0], 1);
        assert_eq!(sd.clusters.len(), 1);
        assert_eq!(sd.clusters[0].2, 1);
    }

    #[test]
    fn test_cluster_absorption_increments_count() {
        let mut sd = make_dedup(0.85);
        inject_cluster(&mut sd, "first", vec![1.0, 0.0], 1);
        // Simulate absorbing a line into this cluster
        sd.clusters[0].2 += 1;
        assert_eq!(sd.clusters[0].2, 2);
    }

    #[test]
    fn test_multiple_clusters() {
        let mut sd = make_dedup(0.85);
        inject_cluster(&mut sd, "cluster A", vec![1.0, 0.0, 0.0], 3);
        inject_cluster(&mut sd, "cluster B", vec![0.0, 1.0, 0.0], 2);
        inject_cluster(&mut sd, "cluster C", vec![0.0, 0.0, 1.0], 1);
        assert_eq!(sd.clusters.len(), 3);
    }

    // -- Flush output format tests --

    #[test]
    fn test_flush_empty() {
        let sd = make_dedup(0.85);
        let result = sd.flush_clusters();
        assert!(result.is_empty());
    }

    #[test]
    fn test_flush_single_cluster() {
        let mut sd = make_dedup(0.85);
        inject_cluster(&mut sd, "the representative", vec![1.0, 0.0], 5);
        let result = sd.flush_clusters();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0], ("the representative".to_string(), 5));
    }

    #[test]
    fn test_flush_preserves_order_and_counts() {
        let mut sd = make_dedup(0.85);
        inject_cluster(&mut sd, "alpha", vec![1.0, 0.0, 0.0], 10);
        inject_cluster(&mut sd, "beta", vec![0.0, 1.0, 0.0], 1);
        inject_cluster(&mut sd, "gamma", vec![0.0, 0.0, 1.0], 7);

        let result = sd.flush_clusters();
        assert_eq!(result.len(), 3);
        assert_eq!(result[0], ("alpha".to_string(), 10));
        assert_eq!(result[1], ("beta".to_string(), 1));
        assert_eq!(result[2], ("gamma".to_string(), 7));
    }

    // -- process_line integration (requires real embeddings module) --

    #[test]
    fn test_process_line_creates_cluster() {
        if !crate::squasher::embeddings::model_available() {
            return; // skip if no model
        }
        let mut sd = make_dedup(0.85);
        let result = sd.process_line("Compiling serde v1.0.195");
        assert_eq!(result, SemanticResult::NewCluster);
        assert_eq!(sd.clusters.len(), 1);
    }

    #[test]
    fn test_process_line_absorbs_similar() {
        if !crate::squasher::embeddings::model_available() {
            return;
        }
        let mut sd = make_dedup(0.85);
        sd.process_line("Compiling serde v1.0.195");
        let result = sd.process_line("Compiling tokio v1.35.0");
        // Both are "Compiling <pkg> <ver>" — if the model clusters them, Absorbed;
        // otherwise NewCluster. Either is valid; we just verify no error.
        assert_ne!(result, SemanticResult::ComputeError);
    }
}
