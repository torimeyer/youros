//! Output compression pipeline for ostk.
//!
//! Output compression pipeline (originated in mish, ostk's experimental predecessor). Minimal subset:
//! - VTE strip: remove ANSI/VTE escape codes from terminal output
//! - Dedup: collapse repeated/similar lines with counts
//!
//! This is 80% of the compression value with ~20% of the code.

pub mod classifier;
pub mod dangerous;
pub mod dedup;
pub mod enrich;
#[cfg(feature = "embeddings")]
pub mod embeddings;
pub mod grammar;
pub mod grammars;

pub mod narrate;
pub mod passthrough;
pub mod router;
pub mod structured;
pub mod vte_strip;

/// Find the state directory by walking up from CWD (best-effort).
fn find_ostk_dir() -> Option<std::path::PathBuf> {
    crate::find_project_root().ok().map(|r| crate::state_dir(&r))
}

/// Find the project root by walking up from CWD looking for state dir.
fn find_project_root() -> Option<std::path::PathBuf> {
    crate::find_project_root().ok()
}

/// Append a squash event to `.ostk/metrics.jsonl` (best-effort, never panics).
fn record_metrics(original: usize, compressed: usize) {
    let saved = original.saturating_sub(compressed);

    let ts = crate::now_iso();

    let line = format!(
        "{{\"event\":\"squash\",\"original\":{original},\"compressed\":{compressed},\"saved\":{saved},\"ts\":\"{ts}\"}}\n"
    );

    if let Some(hs_dir) = find_ostk_dir() {
        let path = hs_dir.join("metrics.jsonl");
        use std::fs::OpenOptions;
        use std::io::Write;
        if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&path) {
            let _ = f.write_all(line.as_bytes());
        }
    }
}

