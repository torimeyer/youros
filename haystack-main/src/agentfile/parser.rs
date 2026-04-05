use std::fmt;

/// Represents a parsed LIMIT directive (key-value constraint).
#[derive(Debug, Clone, PartialEq)]
pub struct Limit {
    pub key: String,
    pub value: String,
}

/// Represents a parsed INTERRUPT directive (event-driven wake trigger).
#[derive(Debug, Clone, PartialEq)]
pub struct Interrupt {
    /// Event type: "file" or "event".
    pub event_type: String,
    /// Target: path for file watches, event name for event triggers.
    pub target: String,
}

/// Represents a parsed WORK directive (pull-model filter expression).
///
/// WORK declares which needles this CPU can execute — an affinity mask, not a todo list.
/// Expressions are matched against needle metadata to determine eligibility.
#[derive(Debug, Clone, PartialEq)]
pub struct WorkFilter {
    /// Raw filter expressions, e.g. ["tags=rust,bugfix", "priority>=P1"]
    pub expressions: Vec<String>,
}

/// Represents a parsed PROMPT directive — either inline text or a file reference.
#[derive(Debug, Clone, PartialEq)]
pub enum PromptSource {
    /// Inline string, e.g. PROMPT "You are a bug fixer."
    Inline(String),
    /// File reference, e.g. PROMPT file://prompts/bug-fixer.md
    FileRef(String),
}

/// A fully parsed Agentfile.
#[derive(Debug, Clone, PartialEq)]
pub struct Agentfile {
    /// Model name from the FROM directive (exactly one required).
    pub from: String,
    /// Ordered list of prompt sources (at least one required).
    pub prompts: Vec<PromptSource>,
    /// MCP tool names from TOOL directives.
    pub tools: Vec<String>,
    /// Skill bundle names from SKILL directives.
    pub skills: Vec<String>,
    /// Resource constraints from LIMIT directives.
    pub limits: Vec<Limit>,
    /// Pull filter from WORK directive (optional, at most one).
    pub work: Option<WorkFilter>,
    /// Event-driven wake triggers from INTERRUPT directives.
    pub interrupts: Vec<Interrupt>,
    /// →622: Kernel init command from BOOT directive.
    /// Runs before PROMPT is loaded. Protocol: `BOOT ostk boot`.
    pub boot_cmd: Option<String>,
    /// →649: Destructive operation policy. Controls what happens when the agent
    /// attempts a destructive shell command (terraform destroy, rm -rf, DROP TABLE, etc.)
    /// Values: "confirm" (default, 60s countdown), "deny" (block), "allow" (pass through).
    pub destructive_ops: Option<String>,
    /// Permission mode for harness tool execution.
    /// Values: "governed" (default — POST + chain verify before bypass),
    ///         "supervised" (human approves each action),
    ///         "restricted" (read-only tools only).
    /// Governed mode: agent runs autonomously but POST must pass and trust chain
    /// must verify. The Agentfile IS the governance — not a raw harness flag.
    pub permissions: Option<String>,
    /// Beta features to enable via API headers.
    /// Values: "context-management", "fast-mode", "token-counting", "code-execution",
    ///         "files-api", "extended-cache-ttl", "output-128k", "context-1m"
    pub betas: Vec<String>,
    /// →775: Pin name for capability enforcement.
    /// When set, `OSTK_PIN` env var is set to this value at dispatch time,
    /// activating pin.caps policy checks in `kernel::policy`.
    pub pin: Option<String>,
    /// →1011: Capability class patterns from TOOL directives.
    /// Parsed from `TOOL shell:read`, `TOOL shell:write(src/)`, etc.
    /// Each entry is (class_label, optional_pattern).
    pub tool_patterns: Vec<(String, Option<String>)>,
}

/// Errors that can occur during Agentfile parsing.
#[derive(Debug, Clone, PartialEq)]
pub enum ParseError {
    /// No FROM directive found.
    MissingFrom,
    /// Multiple FROM directives found.
    MultipleFrom { line: usize },
    /// No PROMPT directive found.
    MissingPrompt,
    /// Multiple WORK directives found (at most one allowed).
    MultipleWork { line: usize },
    /// A directive line could not be parsed.
    MalformedDirective { line: usize, content: String },
    /// Unknown directive keyword.
    UnknownDirective { line: usize, directive: String },
    /// A directive is missing its required argument.
    MissingArgument { line: usize, directive: String },
    /// LIMIT requires exactly two arguments (key and value).
    InvalidLimit { line: usize, content: String },
}

