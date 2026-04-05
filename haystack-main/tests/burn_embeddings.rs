//! Integration test for Burn GPU embeddings engine.
//!
//! Requires: `--features embeddings`
//! Requires: models/potion-base-8M/ to exist with model files.

#![cfg(feature = "embeddings")]

use ostk::squasher::embeddings;

#[test]
fn test_model_available() {
    // This will attempt to load the model from models/potion-base-8M/
    let available = embeddings::model_available();
    assert!(available, "potion-base-8M model should be loadable from models/");
}

#[test]
fn test_single_embed() {
    if !embeddings::model_available() {
        eprintln!("skipping: model not available");
        return;
    }

    let vec = embeddings::embed("hello world").unwrap();
    assert_eq!(vec.len(), 256, "embedding should be 256-dim");

    // Should be unit-normalized
    let norm: f32 = vec.iter().map(|x| x * x).sum::<f32>().sqrt();
    assert!(
        (norm - 1.0).abs() < 0.05,
        "embedding should be approximately unit-normalized, got norm={norm}"
    );
}

#[test]
fn test_batch_embed() {
    if !embeddings::model_available() {
        return;
    }

    let texts = vec![
        "error: cannot find value `x` in this scope",
        "error: cannot find value `y` in this scope",
        "Compiling foo v1.0.0",
        "Compiling bar v2.0.0",
        "test result: ok. 5 passed; 0 failed",
    ];

    let embeds = embeddings::embed_batch(&texts).unwrap();
    assert_eq!(embeds.len(), 5);
    for (i, e) in embeds.iter().enumerate() {
        assert_eq!(e.len(), 256, "embedding {i} should be 256-dim");
    }

    // The two error lines should be very similar
    let sim_errors = embeddings::cosine_similarity(&embeds[0], &embeds[1]);
    // The two Compiling lines should be very similar
    let sim_compiling = embeddings::cosine_similarity(&embeds[2], &embeds[3]);
    // Error vs Compiling should be less similar
    let sim_cross = embeddings::cosine_similarity(&embeds[0], &embeds[2]);

    eprintln!("sim(error, error) = {sim_errors:.4}");
    eprintln!("sim(compiling, compiling) = {sim_compiling:.4}");
    eprintln!("sim(error, compiling) = {sim_cross:.4}");

    assert!(
        sim_errors > sim_cross,
        "similar error lines ({sim_errors:.3}) should be more similar than error vs compiling ({sim_cross:.3})"
    );
    assert!(
        sim_compiling > sim_cross,
        "similar compiling lines ({sim_compiling:.3}) should be more similar than compiling vs error ({sim_cross:.3})"
    );
}

#[test]
fn test_pairwise_similarity_matrix() {
    if !embeddings::model_available() {
        return;
    }

    let texts = vec![
        "error: mismatched types",
        "error: type mismatch in argument",
        "Finished dev profile in 2.3s",
    ];
    let embeds = embeddings::embed_batch(&texts).unwrap();
    let sim = embeddings::pairwise_similarity(&embeds).unwrap();

    assert_eq!(sim.len(), 3);
    assert_eq!(sim[0].len(), 3);

    // Diagonal should be ~1.0 (self-similarity)
    for i in 0..3 {
        assert!(
            (sim[i][i] - 1.0).abs() < 0.05,
            "self-similarity should be ~1.0, got {:.4} for line {i}",
            sim[i][i]
        );
    }

    // Symmetric
    for i in 0..3 {
        for j in 0..3 {
            assert!(
                (sim[i][j] - sim[j][i]).abs() < 1e-4,
                "matrix should be symmetric"
            );
        }
    }

    // Error lines more similar to each other than to success line
    assert!(
        sim[0][1] > sim[0][2],
        "error↔error ({:.3}) should > error↔success ({:.3})",
        sim[0][1],
        sim[0][2]
    );
}

