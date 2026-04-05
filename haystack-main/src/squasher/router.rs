//! Category router for the squasher compression pipeline.
//!
//! Ported from mish's `router/categories.rs`. Every shell command is classified
//! into a category before compression. The category determines which pipeline
//! processes the command output.
//!
//! Currently all categories fall through to the existing condense path.
//! Wave 2 agents will implement individual category handlers (→808–811).

use std::fmt;

// ---------------------------------------------------------------------------
// Category enum
// ---------------------------------------------------------------------------

/// The five command categories for the squasher pipeline.
///
/// Resolution order in `classify_command`:
/// 1. Dangerous patterns (full command string match)
/// 2. Multi-token patterns (e.g. "git status" → Structured)
/// 3. Single-token patterns (first word of command)
/// 4. Fallback → Condense
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[derive(Default)]
pub enum Category {
    /// Verbose output → condensed summary (cargo build, npm install, make)
    #[default]
    Condense,
    /// Silent commands → inspect + execute + narrate (cp, mv, mkdir, touch, chmod, ln)
    Narrate,
    /// Verbatim output + metadata footer (cat, grep, ls, head, tail, find, wc)
    Passthrough,
    /// Machine-readable output → formatted view (git status, docker ps, kubectl get)
    Structured,
    /// Destructive commands → policy gate (rm -rf, git push --force, dd)
    Dangerous,
}


impl fmt::Display for Category {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let name = match self {
            Category::Condense => "condense",
            Category::Narrate => "narrate",
            Category::Passthrough => "passthrough",
            Category::Structured => "structured",
            Category::Dangerous => "dangerous",
        };
        write!(f, "{}", name)
    }
}

impl Category {
    /// Parse a category name string into a Category variant.
    pub fn from_name(s: &str) -> Option<Category> {
        match s {
            "condense" => Some(Category::Condense),
            "narrate" => Some(Category::Narrate),
            "passthrough" => Some(Category::Passthrough),
            "structured" => Some(Category::Structured),
            "dangerous" => Some(Category::Dangerous),
            _ => None,
        }
    }
}

// ---------------------------------------------------------------------------
// Dangerous pattern detection
// ---------------------------------------------------------------------------

/// Check if the full command string matches a known dangerous pattern.
///
/// These patterns match against the entire command (not just the first token)
/// because dangerous operations can appear anywhere in a compound command
/// (e.g., `echo hello && rm -rf /`).
fn is_dangerous(full_cmd: &str) -> bool {
    // rm -rf (force recursive delete)
    if has_rm_rf(full_cmd) {
        return true;
    }

    // git push --force / -f (force push — overwrites remote history)
    if has_force_push(full_cmd) {
        return true;
    }

    // dd (direct disk write)
    if has_dd_command(full_cmd) {
        return true;
    }

    // chmod 777 (world-writable permissions)
    if has_chmod_777(full_cmd) {
        return true;
    }

    // mkfs (format filesystem)
    if full_cmd.split_whitespace().any(|t| t == "mkfs" || t.starts_with("mkfs.")) {
        return true;
    }

    false
}

/// Detect `rm` with both `-r` and `-f` flags (in any order, combined or separate).
fn has_rm_rf(cmd: &str) -> bool {
    let tokens: Vec<&str> = cmd.split_whitespace().collect();
    // Find 'rm' token, then check subsequent flags
    let mut i = 0;
    while i < tokens.len() {
        if tokens[i] == "rm" {
            let mut has_r = false;
            let mut has_f = false;
            for &tok in &tokens[i + 1..] {
                if tok == "&&" || tok == "||" || tok == ";" || tok == "|" {
                    break;
                }
                if tok.starts_with('-') && !tok.starts_with("--") {
                    if tok.contains('r') { has_r = true; }
                    if tok.contains('f') { has_f = true; }
                }
                if tok == "--recursive" { has_r = true; }
                if tok == "--force" { has_f = true; }
            }
            if has_r && has_f {
                return true;
            }
        }
        i += 1;
    }
    false
}

/// Detect `git push` with `--force` or `-f`.
fn has_force_push(cmd: &str) -> bool {
    let tokens: Vec<&str> = cmd.split_whitespace().collect();
    for window in tokens.windows(2) {
        if window[0] == "git" && window[1] == "push" {
            // Check remaining tokens for --force or -f
            let push_idx = tokens.iter().position(|&t| t == "push").unwrap();
            for &tok in &tokens[push_idx + 1..] {
                if tok == "&&" || tok == "||" || tok == ";" || tok == "|" {
                    break;
                }
                if tok == "--force" || tok == "--force-with-lease" || tok == "-f" {
                    return true;
                }
            }
        }
    }
    false
}

