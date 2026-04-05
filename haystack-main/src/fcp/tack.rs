//! fcp-ostk — device driver for human intent.
//!
//! Parses tack grammar (`:verb`, `.? query`, `→NNN`, `:: target`) and resolves
//! to ostk commands. Static verb table + HUMANFILE extension point.

use std::collections::HashMap;
use std::path::Path;

use crate::language::parse_language;

/// Intent type derived from tack prefix.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TackIntent {
    /// `:verb [args]` — command intent
    Command,
    /// `.? [query]` — query intent
    Query,
    /// `→NNN` — needle reference
    NeedleRef,
    /// `:: [target]` — escalate / nudge
    Escalate,
}

impl TackIntent {
    pub fn as_str(&self) -> &'static str {
        match self {
            TackIntent::Command => "command",
            TackIntent::Query => "query",
            TackIntent::NeedleRef => "needle_ref",
            TackIntent::Escalate => "escalate",
        }
    }
}

/// A parsed tack expression.
#[derive(Debug, Clone)]
pub struct TackParse {
    pub intent: TackIntent,
    pub verb: String,
    pub args: Vec<String>,
}

/// Resolution result from the verb table.
#[derive(Debug, Clone)]
pub struct TackResolution {
    pub resolved: bool,
    pub verb: String,
    pub intent: TackIntent,
    pub command: Option<String>,
    pub args: Vec<String>,
    pub source: ResolveSource,
    pub tier: u8,
    pub confidence: f64,
    pub suggestions: Vec<String>,
}

/// →597: Tack Linter (Tier 0) result.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TackLint {
    Ok,
    UnknownVerb(String),
    MalformedSequence,
    HallucinationRisk,
}

impl TackLint {
    pub fn is_ok(&self) -> bool {
        matches!(self, TackLint::Ok)
    }
}

/// →597: Perform static Tier 0 analysis on raw input.
pub fn lint(input: &str, ostk_dir: &Path) -> TackLint {
    let parsed = match parse(input) {
        Some(p) => p,
        None => return TackLint::MalformedSequence,
    };

    if matches!(parsed.intent, TackIntent::Command) {
        let lang = load_language_verbs(ostk_dir);
        let hf = load_humanfile_verbs(ostk_dir);
        let table = static_verb_table();
        let verb = parsed.verb.to_lowercase();

        if !lang.contains_key(&verb) && !hf.contains_key(&verb) && !table.contains_key(verb.as_str()) {
            return TackLint::UnknownVerb(parsed.verb);
        }
    }

    TackLint::Ok
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ResolveSource {
    Language,
    Static,
    Humanfile,
    Inferred,
}

impl ResolveSource {
    pub fn as_str(&self) -> &'static str {
        match self {
            ResolveSource::Language => "language",
            ResolveSource::Static => "static",
            ResolveSource::Humanfile => "humanfile",
            ResolveSource::Inferred => "inferred",
        }
    }
}