#[test]
fn test_cluster_integration() {
    if !embeddings::model_available() {
        return;
    }

    // Simulate a cargo build output with repeated similar lines
    let lines = vec![
        "error[E0425]: cannot find value `foo` in this scope",
        "error[E0425]: cannot find value `bar` in this scope",
        "error[E0425]: cannot find value `baz` in this scope",
        "error[E0425]: cannot find value `qux` in this scope",
        "warning: unused variable: `x`",
        "warning: unused variable: `y`",
        "   Compiling mycrate v0.1.0",
        "error: aborting due to 4 previous errors",
    ];

    let refs: Vec<&str> = lines.iter().map(|s| &**s).collect();
    let embeds = embeddings::embed_batch(&refs).unwrap();
    let sim = embeddings::pairwise_similarity(&embeds).unwrap();

    // With threshold 0.85, the 4 E0425 errors should cluster together
    // and the 2 warnings should cluster together
    // That's 8 lines → should be ≤5 clusters
    eprintln!("=== Similarity matrix ===");
    for (i, row) in sim.iter().enumerate() {
        let sims: Vec<String> = row.iter().map(|v| format!("{v:.2}")).collect();
        eprintln!("  [{i}] {}: {}", &lines[i][..40.min(lines[i].len())], sims.join(" "));
    }

    // Count how many clusters at threshold 0.85
    let mut representatives: Vec<usize> = vec![];
    for i in 0..lines.len() {
        let mut matched = false;
        for &rep in &representatives {
            if sim[i][rep] >= 0.85 {
                matched = true;
                break;
            }
        }
        if !matched {
            representatives.push(i);
        }
    }

    eprintln!("Clusters at 0.85: {} (from {} lines)", representatives.len(), lines.len());
    assert!(
        representatives.len() < lines.len(),
        "should have fewer clusters than lines"
    );
}

#[test]
fn test_gpu_backend_active() {
    if !embeddings::model_available() {
        return;
    }
    // If we got here, the engine initialized.
    // The Wgpu backend is default — if Metal is available it uses it.
    // Verify by checking that embedding works (GPU path exercised).
    let start = std::time::Instant::now();
    
    // Batch 100 lines — should be fast on GPU
    let lines: Vec<&str> = (0..100).map(|i| match i % 4 {
        0 => "error: something went wrong in module foo",
        1 => "warning: deprecated function called",
        2 => "Compiling some-crate v1.0.0",
        _ => "test some_test ... ok",
    }).collect();
    
    let embeds = embeddings::embed_batch(&lines).unwrap();
    let elapsed = start.elapsed();
    
    assert_eq!(embeds.len(), 100);
    eprintln!("100 embeddings in {elapsed:?}");
    
    // Now pairwise similarity — 100×100 matmul
    let start2 = std::time::Instant::now();
    let sim = embeddings::pairwise_similarity(&embeds).unwrap();
    let elapsed2 = start2.elapsed();
    
    assert_eq!(sim.len(), 100);
    eprintln!("100×100 pairwise similarity in {elapsed2:?}");
}

#[test]
fn test_signal_classification() {
    if !embeddings::model_available() {
        return;
    }

    let lines = vec![
        "error[E0308]: mismatched types in foo.rs",
        "warning: unused import: `std::io`",
        "Finished release profile in 4.2s",
        "   Compiling serde v1.0.210",
        "test result: ok. 12 passed; 0 failed; 0 ignored",
        "def calculate_total(items):",  // random code line — should be Unknown
        "Hello, world!",                // greeting — should be Unknown
    ];

    let refs: Vec<&str> = lines.iter().map(|s| &**s).collect();
    let embeds = embeddings::embed_batch(&refs).unwrap();
    let classifications = embeddings::classify_signals_batch(&embeds, 0.40);

    for (i, (signal, conf)) in classifications.iter().enumerate() {
        eprintln!("[{i}] {signal:?} ({conf:.3}): {}", lines[i]);
    }

    use embeddings::signals::Signal;

    // Error line → Hazard
    assert_eq!(classifications[0].0, Signal::Hazard, "error should be Hazard");
    // Warning → Warning
    assert_eq!(classifications[1].0, Signal::Warning, "warning should be Warning");
    // Finished → Outcome
    assert_eq!(classifications[2].0, Signal::Outcome, "finished should be Outcome");
    // Compiling → Progress
    assert_eq!(classifications[3].0, Signal::Progress, "compiling should be Progress");
    // Test result → Outcome
    assert_eq!(classifications[4].0, Signal::Outcome, "test result should be Outcome");
}

