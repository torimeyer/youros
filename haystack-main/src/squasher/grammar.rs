//! Grammar loading and matching for the squasher pipeline.
//!
//! Loads TOML grammars, detects tools from command strings, resolves actions.
//! Uses intermediate "raw" serde structs for TOML deserialization, then
//! converts to final types with compiled Regex fields.
//!
//! Ported from mish's grammar system.

use std::collections::HashMap;
use std::fmt;

use regex::Regex;
use serde::Deserialize;

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

#[derive(Debug)]
pub enum GrammarError {
    Parse(toml::de::Error),
    InvalidRegex { pattern: String, source: regex::Error },
    InvalidAction(String),
    InvalidSeverity(String),
}

impl fmt::Display for GrammarError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            GrammarError::Parse(e) => write!(f, "TOML parse error: {e}"),
            GrammarError::InvalidRegex { pattern, source } => {
                write!(f, "invalid regex '{pattern}': {source}")
            }
            GrammarError::InvalidAction(a) => write!(f, "invalid rule action: {a}"),
            GrammarError::InvalidSeverity(s) => write!(f, "invalid severity: {s}"),
        }
    }
}

impl std::error::Error for GrammarError {}

impl From<toml::de::Error> for GrammarError {
    fn from(e: toml::de::Error) -> Self {
        GrammarError::Parse(e)
    }
}

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct Grammar {
    pub tool: ToolInfo,
    pub detect: Vec<String>,
    pub inherit: Vec<String>,
    pub global_noise: Vec<Rule>,
    pub actions: HashMap<String, Action>,
    pub fallback: Option<Action>,
    pub quiet: Option<QuietConfig>,
    pub block: Vec<BlockRule>,
    pub llm_hints: Vec<LlmHint>,
}

#[derive(Debug, Clone)]
pub struct ToolInfo {
    pub name: String,
}

#[derive(Debug, Clone)]
pub struct Action {
    pub detect: Vec<String>,
    pub noise: Vec<Rule>,
    pub hazard: Vec<Rule>,
    pub outcome: Vec<Rule>,
    pub summary: SummaryTemplate,
    pub llm_hints: Vec<LlmHint>,
    pub category: Option<String>,
}