/// The static verb→command mapping (synced with full CLI surface).
fn static_verb_table() -> HashMap<&'static str, &'static str> {
    let mut m = HashMap::new();

    // ── 1:1 CLI commands ──
    m.insert("init", "init");
    m.insert("install", "install");
    m.insert("boot", "boot");
    m.insert("compile", "compile");
    m.insert("hay", "hay");
    m.insert("thread", "thread");
    m.insert("nudge", "nudge");
    m.insert("run", "run");
    m.insert("show", "show");
    m.insert("log", "log");
    m.insert("history", "history");
    m.insert("shutdown", "shutdown");
    m.insert("import", "import");
    m.insert("ps", "ps");
    m.insert("status", "status");
    m.insert("spawn", "spawn");
    m.insert("needle", "needle add");
    m.insert("reap", "reap");
    m.insert("commit", "commit");
    m.insert("draft", "draft");
    m.insert("promote", "promote");
    m.insert("decompose", "decompose");
    m.insert("trace", "trace");
    m.insert("amend", "amend");
    m.insert("shelve", "shelve");
    m.insert("unshelve", "unshelve");
    m.insert("diff", "diff");
    m.insert("post", "post");
    m.insert("merge", "merge");
    m.insert("secret", "secret");
    m.insert("serve", "serve");
    m.insert("listen", "listen");
    m.insert("scheduler", "scheduler");
    m.insert("ask", "ask");
    m.insert("audit", "audit check");
    m.insert("bench", "bench");
    m.insert("compounds", "compounds");
    m.insert("help", "help");
    m.insert("clock", "clock");
    m.insert("metrics", "metrics");
    m.insert("verify", "verify");
    m.insert("tack", "tack");
    m.insert("search", "search");
    m.insert("purge", "purge");
    m.insert("pull", "pull");
    m.insert("bail", "bail");
    m.insert("upload", "upload"); // →728: Files API upload
    m.insert("handoff", "run"); // agents delegate via run
    m.insert("result", "needle close"); // report completion

    // ── aliases (human-friendly shortcuts) ──
    m.insert("showme", "show");
    m.insert("spec", "promote");
    m.insert("straw", "hay");
    m.insert("emerge", "hay");
    m.insert("proc", "ps");
    m.insert("correct", "nudge");
    m.insert("recover", "boot");
    m.insert("calibrate", "compile");
    m.insert("inform", "nudge");
    m.insert("ls", "needle list");
    m.insert("open", "needle list");
    m.insert("close", "needle close");
    m.insert("add", "needle add");
    m.insert("next", "needle next");
    m.insert("find", "search");
    m.insert("grep", "search");

    m
}

/// Parse a raw tack input string into a TackParse.
///
/// Grammar:
///   `:verb [args...]`    → Command
///   `.? [query]`         → Query
///   `→NNN`               → NeedleRef
///   `:: [target]`        → Escalate
pub fn parse(input: &str) -> Option<TackParse> {
    let trimmed = input.trim();
    if trimmed.is_empty() {
        return None;
    }

    // `→NNN` or `→ NNN` — needle reference
    if trimmed.starts_with('→') || trimmed.starts_with("->") {
        let rest = trimmed
            .strip_prefix('→')
            .or_else(|| trimmed.strip_prefix("->"))
            .unwrap_or("")
            .trim();
        return Some(TackParse {
            intent: TackIntent::NeedleRef,
            verb: "→".to_string(),
            args: if rest.is_empty() {
                vec![]
            } else {
                vec![rest.to_string()]
            },
        });
    }

    // `:: target` — escalate
    if let Some(stripped) = trimmed.strip_prefix("::") {
        let rest = stripped.trim();
        let parts: Vec<String> = split_args(rest);
        return Some(TackParse {
            intent: TackIntent::Escalate,
            verb: "::".to_string(),
            args: parts,
        });
    }

    // `.? query` — query
    if let Some(stripped) = trimmed.strip_prefix(".?") {
        let rest = stripped.trim();
        let parts: Vec<String> = split_args(rest);
        return Some(TackParse {
            intent: TackIntent::Query,
            verb: ".?".to_string(),
            args: parts,
        });
    }

    // `:verb [args]` — command
    if let Some(rest) = trimmed.strip_prefix(':') {
        let parts: Vec<String> = split_args(rest);
        if parts.is_empty() {
            return None;
        }
        let verb = parts[0].clone();
        let args = parts[1..].to_vec();
        return Some(TackParse {
            intent: TackIntent::Command,
            verb,
            args,
        });
    }

    None
}

