use std::fmt;

/// A fully parsed HUMANFILE.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Humanfile {
    /// Operator name from IDENTITY directive (global only).
    pub identity: Option<String>,
    /// GPG key fingerprint from SIGN directive (global only).
    pub sign_key: Option<String>,
    /// Default model from MODEL directive.
    pub model: Option<String>,
    /// Fallback model from FALLBACK directive.
    pub fallback: Option<String>,
    /// Model allowlist from AVAILABLE heredoc.
    pub available_models: Vec<String>,
    /// MCP driver registrations: (name, command).
    pub drivers: Vec<(String, String)>,
    /// Tack verb definitions: (verb, command).
    pub tack_verbs: Vec<(String, String)>,
    /// Path to parent HUMANFILE for inheritance.
    pub extends: Option<String>,
    /// →751: Authorized secret key names from SECRET heredoc.
    pub secrets: Vec<String>,
    /// Trust policy for Agentfile signing: "unsigned" = skip signature requirement.
    /// Set via TRUST directive. Composes with pin TRUST (OS floor) in approval chain.
    pub trust: Option<String>,
    /// →1157 Phase 4: Custom boot steps appended after INIT's post-language sequence.
    /// e.g., BOOT :verify-codeowners
    pub boot_steps: Vec<String>,
    /// →1157 Phase 4: Custom verb registrations merged into .language at boot.
    /// (verb, resolution, signature, doc)
    pub verb_defs: Vec<(String, String, String, String)>,
}


/// Errors that can occur during HUMANFILE parsing.
#[derive(Debug, Clone, PartialEq)]
pub enum ParseError {
    /// Unknown directive keyword.
    UnknownDirective { line: usize, directive: String },
    /// A directive is missing its required argument.
    MissingArgument { line: usize, directive: String },
    /// DRIVER requires name and command.
    InvalidDriver { line: usize, content: String },
    /// Heredoc block was opened but never closed.
    UnclosedHeredoc { line: usize, tag: String },
    /// Malformed tack verb line (missing arrow).
    MalformedTackVerb { line: usize, content: String },
    /// Child HUMANFILE with EXTENDS cannot set IDENTITY or SIGN.
    IdentityOverride { line: usize, directive: String },
}

impl fmt::Display for ParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ParseError::UnknownDirective { line, directive } => {
                write!(f, "line {line}: unknown directive: {directive}")
            }
            ParseError::MissingArgument { line, directive } => {
                write!(f, "line {line}: {directive} requires an argument")
            }
            ParseError::InvalidDriver { line, content } => {
                write!(
                    f,
                    "line {line}: DRIVER requires name and command, got: {content}"
                )
            }
            ParseError::UnclosedHeredoc { line, tag } => {
                write!(f, "line {line}: heredoc <<{tag} was never closed")
            }
            ParseError::MalformedTackVerb { line, content } => {
                write!(
                    f,
                    "line {line}: TACK verb must be `:verb → command`, got: {content}"
                )
            }
            ParseError::IdentityOverride { line, directive } => {
                write!(
                    f,
                    "line {line}: child HUMANFILE with EXTENDS cannot set {directive}"
                )
            }
        }
    }
}

