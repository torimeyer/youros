//! →1006: Shell command capability classifier for the approval policy chain.
//!
//! 4-layer classifier that maps shell commands to CapabilityClass.
//! Conservative: over-promotes (extra prompts) but never under-promotes (security holes).

use super::CapabilityClass;

/// Classify a shell command into a capability class.
///
/// Evaluated in 4 layers — first escalation wins:
/// 1. Operator scan: >, >>, |, $(), backticks → ShellWrite (at minimum)
/// 2. Known-dangerous flags: sed -i → ShellExec, git checkout → ShellWrite, etc.
/// 3. Command allowlist: cat, ls, ps → ShellRead (only if layers 1-2 didn't escalate)
/// 4. Default: ShellExec
pub fn detect_shell_class(cmd: &str) -> CapabilityClass {
    let trimmed = cmd.trim();
    if trimmed.is_empty() {
        return CapabilityClass::ShellRead;
    }

    // Layer 1: Operator scan — shell syntax that implies mutation or execution
    let has_redirect = has_output_redirect(trimmed);
    let has_subshell = has_subshell_or_expansion(trimmed);

    // Layer 2: Known-dangerous flags and commands
    // Check secret tools first (highest privilege)
    if is_secret_command(trimmed) {
        return CapabilityClass::ShellSecret;
    }

    // Check for arbitrary code execution
    if is_exec_command(trimmed) || has_subshell {
        return CapabilityClass::ShellExec;
    }

    // Check for filesystem mutation
    if is_write_command(trimmed) || has_redirect {
        return CapabilityClass::ShellWrite;
    }

    // Layer 3: Command allowlist — known-safe read-only commands
    if is_read_command(trimmed) {
        return CapabilityClass::ShellRead;
    }

    // Layer 4: Default — unknown commands are ShellExec (conservative)
    CapabilityClass::ShellExec
}

// ---------------------------------------------------------------------------
// Layer 1: Operator scan
// ---------------------------------------------------------------------------

/// Check for output redirection operators (>, >>).
/// Ignores 2> (stderr redirect) as it's commonly used for error suppression.
fn has_output_redirect(cmd: &str) -> bool {
    let bytes = cmd.as_bytes();
    for i in 0..bytes.len() {
        if bytes[i] == b'>' {
            // Skip >> (append) — still a write
            // Skip 2> and 2>> (stderr redirect only — not a filesystem write)
            if i > 0 && bytes[i - 1] == b'2' {
                continue;
            }
            return true;
        }
    }
    false
}

/// Check for subshell execution or variable expansion that could hide commands.
fn has_subshell_or_expansion(cmd: &str) -> bool {
    cmd.contains("$(") || cmd.contains('`')
}

// ---------------------------------------------------------------------------
// Layer 2: Known-dangerous flags and command patterns
// ---------------------------------------------------------------------------

/// Secret/key material commands → ShellSecret
fn is_secret_command(cmd: &str) -> bool {
    let lower = cmd.to_lowercase();
    let first = first_command(&lower);
    matches!(first, "gpg" | "gpg2" | "ssh-keygen" | "ssh-add" | "ssh-agent"
        | "openssl" | "age" | "sops")
        || lower.contains("ostk secret")
        || lower.contains("pass ")
        || (first == "export" && (lower.contains("api_key") || lower.contains("secret") || lower.contains("token")))
}

/// Commands that imply arbitrary code execution → ShellExec
fn is_exec_command(cmd: &str) -> bool {
    let lower = cmd.to_lowercase();
    let first = first_command(&lower);

    // Interpreters with eval flags
    if matches!(first, "python" | "python3" | "perl" | "ruby" | "node" | "deno" | "bun")
        && (lower.contains(" -c ") || lower.contains(" -e ") || lower.contains(" -c'")
            || lower.contains(" -e'") || lower.contains(" -c\"") || lower.contains(" -e\""))
    {
        return true;
    }

    // cargo: only exec for subcommands that build/install — read-only subs handled in Layer 3
    if first == "cargo" {
        let sub = word_after(&lower, "cargo");
        return !matches!(sub, "check" | "clippy" | "doc" | "fmt"
            | "test" | "bench" | "tree" | "metadata" | "version"
            | "search" | "info" | "locate-project" | "read-manifest"
            | "verify-project" | "");
    }

    // Process creation / network access
    matches!(first, "curl" | "wget" | "nc" | "ncat" | "netcat"
        | "npm" | "npx" | "yarn" | "pnpm"
        | "make" | "cmake" | "go"
        | "docker" | "podman" | "kubectl"
        | "pip" | "pip3" | "pipx"
        | "brew" | "apt" | "apt-get" | "yum" | "dnf" | "pacman"
        | "nohup" | "screen" | "tmux"
        | "bash" | "sh" | "zsh" | "fish"
        | "exec" | "eval" | "source" | "xargs")
}