#[derive(Debug, Clone)]
pub struct Rule {
    pub pattern: Regex,
    pub action: RuleAction,
    pub severity: Option<Severity>,
    pub captures: Vec<String>,
    pub multiline: Option<u32>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RuleAction {
    Strip,
    Dedup,
    Keep,
    Promote,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Severity {
    Error,
    Warning,
}

#[derive(Debug, Clone, Default)]
pub struct SummaryTemplate {
    pub success: String,
    pub failure: String,
    pub partial: String,
}

#[derive(Debug, Clone)]
pub struct QuietConfig {
    pub safe_inject: Vec<String>,
    pub recommend: Vec<String>,
    pub actions: HashMap<String, QuietActionConfig>,
}

#[derive(Debug, Clone)]
pub struct QuietActionConfig {
    pub safe_inject: Vec<String>,
    pub recommend: Vec<String>,
}

/// An LLM hint declaring a preferred invocation form for a tool or action.
#[derive(Debug, Clone)]
pub struct LlmHint {
    pub prefer: String,
    pub reason: String,
    /// Optional mode filter: "mcp", "cli", or None (emit in both modes).
    pub mode: Option<String>,
}

/// A block compression rule for collapsing multi-line diagnostic blocks
/// into single dense digest lines.
#[derive(Debug, Clone)]
pub struct BlockRule {
    pub start: Regex,
    pub end: Regex,
    pub extract: Regex,
    pub digest: String,
}

// ---------------------------------------------------------------------------
// Raw (serde) types -- intermediate deserialization
// ---------------------------------------------------------------------------

#[derive(Deserialize)]
struct RawGrammar {
    tool: RawToolInfo,
    #[serde(default)]
    detect: Option<Vec<String>>,
    #[serde(default)]
    inherit: Option<Vec<String>>,
    #[serde(default)]
    global_noise: Vec<RawRule>,
    #[serde(default)]
    actions: HashMap<String, RawAction>,
    #[serde(default)]
    fallback: Option<RawAction>,
    #[serde(default)]
    quiet: Option<RawQuietConfig>,
    #[serde(default)]
    block: Vec<RawBlockRule>,
    #[serde(default)]
    llm_hints: Vec<RawLlmHint>,
}

#[derive(Deserialize)]
struct RawBlockRule {
    start: String,
    end: Option<String>,
    extract: String,
    digest: String,
}

#[derive(Deserialize)]
struct RawToolInfo {
    name: String,
    #[serde(default)]
    detect: Option<Vec<String>>,
    #[serde(default)]
    inherit: Option<Vec<String>>,
}

#[derive(Deserialize)]
struct RawRule {
    pattern: String,
    action: String,
    #[serde(default)]
    severity: Option<String>,
    #[serde(default)]
    captures: Option<Vec<String>>,
    #[serde(default)]
    multiline: Option<u32>,
    // Allow an optional description field in TOML without failing
    #[serde(default)]
    #[allow(dead_code)]
    description: Option<String>,
}

#[derive(Deserialize)]
struct RawLlmHint {
    prefer: String,
    reason: String,
    #[serde(default)]
    mode: Option<String>,
}

#[derive(Deserialize)]
struct RawAction {
    #[serde(default)]
    detect: Option<Vec<String>>,
    #[serde(default)]
    noise: Vec<RawRule>,
    #[serde(default)]
    hazard: Vec<RawRule>,
    #[serde(default)]
    outcome: Vec<RawRule>,
    #[serde(default)]
    summary: Option<RawSummaryTemplate>,
    #[serde(default)]
    llm_hints: Vec<RawLlmHint>,
    #[serde(default)]
    category: Option<String>,
}

#[derive(Deserialize)]
struct RawSummaryTemplate {
    #[serde(default)]
    success: Option<String>,
    #[serde(default)]
    failure: Option<String>,
    #[serde(default)]
    partial: Option<String>,
}

#[derive(Deserialize)]
struct RawQuietConfig {
    #[serde(default)]
    safe_inject: Vec<String>,
    #[serde(default)]
    recommend: Vec<String>,
    #[serde(default)]
    actions: HashMap<String, RawQuietActionConfig>,
}

#[derive(Deserialize)]
struct RawQuietActionConfig {
    #[serde(default)]
    safe_inject: Vec<String>,
    #[serde(default)]
    recommend: Vec<String>,
}

// Shared grammar TOML uses `[[rules]]` at the top level
#[derive(Deserialize)]
struct RawSharedGrammar {
    #[serde(default)]
    rules: Vec<RawRule>,
}

// ---------------------------------------------------------------------------
// Conversions: Raw -> compiled types
// ---------------------------------------------------------------------------

fn parse_rule_action(s: &str) -> Result<RuleAction, GrammarError> {
    match s {
        "strip" => Ok(RuleAction::Strip),
        "dedup" => Ok(RuleAction::Dedup),
        "keep" => Ok(RuleAction::Keep),
        "promote" => Ok(RuleAction::Promote),
        other => Err(GrammarError::InvalidAction(other.to_string())),
    }
}

fn parse_severity(s: &str) -> Result<Severity, GrammarError> {
    match s {
        "error" => Ok(Severity::Error),
        "warning" => Ok(Severity::Warning),
        other => Err(GrammarError::InvalidSeverity(other.to_string())),
    }
}

impl TryFrom<RawRule> for Rule {
    type Error = GrammarError;

    fn try_from(raw: RawRule) -> Result<Self, GrammarError> {
        let pattern = Regex::new(&raw.pattern).map_err(|e| GrammarError::InvalidRegex {
            pattern: raw.pattern.clone(),
            source: e,
        })?;
        let action = parse_rule_action(&raw.action)?;
        let severity = raw.severity.as_deref().map(parse_severity).transpose()?;
        let captures = raw.captures.unwrap_or_default();
        Ok(Rule {
            pattern,
            action,
            severity,
            captures,
            multiline: raw.multiline,
        })
    }
}

impl TryFrom<RawBlockRule> for BlockRule {
    type Error = GrammarError;

    fn try_from(raw: RawBlockRule) -> Result<Self, GrammarError> {
        let start = Regex::new(&raw.start).map_err(|e| GrammarError::InvalidRegex {
            pattern: raw.start.clone(),
            source: e,
        })?;
        let end_pattern = raw.end.as_deref().unwrap_or(r"^\s*$");
        let end = Regex::new(end_pattern).map_err(|e| GrammarError::InvalidRegex {
            pattern: end_pattern.to_string(),
            source: e,
        })?;
        // Build multiline regex with (?s) flag for dot-matches-newline
        let extract_pattern = format!("(?s){}", raw.extract);
        let extract =
            Regex::new(&extract_pattern).map_err(|e| GrammarError::InvalidRegex {
                pattern: raw.extract.clone(),
                source: e,
            })?;
        Ok(BlockRule {
            start,
            end,
            extract,
            digest: raw.digest,
        })
    }
}

impl TryFrom<RawAction> for Action {
    type Error = GrammarError;

    fn try_from(raw: RawAction) -> Result<Self, GrammarError> {
        let detect = raw.detect.unwrap_or_default();
        let noise = raw
            .noise
            .into_iter()
            .map(Rule::try_from)
            .collect::<Result<Vec<_>, _>>()?;
        let hazard = raw
            .hazard
            .into_iter()
            .map(Rule::try_from)
            .collect::<Result<Vec<_>, _>>()?;
        let outcome = raw
            .outcome
            .into_iter()
            .map(Rule::try_from)
            .collect::<Result<Vec<_>, _>>()?;
        let summary = match raw.summary {
            Some(s) => SummaryTemplate {
                success: s.success.unwrap_or_default(),
                failure: s.failure.unwrap_or_default(),
                partial: s.partial.unwrap_or_default(),
            },
            None => SummaryTemplate::default(),
        };
        let llm_hints = raw
            .llm_hints
            .into_iter()
            .map(|h| LlmHint {
                prefer: h.prefer,
                reason: h.reason,
                mode: h.mode,
            })
            .collect();
        let category = raw.category;
        Ok(Action {
            detect,
            noise,
            hazard,
            outcome,
            summary,
            llm_hints,
            category,
        })
    }
}

fn convert_raw_grammar(raw: RawGrammar) -> Result<Grammar, GrammarError> {
    // detect list: prefer top-level, fall back to [tool] section, default to [tool.name]
    let detect = raw
        .detect
        .or(raw.tool.detect)
        .unwrap_or_else(|| vec![raw.tool.name.clone()]);

    // inherit: prefer top-level, fall back to [tool] section
    let inherit_list = raw.inherit.or(raw.tool.inherit).unwrap_or_default();

    let global_noise = raw
        .global_noise
        .into_iter()
        .map(Rule::try_from)
        .collect::<Result<Vec<_>, _>>()?;

    let actions = raw
        .actions
        .into_iter()
        .map(|(k, v)| Action::try_from(v).map(|a| (k, a)))
        .collect::<Result<HashMap<_, _>, _>>()?;

    let fallback = raw.fallback.map(Action::try_from).transpose()?;

    let quiet = raw.quiet.map(|q| QuietConfig {
        safe_inject: q.safe_inject,
        recommend: q.recommend,
        actions: q
            .actions
            .into_iter()
            .map(|(k, v)| {
                (
                    k,
                    QuietActionConfig {
                        safe_inject: v.safe_inject,
                        recommend: v.recommend,
                    },
                )
            })
            .collect(),
    });

    let block = raw
        .block
        .into_iter()
        .map(BlockRule::try_from)
        .collect::<Result<Vec<_>, _>>()?;

    let llm_hints = raw
        .llm_hints
        .into_iter()
        .map(|h| LlmHint {
            prefer: h.prefer,
            reason: h.reason,
            mode: h.mode,
        })
        .collect();

    Ok(Grammar {
        tool: ToolInfo {
            name: raw.tool.name,
        },
        detect,
        inherit: inherit_list,
        global_noise,
        actions,
        fallback,
        quiet,
        block,
        llm_hints,
    })
}

// ---------------------------------------------------------------------------
// Public API -- loading
// ---------------------------------------------------------------------------

/// Parse a grammar from a TOML string.
///
/// The `name` parameter is for diagnostic purposes only (not used in parsing).
pub fn load_grammar_from_str(name: &str, toml_str: &str) -> Result<Grammar, GrammarError> {
    let _ = name; // reserved for future diagnostics
    let raw: RawGrammar = toml::from_str(toml_str)?;
    convert_raw_grammar(raw)
}

/// Load shared grammar rules from a `_shared/*.toml` string.
/// These files have `[[rules]]` at the top level (no [tool] section).
pub fn load_shared_rules_from_str(toml_str: &str) -> Result<Vec<Rule>, GrammarError> {
    let raw: RawSharedGrammar = toml::from_str(toml_str)?;
    raw.rules
        .into_iter()
        .map(Rule::try_from)
        .collect::<Result<Vec<_>, _>>()
}

/// Resolve `inherit` references by appending shared rules into `global_noise`.
///
/// Inherited rules are evaluated **after** the tool's own rules. This allows
/// a tool grammar to override shared behavior when needed.
pub fn resolve_inherit(grammar: &mut Grammar, shared_rules: &HashMap<String, Vec<Rule>>) {
    for name in &grammar.inherit {
        if let Some(rules) = shared_rules.get(name) {
            grammar.global_noise.extend(rules.iter().cloned());
        }
    }
}

// ---------------------------------------------------------------------------
// Public API -- tool detection and action resolution
// ---------------------------------------------------------------------------

/// Detect which grammar matches the given command string.
///
/// Splits `cmd` on whitespace and matches `argv[0]` against each grammar's
/// `detect` list. Returns a reference to the matching grammar if found.
pub fn detect_tool<'a>(
    cmd: &str,
    grammars: &'a HashMap<String, Grammar>,
) -> Option<&'a Grammar> {
    let argv0 = cmd.split_whitespace().next()?;
    grammars.values().find(|&grammar| grammar.detect.iter().any(|d| d == argv0)).map(|v| v as _)
}

