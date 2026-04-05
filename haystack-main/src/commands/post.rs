//! `ostk post` — Power-On Self Test.
//!
//! Runs 7 checks before declaring the kernel live. Any failure halts boot.
//! Designed to run before `login.gpg:` prompt in the boot sequence.

use std::fs;
use std::path::Path;

/// Result of a single POST check.
#[derive(Debug)]
pub struct CheckResult {
    pub label: &'static str,
    pub passed: bool,
    pub detail: Option<String>,
}

impl CheckResult {
    fn pass(label: &'static str) -> Self {
        Self { label, passed: true, detail: None }
    }

    fn fail(label: &'static str, detail: impl Into<String>) -> Self {
        Self { label, passed: false, detail: Some(detail.into()) }
    }
}

/// Run `ostk post` using CWD.
pub fn run() -> Result<(), String> {
    let cwd = std::env::current_dir().map_err(|e| e.to_string())?;
    run_at(&cwd)
}

/// Run `ostk post` at a specific directory.
pub fn run_at(root: &Path) -> Result<(), String> {
    println!("ostk POST");

    let results = run_checks(root);

    let mut any_failed = false;
    for r in &results {
        if r.passed {
            println!("[OK] {}", r.label);
        } else {
            let detail = r.detail.as_deref().unwrap_or("unknown error");
            println!("[FAIL] {}: {}", r.label, detail);
            any_failed = true;
        }
    }

    if any_failed {
        println!("POST failed. boot halted.");
        Err("POST failed".to_string())
    } else {
        println!("POST ready.");
        Ok(())
    }
}

/// Run all 7 POST checks and return results.
pub fn run_checks(root: &Path) -> Vec<CheckResult> {
    let hs_dir = crate::state_dir(root);
    vec![
        check_ostk_dir(&hs_dir),
        check_audit_jsonl(&hs_dir),
        check_humanfile(&hs_dir),
        check_primefile(&hs_dir),
        check_needle_store(&hs_dir),
        check_identity_counter(&hs_dir),
        check_no_conflict_markers(&hs_dir),
    ]
}

fn check_ostk_dir(hs_dir: &Path) -> CheckResult {
    if hs_dir.is_dir() {
        CheckResult::pass(".ostk/ present")
    } else {
        CheckResult::fail(".ostk/ present", "not found — run `ostk install`")
    }
}

fn check_audit_jsonl(hs_dir: &Path) -> CheckResult {
    let path = hs_dir.join("audit.jsonl");
    if !path.exists() {
        return CheckResult::fail("audit.jsonl readable", "not found");
    }
    match fs::read_to_string(&path) {
        Ok(_) => CheckResult::pass("audit.jsonl readable"),
        Err(e) => CheckResult::fail("audit.jsonl readable", format!("read error: {e}")),
    }
}

fn check_humanfile(hs_dir: &Path) -> CheckResult {
    let path = hs_dir.join("HUMANFILE");
    if path.exists() {
        CheckResult::pass("HUMANFILE present")
    } else {
        CheckResult::fail("HUMANFILE present", "not found")
    }
}

fn check_primefile(hs_dir: &Path) -> CheckResult {
    // .primefile lives at .ostk/.primefile
    let path = hs_dir.join(".primefile");
    if path.exists() {
        CheckResult::pass(".primefile present")
    } else {
        CheckResult::fail(".primefile present", "not found — kernel identity missing")
    }
}

fn check_needle_store(hs_dir: &Path) -> CheckResult {
    let needles_dir = hs_dir.join("needles");
    if !needles_dir.is_dir() {
        return CheckResult::fail("needle store intact", "needles/ directory missing");
    }
    let counter = needles_dir.join("counter");
    if !counter.exists() {
        return CheckResult::fail("needle store intact", "needles/counter missing");
    }
    match fs::read_to_string(&counter) {
        Ok(s) if s.trim().parse::<u64>().is_ok() => CheckResult::pass("needle store intact"),
        Ok(_) => CheckResult::fail("needle store intact", "needles/counter is not a valid number"),
        Err(e) => CheckResult::fail("needle store intact", format!("counter read error: {e}")),
    }
}

fn check_identity_counter(hs_dir: &Path) -> CheckResult {
    let path = hs_dir.join("identity_counter");
    if !path.exists() {
        // identity_counter is optional on first boot — treat as pass with 0
        return CheckResult::pass("identity counter readable");
    }
    // Use kernel identity module for flock-protected read.
    // read_counter returns 0 on parse failure, but we still want to detect
    // truly corrupt files for POST diagnostics.
    let id = crate::kernel::identity::Identity::new(hs_dir);
    match id.read_counter() {
        Ok(_) => CheckResult::pass("identity counter readable"),
        Err(e) => CheckResult::fail("identity counter readable", format!("kernel read error: {e}")),
    }
}

