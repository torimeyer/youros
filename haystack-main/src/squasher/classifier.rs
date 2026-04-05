//! Stateless three-tier line classifier for ostk.
//!
//! Unlike mish's streaming state machine, ostk captures complete output
//! then classifies each line independently. The classifier is stateless per-line:
//! `classify_line(line, raw_line, grammar, action) -> Classification`.
//!
//! ## Tier evaluation order
//!
//! 1. **Grammar rules** (if grammar + action provided): hazard → outcome → action noise → global noise → inherited
//! 2. **Universal patterns** (always active): ANSI color + keywords, error/warning/success patterns, stack traces
//! 3. **Structural heuristics** (fallback): decorative lines, empty lines, default Unknown

use std::collections::HashMap;
use std::sync::LazyLock;

use regex::Regex;

use super::grammar::{Action, Grammar, Rule, RuleAction, Severity};

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/// How a noise line should be handled.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NoiseAction {
    /// Remove the line entirely.
    Strip,
    /// Collapse consecutive similar lines into one with a count.
    Dedup,
}

/// Classification result for a single line.
#[derive(Debug, Clone)]
pub enum Classification {
    Hazard {
        severity: Severity,
        captures: HashMap<String, String>,
    },
    Outcome {
        captures: HashMap<String, String>,
    },
    Noise {
        action: NoiseAction,
    },
    Unknown,
}

// ---------------------------------------------------------------------------
// Universal patterns — Tier 2 (compiled once via LazyLock)
// ---------------------------------------------------------------------------

/// Error keywords anchored at line start.
static ERROR_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^(error|ERROR|FAIL|FAILED|panic|fatal)[:\s\[]").unwrap());

/// Warning keywords anchored at line start.
static WARNING_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^(warn|warning|WARNING|deprecat)[:\s!]").unwrap());

/// Error keywords anywhere in line (weaker signal).
static ERROR_ANYWHERE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)(command not found|permission denied|No such file or directory|ENOENT|EACCES|EPERM|ECONNREFUSED|segmentation fault|SIGSEGV|out of memory|ModuleNotFoundError|ImportError|SyntaxError)").unwrap()
});

/// Warning keywords anywhere in line.
static WARNING_ANYWHERE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\bdeprecated\b").unwrap());

/// Success keywords (used in combination with green ANSI).
static SUCCESS_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\b(success|succeeded|passed|complete|done|ok|ready)\b").unwrap());

/// Stack trace header patterns.
static STACK_TRACE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?:^Traceback \(most recent call last\)|^\s+at\s+\S+\s+\(|^panic:|^goroutine \d+|^Caused by:|^\s+File .+, line \d+|^\s+\d+:\s+0x[0-9a-f]+\s+-\s+)").unwrap()
});