/// Parse a HUMANFILE from its text content.
///
/// Lines starting with `#` are comments. Blank lines are skipped.
/// Directives are case-sensitive uppercase keywords followed by arguments.
/// Heredoc blocks (AVAILABLE <<TAG, TACK <<TAG) collect lines until the
/// closing TAG appears alone on a line.
pub fn parse(content: &str) -> Result<Humanfile, ParseError> {
    let mut hf = Humanfile::default();

    let lines: Vec<&str> = content.lines().collect();
    let mut i = 0;
    let mut in_code_fence = false;

    while i < lines.len() {
        let line_num = i + 1;
        let line = lines[i].trim();

        // Track code fences — skip everything inside ``` blocks
        if line.starts_with("```") {
            in_code_fence = !in_code_fence;
            i += 1;
            continue;
        }
        if in_code_fence {
            i += 1;
            continue;
        }

        // Skip blank lines and comments
        if line.is_empty() || line.starts_with('#') {
            i += 1;
            continue;
        }

        // Split into directive keyword and the rest
        let (directive, rest) = match line.split_once(char::is_whitespace) {
            Some((d, r)) => (d, r.trim()),
            None => (line, ""),
        };

        match directive {
            "IDENTITY" => {
                if rest.is_empty() {
                    return Err(ParseError::MissingArgument {
                        line: line_num,
                        directive: "IDENTITY".to_string(),
                    });
                }
                hf.identity = Some(rest.to_string());
            }
            "SIGN" => {
                if rest.is_empty() {
                    return Err(ParseError::MissingArgument {
                        line: line_num,
                        directive: "SIGN".to_string(),
                    });
                }
                hf.sign_key = Some(rest.to_string());
            }
            "MODEL" => {
                if rest.is_empty() {
                    return Err(ParseError::MissingArgument {
                        line: line_num,
                        directive: "MODEL".to_string(),
                    });
                }
                hf.model = Some(rest.to_string());
            }
            "FALLBACK" => {
                if rest.is_empty() {
                    return Err(ParseError::MissingArgument {
                        line: line_num,
                        directive: "FALLBACK".to_string(),
                    });
                }
                hf.fallback = Some(rest.to_string());
            }
            "AVAILABLE" => {
                // Expect heredoc: AVAILABLE <<TAG
                let tag = parse_heredoc_open(rest, line_num, "AVAILABLE")?;
                let (items, end_idx) = collect_heredoc_lines(&lines, i, &tag)?;
                for item in items {
                    let trimmed = item.trim();
                    if !trimmed.is_empty() {
                        hf.available_models.push(trimmed.to_string());
                    }
                }
                i = end_idx;
            }
            "DRIVER" => {
                if rest.is_empty() {
                    return Err(ParseError::MissingArgument {
                        line: line_num,
                        directive: "DRIVER".to_string(),
                    });
                }
                let (name, command) = rest.split_once(char::is_whitespace).ok_or_else(|| {
                    ParseError::InvalidDriver {
                        line: line_num,
                        content: rest.to_string(),
                    }
                })?;
                let command = command.trim();
                if command.is_empty() {
                    return Err(ParseError::InvalidDriver {
                        line: line_num,
                        content: rest.to_string(),
                    });
                }
                hf.drivers.push((name.to_string(), command.to_string()));
            }
            "TACK" => {
                // Expect heredoc: TACK <<TAG
                let tag = parse_heredoc_open(rest, line_num, "TACK")?;
                let (verb_lines, end_idx) = collect_heredoc_lines(&lines, i, &tag)?;
                for (offset, verb_line) in verb_lines.iter().enumerate() {
                    let trimmed = verb_line.trim();
                    if trimmed.is_empty() {
                        continue;
                    }
                    let verb_line_num = i + 2 + offset; // +2: 1-indexed + skip opening line
                    let (verb, cmd) = parse_tack_verb(trimmed, verb_line_num)?;
                    hf.tack_verbs.push((verb, cmd));
                }
                i = end_idx;
            }
            "SECRET" => {
                // →751: Authorized secret key names.
                // Heredoc: SECRET <<TAG ... TAG
                let tag = parse_heredoc_open(rest, line_num, "SECRET")?;
                let (items, end_idx) = collect_heredoc_lines(&lines, i, &tag)?;
                for item in items {
                    let trimmed = item.trim();
                    if !trimmed.is_empty() {
                        hf.secrets.push(trimmed.to_string());
                    }
                }
                i = end_idx;
            }
            // →1157 Phase 4: Custom boot steps
            "BOOT" => {
                if rest.is_empty() {
                    return Err(ParseError::MissingArgument {
                        line: line_num,
                        directive: "BOOT".to_string(),
                    });
                }
                hf.boot_steps.push(rest.trim_start_matches(':').to_string());
            }
            // →1157 Phase 4: Custom verb definitions
            // VERB :name resolution signature doc
            "VERB" => {
                if rest.is_empty() {
                    return Err(ParseError::MissingArgument {
                        line: line_num,
                        directive: "VERB".to_string(),
                    });
                }
                // Parse: :name resolution (signature) doc
                let parts: Vec<&str> = rest.splitn(4, char::is_whitespace).collect();
                if parts.len() >= 3 {
                    let verb = parts[0].trim_start_matches(':').to_string();
                    let resolution = parts[1].to_string();
                    let signature = parts.get(2).unwrap_or(&"() -> ()").to_string();
                    let doc = parts.get(3).unwrap_or(&"").to_string();
                    hf.verb_defs.push((verb, resolution, signature, doc));
                }
            }
            "TRUST" => {
                if rest.is_empty() {
                    return Err(ParseError::MissingArgument {
                        line: line_num,
                        directive: "TRUST".to_string(),
                    });
                }
                hf.trust = Some(rest.to_lowercase());
            }
            "EXTENDS" => {
                if rest.is_empty() {
                    return Err(ParseError::MissingArgument {
                        line: line_num,
                        directive: "EXTENDS".to_string(),
                    });
                }
                hf.extends = Some(rest.to_string());
            }
            _ => {
                // Unknown all-caps token = unrecognised directive (forward-compat guard).
                // Mixed-case token = prose line (operator:, key:, markdown, etc.) — skip.
                if directive.chars().all(|c| c.is_ascii_uppercase() || c == '_') {
                    return Err(ParseError::UnknownDirective {
                        line: line_num,
                        directive: directive.to_string(),
                    });
                }
                // prose line — skip silently
            }
        }

        i += 1;
    }

    // Validate: if EXTENDS is set, IDENTITY and SIGN must not be set.
    if hf.extends.is_some() {
        if hf.identity.is_some() {
            // Find the line number of IDENTITY for the error
            let id_line = find_directive_line(&lines, "IDENTITY").unwrap_or(0);
            return Err(ParseError::IdentityOverride {
                line: id_line,
                directive: "IDENTITY".to_string(),
            });
        }
        if hf.sign_key.is_some() {
            let sign_line = find_directive_line(&lines, "SIGN").unwrap_or(0);
            return Err(ParseError::IdentityOverride {
                line: sign_line,
                directive: "SIGN".to_string(),
            });
        }
    }

    Ok(hf)
}