impl fmt::Display for ParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ParseError::MissingFrom => write!(f, "Agentfile missing required FROM directive"),
            ParseError::MultipleFrom { line } => {
                write!(
                    f,
                    "line {line}: multiple FROM directives (exactly one required)"
                )
            }
            ParseError::MissingPrompt => {
                write!(
                    f,
                    "Agentfile missing required PROMPT directive (at least one required)"
                )
            }
            ParseError::MultipleWork { line } => {
                write!(
                    f,
                    "line {line}: multiple WORK directives (at most one allowed)"
                )
            }
            ParseError::MalformedDirective { line, content } => {
                write!(f, "line {line}: malformed directive: {content}")
            }
            ParseError::UnknownDirective { line, directive } => {
                write!(f, "line {line}: unknown directive: {directive}")
            }
            ParseError::MissingArgument { line, directive } => {
                write!(f, "line {line}: {directive} requires an argument")
            }
            ParseError::InvalidLimit { line, content } => {
                write!(
                    f,
                    "line {line}: LIMIT requires key and value, got: {content}"
                )
            }
        }
    }
}

/// Known directive keywords. A line starting with one of these (followed by
/// whitespace or end-of-line) is treated as a new directive, terminating any
/// multi-line PROMPT block in progress.
const DIRECTIVE_KEYWORDS: &[&str] = &[
    "FROM", "PROMPT", "TOOL", "SKILL", "LIMIT", "WORK", "INTERRUPT", "BOOT", "BETA", "PIN",
];

/// Returns true if `line` (trimmed) starts with a known directive keyword.
fn is_directive_line(line: &str) -> bool {
    let first_word = match line.split_once(char::is_whitespace) {
        Some((w, _)) => w,
        None => line,
    };
    DIRECTIVE_KEYWORDS.contains(&first_word)
}

