//! Embedded grammar registry.
//!
//! All grammar TOML files are compiled into the binary via `include_str!`.
//! The `GRAMMARS` static provides a lazy-initialized map of tool name to
//! parsed `Grammar`, with shared rule inheritance already resolved.

use std::collections::HashMap;
use std::sync::LazyLock;

use super::grammar::{self, Grammar, Rule};

// ---------------------------------------------------------------------------
// Embedded TOML sources
// ---------------------------------------------------------------------------

const TOOL_GRAMMAR_SOURCES: &[(&str, &str)] = &[
    ("ansible", include_str!("toml/ansible.toml")),
    ("apt", include_str!("toml/apt.toml")),
    ("brew", include_str!("toml/brew.toml")),
    ("cargo", include_str!("toml/cargo.toml")),
    ("cat", include_str!("toml/cat.toml")),
    ("curl", include_str!("toml/curl.toml")),
    ("docker", include_str!("toml/docker.toml")),
    ("gcc", include_str!("toml/gcc.toml")),
    ("git", include_str!("toml/git.toml")),
    ("go", include_str!("toml/go.toml")),
    ("head", include_str!("toml/head.toml")),
    ("jest", include_str!("toml/jest.toml")),
    ("kubectl", include_str!("toml/kubectl.toml")),
    ("make", include_str!("toml/make.toml")),
    ("npm", include_str!("toml/npm.toml")),
    ("pip", include_str!("toml/pip.toml")),
    ("pytest", include_str!("toml/pytest.toml")),
    ("python3", include_str!("toml/python3.toml")),
    ("rsync", include_str!("toml/rsync.toml")),
    ("rustc", include_str!("toml/rustc.toml")),
    ("sed", include_str!("toml/sed.toml")),
    ("ssh", include_str!("toml/ssh.toml")),
    ("systemctl", include_str!("toml/systemctl.toml")),
    ("tail", include_str!("toml/tail.toml")),
    ("terraform", include_str!("toml/terraform.toml")),
    ("webpack", include_str!("toml/webpack.toml")),
];

const SHARED_GRAMMAR_SOURCES: &[(&str, &str)] = &[
    ("ansi-progress", include_str!("toml/_shared/ansi-progress.toml")),
    ("c-compiler-output", include_str!("toml/_shared/c-compiler-output.toml")),
    ("node-stacktrace", include_str!("toml/_shared/node-stacktrace.toml")),
    ("python-traceback", include_str!("toml/_shared/python-traceback.toml")),
];

// ---------------------------------------------------------------------------
// Lazy registry
// ---------------------------------------------------------------------------

/// All tool grammars, parsed and with shared rule inheritance resolved.
pub static GRAMMARS: LazyLock<HashMap<String, Grammar>> = LazyLock::new(|| {
    // First pass: parse shared rules
    let mut shared_rules: HashMap<String, Vec<Rule>> = HashMap::new();
    for (name, content) in SHARED_GRAMMAR_SOURCES {
        if let Ok(rules) = grammar::load_shared_rules_from_str(content) {
            shared_rules.insert(name.to_string(), rules);
        }
    }

    // Second pass: parse tool grammars and resolve inheritance
    let mut map = HashMap::new();
    for (name, content) in TOOL_GRAMMAR_SOURCES {
        if let Ok(mut g) = grammar::load_grammar_from_str(name, content) {
            grammar::resolve_inherit(&mut g, &shared_rules);
            map.insert(name.to_string(), g);
        }
    }
    map
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_grammar_count() {
        // 26 tool grammars should all parse and load (added tail.toml)
        assert_eq!(
            GRAMMARS.len(),
            26,
            "expected 26 grammars, got {}. loaded: {:?}",
            GRAMMARS.len(),
            GRAMMARS.keys().collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_all_grammars_have_detect() {
        for (name, grammar) in GRAMMARS.iter() {
            assert!(
                !grammar.detect.is_empty(),
                "grammar '{}' has empty detect list",
                name
            );
        }
    }

    #[test]
    fn test_cargo_inherited_rules() {
        let cargo = GRAMMARS.get("cargo").expect("cargo grammar missing");
        // cargo inherits ansi-progress, so global_noise should include shared rules
        assert!(
            cargo.global_noise.len() > 2,
            "cargo should have own rules + inherited ansi-progress rules, got {}",
            cargo.global_noise.len()
        );
    }

    #[test]
    fn test_detect_tool_from_registry() {
        let found = grammar::detect_tool("cargo build", &GRAMMARS);
        assert!(found.is_some());
        assert_eq!(found.unwrap().tool.name, "cargo");

        let found = grammar::detect_tool("npm install", &GRAMMARS);
        assert!(found.is_some());
        assert_eq!(found.unwrap().tool.name, "npm");

        let found = grammar::detect_tool("git push origin main", &GRAMMARS);
        assert!(found.is_some());
        assert_eq!(found.unwrap().tool.name, "git");

        let found = grammar::detect_tool("unknown-tool --flag", &GRAMMARS);
        assert!(found.is_none());
    }
}