/// Grammar-aware compression pipeline.
///
/// Uses the grammar classifier to categorize each line before dedup:
/// - **Hazard** lines are always kept (errors must never be lost).
/// - **Outcome** lines are always kept (the signal the caller wants).
/// - **Noise(Strip)** lines are dropped entirely.
/// - **Noise(Dedup)** and **Unknown** lines are fed through implicit dedup.
///
/// Falls back to `compress()` when no grammar matches the command.
///
/// Returns (compressed_lines, stats_summary).
pub fn compress_with_grammar(raw: &str, command: &str, exit_code: i32) -> (String, String) {
    // Step 1: Detect tool grammar
    let grammars = &*grammars::GRAMMARS;
    let grammar_opt = grammar::detect_tool(command, grammars);

    // No grammar? Fall back to blind compress.
    let grammar = match grammar_opt {
        Some(g) => g,
        None => return compress(raw),
    };

    // Step 2: Resolve action from command args
    let argv: Vec<&str> = command.split_whitespace().collect();
    let action = grammar::resolve_action(grammar, &argv);

    // Step 3: VTE strip the raw output
    let stripped = vte_strip::strip_ansi(raw);

    // Step 4+5: Classify each line, route by classification
    let mut dedup_engine = if let Some(root) = find_project_root() {
        let cfg = crate::config::load_config(&root);
        if let Some(threshold) = cfg
            .squasher
            .and_then(|s| s.dedup)
            .and_then(|d| d.similarity_threshold)
        {
            dedup::ImplicitDedup::with_threshold(threshold)
        } else {
            dedup::ImplicitDedup::new()
        }
    } else {
        dedup::ImplicitDedup::new()
    };

    let mut output_lines: Vec<String> = Vec::new();
    let mut hazard_count: usize = 0;
    let mut outcome_count: usize = 0;
    let mut stripped_count: usize = 0;

    // We need raw lines (with ANSI) for Tier 2 color detection.
    // Walk raw lines in parallel with stripped lines.
    let raw_lines: Vec<&str> = raw.lines().collect();
    let stripped_lines: Vec<&str> = stripped.lines().collect();

    // Pre-compute GPU signal classifications in one batch call.
    // This replaces N individual GPU kernel launches with 1 batch embed.
    #[cfg(feature = "embeddings")]
    let precomputed_signals = classifier::precompute_signal_classifications(&stripped_lines);
    #[cfg(not(feature = "embeddings"))]
    let precomputed_signals: Option<Vec<Option<classifier::Classification>>> = None;

    for (i, line) in stripped_lines.iter().enumerate() {
        let raw_line = raw_lines.get(i).copied().unwrap_or(line);
        let signal_ref = precomputed_signals.as_ref().and_then(|s| s.get(i));
        let classification = classifier::classify_line_with_signal(
            line, raw_line, Some(grammar), action, signal_ref,
        );

        match classification {
            classifier::Classification::Hazard { .. } => {
                // Always keep — flush any pending dedup streak first
                if let Some(dedup::DedupResult::FlushStreak { first, count }) = dedup_engine.flush() {
                    output_lines.push(format!("{first}\n  [\u{22ef} {count} similar lines]"));
                }
                output_lines.push(line.to_string());
                hazard_count += 1;
            }
            classifier::Classification::Outcome { .. } => {
                // Always keep — flush any pending dedup streak first
                if let Some(dedup::DedupResult::FlushStreak { first, count }) = dedup_engine.flush() {
                    output_lines.push(format!("{first}\n  [\u{22ef} {count} similar lines]"));
                }
                output_lines.push(line.to_string());
                outcome_count += 1;
            }
            classifier::Classification::Noise { action: classifier::NoiseAction::Strip } => {
                // Drop entirely
                stripped_count += 1;
            }
            classifier::Classification::Noise { action: classifier::NoiseAction::Dedup }
            | classifier::Classification::Unknown => {
                // Feed through dedup engine
                match dedup_engine.check(line) {
                    dedup::DedupResult::Absorbed => {}
                    dedup::DedupResult::FlushStreak { first, count } => {
                        output_lines.push(format!("{first}\n  [\u{22ef} {count} similar lines]"));
                        output_lines.push(line.to_string());
                    }
                    dedup::DedupResult::NotSimilar => {
                        output_lines.push(line.to_string());
                    }
                }
            }
        }
    }

    // Flush any remaining dedup streak
    if let Some(dedup::DedupResult::FlushStreak { first, count }) = dedup_engine.flush()
        && let Some(last) = output_lines.last_mut() {
            if *last == first {
                *last = format!("{first}\n  [\u{22ef} {count} similar lines]");
            } else {
                output_lines.push(format!("{first}\n  [\u{22ef} {count} similar lines]"));
            }
        }

    // Error enrichment: when a command fails, append path/permission diagnostics
    // so the LLM doesn't need to request them in follow-up turns.
    let mut enrich_count: usize = 0;
    if exit_code != 0 {
        let joined = output_lines.join("\n");
        let enrich_lines = enrich::enrich_on_failure(command, &joined, exit_code);
        if !enrich_lines.is_empty() {
            enrich_count = enrich_lines.len();
            output_lines.push(String::new()); // blank separator
            output_lines.push("[ostk:enrich]".to_string());
            output_lines.extend(enrich_lines);
        }
    }

    let total_lines = stripped_lines.len();
    let shown_lines = output_lines.len();
    let compressed = output_lines.join("\n");

    let stats = if total_lines != shown_lines || enrich_count > 0 {
        let mut parts = format!("({total_lines}\u{2192}{shown_lines})");
        parts.push_str(&format!(
            " [grammar:{} H:{hazard_count} O:{outcome_count} S:{stripped_count}]",
            grammar.tool.name,
        ));
        if enrich_count > 0 {
            parts.push_str(&format!(" [enrich:{enrich_count}]"));
        }
        parts
    } else if hazard_count > 0 || outcome_count > 0 {
        format!(
            "[grammar:{} H:{hazard_count} O:{outcome_count}]",
            grammar.tool.name,
        )
    } else {
        String::new()
    };

    // Record token savings (best-effort)
    let original_len = raw.len();
    let compressed_len = compressed.len();
    if original_len != compressed_len {
        record_metrics(original_len, compressed_len);

        let saved_bytes = original_len.saturating_sub(compressed_len) as u64;
        let tokens_saved = saved_bytes / 4;
        if tokens_saved > 0
            && let Some(hs_dir) = find_ostk_dir() {
                let _ = crate::kernel::quota::record_savings(&hs_dir, &crate::kernel::quota::QuotaEvent {
                    tokens_saved,
                    source: "squash-grammar".into(),
                });
            }
    }

    (compressed, stats)
}