/// Parse an Agentfile from its text content.
///
/// Lines starting with `#` are comments. Blank lines are skipped (except
/// inside multi-line PROMPT blocks where they are preserved).
/// Directives are case-sensitive uppercase keywords followed by arguments.
///
/// →782: PROMPT supports multi-line freeform text. Content continues until
/// the next known directive keyword at the start of a line, or end of file.
pub fn parse(content: &str) -> Result<Agentfile, ParseError> {
    let mut from: Option<String> = None;
    let mut prompts: Vec<PromptSource> = Vec::new();
    let mut tools: Vec<String> = Vec::new();
    let mut skills: Vec<String> = Vec::new();
    let mut limits: Vec<Limit> = Vec::new();
    let mut work: Option<WorkFilter> = None;
    let mut interrupts: Vec<Interrupt> = Vec::new();
    // →622: BOOT directive — kernel command to run before PROMPT loads
    let mut boot_cmd: Option<String> = None;
    // →649: destructive_ops policy — confirm|deny|allow
    let mut _destructive_ops: Option<String> = None;
    let mut betas: Vec<String> = Vec::new();
    // →775: PIN directive — capability pin name
    let mut pin: Option<String> = None;
    // →1011: Capability class patterns from TOOL directives
    let mut tool_patterns: Vec<(String, Option<String>)> = Vec::new();

    let lines: Vec<&str> = content.lines().collect();
    let mut i = 0;

    while i < lines.len() {
        let line_num = i + 1;
        let line = lines[i].trim();

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
            "FROM" => {
                if rest.is_empty() {
                    return Err(ParseError::MissingArgument {
                        line: line_num,
                        directive: "FROM".to_string(),
                    });
                }
                if from.is_some() {
                    return Err(ParseError::MultipleFrom { line: line_num });
                }
                from = Some(rest.to_string());
            }
            "PROMPT" => {
                if rest.is_empty() {
                    return Err(ParseError::MissingArgument {
                        line: line_num,
                        directive: "PROMPT".to_string(),
                    });
                }
                // →782: For file:// refs and quoted strings, no multi-line continuation.
                // For unquoted inline text, collect continuation lines until next directive or EOF.
                if rest.starts_with("file://")
                    || (rest.starts_with('"') && rest.ends_with('"') && rest.len() >= 2)
                {
                    let prompt = parse_prompt_arg(rest);
                    prompts.push(prompt);
                } else {
                    // Unquoted inline text — collect continuation lines
                    let mut text_parts: Vec<String> = vec![rest.to_string()];
                    while i + 1 < lines.len() {
                        let next_line = lines[i + 1].trim();
                        // Comments end the block
                        if next_line.starts_with('#') {
                            break;
                        }
                        // A known directive keyword at the start of a non-empty line ends the block
                        if !next_line.is_empty() && is_directive_line(next_line) {
                            break;
                        }
                        // Blank lines and non-directive lines are continuation
                        text_parts.push(next_line.to_string());
                        i += 1;
                    }
                    // Trim trailing empty lines from the collected text
                    while text_parts.last().is_some_and(|l| l.is_empty()) {
                        text_parts.pop();
                    }
                    let full_text = text_parts.join("\n");
                    prompts.push(PromptSource::Inline(full_text));
                }
            }
            "TOOL" => {
                if rest.is_empty() {
                    return Err(ParseError::MissingArgument {
                        line: line_num,
                        directive: "TOOL".to_string(),
                    });
                }
                // →1011: Check for capability class pattern syntax.
                // Format: "shell:read", "shell:write(src/)", "kernel:spawn", etc.
                if let Some(pattern) = parse_tool_pattern(rest) {
                    tool_patterns.push(pattern);
                } else {
                    let canonical = match rest {
                        "shell" | "bash" | "sh_run" => "shell",
                        "file:read" | "fs_read" => "file:read",
                        "file:edit" | "fs_ops" => "file:edit",
                        "file:write" => "file:edit",
                        "spawn" | "sh_spawn" => "spawn",
                        "interact" | "sh_interact" => "interact",
                        "session" | "sh_session" => "session",
                        "lock" | "sh_lock" => "lock",
                        "help" | "sh_help" => "help",
                        other => other,
                    };
                    tools.push(canonical.to_string());
                }
            }
            "SKILL" => {
                if rest.is_empty() {
                    return Err(ParseError::MissingArgument {
                        line: line_num,
                        directive: "SKILL".to_string(),
                    });
                }
                skills.push(rest.to_string());
            }
            "LIMIT" => {
                if rest.is_empty() {
                    return Err(ParseError::MissingArgument {
                        line: line_num,
                        directive: "LIMIT".to_string(),
                    });
                }
                let parts: Vec<&str> = rest.splitn(2, char::is_whitespace).collect();
                if parts.len() < 2 || parts[1].trim().is_empty() {
                    return Err(ParseError::InvalidLimit {
                        line: line_num,
                        content: rest.to_string(),
                    });
                }
                limits.push(Limit {
                    key: parts[0].to_string(),
                    value: parts[1].trim().to_string(),
                });
            }
            "INTERRUPT" => {
                if rest.is_empty() {
                    return Err(ParseError::MissingArgument {
                        line: line_num,
                        directive: "INTERRUPT".to_string(),
                    });
                }
                let interrupt = parse_interrupt_arg(rest, line_num)?;
                interrupts.push(interrupt);
            }
            "WORK" => {
                if rest.is_empty() {
                    return Err(ParseError::MissingArgument {
                        line: line_num,
                        directive: "WORK".to_string(),
                    });
                }
                if work.is_some() {
                    return Err(ParseError::MultipleWork { line: line_num });
                }
                let expressions: Vec<String> =
                    rest.split_whitespace().map(|s| s.to_string()).collect();
                work = Some(WorkFilter { expressions });
            }
                    // →622: BOOT directive — kernel init command before PROMPT
            // Protocol: ostk boot fires first, PROMPT loads on POST.
            "BOOT" => {
                if rest.is_empty() {
                    return Err(ParseError::MissingArgument {
                        line: line_num,
                        directive: "BOOT".to_string(),
                    });
                }
                boot_cmd = Some(rest.to_string());
            }
            "BETA" => {
                if rest.is_empty() {
                    return Err(ParseError::MissingArgument {
                        line: line_num,
                        directive: "BETA".to_string(),
                    });
                }
                betas.push(rest.to_string());
            }
            // →775: PIN directive — activate pin.caps capability enforcement
            "PIN" => {
                if rest.is_empty() {
                    return Err(ParseError::MissingArgument {
                        line: line_num,
                        directive: "PIN".to_string(),
                    });
                }
                pin = Some(rest.to_string());
            }
            _ => {
                return Err(ParseError::UnknownDirective {
                    line: line_num,
                    directive: directive.to_string(),
                });
            }
        }

        i += 1;
    }

    // Validate required directives
    // →649: extract destructive_ops from limits (LIMIT destructive_ops confirm|deny|allow)
    let destructive_ops = limits
        .iter()
        .find(|l| l.key == "destructive_ops")
        .map(|l| l.value.clone());

    // Extract permissions mode from limits (LIMIT permissions governed|supervised|restricted)
    // governed = POST + chain verify → autonomous. supervised = human approves. restricted = read-only.
    let permissions = limits
        .iter()
        .find(|l| l.key == "permissions")
        .map(|l| l.value.clone());

    let from = from.ok_or(ParseError::MissingFrom)?;
    if prompts.is_empty() {
        return Err(ParseError::MissingPrompt);
    }

    Ok(Agentfile {
        from,
        prompts,
        tools,
        skills,
        limits,
        work,
        interrupts,
        boot_cmd,
        destructive_ops,
        permissions,
        betas,
        pin,
        tool_patterns,
    })
}

