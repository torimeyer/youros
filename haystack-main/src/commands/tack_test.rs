//! tack.t test runner — `ostk bench --tack`
//!
//! Parses `.t` files from bench/tack/, runs tack resolution + command
//! assertion + exit/output assertions, and reports PASS/FAIL per test.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Instant;

use crate::fcp::tack as fcp;

/// A single parsed test case from a `.t` file.
#[derive(Debug, Clone)]
pub struct TackTestCase {
    /// Source file (for reporting)
    pub file: PathBuf,
    /// Line offset in file where this test starts
    pub line: usize,
    /// The tack expression (`.tack` field)
    pub tack: String,
    /// Expected resolved command (`.cmd` field)
    pub cmd: String,
    /// Assertion (`.test` field, e.g. `exit:0`, `contains:OS`)
    pub assertion: String,
    /// Tags (e.g. `.boot-critical true`)
    pub tags: Vec<(String, String)>,
}

/// Outcome of running a single test.
#[derive(Debug)]
pub struct TackTestResult {
    pub test: TackTestCase,
    pub passed: bool,
    pub failure_reason: Option<String>,
    pub duration_ms: u64,
}

impl TackTestResult {
    pub fn verdict(&self) -> &'static str {
        if self.passed { "PASS" } else { "FAIL" }
    }
}

/// Parse `.t` files from `bench/tack/`.
///
/// Format (line-delimited, blank-line-separated blocks):
/// ```ignore
/// .tack  :compile
/// .cmd   ostk compile
/// .test  exit:0
/// .boot-critical true
/// ```
pub fn parse_t_file(path: &Path) -> Result<Vec<TackTestCase>, String> {
    let content = fs::read_to_string(path)
        .map_err(|e| format!("cannot read {}: {}", path.display(), e))?;

    let mut cases = Vec::new();
    let mut current_tack: Option<String> = None;
    let mut current_cmd: Option<String> = None;
    let mut current_test: Option<String> = None;
    let mut current_tags: Vec<(String, String)> = Vec::new();
    let mut current_line = 0usize;

    let flush = |tack: &mut Option<String>,
                 cmd: &mut Option<String>,
                 test: &mut Option<String>,
                 tags: &mut Vec<(String, String)>,
                 line: usize,
                 path: &Path,
                 cases: &mut Vec<TackTestCase>|
     -> Result<(), String> {
        if tack.is_none() && cmd.is_none() && test.is_none() {
            return Ok(());
        }
        let t = tack.take().ok_or_else(|| {
            format!("{}:{}: block missing .tack field", path.display(), line)
        })?;
        let c = cmd.take().ok_or_else(|| {
            format!("{}:{}: block missing .cmd field", path.display(), line)
        })?;
        let a = test.take().ok_or_else(|| {
            format!("{}:{}: block missing .test field", path.display(), line)
        })?;
        cases.push(TackTestCase {
            file: path.to_path_buf(),
            line,
            tack: t,
            cmd: c,
            assertion: a,
            tags: std::mem::take(tags),
        });
        Ok(())
    };

    for (i, raw_line) in content.lines().enumerate() {
        let line = raw_line.trim();

        if line.is_empty() || line.starts_with('#') {
            // Blank line / comment: flush current block
            if current_tack.is_some() || current_cmd.is_some() || current_test.is_some() {
                flush(
                    &mut current_tack,
                    &mut current_cmd,
                    &mut current_test,
                    &mut current_tags,
                    current_line,
                    path,
                    &mut cases,
                )?;
            }
            continue;
        }

        // Parse `.field  value`
        let (field, value) = if let Some(rest) = line.strip_prefix('.') {
            let mut parts = rest.splitn(2, char::is_whitespace);
            let field = parts.next().unwrap_or("").trim().to_string();
            let value = parts.next().unwrap_or("").trim().to_string();
            (field, value)
        } else {
            return Err(format!(
                "{}:{}: unexpected line (expected .field): {}",
                path.display(),
                i + 1,
                line
            ));
        };

        match field.as_str() {
            "tack" => {
                if current_tack.is_none() {
                    current_line = i + 1;
                }
                current_tack = Some(value);
            }
            "cmd" => {
                current_cmd = Some(value);
            }
            "test" => {
                current_test = Some(value);
            }
            _ => {
                // Arbitrary tag like `.boot-critical true`
                current_tags.push((field, value));
            }
        }
    }

    // Flush trailing block
    if current_tack.is_some() || current_cmd.is_some() || current_test.is_some() {
        flush(
            &mut current_tack,
            &mut current_cmd,
            &mut current_test,
            &mut current_tags,
            current_line,
            path,
            &mut cases,
        )?;
    }

    Ok(cases)
}

/// Discover all `.t` files under `bench/tack/`.
pub fn discover_t_files(root: &Path) -> Vec<PathBuf> {
    let tack_dir = root.join("bench/tack");
    if !tack_dir.is_dir() {
        return Vec::new();
    }

    let mut files: Vec<PathBuf> = fs::read_dir(&tack_dir)
        .ok()
        .into_iter()
        .flatten()
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.extension().map(|ext| ext == "t").unwrap_or(false))
        .collect();
    files.sort();
    files
}