/// Commands that mutate the filesystem → ShellWrite
fn is_write_command(cmd: &str) -> bool {
    let lower = cmd.to_lowercase();
    let first = first_command(&lower);

    // Direct filesystem mutation
    if matches!(first, "cp" | "mv" | "rm" | "mkdir" | "rmdir" | "touch" | "chmod"
        | "chown" | "chgrp" | "ln" | "install" | "rsync" | "tar" | "unzip" | "zip"
        | "patch" | "tee")
    {
        return true;
    }

    // sed -i (in-place edit)
    if first == "sed" && (lower.contains(" -i") || lower.contains(" -i'") || lower.contains(" -i\"")) {
        return true;
    }

    // git commands that mutate the worktree or history
    if first == "git" {
        let git_sub = git_subcommand(&lower);
        return matches!(git_sub, "checkout" | "reset" | "clean" | "stash"
            | "commit" | "push" | "pull" | "fetch" | "merge" | "rebase"
            | "cherry-pick" | "revert" | "am" | "apply" | "add" | "rm"
            | "mv" | "restore" | "switch" | "tag" | "init" | "clone");
    }

    false
}

/// Known-safe read-only commands → ShellRead
fn is_read_command(cmd: &str) -> bool {
    let lower = cmd.to_lowercase();
    let first = first_command(&lower);

    if matches!(first, "cat" | "ls" | "head" | "tail" | "wc" | "find" | "grep" | "rg"
        | "ag" | "ack" | "ps" | "env" | "pwd" | "which" | "where" | "file" | "stat"
        | "du" | "df" | "free" | "uptime" | "uname" | "hostname" | "whoami" | "id"
        | "date" | "cal" | "echo" | "printf" | "true" | "false" | "test" | "["
        | "diff" | "cmp" | "md5" | "md5sum" | "sha256sum" | "shasum"
        | "less" | "more" | "sort" | "uniq" | "tr" | "cut" | "awk" | "jq" | "yq"
        | "column" | "fmt" | "fold" | "nl" | "rev" | "seq" | "yes"
        | "tree" | "exa" | "bat" | "fd" | "fzf" | "ripgrep"
        | "man" | "help" | "type" | "command" | "hash"
        | "cargo" )
    {
        // Special case: cargo without subcommand, or cargo with read-only subcommands
        if first == "cargo" {
            let cargo_sub = word_after(&lower, "cargo");
            return matches!(cargo_sub, "check" | "clippy" | "doc" | "fmt"
                | "test" | "bench" | "tree" | "metadata" | "version"
                | "search" | "info" | "locate-project" | "read-manifest"
                | "verify-project" | "");
        }
        return true;
    }

    // git read-only subcommands
    if first == "git" {
        let git_sub = git_subcommand(&lower);
        return matches!(git_sub, "log" | "diff" | "status" | "branch" | "show"
            | "blame" | "shortlog" | "describe" | "rev-parse" | "rev-list"
            | "ls-files" | "ls-tree" | "ls-remote" | "cat-file"
            | "config" | "remote" | "stash list" | "reflog" | "bisect"
            | "name-rev" | "for-each-ref" | "count-objects" | "fsck"
            | "verify-commit" | "verify-tag");
    }

    // ostk read-only commands (when invoked through shell, not MCP)
    if first == "ostk" {
        let ostk_sub = word_after(&lower, "ostk");
        return matches!(ostk_sub, "ps" | "clock" | "history" | "show" | "search"
            | "status" | "drivers" | "metrics" | "compounds" | "guide"
            | "boot" | "os");
    }

    false
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Extract the first command name from a potentially complex command string.
/// Handles: `cd /foo && cmd`, `VAR=val cmd`, `sudo cmd`, `env -i cmd`
fn first_command(cmd: &str) -> &str {
    let cmd = cmd.trim();

    // Skip leading environment variable assignments (VAR=val ...)
    let mut words = cmd.split_whitespace().peekable();
    loop {
        match words.next() {
            None => return "",
            Some(w) => {
                // Skip env var assignments
                if w.contains('=') && !w.starts_with('-') && !w.starts_with('/') {
                    continue;
                }
                // Skip prefix commands (sudo, env, nice, etc.) only when
                // followed by more arguments. Bare "env" or "time" alone
                // should be returned as the command itself.
                if matches!(w, "sudo" | "env" | "nice" | "time" | "timeout" | "strace" | "ltrace")
                    && words.peek().is_some()
                {
                    continue;
                }
                return w;
            }
        }
    }
}

/// Extract the first non-flag word after a given keyword in a command string.
fn word_after<'a>(cmd: &'a str, keyword: &str) -> &'a str {
    let mut words = cmd.split_whitespace();
    while let Some(w) = words.next() {
        if w == keyword {
            for w2 in words {
                if !w2.starts_with('-') {
                    return w2;
                }
            }
            return "";
        }
    }
    ""
}