/// Category-aware compression pipeline.
///
/// Classifies the command via [`router::classify_command`] and dispatches to
/// the appropriate handler:
///
/// - **Narrate**: generates a narration line for silent commands (cp, mv, mkdir, etc.)
/// - **Dangerous**: formats a warning for destructive commands (rm -rf, git push --force, etc.)
/// - **Passthrough**: passes output through verbatim with a metadata footer
/// - **Structured**: parses machine-readable output into a formatted summary
/// - **Condense** (default): runs the full dedup compression pipeline
///
/// # Arguments
///
/// * `raw`         - The raw command output
/// * `cmd`         - Optional command string for category-aware routing
/// * `exit_code`   - Process exit code (used by narrate and passthrough)
/// * `stderr`      - Stderr output (used by narrate for failure messages)
/// * `duration_ms` - Command duration in milliseconds (used by passthrough footer)
pub fn compress_with_cmd(
    raw: &str,
    cmd: Option<&str>,
    exit_code: i32,
    stderr: &str,
    duration_ms: u64,
) -> (String, String) {
    let category = cmd.map(router::classify_command);

    match category {
        Some(router::Category::Narrate) => {
            let cmd_str = cmd.unwrap_or("");
            let body = narrate::narrate_output(cmd_str, exit_code, stderr);
            let stats = "[narrate]".to_string();
            (body, stats)
        }
        Some(router::Category::Dangerous) => {
            let cmd_str = cmd.unwrap_or("");
            let warning = dangerous::format_dangerous_warning(cmd_str);
            // Still compress the output (if any) and prepend the warning
            let body = if raw.trim().is_empty() {
                warning
            } else {
                let (compressed, _) = compress(raw);
                format!("{warning}\n---\n{compressed}")
            };
            let stats = "[dangerous]".to_string();
            (body, stats)
        }
        Some(router::Category::Passthrough) => {
            let cmd_str = cmd.unwrap_or("");
            let stripped = vte_strip::strip_ansi(raw);
            let body = passthrough::format_passthrough(cmd_str, &stripped, exit_code, duration_ms);
            let stats = "[passthrough]".to_string();
            (body, stats)
        }
        Some(router::Category::Structured) => {
            let cmd_str = cmd.unwrap_or("");
            let stripped = vte_strip::strip_ansi(raw);
            let body = structured::format_structured(cmd_str, &stripped);
            let stats = "[structured]".to_string();
            (body, stats)
        }
        Some(router::Category::Condense) | None => {
            // Fall through to existing condense pipeline
            let (body, mut stats) = compress(raw);
            if let Some(cat) = category {
                if stats.is_empty() {
                    stats = format!("[{cat}]");
                } else {
                    stats = format!("{stats} [{cat}]");
                }
            }
            (body, stats)
        }
    }
}

/// Run the squasher pipeline (3-tier operation):
///
/// - **Tier 1 (full):** VTE strip → Levenshtein (consecutive) → Semantic (buffer-wide)
///   — when `embeddings` feature enabled AND model available
/// - **Tier 2 (template):** VTE strip → Levenshtein — always works
/// - **Tier 3 (minimal):** VTE strip only — if even Levenshtein fails
///
/// Returns (compressed_lines, stats_summary).
///
/// Accepts an optional `cmd` parameter for category-aware routing. When provided,
/// the command is classified via [`router::classify_command`] and the category is
/// Run the squasher pipeline without command classification.
///
/// This is the original entry point. Equivalent to `compress_with_cmd(raw, None, ...)`.
pub fn compress(raw: &str) -> (String, String) {
    compress_inner(raw)
}