/// Detect bare `dd` command (direct disk write).
fn has_dd_command(cmd: &str) -> bool {
    let tokens: Vec<&str> = cmd.split_whitespace().collect();
    // dd is dangerous when it's the command itself (not a substring)
    tokens.first() == Some(&"dd")
        || tokens.iter().any(|&t| {
            // dd after a compound operator
            t == "dd"
        } && {
            // Verify it's at command position (after &&, ||, ;, |)
            let pos = tokens.iter().position(|&tok| tok == "dd").unwrap();
            pos == 0
                || matches!(
                    tokens.get(pos.wrapping_sub(1)),
                    Some(&"&&") | Some(&"||") | Some(&";") | Some(&"|")
                )
        })
}

/// Detect `chmod 777`.
fn has_chmod_777(cmd: &str) -> bool {
    let tokens: Vec<&str> = cmd.split_whitespace().collect();
    for window in tokens.windows(2) {
        if window[0] == "chmod" && window[1] == "777" {
            return true;
        }
    }
    false
}

// ---------------------------------------------------------------------------
// Multi-token classification
// ---------------------------------------------------------------------------

/// Classify multi-token command patterns (checked before single-token).
///
/// Some commands change category depending on the subcommand.
/// For example: `git status` → Structured, `git commit` → Condense.
fn classify_multi_token(tokens: &[&str]) -> Option<Category> {
    if tokens.len() < 2 {
        return None;
    }

    match tokens[0] {
        "git" => classify_git_subcommand(tokens[1]),
        "docker" => classify_docker_subcommand(tokens[1]),
        "kubectl" | "k" => classify_kubectl_subcommand(tokens[1]),
        "cargo" => classify_cargo_subcommand(tokens[1]),
        "npm" | "yarn" | "pnpm" | "bun" => classify_npm_subcommand(tokens[1]),
        _ => None,
    }
}

fn classify_git_subcommand(sub: &str) -> Option<Category> {
    match sub {
        // Structured: machine-readable output, benefits from parsing
        "status" | "log" | "diff" | "branch" | "remote" | "stash" | "tag" | "show" => {
            Some(Category::Structured)
        }
        // Passthrough: output should be preserved verbatim
        "blame" | "shortlog" => Some(Category::Passthrough),
        // Condense: verbose build/operation output
        "clone" | "pull" | "fetch" | "merge" | "rebase" | "commit" | "push" | "checkout"
        | "switch" | "restore" | "cherry-pick" | "am" | "format-patch" => {
            Some(Category::Condense)
        }
        // Default: let the single-token fallback handle it
        _ => None,
    }
}

fn classify_docker_subcommand(sub: &str) -> Option<Category> {
    match sub {
        "ps" | "images" | "inspect" | "stats" | "network" | "volume" | "info" => {
            Some(Category::Structured)
        }
        "logs" => Some(Category::Passthrough),
        "build" | "pull" | "push" | "run" | "compose" => Some(Category::Condense),
        _ => None,
    }
}

fn classify_kubectl_subcommand(sub: &str) -> Option<Category> {
    match sub {
        "get" | "describe" | "top" | "api-resources" => Some(Category::Structured),
        "logs" => Some(Category::Passthrough),
        "apply" | "create" | "delete" | "rollout" | "scale" => Some(Category::Condense),
        _ => None,
    }
}

fn classify_npm_subcommand(sub: &str) -> Option<Category> {
    match sub {
        // Condense: verbose build/install output
        "install" | "i" | "ci" | "run" | "build" | "test" | "publish" | "pack" | "start"
        | "exec" | "create" | "init" | "add" | "remove" | "upgrade" | "up" | "dlx" => {
            Some(Category::Condense)
        }
        // Structured: informational queries
        "ls" | "list" | "info" | "view" | "outdated" | "audit" | "doctor" | "why" => {
            Some(Category::Structured)
        }
        _ => None,
    }
}