/// Parse a heredoc opening: expects `<<TAG` and returns the tag name.
fn parse_heredoc_open(rest: &str, line: usize, directive: &str) -> Result<String, ParseError> {
    let rest = rest.trim();
    if !rest.starts_with("<<") {
        return Err(ParseError::MissingArgument {
            line,
            directive: format!("{directive} (expected <<TAG)"),
        });
    }
    let tag = rest[2..].trim().to_string();
    if tag.is_empty() {
        return Err(ParseError::MissingArgument {
            line,
            directive: format!("{directive} (empty heredoc tag)"),
        });
    }
    Ok(tag)
}

/// Collect lines inside a heredoc block. Returns (collected_lines, index_of_closing_tag).
fn collect_heredoc_lines<'a>(
    lines: &'a [&str],
    open_idx: usize,
    tag: &str,
) -> Result<(Vec<&'a str>, usize), ParseError> {
    let mut collected = Vec::new();
    let mut j = open_idx + 1;
    while j < lines.len() {
        let heredoc_line = lines[j].trim();
        if heredoc_line == tag {
            return Ok((collected, j));
        }
        collected.push(lines[j]);
        j += 1;
    }
    Err(ParseError::UnclosedHeredoc {
        line: open_idx + 1,
        tag: tag.to_string(),
    })
}

/// Parse a tack verb line: `:verb → command` or `:verb -> command`.
fn parse_tack_verb(line: &str, line_num: usize) -> Result<(String, String), ParseError> {
    // Support both → (Unicode arrow) and -> (ASCII arrow)
    let (verb, cmd) = if let Some((v, c)) = line.split_once('\u{2192}') {
        (v.trim(), c.trim())
    } else if let Some((v, c)) = line.split_once("->") {
        (v.trim(), c.trim())
    } else {
        return Err(ParseError::MalformedTackVerb {
            line: line_num,
            content: line.to_string(),
        });
    };

    if verb.is_empty() || cmd.is_empty() {
        return Err(ParseError::MalformedTackVerb {
            line: line_num,
            content: line.to_string(),
        });
    }

    Ok((verb.to_string(), cmd.to_string()))
}

/// Find the 1-indexed line number of a directive in the source.
fn find_directive_line(lines: &[&str], directive: &str) -> Option<usize> {
    for (i, line) in lines.iter().enumerate() {
        let trimmed = line.trim();
        if trimmed.starts_with(directive)
            && trimmed[directive.len()..]
                .starts_with(char::is_whitespace)
        {
            return Some(i + 1);
        }
    }
    None
}