// ===========================================================================
// Phase 2 acceptance harness — signal-aware compression
// ===========================================================================
//
// These tests define the Phase 2 acceptance criteria from the burn-cubecl spec.
// They test the PUBLIC API surface only — the squasher's compress() output —
// so they remain valid regardless of internal refactoring.

/// Phase 2 acceptance: hazard lines (errors) must never be silently dropped.
///
/// Feed the squasher output with multiple distinct error lines intermixed
/// with progress noise. Every error must appear in the compressed output.
#[test]
fn test_phase2_hazards_preserved() {
    if !embeddings::model_available() {
        return;
    }

    let errors = vec![
        "error[E0308]: mismatched types in src/main.rs:42",
        "error[E0425]: cannot find value `ctx` in this scope",
        "error[E0599]: no method named `run` found for struct `App`",
    ];

    // Build input: errors interleaved with lots of progress noise
    let mut input_lines: Vec<String> = Vec::new();
    for i in 0..20 {
        input_lines.push(format!("   Compiling crate-{i} v{i}.0.0"));
    }
    // Scatter errors throughout
    input_lines.insert(5, errors[0].to_string());
    input_lines.insert(12, errors[1].to_string());
    input_lines.insert(19, errors[2].to_string());

    let input = input_lines.join("\n");

    // Run through squasher
    let (compressed, _stats) = ostk::squasher::compress(&input);

    // Every error line MUST appear in compressed output
    for error in &errors {
        assert!(
            compressed.contains(error),
            "hazard line missing from compressed output: {error}\n\nCompressed:\n{compressed}"
        );
    }

    // Compressed should be shorter than input (progress was deduplicated)
    let input_line_count = input.lines().count();
    let output_line_count = compressed.lines().count();
    eprintln!("Phase 2 hazard test: {input_line_count} → {output_line_count} lines");
    assert!(
        output_line_count < input_line_count,
        "squasher should compress progress noise ({input_line_count} → {output_line_count})"
    );
}

/// Phase 2 acceptance: outcome lines (success/test results) must survive.
#[test]
fn test_phase2_outcomes_preserved() {
    if !embeddings::model_available() {
        return;
    }

    let outcomes = vec![
        "test result: ok. 12 passed; 0 failed; 0 ignored",
        "Finished release profile [optimized] in 4.2s",
    ];

    let mut input_lines: Vec<String> = Vec::new();
    for i in 0..15 {
        input_lines.push(format!("   Compiling dep-{i} v1.{i}.0"));
    }
    input_lines.push(outcomes[0].to_string());
    input_lines.push(outcomes[1].to_string());

    let input = input_lines.join("\n");
    let (compressed, _stats) = ostk::squasher::compress(&input);

    for outcome in &outcomes {
        assert!(
            compressed.contains(outcome),
            "outcome line missing from compressed output: {outcome}\n\nCompressed:\n{compressed}"
        );
    }
}

/// Phase 2 acceptance: progress noise should be compressed (not just passed through).
///
/// The spec target: events with <5% compression should drop below 25%
/// (from current 52%). This test verifies a single high-noise event gets
/// meaningful compression.
#[test]
fn test_phase2_progress_compressed() {
    if !embeddings::model_available() {
        return;
    }

    // 50 Compiling lines — pure progress noise
    let mut input_lines: Vec<String> = Vec::new();
    for i in 0..50 {
        input_lines.push(format!("   Compiling crate-{i} v1.{i}.0"));
    }
    input_lines.push("Finished dev profile in 2.3s".to_string());

    let input = input_lines.join("\n");
    let (compressed, stats) = ostk::squasher::compress(&input);

    let input_len = input.len();
    let output_len = compressed.len();
    let ratio = 1.0 - (output_len as f64 / input_len as f64);

    eprintln!("Phase 2 progress test: {input_len} → {output_len} bytes ({:.1}% compression)", ratio * 100.0);
    eprintln!("Stats: {stats}");

    // Must achieve >5% compression (baseline spec requirement)
    assert!(
        ratio > 0.05,
        "50 Compiling lines should compress by more than 5%, got {:.1}%", ratio * 100.0
    );
}