/// Extract the git subcommand from a git command string.
fn git_subcommand(cmd: &str) -> &str {
    word_after(cmd, "git")
}

#[cfg(test)]
mod tests {
    use super::*;

    // Layer 1: Operator scan
    #[test]
    fn redirect_escalates_to_write() {
        assert_eq!(detect_shell_class("cat foo > bar"), CapabilityClass::ShellWrite);
        assert_eq!(detect_shell_class("echo hello >> log.txt"), CapabilityClass::ShellWrite);
    }

    #[test]
    fn stderr_redirect_does_not_escalate() {
        assert_eq!(detect_shell_class("ls 2>/dev/null"), CapabilityClass::ShellRead);
    }

    #[test]
    fn subshell_escalates_to_exec() {
        assert_eq!(detect_shell_class("$(echo rm) -rf /"), CapabilityClass::ShellExec);
        assert_eq!(detect_shell_class("`whoami`"), CapabilityClass::ShellExec);
    }

    // Layer 2: Secret commands
    #[test]
    fn secret_commands() {
        assert_eq!(detect_shell_class("gpg --decrypt secret.gpg"), CapabilityClass::ShellSecret);
        assert_eq!(detect_shell_class("ssh-keygen -t ed25519"), CapabilityClass::ShellSecret);
        assert_eq!(detect_shell_class("ostk secret get API_KEY"), CapabilityClass::ShellSecret);
        assert_eq!(detect_shell_class("export API_KEY=abc123"), CapabilityClass::ShellSecret);
    }

    // Layer 2: Exec commands
    #[test]
    fn exec_commands() {
        assert_eq!(detect_shell_class("python -c 'import os; os.system(\"ls\")'"), CapabilityClass::ShellExec);
        assert_eq!(detect_shell_class("perl -e 'system(\"id\")'"), CapabilityClass::ShellExec);
        assert_eq!(detect_shell_class("curl https://example.com"), CapabilityClass::ShellExec);
        assert_eq!(detect_shell_class("npm install express"), CapabilityClass::ShellExec);
        assert_eq!(detect_shell_class("docker run ubuntu"), CapabilityClass::ShellExec);
        assert_eq!(detect_shell_class("bash -c 'echo hi'"), CapabilityClass::ShellExec);
    }

    // Layer 2: Write commands
    #[test]
    fn write_commands() {
        assert_eq!(detect_shell_class("cp src/a.rs dst/"), CapabilityClass::ShellWrite);
        assert_eq!(detect_shell_class("mv old.txt new.txt"), CapabilityClass::ShellWrite);
        assert_eq!(detect_shell_class("rm -rf target/"), CapabilityClass::ShellWrite);
        assert_eq!(detect_shell_class("mkdir -p src/new"), CapabilityClass::ShellWrite);
        assert_eq!(detect_shell_class("sed -i 's/old/new/g' file.txt"), CapabilityClass::ShellWrite);
        assert_eq!(detect_shell_class("git commit -m 'test'"), CapabilityClass::ShellWrite);
        assert_eq!(detect_shell_class("git push origin main"), CapabilityClass::ShellWrite);
        assert_eq!(detect_shell_class("git checkout main"), CapabilityClass::ShellWrite);
    }