/// →1011: Parse a TOOL value as a capability class pattern.
///
/// Returns Some((class_label, optional_pattern)) if the value matches a known
/// capability class (e.g. "shell:read", "shell:write(src/)").
/// Returns None if it's a regular tool name (e.g. "shell", "ostk").
fn parse_tool_pattern(value: &str) -> Option<(String, Option<String>)> {
    // Must contain a colon to be a capability class pattern
    if !value.contains(':') {
        return None;
    }

    // Check for optional pattern in parens: "shell:write(src/)"
    let (class_part, pattern) = if let Some(paren_start) = value.find('(') {
        if value.ends_with(')') {
            let class = &value[..paren_start];
            let pat = &value[paren_start + 1..value.len() - 1];
            (class, Some(pat.to_string()))
        } else {
            (value, None)
        }
    } else {
        (value, None)
    };

    // Only shell:* and kernel:* are treated as capability patterns.
    // file:read / file:edit / file:write are existing tool aliases that must
    // continue flowing into the tools vec for API visibility.
    // Exception: file:* WITH a paren pattern (e.g. "file:write(src/)") is a
    // capability pattern since the existing alias system doesn't support patterns.
    let is_new_class = class_part.starts_with("shell:") || class_part.starts_with("kernel:");
    let is_file_with_pattern = class_part.starts_with("file:") && pattern.is_some();

    if is_new_class || is_file_with_pattern {
        // Validate against known labels
        let known = [
            "file:read", "file:edit", "file:write",
            "shell:read", "shell:write", "shell:exec", "shell:secret",
            "kernel:read", "kernel:write", "kernel:spawn", "kernel:secret",
        ];
        if known.contains(&class_part) {
            return Some((class_part.to_string(), pattern));
        }
    }

    None
}

/// Parse an INTERRUPT argument: `<type>:<target>` where type is "file" or "event".
fn parse_interrupt_arg(arg: &str, line: usize) -> Result<Interrupt, ParseError> {
    if let Some((event_type, target)) = arg.split_once(':') {
        if target.is_empty() {
            return Err(ParseError::MissingArgument {
                line,
                directive: "INTERRUPT".to_string(),
            });
        }
        Ok(Interrupt {
            event_type: event_type.to_string(),
            target: target.to_string(),
        })
    } else {
        Err(ParseError::MalformedDirective {
            line,
            content: format!("INTERRUPT requires <type>:<target>, got: {arg}"),
        })
    }
}

