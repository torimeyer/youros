//! User-facing strings — separated from code logic.
//! Edit strings here, not in command implementations.
//!
//! Conventions:
//! - Constants are `&str` when static, functions when they need formatting.
//! - Modules mirror the command/subsystem that uses them.
//! - Not full i18n — just clean string management.

pub mod cli {
    pub const ABOUT: &str = "Transparent coordination kernel for AI agents and human operators";
}

pub mod boot {
    pub const REAPED_AGENTS: &str = "boot: reaped {} dead agent(s) from process table";
    pub const BOOT_MD_NOT_FOUND: &str = "boot.md not found. generating from project state...";
    pub const BOOT_VERIFIED: &str = "boot.md: verified \u{2713}";
    pub const BOOT_UNSIGNED: &str = "boot.md: unsigned (shutdown with gpg to sign)";
    pub const BOOT_UNSIGNED_GOVERNED: &str =
        "boot.md: unsigned \u{2014} governed agents refuse unsigned boot. \
         Run `ostk shutdown` to sign boot.md with kernel key.";
    pub const BOOT_INVALID_GOVERNED: &str =
        "boot.md: SIGNATURE INVALID \u{2014} governed agents refuse tampered boot. {}";
    pub const BOOT_INVALID: &str = "boot.md: SIGNATURE INVALID \u{2014} {}";
    pub const BOOT_EXPIRED_KEY: &str =
        "boot.md: signature valid but signing key {} expired \u{2014} boot confidence reduced";
    pub const BOOT_GPG_ABSENT: &str = "boot.md: gpg not available, signature not checked";
    pub const VERSION_MISMATCH: &str =
        "boot: OS version mismatch (running v{}, project pins v{})";
    pub const VERSION_MISMATCH_HINT: &str = "  \u{2192} run `cargo install ostk` to update";
    pub const REGISTRY_TIMESTAMP_FAIL: &str = "boot: failed to update registry timestamp: {}";
    pub const HUMANFILE_LOADED: &str = "HUMANFILE: loaded ({} verbs, last updated {})";
    pub const HUMANFILE_LOADED_V2: &str =
        "  \x1b[32m\u{2713}\x1b[0m HUMANFILE loaded ({})";
    pub const HUMANFILE_WARN_V2: &str =
        "  \x1b[33m\u{26a0}\x1b[0m HUMANFILE loaded ({})";
    pub const HUMANFILE_NOT_FOUND: &str = "boot: HUMANFILE not found at {} \u{2014} skipping";
    pub const ENTITYFILE_NOT_FOUND: &str = "boot: ENTITYFILE not found \u{2014} skipping governance check";
    pub const ENTITYFILE_LOADED: &str = "ENTITYFILE: v{} ({})";
    pub const AGENTFILE_NOT_FOUND: &str = "boot: OSTK_AGENTFILE={} not found \u{2014} skipping";
    pub const AGENTFILE_LOADED: &str = "Agentfile: model={}, tools={}";
    pub const CONFIDENCE_STATUS_FULL: &str = "fully operational";
    pub const CONFIDENCE_STATUS_MIN: &str = "minimally operational";
    pub const CONFIDENCE_STATUS_RESTRICTED: &str = "restricted mode";

    // First-run welcome
    pub const NO_GIT_REPO: &str = "no git repository found.";
    pub const GIT_INIT_HINT: &str = "  ostk works on top of git. initialize a repo first:\n    git init\n    ostk init";
    pub const WELCOME: &str = "  welcome.";
    pub const NO_OSTK_DIR: &str = "  no .ostk/ found \u{2014} this is your first time here.";
    pub const OS_INVISIBLE: &str = "  the OS is invisible. it doesn't change your tools.\n  it just starts tracking what you build.";
    pub const FIRST_RUN_COMMANDS: &str = "  start fresh:         ostk init\n  import from a team:  ostk init --import <path-or-url>";
    pub const FREE_TOKENS: &str = "  your first 100M tokens saved: free.\n  \u{2192} ostk.ai";