/// Inner compression pipeline shared by `compress` and `compress_with_cmd`.
fn compress_inner(raw: &str) -> (String, String) {
    // Step 1: Strip ANSI escape codes
    let stripped = vte_strip::strip_ansi(raw);

    // Step 2: Implicit dedup — collapse consecutive similar lines
    // Load similarity_threshold from ostk.toml if available
    let mut dedup = if let Some(root) = find_project_root() {
        let cfg = crate::config::load_config(&root);
        if let Some(threshold) = cfg
            .squasher
            .and_then(|s| s.dedup)
            .and_then(|d| d.similarity_threshold)
        {
            dedup::ImplicitDedup::with_threshold(threshold)
        } else {
            dedup::ImplicitDedup::new()
        }
    } else {
        dedup::ImplicitDedup::new()
    };
    let mut output_lines: Vec<String> = Vec::new();

    for line in stripped.lines() {
        match dedup.check(line) {
            dedup::DedupResult::Absorbed => {
                // Line merged into current streak — don't emit
            }
            dedup::DedupResult::FlushStreak { first, count } => {
                // Previous streak ended — emit summary, then current line
                output_lines.push(format!("{first}\n  [\u{22ef} {count} similar lines]"));
                output_lines.push(line.to_string());
            }
            dedup::DedupResult::NotSimilar => {
                output_lines.push(line.to_string());
            }
        }
    }

    // Flush any remaining streak
    if let Some(dedup::DedupResult::FlushStreak { first, count }) = dedup.flush() {
        // Replace the last emitted line (which was the first of the streak)
        // with the streak summary
        if let Some(last) = output_lines.last_mut() {
            if *last == first {
                *last = format!("{first}\n  [\u{22ef} {count} similar lines]");
            } else {
                output_lines.push(format!("{first}\n  [\u{22ef} {count} similar lines]"));
            }
        }
    }

    // Step 3: Semantic dedup — batch embed + signal-aware clustering.
    // Classify every line by signal (hazard/warning/outcome/progress/unknown),
    // then cluster with per-signal thresholds so errors are never collapsed but
    // build noise is aggressively deduplicated.
    //
    // Lines are tokenized (normalize paths, versions, hashes) BEFORE embedding so
    // that structurally identical lines (e.g. "Compiling serde v1.0" / "Compiling tokio v1.4")
    // produce identical embeddings. Original lines are preserved for output.
    let pre_semantic_count = output_lines.len();

    #[cfg(feature = "embeddings")]
    let (output_lines, semantic_active) = {
        if crate::squasher::embeddings::model_available() {
            // Normalize lines before embedding (paths, versions, hashes → tokens)
            let normalized: Vec<String> = output_lines.iter()
                .map(|line| dedup::tokenize_for_embedding(line))
                .collect();
            let norm_refs: Vec<&str> = normalized.iter().map(|s| s.as_str()).collect();

            match crate::squasher::embeddings::embed_batch(&norm_refs) {
                Ok(embeds) if !embeds.is_empty() => {
                    // Classify every line by signal
                    let signals = crate::squasher::embeddings::classify_signals_batch(
                        &embeds, 0.40,
                    );
                    match crate::squasher::embeddings::pairwise_similarity(&embeds) {
                        Ok(sim_matrix) => {
                            let clustered = cluster_by_signal(
                                &output_lines, &sim_matrix, &signals,
                            );
                            (clustered, true)
                        }
                        Err(_) => (output_lines, false),
                    }
                }
                _ => (output_lines, false),
            }
        } else {
            (output_lines, false)
        }
    };

    #[cfg(not(feature = "embeddings"))]
    let semantic_active = false;


    let total_lines = stripped.lines().count();
    let shown_lines = output_lines.len();
    let compressed = output_lines.join("\n");

    let stats = if total_lines != shown_lines {
        let base = format!("({total_lines}\u{2192}{shown_lines})");
        if semantic_active {
            let semantic_saved = pre_semantic_count.saturating_sub(shown_lines);
            format!("{base} [semantic: -{semantic_saved}]")
        } else {
            base
        }
    } else {
        String::new()
    };

    // Record token savings (best-effort — ignore any errors)
    let original_len = raw.len();
    let compressed_len = compressed.len();
    if original_len != compressed_len {
        record_metrics(original_len, compressed_len);

        // Record savings to quota (bytes/4 ≈ tokens, conservative estimate)
        let saved_bytes = original_len.saturating_sub(compressed_len) as u64;
        let tokens_saved = saved_bytes / 4;
        if tokens_saved > 0
            && let Some(hs_dir) = find_ostk_dir() {
                let _ = crate::kernel::quota::record_savings(&hs_dir, &crate::kernel::quota::QuotaEvent {
                    tokens_saved,
                    source: "squash".into(),
                });
            }
    }

    (compressed, stats)
}

// ---------------------------------------------------------------------------
// GPU-accelerated signal-aware clustering (embeddings)
// ---------------------------------------------------------------------------

/// Per-signal similarity thresholds.
///
/// Hazard/Outcome lines use a very high threshold (effectively no collapse)
/// so errors and success signals are always preserved. Warning uses moderate
/// dedup. Progress and Unknown are collapsed at the general semantic threshold.
///
/// Note: when the squasher tokenizer normalizes lines before embedding
/// (e.g. `Compiling serde v1.0.210` → `Compiling {path} {ver}`),
/// Progress lines become identical and hit sim=1.0, so the threshold
/// doesn't matter much. For raw text, 0.85 catches the highest-similarity
/// pairs (same-family packages) without false positives.
#[cfg(feature = "embeddings")]
fn signal_threshold(
    signal: crate::squasher::embeddings::signals::Signal,
) -> f32 {
    use crate::squasher::embeddings::signals::Signal;
    match signal {
        Signal::Hazard  => 0.98, // near-identical only (preserve all errors)
        Signal::Outcome => 0.98, // near-identical only (preserve success signals)
        Signal::Warning => 0.95, // collapse near-dupes ("unused x" / "unused y")
        Signal::Progress => 0.85, // same-topic collapse (tokenizer normalization helps)
        Signal::Unknown => 0.85, // default semantic dedup
    }
}

