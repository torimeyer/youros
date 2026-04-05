use std::fs;
use std::process::Command;
use tempfile::TempDir;

#[test]
fn test_trace_transitive_remap() {
    let dir = TempDir::new().expect("failed to create temp dir");
    let repo_path = dir.path();

    // 1. git init
    Command::new("git").arg("init").current_dir(repo_path).output().expect("git init failed");

    // 2. haystack install
    let haystack_bin = option_env!("CARGO_BIN_EXE_haystack").unwrap_or("haystack");
    Command::new(haystack_bin).arg("install").arg("--no-symlinks").current_dir(repo_path).output().expect("haystack install failed");

    // 3. Create a needle
    Command::new(haystack_bin).args(["needle", "add", "transitive test", "--priority", "P1"]).current_dir(repo_path).output().expect("needle add failed");

    // 4. Manual audit entries for remap chain: H1 -> H2 -> H3
    let h1 = "1111111111111111111111111111111111111111";
    let h2 = "2222222222222222222222222222222222222222";
    let _h3 = "3333333333333333333333333333333333333333";

    // Create a real commit for the final hash so git log doesn't fail
    fs::write(repo_path.join("file.txt"), "v1").unwrap();
    Command::new("git").args(["add", "file.txt"]).current_dir(repo_path).output().unwrap();
    Command::new("git").args(["commit", "-m", "fix: →001 final"]).current_dir(repo_path).output().unwrap();
    let real_h3 = Command::new("git").args(["rev-parse", "HEAD"]).current_dir(repo_path).output().map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string()).unwrap();

    let audit_file = repo_path.join(".ostk/audit.jsonl");
    let e1 = serde_json::json!({"event": "commit.remapped", "old_commit": h1, "new_commit": h2, "cause": "test", "timestamp": "2026-03-14T00:00:00Z"});
    let e2 = serde_json::json!({"event": "commit.remapped", "old_commit": h2, "new_commit": real_h3, "cause": "test", "timestamp": "2026-03-14T00:00:01Z"});
    
    use std::io::Write;
    let mut file = fs::OpenOptions::new().append(true).open(&audit_file).unwrap();
    writeln!(file, "{}", e1).unwrap();
    writeln!(file, "{}", e2).unwrap();

    // 5. Trace the original hash H1
    let trace_out = Command::new(haystack_bin).args(["trace", h1]).current_dir(repo_path).output().expect("trace failed");
    let stdout = String::from_utf8_lossy(&trace_out.stdout);
    
    println!("Trace output:\n{}", stdout);
    
    // Check for transitive resolution in output
    assert!(stdout.contains(h1));
    assert!(stdout.contains(&real_h3));
    assert!(stdout.contains("remapped chain"));
    assert!(stdout.contains("→001"));
}