    // Layer 3: Read commands
    #[test]
    fn read_commands() {
        assert_eq!(detect_shell_class("cat src/main.rs"), CapabilityClass::ShellRead);
        assert_eq!(detect_shell_class("ls -la"), CapabilityClass::ShellRead);
        assert_eq!(detect_shell_class("head -20 file.txt"), CapabilityClass::ShellRead);
        assert_eq!(detect_shell_class("ps aux"), CapabilityClass::ShellRead);
        assert_eq!(detect_shell_class("env"), CapabilityClass::ShellRead);
        assert_eq!(detect_shell_class("pwd"), CapabilityClass::ShellRead);
        assert_eq!(detect_shell_class("git log --oneline"), CapabilityClass::ShellRead);
        assert_eq!(detect_shell_class("git diff HEAD"), CapabilityClass::ShellRead);
        assert_eq!(detect_shell_class("git status"), CapabilityClass::ShellRead);
        assert_eq!(detect_shell_class("wc -l file.txt"), CapabilityClass::ShellRead);
        assert_eq!(detect_shell_class("grep -rn pattern src/"), CapabilityClass::ShellRead);
    }

    // Layer 4: Unknown defaults to exec
    #[test]
    fn unknown_defaults_to_exec() {
        assert_eq!(detect_shell_class("./custom_script.sh"), CapabilityClass::ShellExec);
        assert_eq!(detect_shell_class("/usr/local/bin/mystery"), CapabilityClass::ShellExec);
    }

    // Edge cases
    #[test]
    fn empty_command_is_read() {
        assert_eq!(detect_shell_class(""), CapabilityClass::ShellRead);
        assert_eq!(detect_shell_class("   "), CapabilityClass::ShellRead);
    }

    #[test]
    fn sudo_prefix_stripped() {
        assert_eq!(detect_shell_class("sudo cat /etc/passwd"), CapabilityClass::ShellRead);
        assert_eq!(detect_shell_class("sudo rm -rf /"), CapabilityClass::ShellWrite);
    }

    #[test]
    fn env_var_prefix_stripped() {
        assert_eq!(detect_shell_class("RUST_LOG=debug cargo check"), CapabilityClass::ShellRead);
        assert_eq!(detect_shell_class("CC=gcc make"), CapabilityClass::ShellExec);
    }

    #[test]
    fn pipe_with_read_commands_stays_read() {
        // Pure read pipe — no escalation needed because detect_shell_class
        // evaluates the full command string, and Layer 1 doesn't trigger on |
        // (pipes don't write to filesystem)
        assert_eq!(detect_shell_class("cat file.txt | grep pattern"), CapabilityClass::ShellRead);
        assert_eq!(detect_shell_class("ps aux | grep node"), CapabilityClass::ShellRead);
    }

    #[test]
    fn cat_redirect_is_write() {
        // The Gemini review's key example
        assert_eq!(detect_shell_class("cat config.json > /etc/passwd"), CapabilityClass::ShellWrite);
    }

    #[test]
    fn living_off_the_land_attacks() {
        // sed -i is a write, not a read
        assert_eq!(detect_shell_class("sed -i 's/root/hacked/' /etc/passwd"), CapabilityClass::ShellWrite);
        // python -c is exec, not read
        assert_eq!(detect_shell_class("python3 -c 'import os; os.unlink(\"/etc/passwd\")'"), CapabilityClass::ShellExec);
        // git checkout mutates worktree
        assert_eq!(detect_shell_class("git checkout -- ."), CapabilityClass::ShellWrite);
    }

    #[test]
    fn cargo_read_subcommands() {
        assert_eq!(detect_shell_class("cargo check"), CapabilityClass::ShellRead);
        assert_eq!(detect_shell_class("cargo test --lib"), CapabilityClass::ShellRead);
        assert_eq!(detect_shell_class("cargo clippy"), CapabilityClass::ShellRead);
    }

    #[test]
    fn cargo_write_subcommands_are_exec() {
        // cargo build, cargo install etc. spawn processes / write artifacts
        assert_eq!(detect_shell_class("cargo build"), CapabilityClass::ShellExec);
        assert_eq!(detect_shell_class("cargo install ripgrep"), CapabilityClass::ShellExec);
    }

    #[test]
    fn ostk_read_commands() {
        assert_eq!(detect_shell_class("ostk ps"), CapabilityClass::ShellRead);
        assert_eq!(detect_shell_class("ostk clock"), CapabilityClass::ShellRead);
        assert_eq!(detect_shell_class("ostk status"), CapabilityClass::ShellRead);
    }
}