/// Resolve a parsed tack expression against the verb table.
///
/// Priority: .language (Tier 1) → HUMANFILE → static_verb_table → unresolved
pub fn resolve(
    parsed: &TackParse,
    humanfile_verbs: &HashMap<String, String>,
    language_verbs: &HashMap<String, String>,
) -> TackResolution {
    tracing::debug!(verb = %parsed.verb, "tack: resolving verb");
    match parsed.intent {
        TackIntent::NeedleRef => {
            let needle_id = parsed.args.first().cloned().unwrap_or_default();
            TackResolution {
                resolved: true,
                verb: format!("→{}", needle_id),
                intent: TackIntent::NeedleRef,
                command: Some("show".to_string()),
                args: vec![format!("→{}", needle_id)],
                source: ResolveSource::Static,
                tier: 1,
                confidence: 1.0,
                suggestions: vec![],
            }
        }
        TackIntent::Query => TackResolution {
            resolved: true,
            verb: ".?".to_string(),
            intent: TackIntent::Query,
            command: Some("show".to_string()),
            args: parsed.args.clone(),
            source: ResolveSource::Static,
            tier: 1,
            confidence: 1.0,
            suggestions: vec![],
        },
        TackIntent::Escalate => TackResolution {
            resolved: true,
            verb: "::".to_string(),
            intent: TackIntent::Escalate,
            command: Some("nudge".to_string()),
            args: parsed.args.clone(),
            source: ResolveSource::Static,
            tier: 1,
            confidence: 1.0,
            suggestions: vec![],
        },
        TackIntent::Command => {
            let verb_lower = parsed.verb.to_lowercase();

            // Check .language first (Tier 1 — compiled dialect, highest priority)
            if let Some(cmd) = language_verbs.get(&verb_lower) {
                return TackResolution {
                    resolved: true,
                    verb: parsed.verb.clone(),
                    intent: TackIntent::Command,
                    command: Some(cmd.clone()),
                    args: parsed.args.clone(),
                    source: ResolveSource::Language,
                    tier: 1,
                    confidence: 1.0,
                    suggestions: vec![],
                };
            }

            // Check HUMANFILE extensions (user overrides static)
            if let Some(cmd) = humanfile_verbs.get(&verb_lower) {
                return TackResolution {
                    resolved: true,
                    verb: parsed.verb.clone(),
                    intent: TackIntent::Command,
                    command: Some(cmd.clone()),
                    args: parsed.args.clone(),
                    source: ResolveSource::Humanfile,
                    tier: 2,
                    confidence: 0.9,
                    suggestions: vec![],
                };
            }

            // Check static table
            let table = static_verb_table();
            if let Some(&cmd) = table.get(verb_lower.as_str()) {
                return TackResolution {
                    resolved: true,
                    verb: parsed.verb.clone(),
                    intent: TackIntent::Command,
                    command: Some(cmd.to_string()),
                    args: parsed.args.clone(),
                    source: ResolveSource::Static,
                    tier: 2,
                    confidence: 0.8,
                    suggestions: vec![],
                };
            }

            // Unknown verb — provide suggestions (→569: structured, not error)
            let suggestions = suggest_verbs(&verb_lower, &table, humanfile_verbs, language_verbs);
            TackResolution {
                resolved: false,
                verb: parsed.verb.clone(),
                intent: TackIntent::Command,
                command: None,
                args: parsed.args.clone(),
                source: ResolveSource::Inferred,
                tier: 3,
                confidence: 0.5,
                suggestions,
            }
        }
    }
}

/// Load `.language` compiled tack dialect (Tier 1 — highest priority).
///
/// Delegates parsing to the shared `language::parse_language` (single source of truth),
/// then converts entries into a verb→command HashMap for resolve dispatch.
pub fn load_language_verbs(ostk_dir: &Path) -> HashMap<String, String> {
    let lang_file = ostk_dir.join(".language");
    let content = match std::fs::read_to_string(&lang_file) {
        Ok(c) => c,
        Err(_) => return HashMap::new(),
    };

    let entries = parse_language(&content);
    let mut verbs = HashMap::new();
    for entry in entries {
        let verb = entry.verb.to_lowercase();
        let resolution = entry.resolution.trim().to_string();
        if verb.is_empty() || resolution.is_empty() {
            continue;
        }
        // Strip "ostk " prefix to match static table format
        let cmd = resolution
            .strip_prefix("ostk ")
            .unwrap_or(&resolution)
            .to_string();
        verbs.insert(verb, cmd);
    }

    verbs
}