/// Merge a parent HUMANFILE with a child HUMANFILE.
///
/// Rules (from spec):
/// - MODEL/FALLBACK: child overrides parent
/// - AVAILABLE: child overrides (project-scoped)
/// - TACK: merge additive, child wins on conflict
/// - DRIVER: child only (project-specific)
/// - IDENTITY/SIGN: parent only (child cannot override when EXTENDS is set,
///   enforced at parse time)
pub fn merge(parent: Humanfile, child: Humanfile) -> Humanfile {
    // IDENTITY/SIGN come from parent only.
    let identity = parent.identity;
    let sign_key = parent.sign_key;

    // MODEL/FALLBACK: child overrides parent.
    let model = child.model.or(parent.model);
    let fallback = child.fallback.or(parent.fallback);

    // AVAILABLE: child overrides entirely if non-empty, else parent.
    let available_models = if child.available_models.is_empty() {
        parent.available_models
    } else {
        child.available_models
    };

    // DRIVER: child only (project-specific). Parent drivers are not inherited.
    let drivers = child.drivers;

    // TACK: merge additive, child wins on conflict.
    let mut verb_map: Vec<(String, String)> = Vec::new();
    // Start with parent verbs
    for (verb, cmd) in parent.tack_verbs {
        verb_map.push((verb, cmd));
    }
    // Child verbs override or add
    for (verb, cmd) in child.tack_verbs {
        if let Some(existing) = verb_map.iter_mut().find(|(v, _)| v == &verb) {
            existing.1 = cmd;
        } else {
            verb_map.push((verb, cmd));
        }
    }
    let tack_verbs = verb_map;

    // SECRET: child overrides entirely if non-empty, else parent.
    let secrets = if child.secrets.is_empty() {
        parent.secrets
    } else {
        child.secrets
    };

    // TRUST: child overrides parent (project-scoped policy wins).
    let trust = child.trust.or(parent.trust);

    // BOOT: additive — parent boot steps first, child appends.
    let mut boot_steps = parent.boot_steps;
    boot_steps.extend(child.boot_steps);

    // VERB: additive — child overrides parent on conflict.
    let mut verb_defs = parent.verb_defs;
    for def in child.verb_defs {
        if let Some(existing) = verb_defs.iter_mut().find(|(v, _, _, _)| *v == def.0) {
            *existing = def;
        } else {
            verb_defs.push(def);
        }
    }

    // EXTENDS: not carried forward in merged result.
    Humanfile {
        identity,
        sign_key,
        model,
        fallback,
        available_models,
        drivers,
        tack_verbs,
        extends: None,
        secrets,
        trust,
        boot_steps,
        verb_defs,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ---------------------------------------------------------------
    // Parse basic HUMANFILE with all directives
    // ---------------------------------------------------------------

    #[test]
    fn test_parse_full_humanfile() {
        let content = r#"
# Global HUMANFILE
IDENTITY scott
SIGN BAF08C963C7E3184

MODEL claude-sonnet-4-5
FALLBACK mistral-large-latest

AVAILABLE <<LIST
claude-sonnet-4-5
claude-opus-4-6
claude-haiku-4-5
gemini-2.5-pro
LIST

DRIVER rust ostk mcp serve fcp-rust
DRIVER python ostk mcp serve fcp-python

TACK <<VERBS
:deploy → scripts/deploy.sh
:bench → ostk bench --docker
:status → git status --short
VERBS
"#;
        let hf = parse(content).unwrap();
        assert_eq!(hf.identity, Some("scott".to_string()));
        assert_eq!(hf.sign_key, Some("BAF08C963C7E3184".to_string()));
        assert_eq!(hf.model, Some("claude-sonnet-4-5".to_string()));
        assert_eq!(hf.fallback, Some("mistral-large-latest".to_string()));
        assert_eq!(hf.available_models.len(), 4);
        assert_eq!(hf.available_models[0], "claude-sonnet-4-5");
        assert_eq!(hf.available_models[3], "gemini-2.5-pro");
        assert_eq!(hf.drivers.len(), 2);
        assert_eq!(hf.drivers[0].0, "rust");
        assert_eq!(hf.drivers[0].1, "ostk mcp serve fcp-rust");
        assert_eq!(hf.drivers[1].0, "python");
        assert_eq!(hf.tack_verbs.len(), 3);
        assert_eq!(hf.tack_verbs[0].0, ":deploy");
        assert_eq!(hf.tack_verbs[0].1, "scripts/deploy.sh");
        assert_eq!(hf.tack_verbs[2].0, ":status");
        assert_eq!(hf.tack_verbs[2].1, "git status --short");
        assert!(hf.extends.is_none());
    }

    // ---------------------------------------------------------------
    // Parse with heredoc blocks (AVAILABLE, TACK)
    // ---------------------------------------------------------------

    #[test]
    fn test_parse_heredoc_available() {
        let content = r#"
IDENTITY me
SIGN AABBCCDD

AVAILABLE <<MODELS
model-a
model-b
model-c
MODELS
"#;
        let hf = parse(content).unwrap();
        assert_eq!(hf.available_models, vec!["model-a", "model-b", "model-c"]);
    }

    #[test]
    fn test_parse_heredoc_tack() {
        let content = r#"
IDENTITY me
SIGN AABB

TACK <<VERBS
:test → cargo test
:lint → cargo clippy
VERBS
"#;
        let hf = parse(content).unwrap();
        assert_eq!(hf.tack_verbs.len(), 2);
        assert_eq!(hf.tack_verbs[0], (":test".to_string(), "cargo test".to_string()));
        assert_eq!(hf.tack_verbs[1], (":lint".to_string(), "cargo clippy".to_string()));
    }

    #[test]
    fn test_parse_tack_ascii_arrow() {
        let content = r#"
IDENTITY me
SIGN AABB

TACK <<VERBS
:test -> cargo test
:lint -> cargo clippy
VERBS
"#;
        let hf = parse(content).unwrap();
        assert_eq!(hf.tack_verbs.len(), 2);
        assert_eq!(hf.tack_verbs[0].1, "cargo test");
    }

    #[test]
    fn test_heredoc_unclosed() {
        let content = "IDENTITY me\nSIGN AB\nAVAILABLE <<LIST\nmodel-a\nmodel-b\n";
        let err = parse(content).unwrap_err();
        match err {
            ParseError::UnclosedHeredoc { tag, .. } => assert_eq!(tag, "LIST"),
            other => panic!("expected UnclosedHeredoc, got: {other}"),
        }
    }

    #[test]
    fn test_heredoc_empty_block() {
        let content = "IDENTITY me\nSIGN AB\nAVAILABLE <<LIST\nLIST\n";
        let hf = parse(content).unwrap();
        assert!(hf.available_models.is_empty());
    }

    // ---------------------------------------------------------------
    // EXTENDS merge: child MODEL overrides parent
    // ---------------------------------------------------------------

    #[test]
    fn test_merge_model_override() {
        let parent = Humanfile {
            identity: Some("scott".to_string()),
            sign_key: Some("KEY123".to_string()),
            model: Some("claude-sonnet-4-5".to_string()),
            fallback: Some("mistral-large".to_string()),
            available_models: vec!["claude-sonnet-4-5".into(), "claude-opus-4-6".into()],
            ..Humanfile::default()
        };

        let child = Humanfile {
            model: Some("claude-opus-4-6".to_string()),
            drivers: vec![("rust".into(), "ostk mcp serve fcp-rust".into())],
            extends: Some("~/.HUMANFILE".to_string()),
            ..Humanfile::default()
        };

        let merged = merge(parent, child);
        // Child model overrides
        assert_eq!(merged.model, Some("claude-opus-4-6".to_string()));
        // Parent fallback preserved
        assert_eq!(merged.fallback, Some("mistral-large".to_string()));
        // Identity from parent
        assert_eq!(merged.identity, Some("scott".to_string()));
        assert_eq!(merged.sign_key, Some("KEY123".to_string()));
        // Child available_models is empty, so parent's is used
        assert_eq!(merged.available_models.len(), 2);
        // Drivers from child only
        assert_eq!(merged.drivers.len(), 1);
        assert_eq!(merged.drivers[0].0, "rust");
        // EXTENDS not carried forward
        assert!(merged.extends.is_none());
    }

    #[test]
    fn test_merge_available_override() {
        let parent = Humanfile {
            available_models: vec!["model-a".into(), "model-b".into()],
            ..Humanfile::default()
        };
        let child = Humanfile {
            available_models: vec!["model-x".into()],
            ..Humanfile::default()
        };
        let merged = merge(parent, child);
        // Child overrides entirely
        assert_eq!(merged.available_models, vec!["model-x"]);
    }

    #[test]
    fn test_merge_fallback_override() {
        let parent = Humanfile {
            fallback: Some("parent-fallback".to_string()),
            ..Humanfile::default()
        };
        let child = Humanfile {
            fallback: Some("child-fallback".to_string()),
            ..Humanfile::default()
        };
        let merged = merge(parent, child);
        assert_eq!(merged.fallback, Some("child-fallback".to_string()));
    }

    // ---------------------------------------------------------------
    // EXTENDS merge: TACK verbs merge, child wins conflict
    // ---------------------------------------------------------------

    #[test]
    fn test_merge_tack_additive() {
        let parent = Humanfile {
            tack_verbs: vec![
                (":deploy".into(), "scripts/deploy.sh".into()),
                (":status".into(), "git status".into()),
            ],
            ..Humanfile::default()
        };
        let child = Humanfile {
            tack_verbs: vec![
                (":test".into(), "cargo test --lib".into()),
                (":lint".into(), "cargo clippy".into()),
            ],
            ..Humanfile::default()
        };
        let merged = merge(parent, child);
        assert_eq!(merged.tack_verbs.len(), 4);
        // Parent verbs preserved
        assert!(merged.tack_verbs.iter().any(|(v, _)| v == ":deploy"));
        assert!(merged.tack_verbs.iter().any(|(v, _)| v == ":status"));
        // Child verbs added
        assert!(merged.tack_verbs.iter().any(|(v, _)| v == ":test"));
        assert!(merged.tack_verbs.iter().any(|(v, _)| v == ":lint"));
    }

    #[test]
    fn test_merge_tack_child_wins_conflict() {
        let parent = Humanfile {
            tack_verbs: vec![
                (":deploy".into(), "scripts/deploy.sh".into()),
                (":test".into(), "make test".into()),
            ],
            ..Humanfile::default()
        };
        let child = Humanfile {
            tack_verbs: vec![
                (":test".into(), "cargo test --lib".into()),
            ],
            ..Humanfile::default()
        };
        let merged = merge(parent, child);
        assert_eq!(merged.tack_verbs.len(), 2);
        // :deploy from parent preserved
        let deploy = merged.tack_verbs.iter().find(|(v, _)| v == ":deploy").unwrap();
        assert_eq!(deploy.1, "scripts/deploy.sh");
        // :test overridden by child
        let test = merged.tack_verbs.iter().find(|(v, _)| v == ":test").unwrap();
        assert_eq!(test.1, "cargo test --lib");
    }

    // ---------------------------------------------------------------
    // IDENTITY immutability: child with EXTENDS cannot set IDENTITY
    // ---------------------------------------------------------------

    #[test]
    fn test_identity_immutable_with_extends() {
        let content = r#"
EXTENDS ~/.HUMANFILE
IDENTITY impostor
MODEL claude-opus-4-6
"#;
        let err = parse(content).unwrap_err();
        match err {
            ParseError::IdentityOverride { directive, .. } => {
                assert_eq!(directive, "IDENTITY");
            }
            other => panic!("expected IdentityOverride, got: {other}"),
        }
    }

    #[test]
    fn test_sign_immutable_with_extends() {
        let content = r#"
EXTENDS ~/.HUMANFILE
SIGN DEADBEEF
MODEL claude-opus-4-6
"#;
        let err = parse(content).unwrap_err();
        match err {
            ParseError::IdentityOverride { directive, .. } => {
                assert_eq!(directive, "SIGN");
            }
            other => panic!("expected IdentityOverride, got: {other}"),
        }
    }

    #[test]
    fn test_identity_allowed_without_extends() {
        let content = "IDENTITY scott\nSIGN KEY123\nMODEL claude-sonnet-4-5";
        let hf = parse(content).unwrap();
        assert_eq!(hf.identity, Some("scott".to_string()));
        assert_eq!(hf.sign_key, Some("KEY123".to_string()));
    }

    // ---------------------------------------------------------------
    // Empty/missing file graceful handling
    // ---------------------------------------------------------------

    #[test]
    fn test_parse_empty_content() {
        let hf = parse("").unwrap();
        assert_eq!(hf, Humanfile::default());
    }

    #[test]
    fn test_parse_only_comments() {
        let content = "# just a comment\n# another one\n";
        let hf = parse(content).unwrap();
        assert_eq!(hf, Humanfile::default());
    }

    #[test]
    fn test_parse_only_blank_lines() {
        let content = "\n\n\n";
        let hf = parse(content).unwrap();
        assert_eq!(hf, Humanfile::default());
    }

    // ---------------------------------------------------------------
    // Edge cases and error handling
    // ---------------------------------------------------------------

    #[test]
    fn test_unknown_directive() {
        let content = "IDENTITY me\nSIGN AB\nFROM model-x\n";
        let err = parse(content).unwrap_err();
        match err {
            ParseError::UnknownDirective { directive, .. } => {
                assert_eq!(directive, "FROM");
            }
            other => panic!("expected UnknownDirective, got: {other}"),
        }
    }

    #[test]
    fn test_missing_identity_argument() {
        let content = "IDENTITY\n";
        let err = parse(content).unwrap_err();
        match err {
            ParseError::MissingArgument { directive, .. } => {
                assert_eq!(directive, "IDENTITY");
            }
            other => panic!("expected MissingArgument, got: {other}"),
        }
    }

    #[test]
    fn test_driver_missing_command() {
        let content = "IDENTITY me\nSIGN AB\nDRIVER rust\n";
        let err = parse(content).unwrap_err();
        match err {
            ParseError::InvalidDriver { .. } => {}
            other => panic!("expected InvalidDriver, got: {other}"),
        }
    }

    #[test]
    fn test_malformed_tack_verb_no_arrow() {
        let content = "IDENTITY me\nSIGN AB\nTACK <<VERBS\n:test cargo test\nVERBS\n";
        let err = parse(content).unwrap_err();
        match err {
            ParseError::MalformedTackVerb { .. } => {}
            other => panic!("expected MalformedTackVerb, got: {other}"),
        }
    }

    #[test]
    fn test_parse_project_humanfile_with_extends() {
        let content = r#"
EXTENDS ~/.HUMANFILE

MODEL claude-opus-4-6

DRIVER rust ostk mcp serve fcp-rust
DRIVER python ostk mcp serve fcp-python

TACK <<VERBS
:test → cargo test --lib
:lint → cargo clippy --all-targets
VERBS
"#;
        let hf = parse(content).unwrap();
        assert_eq!(hf.extends, Some("~/.HUMANFILE".to_string()));
        assert_eq!(hf.model, Some("claude-opus-4-6".to_string()));
        assert_eq!(hf.drivers.len(), 2);
        assert_eq!(hf.tack_verbs.len(), 2);
        assert!(hf.identity.is_none());
        assert!(hf.sign_key.is_none());
    }

    #[test]
    fn test_merge_drivers_child_only() {
        let parent = Humanfile {
            drivers: vec![("old".into(), "old-cmd".into())],
            ..Humanfile::default()
        };
        let child = Humanfile {
            drivers: vec![("rust".into(), "ostk mcp serve fcp-rust".into())],
            ..Humanfile::default()
        };
        let merged = merge(parent, child);
        // Only child drivers
        assert_eq!(merged.drivers.len(), 1);
        assert_eq!(merged.drivers[0].0, "rust");
    }

    #[test]
    fn test_merge_identity_from_parent() {
        let parent = Humanfile {
            identity: Some("scott".to_string()),
            sign_key: Some("KEY".to_string()),
            ..Humanfile::default()
        };
        let child = Humanfile::default();
        let merged = merge(parent, child);
        assert_eq!(merged.identity, Some("scott".to_string()));
        assert_eq!(merged.sign_key, Some("KEY".to_string()));
    }

    #[test]
    fn test_merge_both_empty() {
        let merged = merge(Humanfile::default(), Humanfile::default());
        assert_eq!(merged, Humanfile::default());
    }

    #[test]
    fn test_parse_model_only() {
        let content = "MODEL claude-sonnet-4-5";
        let hf = parse(content).unwrap();
        assert_eq!(hf.model, Some("claude-sonnet-4-5".to_string()));
        assert!(hf.identity.is_none());
    }

    #[test]
    fn test_heredoc_with_blank_lines_inside() {
        let content = "IDENTITY me\nSIGN AB\nAVAILABLE <<LIST\nmodel-a\n\nmodel-b\n\nLIST\n";
        let hf = parse(content).unwrap();
        // Blank lines inside heredoc are skipped
        assert_eq!(hf.available_models, vec!["model-a", "model-b"]);
    }

    #[test]
    fn test_code_fences_skip_allcaps_keywords() {
        // All-caps words inside code fences should not be parsed as directives
        let content = "IDENTITY me\nSIGN AB\n\n```\nBOOT → REFINE → COMPILE\nDELETE everything\n```\n";
        let hf = parse(content).unwrap();
        assert_eq!(hf.identity, Some("me".to_string()));
    }

    #[test]
    fn test_nested_code_fence_toggle() {
        // Code fence toggles off correctly
        let content = "MODEL claude-sonnet-4-5\n```\nBOOT\n```\nFALLBACK gpt-4o\n";
        let hf = parse(content).unwrap();
        assert_eq!(hf.model, Some("claude-sonnet-4-5".to_string()));
        assert_eq!(hf.fallback, Some("gpt-4o".to_string()));
    }

    #[test]
    fn test_legacy_markdown_humanfile_parses_without_error() {
        // The global ~/.HUMANFILE may contain markdown prose, YAML sections,
        // code fences with ALL_CAPS words (BOOT, DELETE), tables, etc.
        // The parser must skip prose lines (mixed case) and code-fenced
        // all-caps words without returning UnknownDirective errors.
        let content = r#"# HUMANFILE — Preferences
Codified from sessions.
This is prose that should be skipped.

## Core Philosophy

1. **Don't present options — execute**
- **Commit prefix:** `hay:` for resharpening

## Boot Protocol

```
BOOT → REFINE → COMPILE. Three steps.
DELETE everything dangerous.
```

## Tack Grammar

| Verb | Meaning |
|------|---------|
| `:plan` | plan work |

llm:
  model: mistral-large-latest

secrets:
  OPENROUTER_API_KEY: authorized

---

Last updated: 2026-03-11
"#;
        // Should parse without error — all prose/markdown/YAML is skipped
        let result = parse(content);
        assert!(result.is_ok(), "parse failed: {:?}", result.err());
    }

    // ---------------------------------------------------------------
    // TRUST directive
    // ---------------------------------------------------------------

    #[test]
    fn test_parse_trust_unsigned() {
        let content = "MODEL claude-sonnet-4-5\nTRUST unsigned\n";
        let hf = parse(content).unwrap();
        assert_eq!(hf.trust, Some("unsigned".to_string()));
    }

    #[test]
    fn test_parse_trust_signed() {
        let content = "MODEL claude-sonnet-4-5\nTRUST signed\n";
        let hf = parse(content).unwrap();
        assert_eq!(hf.trust, Some("signed".to_string()));
    }

    #[test]
    fn test_parse_trust_case_insensitive() {
        let content = "TRUST Unsigned\n";
        let hf = parse(content).unwrap();
        assert_eq!(hf.trust, Some("unsigned".to_string()));
    }

    #[test]
    fn test_parse_trust_missing_arg() {
        let content = "TRUST\n";
        let err = parse(content).unwrap_err();
        match err {
            ParseError::MissingArgument { directive, .. } => {
                assert_eq!(directive, "TRUST");
            }
            other => panic!("expected MissingArgument, got: {other}"),
        }
    }

    #[test]
    fn test_parse_boot_steps() {
        let content = "BOOT :verify-codeowners\nBOOT :load-team-context\n";
        let hf = parse(content).unwrap();
        assert_eq!(hf.boot_steps, vec!["verify-codeowners", "load-team-context"]);
    }

    #[test]
    fn test_parse_verb_defs() {
        let content = "VERB :deploy internal (path) deploy to production\n";
        let hf = parse(content).unwrap();
        assert_eq!(hf.verb_defs.len(), 1);
        assert_eq!(hf.verb_defs[0].0, "deploy");
        assert_eq!(hf.verb_defs[0].1, "internal");
        assert_eq!(hf.verb_defs[0].2, "(path)");
        assert_eq!(hf.verb_defs[0].3, "deploy to production");
    }

    #[test]
    fn test_merge_boot_steps_additive() {
        let parent = Humanfile {
            boot_steps: vec!["verify-codeowners".into()],
            ..Default::default()
        };
        let child = Humanfile {
            boot_steps: vec!["load-team-context".into()],
            ..Default::default()
        };
        let merged = merge(parent, child);
        assert_eq!(merged.boot_steps, vec!["verify-codeowners", "load-team-context"]);
    }

    #[test]
    fn test_merge_verb_defs_child_overrides() {
        let parent = Humanfile {
            verb_defs: vec![("deploy".into(), "ostk deploy".into(), "()".into(), "old".into())],
            ..Default::default()
        };
        let child = Humanfile {
            verb_defs: vec![("deploy".into(), "internal".into(), "(path)".into(), "new".into())],
            ..Default::default()
        };
        let merged = merge(parent, child);
        assert_eq!(merged.verb_defs.len(), 1);
        assert_eq!(merged.verb_defs[0].1, "internal", "child should override parent");
    }

    #[test]
    fn test_merge_trust_child_wins() {
        let parent = Humanfile {
            trust: Some("signed".to_string()),
            ..Humanfile::default()
        };
        let child = Humanfile {
            trust: Some("unsigned".to_string()),
            ..Humanfile::default()
        };
        let merged = merge(parent, child);
        assert_eq!(merged.trust, Some("unsigned".to_string()));
    }

    #[test]
    fn test_merge_trust_parent_fallback() {
        let parent = Humanfile {
            trust: Some("unsigned".to_string()),
            ..Humanfile::default()
        };
        let child = Humanfile::default();
        let merged = merge(parent, child);
        assert_eq!(merged.trust, Some("unsigned".to_string()));
    }

    #[test]
    fn test_no_trust_defaults_none() {
        let content = "MODEL claude-sonnet-4-5\n";
        let hf = parse(content).unwrap();
        assert!(hf.trust.is_none());
    }
}