    // Harness tool hints
    pub const TOOL_HINT_CLAUDE_CODE: &str = "ostk -c \"cmd\" via native Bash tool";
    pub const TOOL_HINT_SERVE: &str = "shell / spawn MCP tools";
    pub const TOOL_HINT_CI: &str = "native Bash (no TTY)";
    pub const TOOL_HINT_DEFAULT: &str = "native Bash";
}

pub mod shutdown {
    pub const HEADER: &str = "ostk shutdown: clean session termination";
    pub const STEP1_REGEN: &str = "1. regenerating boot.md from current state...";
    pub const STEP1_DONE: &str = "   boot.md regenerated";
    pub const STEP1C_COMPILING: &str = "1c. decaying unused verbs in .language...";
    pub const STEP2_WRITING: &str = "2. writing registers-dump.md...";
    pub const STEP2_DONE: &str = "   registers-dump.md written";
    pub const STEP3_VERIFYING: &str = "3. verifying boot.md is current...";
    pub const STEP3_DONE: &str = "   boot.md verified: counts match ground truth";
    pub const STEP4_COMMITTING: &str = "4. committing...";
    pub const DEFAULT_MESSAGE: &str = "session shutdown: boot.md regenerated, state verified";
    pub const NOTHING_TO_COMMIT: &str = "   nothing to commit (clean tree)";
    pub const STEP5B_COMPILING: &str = "5b. compiling tack patterns into HUMANFILE...";
    pub const STEP5_OFFER: &str = "5. compiling session checkpoint (background)...";
    pub const COMPLETE: &str = "shutdown complete. next session boots from verified state.";
    pub const BOOT_MD_SIGNED: &str =
        "   boot.md: signed by @ostk.prime (boot.md.asc written)";
    pub const BOOT_MD_SIGN_FAIL: &str =
        "[ostk] warning: could not sign boot.md with kernel key \u{2014} {}";
    pub const GPG_NOT_AVAILABLE: &str =
        "[ostk] warning: gpg not available, boot.md not signed \u{2014} {}";
}

pub mod init {
    pub const INITIALIZED: &str = "initialized ostk project at {}";
    pub const HOOKS_PATH_FAIL: &str = "failed to set git core.hooksPath";
}

pub mod errors {
    pub const NO_PROJECT_ROOT: &str = "not an ostk project \u{2014} run 'ostk init'";
    pub const GIT_ADD_FAILED: &str = "git add -A failed";
    pub const NOT_A_TACK: &str = "not a tack expression: {}";
    pub const UNRECOGNIZED_TACK: &str = "unrecognized tack verb";
    pub const VERB_NOT_FOUND: &str = "verb not found: {}";
    pub const BOOT_BAIL_POST_FAIL: &str = "[ostk boot --bail] POST failed: {}";
    pub const BOOT_BAIL_UNHEALTHY: &str = "boot --bail: kernel not healthy: {}";
}

pub mod commit {
    pub const NO_SPEC_WARNING: &str =
        "warning: no spec reference \u{2014} run `ostk trace` to verify attribution later";
    pub const SPEC_NOT_FOUND: &str = "spec '{}' not found at {}";
    pub const NEEDLE_NOT_FOUND: &str = "needle '{}' not found";
    pub const AUTO_DETECTED_NEEDLE: &str = "auto-detected needle {} from commit message";
    pub const NEEDLE_NOT_IN_ISSUES: &str =
        "warning: '{}' found in message but not in issues.jsonl";
    pub const AUTO_DETECTED_IN_PROGRESS: &str =
        "auto-detected in_progress needle {} for agent {}";
    pub const NO_NEEDLE_REF: &str = "warning: no needle reference";
    pub const OS_INTEGRITY_FAIL: &str = "warning: OS integrity check failed: {}";
}