fn check_no_conflict_markers(hs_dir: &Path) -> CheckResult {
    if !hs_dir.is_dir() {
        // Already caught by check 1 — pass here to avoid cascading noise
        return CheckResult::pass("no conflict markers");
    }

    let entries = match fs::read_dir(hs_dir) {
        Ok(e) => e,
        Err(e) => {
            return CheckResult::fail(
                "no conflict markers",
                format!("could not read .ostk/: {e}"),
            )
        }
    };

    for entry in entries.flatten() {
        let path = entry.path();
        // Only scan text files (md, jsonl, txt)
        let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
        if !matches!(ext, "md" | "jsonl" | "txt") {
            continue;
        }
        if let Ok(content) = fs::read_to_string(&path)
            && content.contains("<<<<<<<") {
                let name = path
                    .file_name()
                    .and_then(|n| n.to_str())
                    .unwrap_or("unknown");
                return CheckResult::fail(
                    "no conflict markers",
                    format!("conflict markers found in {name}"),
                );
            }
    }

    CheckResult::pass("no conflict markers")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::{AtomicU32, Ordering};

    static TEST_COUNTER: AtomicU32 = AtomicU32::new(0);

    fn test_dir(prefix: &str) -> std::path::PathBuf {
        let n = TEST_COUNTER.fetch_add(1, Ordering::SeqCst);
        let pid = std::process::id();
        std::env::temp_dir().join(format!("ostk_post_test_{prefix}_{pid}_{n}"))
    }

    /// Create a fully valid POST environment in tmp dir.
    fn make_valid_root(tmp: &std::path::Path) {
        let hs = tmp.join(".ostk");
        fs::create_dir_all(hs.join("needles")).unwrap();
        fs::write(hs.join("audit.jsonl"), "").unwrap();
        fs::write(hs.join("HUMANFILE"), "scott").unwrap();
        fs::write(hs.join(".primefile"), "kernel identity").unwrap();
        fs::write(hs.join("needles/counter"), "0").unwrap();
        fs::write(hs.join("identity_counter"), "42").unwrap();
    }

    #[test]
    fn test_post_all_pass() {
        let tmp = test_dir("all_pass");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        make_valid_root(&tmp);

        let result = run_at(&tmp);
        assert!(result.is_ok(), "POST should pass with valid env: {:?}", result);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_post_missing_ostk_dir() {
        let tmp = test_dir("no_hs_dir");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        // Don't create .ostk/

        let result = run_at(&tmp);
        assert!(result.is_err(), "POST should fail without .ostk/");
        assert!(result.unwrap_err().contains("POST failed"));

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_post_missing_audit_jsonl() {
        let tmp = test_dir("no_audit");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        make_valid_root(&tmp);
        // Remove audit.jsonl
        fs::remove_file(tmp.join(".ostk/audit.jsonl")).unwrap();

        let result = run_at(&tmp);
        assert!(result.is_err(), "POST should fail without audit.jsonl");

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_post_missing_humanfile() {
        let tmp = test_dir("no_humanfile");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        make_valid_root(&tmp);
        fs::remove_file(tmp.join(".ostk/HUMANFILE")).unwrap();

        let result = run_at(&tmp);
        assert!(result.is_err(), "POST should fail without HUMANFILE");

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_post_missing_primefile() {
        let tmp = test_dir("no_primefile");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        make_valid_root(&tmp);
        fs::remove_file(tmp.join(".ostk/.primefile")).unwrap();

        let result = run_at(&tmp);
        assert!(result.is_err(), "POST should fail without .primefile");

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_post_missing_needle_store() {
        let tmp = test_dir("no_needles");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        make_valid_root(&tmp);
        // Remove the needles directory entirely
        fs::remove_dir_all(tmp.join(".ostk/needles")).unwrap();

        let result = run_at(&tmp);
        assert!(result.is_err(), "POST should fail without needles/");

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_post_missing_needle_counter() {
        let tmp = test_dir("no_needle_counter");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        make_valid_root(&tmp);
        fs::remove_file(tmp.join(".ostk/needles/counter")).unwrap();

        let result = run_at(&tmp);
        assert!(result.is_err(), "POST should fail without needles/counter");

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_post_identity_counter_absent_is_ok() {
        // identity_counter is optional — absent = first boot, still passes
        let tmp = test_dir("no_id_counter");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        make_valid_root(&tmp);
        // Remove optional identity counter
        let _ = fs::remove_file(tmp.join(".ostk/identity_counter"));

        let result = run_at(&tmp);
        assert!(result.is_ok(), "POST should pass when identity_counter absent: {:?}", result);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_post_conflict_markers_detected() {
        let tmp = test_dir("conflict");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        make_valid_root(&tmp);
        // Inject conflict markers into boot.md
        fs::write(
            tmp.join(".ostk/boot.md"),
            "# boot\n<<<<<<< HEAD\nsome content\n=======\nother content\n>>>>>>>\n",
        )
        .unwrap();

        let result = run_at(&tmp);
        assert!(result.is_err(), "POST should fail with conflict markers present");

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_post_run_checks_returns_7_results() {
        let tmp = test_dir("seven_checks");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        make_valid_root(&tmp);

        let results = run_checks(&tmp);
        assert_eq!(results.len(), 7, "POST must run exactly 7 checks");

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_post_all_checks_pass_on_valid_env() {
        let tmp = test_dir("all_checks_pass");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        make_valid_root(&tmp);

        let results = run_checks(&tmp);
        for r in &results {
            assert!(r.passed, "check '{}' should pass: {:?}", r.label, r.detail);
        }

        let _ = fs::remove_dir_all(&tmp);
    }
}
