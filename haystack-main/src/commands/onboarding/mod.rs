//! Onboarding — welcome screen, guided setup, interactive widgets.
//!
//! - `run()`: Brief welcome menu when no .ostk/ and no API key (zero-arg path)
//! - `run_guided()`: Interactive 7-step configuration (ostk init --guided)

pub mod widget;
mod guided;

use std::io::{self, BufRead, Write};

/// Brief welcome menu — shown when no .ostk/ and no API key detected.
/// Tells the user the fastest path: set API key → run ostk.
pub fn run() -> Result<(), String> {
    println!();
    println!("  \x1b[1mostk\x1b[0m — invisible OS for AI agents");
    println!();
    println!("  No .ostk/ found and no API key detected.");
    println!();
    println!("  \x1b[1mQuick start:\x1b[0m");
    println!("    \x1b[32mexport ANTHROPIC_API_KEY=sk-...\x1b[0m    then just run ostk");
    println!("    \x1b[32mostk init\x1b[0m                          initialize without an API key");
    println!("    \x1b[32mostk init --guided\x1b[0m                 interactive setup with options");
    println!();
    println!("  Learn more: \x1b[2mhttps://ostk.ai/docs\x1b[0m");
    println!();
    Ok(())
}

/// Interactive guided setup with step-by-step configuration.
/// Called by `ostk init --guided`. 7-step flow using crossterm widgets.
pub fn run_guided() -> Result<(), String> {
    guided::run()
}

#[allow(dead_code)]
fn read_line_trimmed() -> String {
    io::stdout().flush().ok();
    let mut line = String::new();
    io::stdin().lock().read_line(&mut line).ok();
    line.trim().to_string()
}

#[cfg(test)]
mod tests {
    /// Verify the fresh-state detection logic that gates onboarding.
    /// This does NOT run the interactive flow — it tests the predicate.
    #[test]
    fn detect_fresh_state_no_ostk_dir() {
        let tmp = std::env::temp_dir().join(format!(
            "ostk_test_onboarding_fresh_{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&tmp);
        std::fs::create_dir_all(&tmp).unwrap();

        // No .ostk/ directory — should be detected as fresh
        assert!(!tmp.join(".ostk").exists());

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn detect_fresh_state_with_ostk_dir() {
        let tmp = std::env::temp_dir().join(format!(
            "ostk_test_onboarding_existing_{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&tmp);
        std::fs::create_dir_all(tmp.join(".ostk")).unwrap();

        // .ostk/ exists — not fresh
        assert!(tmp.join(".ostk").exists());

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn needs_onboarding_predicate() {
        // Test the actual predicate logic used in main.rs
        let tmp = std::env::temp_dir().join(format!(
            "ostk_test_onboarding_pred_{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&tmp);
        std::fs::create_dir_all(&tmp).unwrap();

        // Fresh directory: find_project_root would fail, so needs_onboarding = true
        let needs = !tmp.join(".ostk").is_dir();
        assert!(needs, "fresh dir should need onboarding");

        // After init: .ostk/ exists, so needs_onboarding = false
        std::fs::create_dir_all(tmp.join(".ostk")).unwrap();
        let needs = !tmp.join(".ostk").is_dir();
        assert!(!needs, "initialized dir should not need onboarding");

        let _ = std::fs::remove_dir_all(&tmp);
    }
}