pub mod tack {
    pub const RESOLVED_FMT: &str = "+ {} \u{2192} ostk {}{} ({})";
    pub const UNRECOGNIZED_FMT: &str = "? {} \u{2014} unrecognized";
    pub const DID_YOU_MEAN: &str = ". did you mean: {}";
}

pub mod sign {
    pub const GPG_NOT_FOUND: &str =
        "GPG not found. Install: https://gnupg.org/download/";
    pub const NO_GPG_KEY: &str =
        "No GPG key found.\n\n\
         ostk requires a GPG key published to GitHub.\n\
         Create one: https://docs.github.com/en/authentication/managing-commit-signature-verification/generating-a-new-gpg-key\n\
         Then: gpg --armor --export YOUR_KEY_ID | gh gpg-key add -";
    pub const GPG_KEY_NOT_ON_GITHUB: &str =
        "  \x1b[33m\u{26a0}\x1b[0m  GPG key {} not found on GitHub";
    pub const GPG_KEY_ADD_HINT: &str =
        "     Add it: gpg --armor --export {} | gh gpg-key add -";
    pub const GPG_KEY_CONTINUING: &str =
        "     Continuing without GitHub verification...";
    pub const GPG_KEY_VERIFIED: &str =
        "  \x1b[32m\u{2713}\x1b[0m  GPG key verified on GitHub: {}";
    pub const HUMANFILE_SIGNED: &str =
        "  \x1b[32m\u{2713}\x1b[0m  HUMANFILE signed by {}";
    pub const HUMANFILE_SIGN_FAIL: &str = "Failed to sign HUMANFILE";
    pub const HUMANFILE_NOT_FOUND: &str = "HUMANFILE not found at {}";
    pub const SIGN_COMPLETE: &str =
        "  \x1b[32m\u{2713}\x1b[0m  HUMANFILE signed. Signature: HUMANFILE.asc";
    pub const AGENTFILE_SIGNED: &str =
        "  \x1b[32m\u{2713}\x1b[0m  Agentfile signed by kernel key {}";
    pub const AGENTFILE_SIGN_FAIL: &str = "Failed to sign Agentfile";
    pub const AGENTFILE_SIGN_COMPLETE: &str =
        "  \x1b[32m\u{2713}\x1b[0m  Agentfile signed. Signature: {}.asc";
    pub const AGENTFILE_SIG_VERIFIED: &str =
        "Agentfile: \x1b[32m\u{2713}\x1b[0m signature verified (kernel key {})";
    pub const AGENTFILE_SIG_UNSIGNED: &str =
        "Agentfile: unsigned (sign with `ostk sign` or `ostk shutdown`)";
    pub const AGENTFILE_SIG_INVALID: &str =
        "Agentfile: \x1b[31m\u{2718}\x1b[0m SIGNATURE INVALID \u{2014} {}";
    pub const AGENTFILE_SIG_GPG_ABSENT: &str =
        "Agentfile: gpg not available, signature not checked";
}

pub mod humanfile_trust {
    pub const VERIFIED: &str = "HUMANFILE: \u{2713} verified (key: {})";
    pub const UNSIGNED: &str =
        "HUMANFILE: \u{26a0} unsigned \u{2014} secrets disabled, run `ostk sign`";
    pub const BAD_SIGNATURE: &str =
        "HUMANFILE: \u{2718} signature invalid \u{2014} refusing secret injection";
    pub const GPG_ABSENT: &str =
        "HUMANFILE: gpg not available, signature not checked";
    pub const SECRET_BLOCKED_UNSIGNED: &str =
        "[warn] HUMANFILE unsigned \u{2014} secret injection disabled";
    pub const SECRET_BLOCKED_BAD_SIG: &str =
        "HUMANFILE signature invalid \u{2014} refusing to inject secrets";
}

pub mod shell {
    pub const PTY_FALLBACK: &str =
        "ostk: PTY failed ({}), falling back to direct exec for: {}";
    pub const TUI_ERROR: &str = "tui error: {}";
}