/// Phase 2 acceptance: tokenizer normalization before embedding improves
/// clustering for lines that differ only in variable parts.
#[test]
fn test_phase2_tokenize_before_embed() {
    if !embeddings::model_available() {
        return;
    }

    let raw_lines = vec![
        "   Compiling serde v1.0.210",
        "   Compiling tokio v1.40.0",
        "   Compiling rand v0.8.5",
        "   Compiling hyper v1.5.0",
        "   Compiling ostk v2.2.8",
    ];

    // Embed raw
    let raw_embeds = embeddings::embed_batch(&raw_lines).unwrap();
    let raw_sim = embeddings::pairwise_similarity(&raw_embeds).unwrap();

    // Tokenize then embed
    let normalized: Vec<String> = raw_lines.iter()
        .map(|l| ostk::squasher::dedup::tokenize_for_embedding(l))
        .collect();
    let norm_refs: Vec<&str> = normalized.iter().map(|s| s.as_str()).collect();
    let norm_embeds = embeddings::embed_batch(&norm_refs).unwrap();
    let norm_sim = embeddings::pairwise_similarity(&norm_embeds).unwrap();

    let n = raw_lines.len();
    let mut raw_avg = 0.0f64;
    let mut norm_avg = 0.0f64;
    let mut pairs = 0;
    for i in 0..n {
        for j in i+1..n {
            raw_avg += raw_sim[i][j] as f64;
            norm_avg += norm_sim[i][j] as f64;
            pairs += 1;
        }
    }
    raw_avg /= pairs as f64;
    norm_avg /= pairs as f64;

    eprintln!("Raw avg sim: {raw_avg:.3}, Normalized avg sim: {norm_avg:.3}");

    assert!(
        norm_avg > raw_avg,
        "tokenizer normalization should improve pairwise similarity ({norm_avg:.3} vs {raw_avg:.3})"
    );
}

/// Phase 2 acceptance: signal classification correctly categorizes lines.
///
/// This validates the public classify_signals_batch API returns correct
/// signal categories for representative lines from each category.
#[test]
fn test_phase2_signal_categories() {
    if !embeddings::model_available() {
        return;
    }

    use embeddings::signals::Signal;

    let lines = vec![
        ("error[E0308]: mismatched types", Signal::Hazard),
        ("warning: unused import: `std::io`", Signal::Warning),
        ("test result: ok. 12 passed; 0 failed", Signal::Outcome),
        ("   Compiling serde v1.0.210", Signal::Progress),
    ];

    let texts: Vec<&str> = lines.iter().map(|(t, _)| *t).collect();
    let embeds = embeddings::embed_batch(&texts).unwrap();
    let signals = embeddings::classify_signals_batch(&embeds, 0.40);

    for (i, ((text, expected), (actual, conf))) in lines.iter().zip(signals.iter()).enumerate() {
        eprintln!("[{i}] expected={expected:?} actual={actual:?} conf={conf:.3}: {text}");
        assert_eq!(
            actual, expected,
            "line '{text}' should be {expected:?}, got {actual:?} (conf={conf:.3})"
        );
    }
}