/// Decorative/separator lines (all `=`, `-`, `*`, `─`, `━`, etc.).
static DECORATIVE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[\s=\-*─━_#~.·•┌┐└┘├┤┬┴┼│]+$").unwrap());

/// Empty or whitespace-only lines.
static WHITESPACE_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\s*$").unwrap());

// ---------------------------------------------------------------------------
// ANSI color detection helpers (for raw_line)
// ---------------------------------------------------------------------------

/// Check if the raw (pre-strip) line contains red ANSI codes.
fn has_red_ansi(raw: &str) -> bool {
    raw.contains("\x1b[31m") || raw.contains("\x1b[1;31m") || raw.contains("\x1b[91m")
}

/// Check if the raw (pre-strip) line contains yellow ANSI codes.
fn has_yellow_ansi(raw: &str) -> bool {
    raw.contains("\x1b[33m") || raw.contains("\x1b[1;33m") || raw.contains("\x1b[93m")
}

/// Check if the raw (pre-strip) line contains green ANSI codes.
fn has_green_ansi(raw: &str) -> bool {
    raw.contains("\x1b[32m") || raw.contains("\x1b[1;32m") || raw.contains("\x1b[92m")
}

/// Check if cleaned text contains error-like keywords (for color-boosted detection).
fn has_error_keywords(text: &str) -> bool {
    ERROR_RE.is_match(text) || ERROR_ANYWHERE_RE.is_match(text)
}

/// Check if cleaned text contains warning-like keywords (for color-boosted detection).
fn has_warning_keywords(text: &str) -> bool {
    WARNING_RE.is_match(text) || WARNING_ANYWHERE_RE.is_match(text)
}

/// Check if cleaned text contains success-like keywords (for color-boosted detection).
fn has_success_keywords(text: &str) -> bool {
    SUCCESS_RE.is_match(text)
}

// ---------------------------------------------------------------------------
// Capture extraction helper
// ---------------------------------------------------------------------------

fn extract_captures(
    pattern: &Regex,
    text: &str,
    capture_names: &[String],
) -> HashMap<String, String> {
    let mut map = HashMap::new();
    if let Some(caps) = pattern.captures(text) {
        for name in capture_names {
            if let Some(m) = caps.name(name) {
                map.insert(name.clone(), m.as_str().to_string());
            }
        }
    }
    map
}

// ---------------------------------------------------------------------------
// Main classification function
// ---------------------------------------------------------------------------

/// Classify a single line of shell output.
///
/// # Arguments
///
/// * `line`     — VTE-stripped clean text (no ANSI codes)
/// * `raw_line` — original text with ANSI codes (for Tier 2 color detection)
/// * `grammar`  — optional grammar for the detected tool
/// * `action`   — optional resolved action within the grammar
///
/// # Returns
///
/// A `Classification` indicating how the line should be treated.
pub fn classify_line(
    line: &str,
    raw_line: &str,
    grammar: Option<&Grammar>,
    action: Option<&Action>,
) -> Classification {
    // No pre-computed signal — classify_line_with_signal will fall back to
    // per-line GPU path if embeddings are available.
    classify_line_with_signal(line, raw_line, grammar, action, None)
}

/// Classify a line using GPU embedding signal extraction.
///
/// Maps `Signal` categories to `Classification`:
/// - Hazard → Classification::Hazard
/// - Warning → Classification::Unknown (keep, but allow dedup)
/// - Outcome → Classification::Outcome
/// - Progress → Classification::Noise(Dedup)
/// - Unknown → None (fall through to regex tiers)
#[cfg(feature = "embeddings")]
fn classify_by_signal(line: &str) -> Option<Classification> {
    let embeds = crate::squasher::embeddings::embed_batch(&[line]).ok()?;
    if embeds.is_empty() { return None; }

    let signals = crate::squasher::embeddings::classify_signals_batch(&embeds, 0.40);
    let (signal, _conf) = signals.first()?;

    signal_to_classification(signal)
}

/// Convert a pre-computed signal to a classification.
/// Shared by both per-line and batch paths.
#[cfg(feature = "embeddings")]
fn signal_to_classification(signal: &crate::squasher::embeddings::signals::Signal) -> Option<Classification> {
    use crate::squasher::embeddings::signals::Signal;

    match signal {
        Signal::Hazard => Some(Classification::Hazard {
            severity: crate::squasher::grammar::Severity::Error,
            captures: HashMap::new(),
        }),
        Signal::Outcome => Some(Classification::Outcome {
            captures: HashMap::new(),
        }),
        Signal::Progress => Some(Classification::Noise {
            action: NoiseAction::Dedup,
        }),
        Signal::Warning => None, // let regex tiers handle for finer classification
        Signal::Unknown => None, // fall through to regex
    }
}

/// Pre-compute signal classifications for all lines in a single batch GPU call.
///
/// Returns a Vec of Option<Classification> — one per input line.
/// Lines that got Tier 1 grammar matches should still be classified there;
/// the caller should only use these for lines that would otherwise fall through
/// to Tier 1.5.
///
/// This replaces N individual embed_batch(&[line]) calls with 1 batch call,
/// dramatically improving GPU utilization.
#[cfg(feature = "embeddings")]
pub fn precompute_signal_classifications(lines: &[&str]) -> Option<Vec<Option<Classification>>> {
    if !crate::squasher::embeddings::model_available() {
        return None;
    }

    let embeds = crate::squasher::embeddings::embed_batch(lines).ok()?;
    if embeds.is_empty() { return None; }

    let signals = crate::squasher::embeddings::classify_signals_batch(&embeds, 0.40);

    Some(signals.iter()
        .map(|(signal, _conf)| signal_to_classification(signal))
        .collect())
}

/// Classify a line, using a pre-computed signal classification if available.
///
/// When `precomputed_signal` is Some, skips the GPU embedding call entirely.
/// When None, falls through to regex tiers as usual.
pub fn classify_line_with_signal(
    line: &str,
    raw_line: &str,
    grammar: Option<&Grammar>,
    action: Option<&Action>,
    precomputed_signal: Option<&Option<Classification>>,
) -> Classification {
    // Tier 1: Grammar rules (if grammar + action provided)
    if let Some(g) = grammar
        && let Some(c) = classify_tier1(line, g, action) {
            return c;
        }

    // Tier 1.5: Use pre-computed signal if available
    if let Some(signal_result) = precomputed_signal {
        if let Some(c) = signal_result.clone() {
            return c;
        }
        // Signal was Unknown/Warning — fall through to regex tiers
    } else {
        // No pre-computed signals — try per-line GPU path (legacy fallback)
        #[cfg(feature = "embeddings")]
        if crate::squasher::embeddings::model_available() {
            if let Some(c) = classify_by_signal(line) {
                return c;
            }
        }
    }

    // Tier 2: Universal patterns (always active — fallback when no embeddings)
    if let Some(c) = classify_tier2(line, raw_line) {
        return c;
    }

    // Tier 3: Structural heuristics (fallback)
    if let Some(c) = classify_tier3(line) {
        return c;
    }

    // No match — Unknown
    Classification::Unknown
}

// ---------------------------------------------------------------------------
// Tier 1: Grammar rule evaluation
// ---------------------------------------------------------------------------

fn classify_tier1(line: &str, grammar: &Grammar, action: Option<&Action>) -> Option<Classification> {
    // Collect rule sets: action-specific first, then global, then inherited (already merged)
    let (hazard_rules, outcome_rules, action_noise_rules) = if let Some(a) = action {
        (&a.hazard, &a.outcome, &a.noise)
    } else if let Some(ref fb) = grammar.fallback {
        (&fb.hazard, &fb.outcome, &fb.noise)
    } else {
        // Grammar exists but no action and no fallback — only global noise applies
        return classify_global_noise(line, &grammar.global_noise);
    };

    // 1a. Hazard rules (errors must never be missed — check first)
    for rule in hazard_rules {
        if rule.pattern.is_match(line) {
            let captures = extract_captures(&rule.pattern, line, &rule.captures);
            let severity = rule.severity.unwrap_or(Severity::Error);
            return Some(Classification::Hazard {
                severity,
                captures,
            });
        }
    }

    // 1b. Outcome rules
    for rule in outcome_rules {
        if rule.pattern.is_match(line) {
            let captures = extract_captures(&rule.pattern, line, &rule.captures);
            return Some(Classification::Outcome { captures });
        }
    }

    // 1c. Action-specific noise rules
    for rule in action_noise_rules {
        if rule.pattern.is_match(line) {
            let noise_action = match rule.action {
                RuleAction::Strip => NoiseAction::Strip,
                RuleAction::Dedup => NoiseAction::Dedup,
                _ => NoiseAction::Strip,
            };
            return Some(Classification::Noise {
                action: noise_action,
            });
        }
    }

    // 1d. Global noise rules (includes inherited rules after resolve_inherit)
    classify_global_noise(line, &grammar.global_noise)
}

fn classify_global_noise(line: &str, global_noise: &[Rule]) -> Option<Classification> {
    for rule in global_noise {
        if rule.pattern.is_match(line) {
            let noise_action = match rule.action {
                RuleAction::Strip => NoiseAction::Strip,
                RuleAction::Dedup => NoiseAction::Dedup,
                _ => NoiseAction::Strip,
            };
            return Some(Classification::Noise {
                action: noise_action,
            });
        }
    }
    None
}

// ---------------------------------------------------------------------------
// Tier 2: Universal patterns
// ---------------------------------------------------------------------------

fn classify_tier2(line: &str, raw_line: &str) -> Option<Classification> {
    // 2a. ANSI color + content keywords (strongest signal when color present)
    if has_red_ansi(raw_line) && has_error_keywords(line) {
        return Some(Classification::Hazard {
            severity: Severity::Error,
            captures: HashMap::new(),
        });
    }

    if has_yellow_ansi(raw_line) && has_warning_keywords(line) {
        return Some(Classification::Hazard {
            severity: Severity::Warning,
            captures: HashMap::new(),
        });
    }

    if has_green_ansi(raw_line) && has_success_keywords(line) {
        return Some(Classification::Outcome {
            captures: HashMap::new(),
        });
    }

    // 2b. Error keywords (line-start anchored — strongest textual signal)
    if ERROR_RE.is_match(line) {
        return Some(Classification::Hazard {
            severity: Severity::Error,
            captures: HashMap::new(),
        });
    }

    // 2c. Warning keywords
    if WARNING_RE.is_match(line) {
        return Some(Classification::Hazard {
            severity: Severity::Warning,
            captures: HashMap::new(),
        });
    }

    // 2d. Stack trace headers
    if STACK_TRACE_RE.is_match(line) {
        return Some(Classification::Hazard {
            severity: Severity::Error,
            captures: HashMap::new(),
        });
    }

    None
}

// ---------------------------------------------------------------------------
// Tier 3: Structural heuristics
// ---------------------------------------------------------------------------

fn classify_tier3(line: &str) -> Option<Classification> {
    // 3a. Empty or whitespace-only
    if WHITESPACE_RE.is_match(line) {
        return Some(Classification::Noise {
            action: NoiseAction::Strip,
        });
    }

    // 3b. Decorative/separator lines
    if DECORATIVE_RE.is_match(line) {
        return Some(Classification::Noise {
            action: NoiseAction::Strip,
        });
    }

    None
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::squasher::grammar::load_grammar_from_str;

    /// Helper: load the cargo grammar.
    fn cargo_grammar() -> Grammar {
        let toml_str = include_str!("grammars/toml/cargo.toml");
        load_grammar_from_str("cargo", toml_str).unwrap()
    }

    /// Helper: load the npm grammar.
    fn npm_grammar() -> Grammar {
        let toml_str = include_str!("grammars/toml/npm.toml");
        load_grammar_from_str("npm", toml_str).unwrap()
    }

    #[test]
    fn test_classify_cargo_error() {
        let grammar = cargo_grammar();
        let action = grammar.actions.get("build").unwrap();
        let line = "error[E0308]: mismatched types";
        let result = classify_line(line, line, Some(&grammar), Some(action));
        match result {
            Classification::Hazard { severity, captures, .. } => {
                assert_eq!(severity, Severity::Error);
                assert_eq!(captures.get("code").map(|s| s.as_str()), Some("E0308"));
                assert_eq!(
                    captures.get("msg").map(|s| s.as_str()),
                    Some("mismatched types")
                );
            }
            other => panic!("expected Hazard(Error), got {:?}", other),
        }
    }

    #[test]
    fn test_classify_npm_noise() {
        let grammar = npm_grammar();
        let action = grammar.actions.get("install").unwrap();
        let line = "npm warn deprecated something@1.0.0";
        let result = classify_line(line, line, Some(&grammar), Some(action));
        match result {
            Classification::Noise { action, .. } => {
                assert_eq!(action, NoiseAction::Dedup);
            }
            other => panic!("expected Noise(Dedup), got {:?}", other),
        }
    }

    #[test]
    fn test_classify_universal_error_no_grammar() {
        let line = "ERROR: something failed";
        let result = classify_line(line, line, None, None);
        match result {
            Classification::Hazard { severity, .. } => {
                assert_eq!(severity, Severity::Error);
            }
            other => panic!("expected Hazard(Error) via Tier 2, got {:?}", other),
        }
    }

    #[test]
    fn test_classify_decorative_line() {
        let line = "============================";
        let result = classify_line(line, line, None, None);
        match result {
            Classification::Noise { action, .. } => {
                assert_eq!(action, NoiseAction::Strip);
            }
            other => panic!("expected Noise(Strip) via Tier 3, got {:?}", other),
        }
    }

    #[test]
    fn test_classify_plain_text() {
        let line = "hello world";
        let result = classify_line(line, line, None, None);
        assert!(
            matches!(result, Classification::Unknown),
            "expected Unknown, got {:?}",
            result
        );
    }

    #[test]
    fn test_classify_empty_line() {
        let line = "";
        let result = classify_line(line, line, None, None);
        match result {
            Classification::Noise { action, .. } => {
                assert_eq!(action, NoiseAction::Strip);
            }
            other => panic!("expected Noise(Strip), got {:?}", other),
        }
    }

    #[test]
    fn test_hazard_priority_over_noise() {
        // Construct a grammar where a line matches both a hazard and a noise rule.
        // Hazard must win because Tier 1 checks hazard before noise.
        let toml_str = r#"
[tool]
name = "test-tool"

[fallback]
detect = []

[[fallback.hazard]]
pattern = "^CRITICAL"
severity = "error"
action = "keep"

[[fallback.noise]]
pattern = "^CRITICAL"
action = "strip"
"#;
        let grammar = load_grammar_from_str("test-tool", toml_str).unwrap();
        let line = "CRITICAL: system failure";
        let result = classify_line(line, line, Some(&grammar), None);
        match result {
            Classification::Hazard { severity, .. } => {
                assert_eq!(severity, Severity::Error);
            }
            other => panic!("expected Hazard(Error) over Noise, got {:?}", other),
        }
    }

    #[test]
    fn test_classify_ansi_red_error() {
        // Red ANSI + "error" keyword → Hazard(Error) via Tier 2
        let raw = "\x1b[31merror: something broke\x1b[0m";
        let clean = "error: something broke";
        let result = classify_line(clean, raw, None, None);
        match result {
            Classification::Hazard { severity, .. } => {
                assert_eq!(severity, Severity::Error);
            }
            other => panic!("expected Hazard(Error) via ANSI color, got {:?}", other),
        }
    }

    #[test]
    fn test_classify_green_success() {
        // Green ANSI + "success" keyword → Outcome via Tier 2
        let raw = "\x1b[32mBuild success\x1b[0m";
        let clean = "Build success";
        let result = classify_line(clean, raw, None, None);
        match result {
            Classification::Outcome { .. } => {}
            other => panic!("expected Outcome via green ANSI, got {:?}", other),
        }
    }

    #[test]
    fn test_classify_stack_trace() {
        let line = "Traceback (most recent call last):";
        let result = classify_line(line, line, None, None);
        match result {
            Classification::Hazard { severity, .. } => {
                assert_eq!(severity, Severity::Error);
            }
            other => panic!("expected Hazard(Error) for stack trace, got {:?}", other),
        }
    }

    #[test]
    fn test_classify_warning_keyword() {
        let line = "warning: unused variable `x`";
        let result = classify_line(line, line, None, None);
        match result {
            Classification::Hazard { severity, .. } => {
                assert_eq!(severity, Severity::Warning);
            }
            other => panic!("expected Hazard(Warning), got {:?}", other),
        }
    }

    #[test]
    fn test_classify_whitespace_only() {
        let line = "   \t  ";
        let result = classify_line(line, line, None, None);
        match result {
            Classification::Noise { action, .. } => {
                assert_eq!(action, NoiseAction::Strip);
            }
            other => panic!("expected Noise(Strip) for whitespace, got {:?}", other),
        }
    }
}