/// Cluster lines using a precomputed N×N pairwise similarity matrix,
/// with per-signal thresholds so hazard/outcome lines are preserved
/// while progress noise is aggressively collapsed.
///
/// Each line's signal classification determines its collapse threshold.
/// A line can only be absorbed into a cluster whose representative has
/// a **compatible signal** (same category or both Unknown) — this prevents
/// an error line from being swallowed into a progress cluster that happens
/// to have high textual similarity.
#[cfg(feature = "embeddings")]
fn cluster_by_signal(
    lines: &[String],
    sim_matrix: &[Vec<f32>],
    signals: &[(crate::squasher::embeddings::signals::Signal, f32)],
) -> Vec<String> {
    use crate::squasher::embeddings::signals::Signal;

    if lines.is_empty() {
        return vec![];
    }

    // clusters: (representative_index, signal, count)
    let mut clusters: Vec<(usize, Signal, u32)> = Vec::new();
    // which cluster each line belongs to
    let mut assignments: Vec<Option<usize>> = vec![None; lines.len()];

    for i in 0..lines.len() {
        let (line_signal, _conf) = signals[i];
        let threshold = signal_threshold(line_signal);

        // Find the best matching existing cluster with compatible signal
        let mut best_sim = -1.0f32;
        let mut best_cluster = None;

        for (ci, &(rep_idx, rep_signal, _)) in clusters.iter().enumerate() {
            // Only merge within same signal category, or if both Unknown
            if !signals_compatible(line_signal, rep_signal) {
                continue;
            }

            let sim = sim_matrix[i][rep_idx];
            if sim >= threshold && sim > best_sim {
                best_sim = sim;
                best_cluster = Some(ci);
            }
        }

        if let Some(ci) = best_cluster {
            // Absorb into existing cluster
            clusters[ci].2 += 1;
            assignments[i] = Some(ci);
        } else {
            // New cluster with this line as representative
            assignments[i] = Some(clusters.len());
            clusters.push((i, line_signal, 1));
        }
    }

    // Emit: one line per cluster, with count annotation if collapsed.
    // Hazard/Outcome clusters always show the annotation so the user
    // knows nothing was silently dropped.
    clusters
        .iter()
        .map(|&(rep_idx, signal, count)| {
            if count > 1 {
                let label = match signal {
                    Signal::Hazard   => "similar errors",
                    Signal::Warning  => "similar warnings",
                    Signal::Outcome  => "similar results",
                    Signal::Progress => "similar lines",
                    Signal::Unknown  => "similar lines",
                };
                format!(
                    "{}\n  [\u{22ef} {} {}]",
                    lines[rep_idx],
                    count - 1,
                    label,
                )
            } else {
                lines[rep_idx].clone()
            }
        })
        .collect()
}

/// Two signals are compatible for clustering if they're the same category,
/// or if either is Unknown (Unknown lines can cluster with anything).
#[cfg(feature = "embeddings")]
fn signals_compatible(
    a: crate::squasher::embeddings::signals::Signal,
    b: crate::squasher::embeddings::signals::Signal,
) -> bool {
    use crate::squasher::embeddings::signals::Signal;
    if a == b { return true; }
    // Unknown can merge with any category
    matches!((a, b), (Signal::Unknown, _) | (_, Signal::Unknown))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_compress_plain_text() {
        let (result, stats) = compress("hello\nworld");
        assert_eq!(result, "hello\nworld");
        assert!(stats.is_empty());
    }

    #[test]
    fn test_compress_with_ansi() {
        let (result, _) = compress("\x1b[31merror\x1b[0m\nok");
        assert_eq!(result, "error\nok");
    }

    #[test]
    fn test_compress_repeated_lines() {
        let input = "Compiling foo v1.0.0\nCompiling bar v2.0.0\nCompiling baz v3.0.0\ndone";
        let (result, stats) = compress(input);
        // Should dedup the Compiling lines
        assert!(result.contains("similar lines") || result.lines().count() < 4);
        if result.lines().count() < 4 {
            assert!(!stats.is_empty());
        }
    }
}