/// Load HUMANFILE verb extensions.
///
/// Scans for lines matching: `VERB <name> <command>` or `TACK <name> <command>`
pub fn load_humanfile_verbs(ostk_dir: &Path) -> HashMap<String, String> {
    let mut verbs = HashMap::new();
    let humanfile = ostk_dir.join("HUMANFILE");
    let content = match std::fs::read_to_string(&humanfile) {
        Ok(c) => c,
        Err(_) => return verbs,
    };

    for line in content.lines() {
        let trimmed = line.trim();
        if let Some(rest) = trimmed
            .strip_prefix("VERB ")
            .or_else(|| trimmed.strip_prefix("TACK "))
        {
            let parts: Vec<&str> = rest.splitn(2, ' ').collect();
            if parts.len() == 2 {
                verbs.insert(parts[0].to_lowercase(), parts[1].to_string());
            }
        }
    }

    verbs
}

/// Full resolve pipeline: parse + load .language + load HUMANFILE + resolve.
///
/// When a `:verb` resolves successfully, records it directly to `.language`
/// via `record_verb()` — intent is captured at resolution time, not retroactively
/// from audit logs.
pub fn resolve_tack(input: &str, ostk_dir: &Path) -> Option<TackResolution> {
    let parsed = parse(input)?;
    let language_verbs = load_language_verbs(ostk_dir);
    let humanfile_verbs = load_humanfile_verbs(ostk_dir);
    let result = resolve(&parsed, &humanfile_verbs, &language_verbs);
    tracing::debug!(resolved = %result.resolved, confidence = %result.confidence, "tack: resolution complete");

    // Record verb usage to .language at resolution time (intent capture).
    // Only record Command intents that actually resolved.
    if result.resolved && matches!(result.intent, TackIntent::Command) {
        let root = ostk_dir.parent().unwrap_or(ostk_dir);
        let resolution_str = result.command.as_deref().unwrap_or("");
        if let Err(e) = crate::language::record_verb(root, &result.verb, resolution_str) {
            tracing::warn!(verb = %result.verb, error = %e, "tack: failed to record verb to .language");
        }
    }

    Some(result)
}

// ── Internal helpers ──

fn split_args(s: &str) -> Vec<String> {
    s.split_whitespace().map(|s| s.to_string()).collect()
}

/// Suggest similar verbs using edit distance.
fn suggest_verbs(
    input: &str,
    static_table: &HashMap<&str, &str>,
    humanfile_verbs: &HashMap<String, String>,
    language_verbs: &HashMap<String, String>,
) -> Vec<String> {
    let mut candidates: Vec<(usize, String)> = Vec::new();

    for &verb in static_table.keys() {
        let dist = edit_distance(input, verb);
        if dist <= 3 {
            candidates.push((dist, format!(":{}", verb)));
        }
    }
    for verb in humanfile_verbs.keys() {
        let dist = edit_distance(input, verb);
        if dist <= 3 {
            candidates.push((dist, format!(":{}", verb)));
        }
    }
    for verb in language_verbs.keys() {
        let dist = edit_distance(input, verb);
        if dist <= 3 {
            candidates.push((dist, format!(":{}", verb)));
        }
    }

    candidates.sort_by_key(|(d, _)| *d);
    candidates.into_iter().take(3).map(|(_, v)| v).collect()
}