/// Resolve which action within a grammar matches the command arguments.
///
/// Walks the args (skipping argv[0]) and checks each action's detect list
/// for a match. Returns `None` if no action matches and there is no fallback.
pub fn resolve_action<'a>(grammar: &'a Grammar, args: &[&str]) -> Option<&'a Action> {
    if args.len() < 2 {
        return grammar.fallback.as_ref();
    }

    // Check each action's detect list against args[1..]
    for action in grammar.actions.values() {
        for arg in &args[1..] {
            if action.detect.iter().any(|d| d == arg) {
                return Some(action);
            }
        }
    }

    // No action matched -- use fallback if available
    grammar.fallback.as_ref()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_load_cargo_grammar() {
        let toml_str = include_str!("grammars/toml/cargo.toml");
        let grammar = load_grammar_from_str("cargo", toml_str).unwrap();
        assert_eq!(grammar.tool.name, "cargo");
        assert_eq!(grammar.detect, vec!["cargo"]);
        // cargo has at least build + test actions
        assert!(grammar.actions.len() >= 2, "expected at least 2 actions, got {}", grammar.actions.len());
        assert!(grammar.actions.contains_key("build"));
        assert!(grammar.actions.contains_key("test"));
    }

    #[test]
    fn test_detect_tool_cargo() {
        let toml_str = include_str!("grammars/toml/cargo.toml");
        let grammar = load_grammar_from_str("cargo", toml_str).unwrap();
        let mut grammars = HashMap::new();
        grammars.insert("cargo".to_string(), grammar);

        let found = detect_tool("cargo build", &grammars);
        assert!(found.is_some());
        assert_eq!(found.unwrap().tool.name, "cargo");

        // Should not match non-cargo command
        let not_found = detect_tool("npm install", &grammars);
        assert!(not_found.is_none());
    }

    #[test]
    fn test_resolve_action_build() {
        let toml_str = include_str!("grammars/toml/cargo.toml");
        let grammar = load_grammar_from_str("cargo", toml_str).unwrap();

        let action = resolve_action(&grammar, &["cargo", "build"]);
        assert!(action.is_some());
        let action = action.unwrap();
        assert!(action.detect.contains(&"build".to_string()));
    }

    #[test]
    fn test_resolve_action_test() {
        let toml_str = include_str!("grammars/toml/cargo.toml");
        let grammar = load_grammar_from_str("cargo", toml_str).unwrap();

        let action = resolve_action(&grammar, &["cargo", "test"]);
        assert!(action.is_some());
        let action = action.unwrap();
        assert!(action.detect.contains(&"test".to_string()));
    }

    #[test]
    fn test_resolve_action_fallback() {
        // curl uses fallback (no subcommands)
        let toml_str = include_str!("grammars/toml/curl.toml");
        let grammar = load_grammar_from_str("curl", toml_str).unwrap();

        // With just "curl" (no subcommand), should get fallback
        let action = resolve_action(&grammar, &["curl"]);
        assert!(action.is_some(), "curl should have a fallback action");
    }

    #[test]
    fn test_shared_rules_parse() {
        let toml_str = include_str!("grammars/toml/_shared/ansi-progress.toml");
        let rules = load_shared_rules_from_str(toml_str).unwrap();
        assert!(rules.len() >= 3, "expected at least 3 shared rules, got {}", rules.len());
    }

    #[test]
    fn test_inherit_resolution() {
        let grammar_toml = include_str!("grammars/toml/cargo.toml");
        let shared_toml = include_str!("grammars/toml/_shared/ansi-progress.toml");

        let mut grammar = load_grammar_from_str("cargo", grammar_toml).unwrap();
        let shared_rules = load_shared_rules_from_str(shared_toml).unwrap();

        let noise_before = grammar.global_noise.len();

        let mut shared_map = HashMap::new();
        shared_map.insert("ansi-progress".to_string(), shared_rules);
        resolve_inherit(&mut grammar, &shared_map);

        // After inheritance, global_noise should have grown
        assert!(grammar.global_noise.len() > noise_before,
            "expected global_noise to grow after inherit, was {} now {}",
            noise_before, grammar.global_noise.len());
    }

    #[test]
    fn test_parse_all_tool_grammars() {
        // Verify every embedded tool grammar parses without error
        let grammars: &[(&str, &str)] = &[
            ("ansible", include_str!("grammars/toml/ansible.toml")),
            ("apt", include_str!("grammars/toml/apt.toml")),
            ("brew", include_str!("grammars/toml/brew.toml")),
            ("cargo", include_str!("grammars/toml/cargo.toml")),
            ("cat", include_str!("grammars/toml/cat.toml")),
            ("curl", include_str!("grammars/toml/curl.toml")),
            ("docker", include_str!("grammars/toml/docker.toml")),
            ("gcc", include_str!("grammars/toml/gcc.toml")),
            ("git", include_str!("grammars/toml/git.toml")),
            ("go", include_str!("grammars/toml/go.toml")),
            ("head", include_str!("grammars/toml/head.toml")),
            ("jest", include_str!("grammars/toml/jest.toml")),
            ("kubectl", include_str!("grammars/toml/kubectl.toml")),
            ("make", include_str!("grammars/toml/make.toml")),
            ("npm", include_str!("grammars/toml/npm.toml")),
            ("pip", include_str!("grammars/toml/pip.toml")),
            ("pytest", include_str!("grammars/toml/pytest.toml")),
            ("python3", include_str!("grammars/toml/python3.toml")),
            ("rsync", include_str!("grammars/toml/rsync.toml")),
            ("rustc", include_str!("grammars/toml/rustc.toml")),
            ("sed", include_str!("grammars/toml/sed.toml")),
            ("ssh", include_str!("grammars/toml/ssh.toml")),
            ("systemctl", include_str!("grammars/toml/systemctl.toml")),
            ("terraform", include_str!("grammars/toml/terraform.toml")),
            ("webpack", include_str!("grammars/toml/webpack.toml")),
        ];

        for (name, content) in grammars {
            let result = load_grammar_from_str(name, content);
            assert!(result.is_ok(), "failed to parse grammar '{}': {:?}", name, result.err());
        }
    }
}