/// Run a single test case.
///
/// Steps:
/// 1. Resolve `.tack` expression via fcp::tack::resolve_tack()
/// 2. Check resolved command matches `.cmd`
/// 3. Run `.cmd` as a shell command and evaluate `.test` assertion
pub fn run_test(test: &TackTestCase, ostk_dir: &Path) -> TackTestResult {
    let start = Instant::now();

    // Step 1: resolve tack expression
    let resolution = match fcp::resolve_tack(&test.tack, ostk_dir) {
        Some(r) => r,
        None => {
            return TackTestResult {
                test: test.clone(),
                passed: false,
                failure_reason: Some(format!(
                    "tack parse failed: '{}' did not match any grammar prefix",
                    test.tack
                )),
                duration_ms: start.elapsed().as_millis() as u64,
            };
        }
    };

    // Build the full resolved command string
    // e.g. command="compile" args=[] → "ostk compile"
    //      command="show" args=["status"] → "ostk show status"
    let resolved_cmd = if let Some(ref cmd) = resolution.command {
        let mut parts = vec!["ostk".to_string(), cmd.clone()];
        parts.extend(resolution.args.iter().cloned());
        parts.join(" ")
    } else {
        String::new()
    };

    // Step 2: compare resolved command to expected .cmd
    if resolved_cmd != test.cmd {
        return TackTestResult {
            test: test.clone(),
            passed: false,
            failure_reason: Some(format!(
                "tack resolution mismatch: got '{}', expected '{}'",
                resolved_cmd, test.cmd
            )),
            duration_ms: start.elapsed().as_millis() as u64,
        };
    }

    // Step 3: run the .cmd and evaluate assertion
    let assertion_result = evaluate_assertion(&test.cmd, &test.assertion);
    let duration_ms = start.elapsed().as_millis() as u64;

    match assertion_result {
        Ok(()) => TackTestResult {
            test: test.clone(),
            passed: true,
            failure_reason: None,
            duration_ms,
        },
        Err(reason) => TackTestResult {
            test: test.clone(),
            passed: false,
            failure_reason: Some(reason),
            duration_ms,
        },
    }
}

/// Run a shell command and evaluate an assertion against it.
///
/// Assertion forms:
/// - `exit:0`          — command must exit with code 0
/// - `exit:N`          — command must exit with code N
/// - `contains:TEXT`   — stdout or stderr must contain TEXT
/// - `not-contains:X`  — stdout and stderr must NOT contain X
fn evaluate_assertion(cmd: &str, assertion: &str) -> Result<(), String> {
    // Split the shell command into argv (simple split — no shell expansion)
    let mut parts = cmd.split_whitespace();
    let binary = match parts.next() {
        Some(b) => b,
        None => return Err("empty .cmd".to_string()),
    };
    let args: Vec<&str> = parts.collect();

    let output = Command::new(binary)
        .args(&args)
        .output()
        .map_err(|e| format!("failed to run '{}': {}", cmd, e))?;

    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
    let combined = format!("{}{}", stdout, stderr);
    let exit_code = output.status.code().unwrap_or(-1);

    // Parse and evaluate
    if let Some(expected) = assertion.strip_prefix("exit:") {
        let expected_code: i32 = expected
            .parse()
            .map_err(|_| format!("invalid exit code in assertion: '{}'", assertion))?;
        if exit_code != expected_code {
            return Err(format!(
                "exit code: got {}, expected {}",
                exit_code, expected_code
            ));
        }
        return Ok(());
    }

    if let Some(needle) = assertion.strip_prefix("contains:") {
        if !combined.contains(needle) {
            let snippet = combined.chars().take(120).collect::<String>();
            return Err(format!(
                "output does not contain '{}'. Got: {}",
                needle, snippet
            ));
        }
        return Ok(());
    }

    if let Some(needle) = assertion.strip_prefix("not-contains:") {
        if combined.contains(needle) {
            return Err(format!("output unexpectedly contains '{}'", needle));
        }
        return Ok(());
    }

    Err(format!("unknown assertion format: '{}'", assertion))
}