/// Simple Levenshtein edit distance.
fn edit_distance(a: &str, b: &str) -> usize {
    let a: Vec<char> = a.chars().collect();
    let b: Vec<char> = b.chars().collect();
    let (m, n) = (a.len(), b.len());
    let mut dp = vec![vec![0usize; n + 1]; m + 1];

    for (i, row) in dp.iter_mut().enumerate().take(m + 1) {
        row[0] = i;
    }
    for (j, val) in dp[0].iter_mut().enumerate().take(n + 1) {
        *val = j;
    }
    for i in 1..=m {
        for j in 1..=n {
            let cost = if a[i - 1] == b[j - 1] { 0 } else { 1 };
            dp[i][j] = (dp[i - 1][j] + 1)
                .min(dp[i][j - 1] + 1)
                .min(dp[i - 1][j - 1] + cost);
        }
    }
    dp[m][n]
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── parse ──

    #[test]
    fn parse_command() {
        let p = parse(":compile --dry-run").unwrap();
        assert_eq!(p.intent, TackIntent::Command);
        assert_eq!(p.verb, "compile");
        assert_eq!(p.args, vec!["--dry-run"]);
    }

    #[test]
    fn parse_command_no_args() {
        let p = parse(":boot").unwrap();
        assert_eq!(p.intent, TackIntent::Command);
        assert_eq!(p.verb, "boot");
        assert!(p.args.is_empty());
    }

    #[test]
    fn parse_query() {
        let p = parse(".? status").unwrap();
        assert_eq!(p.intent, TackIntent::Query);
        assert_eq!(p.verb, ".?");
        assert_eq!(p.args, vec!["status"]);
    }

    #[test]
    fn parse_needle_ref() {
        let p = parse("→437").unwrap();
        assert_eq!(p.intent, TackIntent::NeedleRef);
        assert_eq!(p.args, vec!["437"]);
    }

    #[test]
    fn parse_needle_ref_ascii() {
        let p = parse("->437").unwrap();
        assert_eq!(p.intent, TackIntent::NeedleRef);
        assert_eq!(p.args, vec!["437"]);
    }

    #[test]
    fn parse_escalate() {
        let p = parse(":: agent1 fix the build").unwrap();
        assert_eq!(p.intent, TackIntent::Escalate);
        assert_eq!(p.args, vec!["agent1", "fix", "the", "build"]);
    }

    #[test]
    fn parse_empty() {
        assert!(parse("").is_none());
        assert!(parse("   ").is_none());
    }

    #[test]
    fn parse_no_prefix() {
        assert!(parse("just some text").is_none());
    }

    #[test]
    fn parse_colon_only() {
        assert!(parse(":").is_none());
    }

    // ── resolve ──

    #[test]
    fn resolve_static_verb() {
        let parsed = parse(":compile").unwrap();
        let r = resolve(&parsed, &HashMap::new(), &HashMap::new());
        assert!(r.resolved);
        assert_eq!(r.command.as_deref(), Some("compile"));
        assert_eq!(r.source, ResolveSource::Static);
    }

    #[test]
    fn resolve_alias_verb() {
        let parsed = parse(":showme needles").unwrap();
        let r = resolve(&parsed, &HashMap::new(), &HashMap::new());
        assert!(r.resolved);
        assert_eq!(r.command.as_deref(), Some("show"));
        assert_eq!(r.args, vec!["needles"]);
    }

    #[test]
    fn resolve_humanfile_override() {
        let mut hf = HashMap::new();
        hf.insert("yeet".to_string(), "reap".to_string());
        let parsed = parse(":yeet").unwrap();
        let r = resolve(&parsed, &hf, &HashMap::new());
        assert!(r.resolved);
        assert_eq!(r.command.as_deref(), Some("reap"));
        assert_eq!(r.source, ResolveSource::Humanfile);
    }

    #[test]
    fn resolve_unknown_with_suggestions() {
        let parsed = parse(":copile").unwrap();
        let r = resolve(&parsed, &HashMap::new(), &HashMap::new());
        assert!(!r.resolved);
        assert!(r.command.is_none());
        assert!(!r.suggestions.is_empty());
        // Should suggest :compile
        assert!(r.suggestions.iter().any(|s| s == ":compile"));
    }

    #[test]
    fn resolve_needle_ref() {
        let parsed = parse("→567").unwrap();
        let r = resolve(&parsed, &HashMap::new(), &HashMap::new());
        assert!(r.resolved);
        assert_eq!(r.command.as_deref(), Some("show"));
        assert_eq!(r.args, vec!["→567"]);
    }

    #[test]
    fn resolve_query() {
        let parsed = parse(".? threads").unwrap();
        let r = resolve(&parsed, &HashMap::new(), &HashMap::new());
        assert!(r.resolved);
        assert_eq!(r.command.as_deref(), Some("show"));
    }

    #[test]
    fn resolve_escalate() {
        let parsed = parse(":: rtx3 build broken").unwrap();
        let r = resolve(&parsed, &HashMap::new(), &HashMap::new());
        assert!(r.resolved);
        assert_eq!(r.command.as_deref(), Some("nudge"));
    }

    // ── edit_distance ──

    #[test]
    fn edit_distance_identical() {
        assert_eq!(edit_distance("compile", "compile"), 0);
    }

    #[test]
    fn edit_distance_one_char() {
        assert_eq!(edit_distance("compile", "copile"), 1);
    }

    #[test]
    fn edit_distance_different() {
        assert!(edit_distance("compile", "xyz") > 3);
    }

    // ── load_humanfile_verbs ──

    #[test]
    fn load_humanfile_verbs_missing_file() {
        let verbs = load_humanfile_verbs(Path::new("/nonexistent/path"));
        assert!(verbs.is_empty());
    }

    // ── intent as_str ──

    #[test]
    fn intent_as_str() {
        assert_eq!(TackIntent::Command.as_str(), "command");
        assert_eq!(TackIntent::Query.as_str(), "query");
        assert_eq!(TackIntent::NeedleRef.as_str(), "needle_ref");
        assert_eq!(TackIntent::Escalate.as_str(), "escalate");
    }

    #[test]
    fn resolve_source_as_str() {
        assert_eq!(ResolveSource::Language.as_str(), "language");
        assert_eq!(ResolveSource::Static.as_str(), "static");
        assert_eq!(ResolveSource::Humanfile.as_str(), "humanfile");
        assert_eq!(ResolveSource::Inferred.as_str(), "inferred");
    }

    // ── case insensitive ──

    #[test]
    fn resolve_case_insensitive() {
        let parsed = parse(":COMPILE").unwrap();
        let r = resolve(&parsed, &HashMap::new(), &HashMap::new());
        assert!(r.resolved);
        assert_eq!(r.command.as_deref(), Some("compile"));
    }

    // ── .language ──

    #[test]
    fn load_language_verbs_correct_format() {
        let dir = std::env::temp_dir().join("ostk_test_lang");
        let _ = std::fs::create_dir_all(&dir);
        std::fs::write(
            dir.join(".language"),
            "# comment\n\
             :boot       | 1 | kernel  | 0    | 0     | 1.0  | ostk boot\n\
             :pitchfork  | 2 | user    | 0    | 100   | 0.85 | ostk show --semantic\n",
        )
        .unwrap();
        let verbs = load_language_verbs(&dir);
        assert_eq!(verbs.get("boot").map(|s| s.as_str()), Some("boot"));
        assert_eq!(
            verbs.get("pitchfork").map(|s| s.as_str()),
            Some("show --semantic")
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn load_language_verbs_missing_file() {
        let verbs = load_language_verbs(Path::new("/nonexistent/path"));
        assert!(verbs.is_empty());
    }

    #[test]
    fn language_takes_priority_over_static() {
        let mut lang = HashMap::new();
        // Override "boot" which exists in static table
        lang.insert("boot".to_string(), "custom-boot".to_string());
        let parsed = parse(":boot").unwrap();
        let r = resolve(&parsed, &HashMap::new(), &lang);
        assert!(r.resolved);
        assert_eq!(r.command.as_deref(), Some("custom-boot"));
        assert_eq!(r.source, ResolveSource::Language);
    }

    #[test]
    fn language_verb_resolves_with_source_language() {
        let mut lang = HashMap::new();
        lang.insert("pitchfork".to_string(), "show --semantic".to_string());
        let parsed = parse(":pitchfork").unwrap();
        let r = resolve(&parsed, &HashMap::new(), &lang);
        assert!(r.resolved);
        assert_eq!(r.command.as_deref(), Some("show --semantic"));
        assert_eq!(r.source, ResolveSource::Language);
    }
}