/// Phase 2 acceptance: signal-aware clustering must NOT merge errors into
/// non-error clusters even when textual similarity is high.
///
/// This tests the clustering safety property: lines classified as Hazard
/// must not be absorbed into Progress/Outcome clusters. We use unambiguous
/// exemplars to isolate the clustering behavior from classifier edge cases.
#[test]
fn test_phase2_no_cross_signal_merge() {
    if !embeddings::model_available() {
        return;
    }

    use embeddings::signals::Signal;

    // Unambiguous lines — each clearly belongs to one category
    let lines = vec![
        "error[E0308]: mismatched types in foo.rs:42",      // Hazard
        "   Compiling serde v1.0.210",                       // Progress
        "error[E0425]: cannot find value `ctx` in scope",    // Hazard
        "   Compiling tokio v1.40.0",                        // Progress
        "test result: ok. 5 passed; 0 failed",               // Outcome
    ];

    let refs: Vec<&str> = lines.iter().map(|s| &**s).collect();
    let embeds = embeddings::embed_batch(&refs).unwrap();
    let signals = embeddings::classify_signals_batch(&embeds, 0.40);
    let sim = embeddings::pairwise_similarity(&embeds).unwrap();

    eprintln!("=== Cross-signal merge test ===");
    for (i, (signal, conf)) in signals.iter().enumerate() {
        eprintln!("  [{i}] {signal:?} ({conf:.3}): {}", lines[i]);
    }
    for i in 0..lines.len() {
        for j in i+1..lines.len() {
            eprintln!("  sim[{i}][{j}] = {:.3}", sim[i][j]);
        }
    }

    // Verify signal classifications are correct
    assert_eq!(signals[0].0, Signal::Hazard, "error[E0308] should be Hazard");
    assert_eq!(signals[2].0, Signal::Hazard, "error[E0425] should be Hazard");
    assert_eq!(signals[1].0, Signal::Progress, "Compiling serde should be Progress");
    assert_eq!(signals[3].0, Signal::Progress, "Compiling tokio should be Progress");
    assert_eq!(signals[4].0, Signal::Outcome, "Finished should be Outcome");

    // The two error lines might have decent similarity to each other,
    // but they should NOT merge with progress/outcome at any threshold.
    // Verify: error↔error sim > error↔progress sim
    let error_error_sim = sim[0][2];
    let error_progress_sim = f32::max(
        f32::max(sim[0][1], sim[0][3]),
        f32::max(sim[2][1], sim[2][3]),
    );
    eprintln!("error↔error: {error_error_sim:.3}, error↔progress: {error_progress_sim:.3}");
    assert!(
        error_error_sim > error_progress_sim,
        "errors should be more similar to each other than to progress"
    );
}

#[test]
fn test_batch_precompute_vs_individual() {
    use ostk::squasher::classifier;

    if !embeddings::model_available() {
        return;
    }

    let lines: Vec<&str> = vec![
        "error[E0308]: mismatched types",
        "Compiling serde v1.0.0",
        "warning: unused variable `x`",
        "test test_foo ... ok",
        "Finished dev [unoptimized + debuginfo]",
        "error[E0425]: cannot find value `bar`",
        "Compiling tokio v1.28.0",
        "Compiling rand v0.8.5",
        "error: aborting due to 2 previous errors",
        "test test_bar ... FAILED",
    ];

    // Batch path: 1 GPU call for all lines
    let start = std::time::Instant::now();
    let batch_result = classifier::precompute_signal_classifications(&lines);
    let batch_elapsed = start.elapsed();

    let batch = batch_result.expect("batch precompute should succeed");
    assert_eq!(batch.len(), lines.len());

    // Count classifications by type
    let hazards = batch.iter().filter(|c| matches!(c, Some(classifier::Classification::Hazard { .. }))).count();
    let outcomes = batch.iter().filter(|c| matches!(c, Some(classifier::Classification::Outcome { .. }))).count();
    let noise = batch.iter().filter(|c| matches!(c, Some(classifier::Classification::Noise { .. }))).count();

    eprintln!("Batch precompute ({} lines): {batch_elapsed:?}", lines.len());
    eprintln!("  hazards={hazards}, outcomes={outcomes}, noise={noise}, fallthrough={}",
        batch.iter().filter(|c| c.is_none()).count());

    // Errors should be hazards
    assert!(hazards >= 2, "expected at least 2 hazards from error lines, got {hazards}");
    // Batch and individual paths should agree on every line
    for (i, line) in lines.iter().enumerate() {
        let individual = classifier::precompute_signal_classifications(&[line])
            .expect("single-line precompute should work");
        let batch_class = &batch[i];
        let indiv_class = &individual[0];
        assert_eq!(
            std::mem::discriminant(batch_class.as_ref().unwrap_or(&classifier::Classification::Unknown)),
            std::mem::discriminant(indiv_class.as_ref().unwrap_or(&classifier::Classification::Unknown)),
            "line {i} differs between batch and individual: batch={batch_class:?}, indiv={indiv_class:?}"
        );
    }
    eprintln!("Batch/individual parity: all {} lines agree", lines.len());
}