/// Parse a PROMPT argument: either a quoted inline string or a file:// reference.
fn parse_prompt_arg(arg: &str) -> PromptSource {
    if arg.starts_with("file://") {
        PromptSource::FileRef(arg.strip_prefix("file://").unwrap().to_string())
    } else if arg.starts_with('"') && arg.ends_with('"') && arg.len() >= 2 {
        // Strip surrounding quotes
        PromptSource::Inline(arg[1..arg.len() - 1].to_string())
    } else {
        // Treat as inline text (no quotes required for simple values)
        PromptSource::Inline(arg.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_full_agentfile() {
        let content = r#"
# This is a comment
FROM claude-sonnet-4-6
PROMPT "You are a Rust systems engineer."
PROMPT file://prompts/bug-fixer.md
TOOL ostk
TOOL fcp-rust
SKILL tdd
LIMIT context_pct 80
LIMIT budget_usd 5
WORK tags=rust,bugfix priority>=P1
"#;
        let af = parse(content).unwrap();
        assert_eq!(af.from, "claude-sonnet-4-6");
        assert_eq!(af.prompts.len(), 2);
        assert_eq!(
            af.prompts[0],
            PromptSource::Inline("You are a Rust systems engineer.".to_string())
        );
        assert_eq!(
            af.prompts[1],
            PromptSource::FileRef("prompts/bug-fixer.md".to_string())
        );
        assert_eq!(af.tools, vec!["ostk", "fcp-rust"]);
        assert_eq!(af.skills, vec!["tdd"]);
        assert_eq!(af.limits.len(), 2);
        assert_eq!(af.limits[0].key, "context_pct");
        assert_eq!(af.limits[0].value, "80");
        assert_eq!(af.limits[1].key, "budget_usd");
        assert_eq!(af.limits[1].value, "5");
        let work = af.work.unwrap();
        assert_eq!(work.expressions, vec!["tags=rust,bugfix", "priority>=P1"]);
    }

    #[test]
    fn test_from_directive() {
        let content = "FROM claude-sonnet-4-6\nPROMPT \"hello\"";
        let af = parse(content).unwrap();
        assert_eq!(af.from, "claude-sonnet-4-6");
    }

    #[test]
    fn test_missing_from() {
        let content = "PROMPT \"hello\"\nTOOL ostk";
        let err = parse(content).unwrap_err();
        assert_eq!(err, ParseError::MissingFrom);
    }

    #[test]
    fn test_multiple_from() {
        let content = "FROM model-a\nPROMPT \"hello\"\nFROM model-b";
        let err = parse(content).unwrap_err();
        assert_eq!(err, ParseError::MultipleFrom { line: 3 });
    }

    #[test]
    fn test_from_missing_argument() {
        let content = "FROM\nPROMPT \"hello\"";
        let err = parse(content).unwrap_err();
        assert_eq!(
            err,
            ParseError::MissingArgument {
                line: 1,
                directive: "FROM".to_string()
            }
        );
    }

    #[test]
    fn test_prompt_inline() {
        let content = "FROM m\nPROMPT \"You are a bug fixer.\"";
        let af = parse(content).unwrap();
        assert_eq!(
            af.prompts[0],
            PromptSource::Inline("You are a bug fixer.".to_string())
        );
    }

    #[test]
    fn test_prompt_file_ref() {
        let content = "FROM m\nPROMPT file://prompts/bug-fixer.md";
        let af = parse(content).unwrap();
        assert_eq!(
            af.prompts[0],
            PromptSource::FileRef("prompts/bug-fixer.md".to_string())
        );
    }

    #[test]
    fn test_prompt_multiple_concatenated() {
        let content = "FROM m\nPROMPT \"First.\"\nPROMPT \"Second.\"\nPROMPT file://third.md";
        let af = parse(content).unwrap();
        assert_eq!(af.prompts.len(), 3);
    }

    #[test]
    fn test_missing_prompt() {
        let content = "FROM m\nTOOL ostk";
        let err = parse(content).unwrap_err();
        assert_eq!(err, ParseError::MissingPrompt);
    }

    #[test]
    fn test_prompt_missing_argument() {
        let content = "FROM m\nPROMPT";
        let err = parse(content).unwrap_err();
        assert_eq!(
            err,
            ParseError::MissingArgument {
                line: 2,
                directive: "PROMPT".to_string()
            }
        );
    }

    #[test]
    fn test_tool_directive() {
        let content = "FROM m\nPROMPT \"p\"\nTOOL ostk\nTOOL fcp-rust\nTOOL fs_ops";
        let af = parse(content).unwrap();
        assert_eq!(af.tools, vec!["ostk", "fcp-rust", "file:edit"]);
    }

    #[test]
    fn test_tool_missing_argument() {
        let content = "FROM m\nPROMPT \"p\"\nTOOL";
        let err = parse(content).unwrap_err();
        assert_eq!(
            err,
            ParseError::MissingArgument {
                line: 3,
                directive: "TOOL".to_string()
            }
        );
    }

    #[test]
    fn test_skill_directive() {
        let content = "FROM m\nPROMPT \"p\"\nSKILL tdd\nSKILL commit";
        let af = parse(content).unwrap();
        assert_eq!(af.skills, vec!["tdd", "commit"]);
    }

    #[test]
    fn test_skill_missing_argument() {
        let content = "FROM m\nPROMPT \"p\"\nSKILL";
        let err = parse(content).unwrap_err();
        assert_eq!(
            err,
            ParseError::MissingArgument {
                line: 3,
                directive: "SKILL".to_string()
            }
        );
    }

    #[test]
    fn test_limit_directive() {
        let content = "FROM m\nPROMPT \"p\"\nLIMIT context_pct 80\nLIMIT budget_usd 5.00";
        let af = parse(content).unwrap();
        assert_eq!(af.limits.len(), 2);
        assert_eq!(af.limits[0].key, "context_pct");
        assert_eq!(af.limits[0].value, "80");
        assert_eq!(af.limits[1].key, "budget_usd");
        assert_eq!(af.limits[1].value, "5.00");
    }

    #[test]
    fn test_limit_missing_value() {
        let content = "FROM m\nPROMPT \"p\"\nLIMIT context_pct";
        let err = parse(content).unwrap_err();
        assert_eq!(
            err,
            ParseError::InvalidLimit {
                line: 3,
                content: "context_pct".to_string()
            }
        );
    }

    #[test]
    fn test_limit_missing_argument() {
        let content = "FROM m\nPROMPT \"p\"\nLIMIT";
        let err = parse(content).unwrap_err();
        assert_eq!(
            err,
            ParseError::MissingArgument {
                line: 3,
                directive: "LIMIT".to_string()
            }
        );
    }

    #[test]
    fn test_work_directive() {
        let content = "FROM m\nPROMPT \"p\"\nWORK tags=rust,bugfix priority>=P1";
        let af = parse(content).unwrap();
        let work = af.work.unwrap();
        assert_eq!(work.expressions, vec!["tags=rust,bugfix", "priority>=P1"]);
    }

    #[test]
    fn test_work_missing_argument() {
        let content = "FROM m\nPROMPT \"p\"\nWORK";
        let err = parse(content).unwrap_err();
        assert_eq!(
            err,
            ParseError::MissingArgument {
                line: 3,
                directive: "WORK".to_string()
            }
        );
    }

    #[test]
    fn test_multiple_work() {
        let content = "FROM m\nPROMPT \"p\"\nWORK tags=a\nWORK tags=b";
        let err = parse(content).unwrap_err();
        assert_eq!(err, ParseError::MultipleWork { line: 4 });
    }

    #[test]
    fn test_work_optional() {
        let content = "FROM m\nPROMPT \"p\"";
        let af = parse(content).unwrap();
        assert!(af.work.is_none());
    }

    #[test]
    fn test_unknown_directive() {
        let content = "FROM m\nPROMPT \"p\"\nRUN echo hello";
        let err = parse(content).unwrap_err();
        assert_eq!(
            err,
            ParseError::UnknownDirective {
                line: 3,
                directive: "RUN".to_string()
            }
        );
    }

    #[test]
    fn test_comments_and_blanks_skipped() {
        let content = r#"
# comment
FROM m

# another comment
PROMPT "p"

"#;
        let af = parse(content).unwrap();
        assert_eq!(af.from, "m");
    }

    #[test]
    fn test_no_tools_is_valid() {
        let content = "FROM m\nPROMPT \"p\"";
        let af = parse(content).unwrap();
        assert!(af.tools.is_empty());
    }

    #[test]
    fn test_no_skills_is_valid() {
        let content = "FROM m\nPROMPT \"p\"";
        let af = parse(content).unwrap();
        assert!(af.skills.is_empty());
    }

    #[test]
    fn test_no_limits_is_valid() {
        let content = "FROM m\nPROMPT \"p\"";
        let af = parse(content).unwrap();
        assert!(af.limits.is_empty());
    }

    #[test]
    fn test_prompt_unquoted_inline() {
        let content = "FROM m\nPROMPT You are a helper";
        let af = parse(content).unwrap();
        assert_eq!(
            af.prompts[0],
            PromptSource::Inline("You are a helper".to_string())
        );
    }

    #[test]
    fn test_empty_file() {
        let content = "";
        let err = parse(content).unwrap_err();
        assert_eq!(err, ParseError::MissingFrom);
    }

    #[test]
    fn test_only_comments() {
        let content = "# just a comment\n# another one";
        let err = parse(content).unwrap_err();
        assert_eq!(err, ParseError::MissingFrom);
    }

    // --- INTERRUPT directive tests (→422) ---

    #[test]
    fn test_interrupt_file_trigger() {
        let content = "FROM m\nPROMPT \"p\"\nINTERRUPT file:.ostk/needles/issues.jsonl";
        let af = parse(content).unwrap();
        assert_eq!(af.interrupts.len(), 1);
        assert_eq!(af.interrupts[0].event_type, "file");
        assert_eq!(
            af.interrupts[0].target,
            ".ostk/needles/issues.jsonl"
        );
    }

    #[test]
    fn test_interrupt_event_trigger() {
        let content = "FROM m\nPROMPT \"p\"\nINTERRUPT event:hay.filed";
        let af = parse(content).unwrap();
        assert_eq!(af.interrupts.len(), 1);
        assert_eq!(af.interrupts[0].event_type, "event");
        assert_eq!(af.interrupts[0].target, "hay.filed");
    }

    #[test]
    fn test_multiple_interrupts() {
        let content = "FROM m\nPROMPT \"p\"\nINTERRUPT file:.ostk/needles/issues.jsonl\nINTERRUPT event:needle.closed\nINTERRUPT event:hay.filed";
        let af = parse(content).unwrap();
        assert_eq!(af.interrupts.len(), 3);
        assert_eq!(af.interrupts[0].event_type, "file");
        assert_eq!(af.interrupts[1].event_type, "event");
        assert_eq!(af.interrupts[1].target, "needle.closed");
        assert_eq!(af.interrupts[2].target, "hay.filed");
    }

    #[test]
    fn test_interrupt_missing_argument() {
        let content = "FROM m\nPROMPT \"p\"\nINTERRUPT";
        let err = parse(content).unwrap_err();
        assert_eq!(
            err,
            ParseError::MissingArgument {
                line: 3,
                directive: "INTERRUPT".to_string()
            }
        );
    }

    #[test]
    fn test_interrupt_malformed_no_colon() {
        let content = "FROM m\nPROMPT \"p\"\nINTERRUPT badformat";
        let err = parse(content).unwrap_err();
        assert_eq!(
            err,
            ParseError::MalformedDirective {
                line: 3,
                content: "INTERRUPT requires <type>:<target>, got: badformat".to_string()
            }
        );
    }

    #[test]
    fn test_interrupt_empty_target() {
        let content = "FROM m\nPROMPT \"p\"\nINTERRUPT file:";
        let err = parse(content).unwrap_err();
        assert_eq!(
            err,
            ParseError::MissingArgument {
                line: 3,
                directive: "INTERRUPT".to_string()
            }
        );
    }

    #[test]
    fn test_no_interrupts_is_valid() {
        let content = "FROM m\nPROMPT \"p\"";
        let af = parse(content).unwrap();
        assert!(af.interrupts.is_empty());
    }

    // --- WORK affinity semantics tests (→423) ---

    #[test]
    fn test_work_needle_affinity_filters() {
        let content = "FROM m\nPROMPT \"p\"\nWORK priority=P0 tags=rust";
        let af = parse(content).unwrap();
        let work = af.work.unwrap();
        assert_eq!(work.expressions, vec!["priority=P0", "tags=rust"]);
    }

    #[test]
    fn test_full_agentfile_with_interrupts() {
        let content = r#"
# Full Agentfile with all directives
FROM claude-sonnet-4-6
PROMPT "You are a Rust systems engineer."
PROMPT file://prompts/bug-fixer.md
TOOL ostk
TOOL fcp-rust
SKILL tdd
LIMIT context_pct 80
LIMIT budget_usd 5
WORK tags=rust,bugfix priority>=P1
INTERRUPT file:.ostk/needles/issues.jsonl
INTERRUPT event:needle.closed
"#;
        let af = parse(content).unwrap();
        assert_eq!(af.from, "claude-sonnet-4-6");
        assert_eq!(af.prompts.len(), 2);
        assert_eq!(af.tools, vec!["ostk", "fcp-rust"]);
        assert_eq!(af.skills, vec!["tdd"]);
        assert_eq!(af.limits.len(), 2);
        let work = af.work.unwrap();
        assert_eq!(work.expressions, vec!["tags=rust,bugfix", "priority>=P1"]);
        assert_eq!(af.interrupts.len(), 2);
        assert_eq!(af.interrupts[0].event_type, "file");
        assert_eq!(af.interrupts[0].target, ".ostk/needles/issues.jsonl");
        assert_eq!(af.interrupts[1].event_type, "event");
        assert_eq!(af.interrupts[1].target, "needle.closed");
    }

    // --- PIN directive tests (→775) ---

    #[test]
    fn test_pin_directive() {
        let content = "FROM m\nPROMPT \"p\"\nPIN default";
        let af = parse(content).unwrap();
        assert_eq!(af.pin, Some("default".to_string()));
    }

    #[test]
    fn test_pin_directive_custom_name() {
        let content = "FROM m\nPROMPT \"p\"\nPIN worker-1";
        let af = parse(content).unwrap();
        assert_eq!(af.pin, Some("worker-1".to_string()));
    }

    #[test]
    fn test_pin_missing_argument() {
        let content = "FROM m\nPROMPT \"p\"\nPIN";
        let err = parse(content).unwrap_err();
        assert_eq!(
            err,
            ParseError::MissingArgument {
                line: 3,
                directive: "PIN".to_string()
            }
        );
    }

    #[test]
    fn test_no_pin_is_valid() {
        let content = "FROM m\nPROMPT \"p\"";
        let af = parse(content).unwrap();
        assert!(af.pin.is_none());
    }

    // --- →782: Multi-line PROMPT tests ---

    #[test]
    fn test_prompt_multiline_with_the_on_own_line() {
        // "The" on its own line should NOT be treated as a directive
        let content = "FROM m\nPROMPT You are a helpful assistant.\nThe best one ever.\nAlways be kind.\nTOOL shell";
        let af = parse(content).unwrap();
        assert_eq!(af.prompts.len(), 1);
        assert_eq!(
            af.prompts[0],
            PromptSource::Inline("You are a helpful assistant.\nThe best one ever.\nAlways be kind.".to_string())
        );
        assert_eq!(af.tools, vec!["shell"]);
    }

    #[test]
    fn test_prompt_multiline_ends_at_next_directive() {
        let content = "FROM m\nPROMPT You are a bug fixer.\nFix all the bugs.\nLIMIT tokens 8192";
        let af = parse(content).unwrap();
        assert_eq!(af.prompts.len(), 1);
        assert_eq!(
            af.prompts[0],
            PromptSource::Inline("You are a bug fixer.\nFix all the bugs.".to_string())
        );
        assert_eq!(af.limits[0].key, "tokens");
        assert_eq!(af.limits[0].value, "8192");
    }

    #[test]
    fn test_prompt_multiline_ends_at_eof() {
        let content = "FROM m\nPROMPT You are a helper.\nDo your best.\nBe thorough.";
        let af = parse(content).unwrap();
        assert_eq!(af.prompts.len(), 1);
        assert_eq!(
            af.prompts[0],
            PromptSource::Inline("You are a helper.\nDo your best.\nBe thorough.".to_string())
        );
    }

    #[test]
    fn test_prompt_multiline_with_blank_lines() {
        // Blank lines inside multi-line PROMPT are preserved
        let content = "FROM m\nPROMPT First paragraph.\n\nSecond paragraph.\nTOOL shell";
        let af = parse(content).unwrap();
        assert_eq!(af.prompts.len(), 1);
        assert_eq!(
            af.prompts[0],
            PromptSource::Inline("First paragraph.\n\nSecond paragraph.".to_string())
        );
        assert_eq!(af.tools, vec!["shell"]);
    }

    #[test]
    fn test_prompt_quoted_no_multiline() {
        // Quoted PROMPT stays single-line (no continuation)
        let content = "FROM m\nPROMPT \"You are a helper.\"\nTOOL shell";
        let af = parse(content).unwrap();
        assert_eq!(af.prompts.len(), 1);
        assert_eq!(
            af.prompts[0],
            PromptSource::Inline("You are a helper.".to_string())
        );
        assert_eq!(af.tools, vec!["shell"]);
    }

    #[test]
    fn test_prompt_fileref_no_multiline() {
        // file:// PROMPT stays single-line (no continuation)
        let content = "FROM m\nPROMPT file://prompts/system.md\nTOOL shell";
        let af = parse(content).unwrap();
        assert_eq!(af.prompts.len(), 1);
        assert_eq!(
            af.prompts[0],
            PromptSource::FileRef("prompts/system.md".to_string())
        );
        assert_eq!(af.tools, vec!["shell"]);
    }

    #[test]
    fn test_prompt_multiline_stops_at_comment() {
        let content = "FROM m\nPROMPT You are a helper.\nBe kind.\n# This is a comment\nTOOL shell";
        let af = parse(content).unwrap();
        assert_eq!(af.prompts.len(), 1);
        assert_eq!(
            af.prompts[0],
            PromptSource::Inline("You are a helper.\nBe kind.".to_string())
        );
        assert_eq!(af.tools, vec!["shell"]);
    }

    #[test]
    fn test_prompt_multiline_multiple_prompts() {
        // Two separate PROMPT directives, each multi-line
        let content = "FROM m\nPROMPT First prompt.\nContinues here.\nPROMPT Second prompt.\nAlso continues.";
        let af = parse(content).unwrap();
        assert_eq!(af.prompts.len(), 2);
        assert_eq!(
            af.prompts[0],
            PromptSource::Inline("First prompt.\nContinues here.".to_string())
        );
        assert_eq!(
            af.prompts[1],
            PromptSource::Inline("Second prompt.\nAlso continues.".to_string())
        );
    }

    #[test]
    fn test_prompt_multiline_trims_trailing_blank_lines() {
        let content = "FROM m\nPROMPT You are a helper.\n\n\nTOOL shell";
        let af = parse(content).unwrap();
        assert_eq!(af.prompts.len(), 1);
        assert_eq!(
            af.prompts[0],
            PromptSource::Inline("You are a helper.".to_string())
        );
    }

    #[test]
    fn test_prompt_unquoted_the_quick_brown_fox() {
        // →782: "The" is not a directive — entire line is prompt text
        let content = "FROM m\nPROMPT The quick brown fox";
        let af = parse(content).unwrap();
        assert_eq!(
            af.prompts[0],
            PromptSource::Inline("The quick brown fox".to_string())
        );
    }

    #[test]
    fn test_prompt_unquoted_fix_the_bug() {
        // →782: "Fix" is not a directive — rest of line is prompt text
        let content = "FROM m\nPROMPT Fix the bug";
        let af = parse(content).unwrap();
        assert_eq!(
            af.prompts[0],
            PromptSource::Inline("Fix the bug".to_string())
        );
    }

    #[test]
    fn test_prompt_unquoted_sentence_with_from_word() {
        // →782: "From" (lowercase) is not the FROM directive
        let content = "FROM m\nPROMPT Messages from the user should be answered.";
        let af = parse(content).unwrap();
        assert_eq!(
            af.prompts[0],
            PromptSource::Inline("Messages from the user should be answered.".to_string())
        );
    }

    #[test]
    fn test_prompt_unquoted_starts_with_directive_word() {
        // →782: "TOOL" as part of prompt text on the same line is fine
        let content = "FROM m\nPROMPT TOOL usage instructions follow.\nLIMIT tokens 8192";
        let af = parse(content).unwrap();
        assert_eq!(
            af.prompts[0],
            PromptSource::Inline("TOOL usage instructions follow.".to_string())
        );
    }

    #[test]
    fn test_prompt_multiline_all_directive_keywords_terminate() {
        // Each known keyword should terminate the multi-line block.
        // Use valid arguments for each directive to avoid parse errors.
        let cases: &[(&str, &str)] = &[
            ("FROM", "other-model"),    // will error (MultipleFrom), but tests termination
            ("PROMPT", "\"second\""),
            ("TOOL", "shell"),
            ("SKILL", "tdd"),
            ("LIMIT", "tokens 8192"),
            ("WORK", "tags=rust"),
            ("INTERRUPT", "event:test"),
            ("BOOT", "ostk boot"),
            ("BETA", "context-management"),
            ("PIN", "default"),
        ];
        for (keyword, arg) in cases {
            let content = format!("FROM m\nPROMPT Hello world.\nSome text.\n{} {}", keyword, arg);
            let af = parse(&content);
            if *keyword == "FROM" {
                assert!(af.is_err(), "double FROM should error");
            } else {
                let af = af.unwrap_or_else(|e| panic!("failed for keyword {}: {}", keyword, e));
                assert_eq!(
                    af.prompts[0],
                    PromptSource::Inline("Hello world.\nSome text.".to_string()),
                    "multi-line PROMPT should terminate before {} directive",
                    keyword
                );
            }
        }
    }
}