/// Entry point for `ostk bench --tack`.
pub fn run(root: &Path) -> Result<(), String> {
    let tack_dir = root.join("bench/tack");
    if !tack_dir.is_dir() {
        return Err(format!(
            "bench/tack/ directory not found at {}",
            tack_dir.display()
        ));
    }

    let files = discover_t_files(root);
    if files.is_empty() {
        println!("no .t files found in bench/tack/");
        return Ok(());
    }

    // Parse all test cases
    let mut all_tests: Vec<TackTestCase> = Vec::new();
    for file in &files {
        match parse_t_file(file) {
            Ok(cases) => {
                let rel = file.strip_prefix(root).unwrap_or(file);
                println!("  loaded {} test(s) from {}", cases.len(), rel.display());
                all_tests.extend(cases);
            }
            Err(e) => {
                eprintln!("  parse error: {}", e);
                return Err(e);
            }
        }
    }

    println!();
    println!("ostk bench --tack: {} test(s)", all_tests.len());
    println!();

    let ostk_dir = root.join(".ostk");
    let mut passed = 0usize;
    let mut failed = 0usize;
    let mut total_ms = 0u64;

    let mut results: Vec<TackTestResult> = Vec::new();
    for test in &all_tests {
        let result = run_test(test, &ostk_dir);
        total_ms += result.duration_ms;
        if result.passed {
            passed += 1;
        } else {
            failed += 1;
        }
        results.push(result);
    }

    // Report
    for result in &results {
        let file_rel = result
            .test
            .file
            .strip_prefix(root)
            .unwrap_or(&result.test.file);
        let tags_str = if result.test.tags.is_empty() {
            String::new()
        } else {
            let t = result
                .test
                .tags
                .iter()
                .map(|(k, v)| format!("[{k}={v}]"))
                .collect::<Vec<_>>()
                .join(" ");
            format!("  {t}")
        };

        println!(
            "{}  {}:{}  {}{}",
            result.verdict(),
            file_rel.display(),
            result.test.line,
            result.test.tack,
            tags_str
        );

        if let Some(ref reason) = result.failure_reason {
            println!("      reason: {}", reason);
        }
    }

    println!();
    println!(
        "=== tack test summary: {}/{} passed, {}ms total ===",
        passed,
        passed + failed,
        total_ms
    );

    if failed > 0 {
        Err(format!("{} tack test(s) failed", failed))
    } else {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write as _;

    fn write_temp_t(content: &str) -> tempfile::NamedTempFile {
        let mut f = tempfile::Builder::new().suffix(".t").tempfile().unwrap();
        f.write_all(content.as_bytes()).unwrap();
        f
    }

    #[test]
    fn parse_single_block() {
        let f = write_temp_t(
            ".tack  :compile\n.cmd   ostk compile\n.test  exit:0\n",
        );
        let cases = parse_t_file(f.path()).unwrap();
        assert_eq!(cases.len(), 1);
        assert_eq!(cases[0].tack, ":compile");
        assert_eq!(cases[0].cmd, "ostk compile");
        assert_eq!(cases[0].assertion, "exit:0");
        assert!(cases[0].tags.is_empty());
    }

    #[test]
    fn parse_multiple_blocks() {
        let f = write_temp_t(
            ".tack  :compile\n.cmd   ostk compile\n.test  exit:0\n\n\
             .tack  :boot\n.cmd   ostk boot\n.test  exit:0\n",
        );
        let cases = parse_t_file(f.path()).unwrap();
        assert_eq!(cases.len(), 2);
    }

    #[test]
    fn parse_tags() {
        let f = write_temp_t(
            ".tack  :boot\n.cmd   ostk boot\n.test  exit:0\n.boot-critical  true\n",
        );
        let cases = parse_t_file(f.path()).unwrap();
        assert_eq!(cases[0].tags, vec![("boot-critical".to_string(), "true".to_string())]);
    }

    #[test]
    fn parse_comment_and_blank() {
        let f = write_temp_t(
            "# this is a comment\n\n.tack  :compile\n.cmd   ostk compile\n.test  exit:0\n",
        );
        let cases = parse_t_file(f.path()).unwrap();
        assert_eq!(cases.len(), 1);
    }

    #[test]
    fn parse_missing_tack_field() {
        let f = write_temp_t(".cmd   ostk compile\n.test  exit:0\n");
        let result = parse_t_file(f.path());
        assert!(result.is_err());
    }

    #[test]
    fn parse_missing_cmd_field() {
        let f = write_temp_t(".tack  :compile\n.test  exit:0\n");
        let result = parse_t_file(f.path());
        assert!(result.is_err());
    }

    #[test]
    fn evaluate_assertion_exit_0() {
        // `true` exits 0
        let result = evaluate_assertion("true", "exit:0");
        assert!(result.is_ok(), "{:?}", result);
    }

    #[test]
    fn evaluate_assertion_exit_nonzero() {
        // `false` exits 1
        let result = evaluate_assertion("false", "exit:1");
        assert!(result.is_ok(), "{:?}", result);
    }

    #[test]
    fn evaluate_assertion_exit_fail() {
        let result = evaluate_assertion("false", "exit:0");
        assert!(result.is_err());
    }

    #[test]
    fn evaluate_assertion_contains() {
        let result = evaluate_assertion("echo hello", "contains:hello");
        assert!(result.is_ok(), "{:?}", result);
    }

    #[test]
    fn evaluate_assertion_contains_fail() {
        let result = evaluate_assertion("echo hello", "contains:xyz");
        assert!(result.is_err());
    }

    #[test]
    fn evaluate_assertion_not_contains() {
        let result = evaluate_assertion("echo hello", "not-contains:xyz");
        assert!(result.is_ok(), "{:?}", result);
    }

    #[test]
    fn evaluate_assertion_not_contains_fail() {
        let result = evaluate_assertion("echo hello", "not-contains:hello");
        assert!(result.is_err());
    }

    #[test]
    fn evaluate_unknown_assertion() {
        let result = evaluate_assertion("true", "badformat");
        assert!(result.is_err());
    }
}