fn classify_cargo_subcommand(sub: &str) -> Option<Category> {
    match sub {
        // Condense: verbose build output, many repeated lines
        "build" | "test" | "check" | "clippy" | "install" | "publish" | "bench" | "run"
        | "doc" => Some(Category::Condense),
        // Passthrough: brief informational output
        "tree" | "metadata" => Some(Category::Passthrough),
        _ => None,
    }
}

// ---------------------------------------------------------------------------
// Single-token classification
// ---------------------------------------------------------------------------

/// Classify based on the first token (command name) alone.
fn classify_single_token(cmd_name: &str) -> Category {
    match cmd_name {
        // Narrate: silent/near-silent commands that benefit from narration
        "cp" | "mv" | "rm" | "mkdir" | "rmdir" | "touch" | "chmod" | "chown" | "chgrp" | "ln"
        | "install" | "unlink" | "rename" => Category::Narrate,

        // Passthrough: output should be preserved verbatim
        "cat" | "head" | "tail" | "less" | "more" | "grep" | "rg" | "ag" | "ack" | "ls"
        | "exa" | "eza" | "tree" | "find" | "fd" | "wc" | "sort" | "uniq" | "cut" | "tr"
        | "sed" | "awk" | "jq" | "yq" | "xargs" | "tee" | "diff" | "comm" | "paste"
        | "column" | "bat" | "hexdump" | "xxd" | "od" | "file" | "stat" | "du" | "df"
        | "which" | "whereis" | "type" | "echo" | "printf" | "date" | "cal" | "env"
        | "printenv" | "whoami" | "id" | "uname" | "hostname" | "uptime" | "free"
        | "lsof" | "ps" | "top" | "htop" | "readlink" | "realpath" | "basename"
        | "dirname" | "pwd" | "true" | "false" => Category::Passthrough,

        // Structured: commands with machine-readable output
        "git" | "docker" | "kubectl" | "k" | "helm" | "terraform" | "tf" | "aws" | "gcloud"
        | "az" | "gh" | "jira" | "systemctl" | "journalctl" | "launchctl" | "brew"
        | "apt" | "dpkg" | "rpm" | "yum" | "dnf" | "pacman" | "snap" | "flatpak"
        | "pip" | "pip3" | "npm" | "yarn" | "pnpm" | "bun" | "deno" => {
            // Note: many of these will be overridden by multi-token classification
            // for specific subcommands. This is the fallback for unknown subcommands.
            Category::Structured
        }

        // Condense: verbose build/run commands (default for build tools)
        "make" | "cmake" | "ninja" | "meson" | "bazel" | "gradle" | "mvn" | "ant" | "sbt"
        | "lein" | "mix" | "stack" | "cabal" | "go" | "rustc" | "gcc" | "g++" | "clang"
        | "clang++" | "ld" | "ar" | "cc" | "c++" | "javac" | "java" | "python" | "python3"
        | "ruby" | "perl" | "node" | "tsx" | "ts-node" | "npx" | "cargo" | "pytest"
        | "jest" | "mocha" | "vitest" | "tap" | "prove" | "phpunit" | "dotnet" | "msbuild"
        | "nix" | "guix" | "emerge" | "portage" => Category::Condense,

        // Everything else falls through to Condense (the safe default)
        _ => Category::Condense,
    }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Classify a shell command string into a category.
///
/// This is a pure function: command string in, category out.
/// No I/O, no config files, no side effects.
///
/// Resolution order (first match wins):
/// 1. **Dangerous** — pattern match against the full command string
/// 2. **Multi-token** — subcommand-aware classification (e.g. `git status`)
/// 3. **Single-token** — first word of the command
/// 4. **Fallback** — Condense (existing compression behavior)
///
/// # Examples
///
/// ```
/// use ostk::squasher::router::{classify_command, Category};
///
/// assert_eq!(classify_command("cargo test"), Category::Condense);
/// assert_eq!(classify_command("ls -la"), Category::Passthrough);
/// assert_eq!(classify_command("git status"), Category::Structured);
/// assert_eq!(classify_command("cp a b"), Category::Narrate);
/// assert_eq!(classify_command("rm -rf /"), Category::Dangerous);
/// ```
pub fn classify_command(cmd: &str) -> Category {
    let trimmed = cmd.trim();
    if trimmed.is_empty() {
        return Category::default();
    }

    // Step 1: Dangerous pattern check (full command string)
    if is_dangerous(trimmed) {
        return Category::Dangerous;
    }

    // Step 2: For compound commands (pipes, &&, ||, ;), classify the LAST
    // command in the chain — that's the one whose stdout we're compressing.
    // e.g. `cd /foo && sed -n '30p' file.rs` → classify `sed -n '30p' file.rs`
    //       `echo hello | grep hello`         → classify `grep hello`
    let effective = extract_last_command(trimmed);

    // Step 3: Tokenize and try multi-token classification
    let tokens: Vec<&str> = effective.split_whitespace().collect();
    if tokens.is_empty() {
        return Category::default();
    }
    if let Some(category) = classify_multi_token(&tokens) {
        return category;
    }

    // Step 4: Single-token classification on command name
    // Handle path-qualified commands: /usr/bin/ls → ls
    let cmd_name = tokens[0]
        .rsplit('/')
        .next()
        .unwrap_or(tokens[0]);

    classify_single_token(cmd_name)
}

/// Extract the last command from a compound command string.
///
/// Splits on `|`, `&&`, `||`, `;` and returns the rightmost segment trimmed.
/// For simple commands, returns the input unchanged.
///
/// # Examples
/// - `cd /foo && sed -n '30p' file` → `sed -n '30p' file`
/// - `echo hello | grep hello` → `grep hello`
/// - `ls -la` → `ls -la`
fn extract_last_command(cmd: &str) -> &str {
    // Split on shell operators, rightmost segment wins.
    // Order matters: try || before | to avoid splitting on || as two |'s.
    let last = cmd
        .rsplit_once("||")
        .map(|(_, r)| r)
        .or_else(|| cmd.rsplit_once('|').map(|(_, r)| r))
        .or_else(|| cmd.rsplit_once("&&").map(|(_, r)| r))
        .or_else(|| cmd.rsplit_once(';').map(|(_, r)| r))
        .unwrap_or(cmd);
    last.trim()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    // -----------------------------------------------------------------------
    // Core classification tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_empty_command() {
        assert_eq!(classify_command(""), Category::Condense);
        assert_eq!(classify_command("   "), Category::Condense);
    }

    #[test]
    fn test_condense_commands() {
        assert_eq!(classify_command("cargo test"), Category::Condense);
        assert_eq!(classify_command("cargo build --release"), Category::Condense);
        assert_eq!(classify_command("make -j8"), Category::Condense);
        assert_eq!(classify_command("python3 setup.py install"), Category::Condense);
        assert_eq!(classify_command("gcc -o main main.c"), Category::Condense);
        assert_eq!(classify_command("npm run build"), Category::Condense);
    }

    #[test]
    fn test_narrate_commands() {
        assert_eq!(classify_command("cp a b"), Category::Narrate);
        assert_eq!(classify_command("mv old.txt new.txt"), Category::Narrate);
        assert_eq!(classify_command("mkdir -p src/foo/bar"), Category::Narrate);
        assert_eq!(classify_command("touch file.txt"), Category::Narrate);
        assert_eq!(classify_command("chmod +x script.sh"), Category::Narrate);
        assert_eq!(classify_command("ln -s target link"), Category::Narrate);
    }

    #[test]
    fn test_passthrough_commands() {
        assert_eq!(classify_command("ls -la"), Category::Passthrough);
        assert_eq!(classify_command("cat src/main.rs"), Category::Passthrough);
        assert_eq!(classify_command("grep -rn pattern src/"), Category::Passthrough);
        assert_eq!(classify_command("head -20 file.txt"), Category::Passthrough);
        assert_eq!(classify_command("tail -f /var/log/syslog"), Category::Passthrough);
        assert_eq!(classify_command("find . -name '*.rs'"), Category::Passthrough);
        assert_eq!(classify_command("wc -l file.txt"), Category::Passthrough);
        assert_eq!(classify_command("echo hello world"), Category::Passthrough);
        assert_eq!(classify_command("pwd"), Category::Passthrough);
    }

    #[test]
    fn test_structured_commands() {
        assert_eq!(classify_command("git status"), Category::Structured);
        assert_eq!(classify_command("git log --oneline"), Category::Structured);
        assert_eq!(classify_command("git diff HEAD~1"), Category::Structured);
        assert_eq!(classify_command("docker ps"), Category::Structured);
        assert_eq!(classify_command("docker images"), Category::Structured);
        assert_eq!(classify_command("kubectl get pods"), Category::Structured);
    }

    #[test]
    fn test_dangerous_commands() {
        assert_eq!(classify_command("rm -rf /"), Category::Dangerous);
        assert_eq!(classify_command("rm -rf /tmp/foo"), Category::Dangerous);
        assert_eq!(classify_command("rm -f -r bar"), Category::Dangerous);
        assert_eq!(classify_command("git push origin main --force"), Category::Dangerous);
        assert_eq!(classify_command("git push -f"), Category::Dangerous);
        assert_eq!(classify_command("dd if=/dev/zero of=/dev/sda"), Category::Dangerous);
        assert_eq!(classify_command("chmod 777 /etc/passwd"), Category::Dangerous);
        assert_eq!(classify_command("mkfs.ext4 /dev/sda1"), Category::Dangerous);
    }

    // -----------------------------------------------------------------------
    // Multi-token subcommand tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_git_subcommand_classification() {
        // Structured subcommands
        assert_eq!(classify_command("git status"), Category::Structured);
        assert_eq!(classify_command("git log"), Category::Structured);
        assert_eq!(classify_command("git diff"), Category::Structured);
        assert_eq!(classify_command("git branch -a"), Category::Structured);
        assert_eq!(classify_command("git remote -v"), Category::Structured);

        // Condense subcommands
        assert_eq!(classify_command("git clone https://github.com/foo/bar"), Category::Condense);
        assert_eq!(classify_command("git pull"), Category::Condense);
        assert_eq!(classify_command("git fetch --all"), Category::Condense);
        assert_eq!(classify_command("git merge feature"), Category::Condense);
        assert_eq!(classify_command("git commit -m 'msg'"), Category::Condense);

        // Passthrough subcommands
        assert_eq!(classify_command("git blame src/main.rs"), Category::Passthrough);
    }

    #[test]
    fn test_cargo_subcommand_classification() {
        assert_eq!(classify_command("cargo build"), Category::Condense);
        assert_eq!(classify_command("cargo test"), Category::Condense);
        assert_eq!(classify_command("cargo clippy"), Category::Condense);
        assert_eq!(classify_command("cargo tree"), Category::Passthrough);
    }

    #[test]
    fn test_docker_subcommand_classification() {
        assert_eq!(classify_command("docker ps"), Category::Structured);
        assert_eq!(classify_command("docker images"), Category::Structured);
        assert_eq!(classify_command("docker inspect foo"), Category::Structured);
        assert_eq!(classify_command("docker logs foo"), Category::Passthrough);
        assert_eq!(classify_command("docker build ."), Category::Condense);
    }

    // -----------------------------------------------------------------------
    // Edge cases
    // -----------------------------------------------------------------------

    #[test]
    fn test_unknown_command_fallback() {
        assert_eq!(classify_command("some-unknown-tool --flag"), Category::Condense);
        assert_eq!(classify_command("my-script.sh"), Category::Condense);
    }

    #[test]
    fn test_path_qualified_command() {
        assert_eq!(classify_command("/usr/bin/ls -la"), Category::Passthrough);
        assert_eq!(classify_command("/bin/cat file.txt"), Category::Passthrough);
        assert_eq!(classify_command("/usr/local/bin/grep pattern"), Category::Passthrough);
    }

    #[test]
    fn test_compound_command_uses_last_command() {
        // Compound commands classify based on the LAST command in the chain
        // (that's the one producing stdout). Dangerous patterns still scan full string.
        assert_eq!(classify_command("echo hello && ls"), Category::Passthrough);
        assert_eq!(classify_command("cd /foo && sed -n '30p' file.rs"), Category::Passthrough);
        assert_eq!(classify_command("cd /foo && cat file.rs"), Category::Passthrough);
        assert_eq!(classify_command("echo hello | grep hello"), Category::Passthrough);
        assert_eq!(classify_command("cd /foo && cargo test"), Category::Condense);
        assert_eq!(classify_command("export FOO=bar; python3 script.py"), Category::Condense);
    }

    #[test]
    fn test_dangerous_in_compound_command() {
        // Dangerous patterns scan the full string
        assert_eq!(classify_command("echo hello && rm -rf /"), Category::Dangerous);
        assert_eq!(classify_command("ls && git push --force"), Category::Dangerous);
    }

    #[test]
    fn test_rm_without_rf_is_narrate() {
        // rm without -rf is just narrate (normal file removal)
        assert_eq!(classify_command("rm file.txt"), Category::Narrate);
        assert_eq!(classify_command("rm -f file.txt"), Category::Narrate);
        assert_eq!(classify_command("rm -r dir/"), Category::Narrate);
    }

    #[test]
    fn test_display_impl() {
        assert_eq!(format!("{}", Category::Condense), "condense");
        assert_eq!(format!("{}", Category::Narrate), "narrate");
        assert_eq!(format!("{}", Category::Passthrough), "passthrough");
        assert_eq!(format!("{}", Category::Structured), "structured");
        assert_eq!(format!("{}", Category::Dangerous), "dangerous");
    }

    #[test]
    fn test_from_name() {
        assert_eq!(Category::from_name("condense"), Some(Category::Condense));
        assert_eq!(Category::from_name("narrate"), Some(Category::Narrate));
        assert_eq!(Category::from_name("passthrough"), Some(Category::Passthrough));
        assert_eq!(Category::from_name("structured"), Some(Category::Structured));
        assert_eq!(Category::from_name("dangerous"), Some(Category::Dangerous));
        assert_eq!(Category::from_name("unknown"), None);
        assert_eq!(Category::from_name(""), None);
    }

    // -----------------------------------------------------------------------
    // Dangerous detection edge cases
    // -----------------------------------------------------------------------

    #[test]
    fn test_rm_rf_combined_flags() {
        assert_eq!(classify_command("rm -rf /tmp"), Category::Dangerous);
        assert_eq!(classify_command("rm -fr /tmp"), Category::Dangerous);
        assert_eq!(classify_command("rm -r -f /tmp"), Category::Dangerous);
        assert_eq!(classify_command("rm -f -r /tmp"), Category::Dangerous);
    }

    #[test]
    fn test_force_push_variants() {
        assert_eq!(classify_command("git push --force"), Category::Dangerous);
        assert_eq!(classify_command("git push origin main --force"), Category::Dangerous);
        assert_eq!(classify_command("git push -f"), Category::Dangerous);
        assert_eq!(classify_command("git push --force-with-lease"), Category::Dangerous);
    }

    #[test]
    fn test_dd_detection() {
        assert_eq!(classify_command("dd if=/dev/zero of=/dev/sda"), Category::Dangerous);
        // 'dd' as a substring in another command should NOT trigger
        assert_eq!(classify_command("oddity --flag"), Category::Condense);
    }

    #[test]
    fn test_normal_rm_not_dangerous() {
        // Removing a single file is narrate, not dangerous
        assert_eq!(classify_command("rm foo.txt"), Category::Narrate);
        assert_eq!(classify_command("rm -i bar.txt"), Category::Narrate);
    }

    // npm goes through single-token as Structured (package manager),
    // but npm-specific subcommands that produce verbose output are Condense
    // via the default. This is fine — wave 2 can refine with grammar overrides.
    #[test]
    fn test_package_manager_default() {
        // npm/yarn/pnpm default to Structured via single-token
        assert_eq!(classify_command("npm ls"), Category::Structured);
        assert_eq!(classify_command("yarn info react"), Category::Structured);
    }

    #[test]
    fn test_extract_last_command() {
        assert_eq!(extract_last_command("cd /foo && sed -n '30p' file"), "sed -n '30p' file");
        assert_eq!(extract_last_command("echo hello | grep hello"), "grep hello");
        assert_eq!(extract_last_command("export FOO=bar; python3 x.py"), "python3 x.py");
        assert_eq!(extract_last_command("ls -la"), "ls -la");
        assert_eq!(extract_last_command("a || b"), "b");
        assert_eq!(extract_last_command("cd /x && cat foo.rs | head -20"), "head -20");
    }

    #[test]
    fn test_pipe_to_sed_is_passthrough() {
        // This was the original bug: `cd /x && sed ...` classified as
        // Narrate (cd) instead of Passthrough (sed). The squasher would
        // dedup source code lines, inserting [⋯ N similar lines] markers
        // that made it look like the file was corrupted.
        assert_eq!(classify_command("cd /Users/x/proj && sed -n '30,31p' src/language.rs"), Category::Passthrough);
        assert_eq!(classify_command("cd /Users/x/proj && cat src/language.rs"), Category::Passthrough);
        assert_eq!(classify_command("echo 'test' | sed -n 'p'"), Category::Passthrough);
    }
}
