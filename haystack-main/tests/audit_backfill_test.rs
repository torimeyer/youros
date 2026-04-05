use std::fs;
use std::process::Command;
use tempfile::TempDir;

#[test]
fn test_audit_backfill_fix_rewrites() {
    let dir = TempDir::new().expect("failed to create temp dir");
    let repo_path = dir.path();

    // 1. git init
    Command::new("git")
        .arg("init")
        .current_dir(repo_path)
        .output()
        .expect("git init failed");

    // 2. haystack install
    let haystack_bin = option_env!("CARGO_BIN_EXE_haystack").unwrap_or("haystack");
    Command::new(haystack_bin)
        .arg("install")
        .arg("--no-symlinks")
        .current_dir(repo_path)
        .output()
        .expect("haystack install failed");

    // 3. Create a needle
    Command::new(haystack_bin)
        .args(["needle", "add", "test task", "--priority", "P1"])
        .current_dir(repo_path)
        .output()
        .expect("needle add failed");

    // 4. Create a commit with bead ID
    fs::write(repo_path.join("file.txt"), "hello").unwrap();
    Command::new("git").args(["add", "file.txt"]).current_dir(repo_path).output().unwrap();
    Command::new("git")
        .args(["commit", "-m", "fix: →001 test task"])
        .current_dir(repo_path)
        .output()
        .unwrap();

    let first_hash = Command::new("git")
        .args(["rev-parse", "HEAD"])
        .current_dir(repo_path)
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap();

    // 5. Add bead.committed to audit (already done by needle add + commit if we used haystack commit, but let's do it manually to ensure we have a "phantom" soon)
    // Actually haystack needle add + manual git commit means audit doesn't have it yet.
    // Let's run backfill once to get the first hash in.
    let backfill_out1 = Command::new(haystack_bin)
        .args(["os", "audit", "backfill"])
        .current_dir(repo_path)
        .output()
        .unwrap();
    println!("Backfill 1 stdout: {}", String::from_utf8_lossy(&backfill_out1.stdout));
    println!("Backfill 1 stderr: {}", String::from_utf8_lossy(&backfill_out1.stderr));

    // Verify it's in audit
    let audit_content = fs::read_to_string(repo_path.join(".ostk/audit.jsonl")).unwrap();
    assert!(audit_content.contains(&first_hash), "Audit should contain first_hash {}. Content:\n{}", first_hash, audit_content);
    assert!(audit_content.contains("→001"));

    // 6. Rewrite history (amend)
    fs::write(repo_path.join("file.txt"), "hello world").unwrap();
    Command::new("git").args(["add", "file.txt"]).current_dir(repo_path).output().unwrap();
    Command::new("git")
        .args(["commit", "--amend", "-m", "fix: →001 test task updated"])
        .current_dir(repo_path)
        .output()
        .unwrap();

    let second_hash = Command::new("git")
        .args(["rev-parse", "HEAD"])
        .current_dir(repo_path)
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap();

    let git_log_debug = Command::new("git").args(["log", "--oneline"]).current_dir(repo_path).output().unwrap();
    println!("GIT LOG DEBUG:\n{}", String::from_utf8_lossy(&git_log_debug.stdout));

    assert_ne!(first_hash, second_hash);

    // 7. Manually add a phantom hash to audit.jsonl to test fix-rewrites
    let fake_hash = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef";
    let phantom_event = serde_json::json!({
        "event": "bead.committed",
        "timestamp": "2026-03-13T00:00:00Z",
        "commit": fake_hash,
        "bead_id": "→001",
        "spec_ref": ""
    });
    use std::io::Write;
    let mut file = fs::OpenOptions::new().append(true).open(repo_path.join(".ostk/audit.jsonl")).unwrap();
    writeln!(file, "{}", phantom_event).unwrap();

    let audit_debug = fs::read_to_string(repo_path.join(".ostk/audit.jsonl")).unwrap();
    println!("AUDIT DEBUG BEFORE BACKFILL:\n{}", audit_debug);

    // 8. Run audit check — should detect phantom hash AND trigger self-healing backfill
    let check_out = Command::new(haystack_bin)
        .args(["os", "audit", "check"])
        .current_dir(repo_path)
        .output()
        .unwrap();
    
    let check_stdout = String::from_utf8_lossy(&check_out.stdout);
    println!("Check 1 stdout: {}", check_stdout);
    assert!(check_stdout.contains("phantom hashes detected"));
    assert!(check_stdout.contains("triggering automatic backfill recovery"));

    // 9. Verify audit.jsonl now has commit.remapped for fake_hash
    let audit_after = fs::read_to_string(repo_path.join(".ostk/audit.jsonl")).unwrap();
    println!("AUDIT AFTER HEALING:\n{}", audit_after);
    assert!(audit_after.contains("commit.remapped"));
    assert!(audit_after.contains(fake_hash));
    assert!(audit_after.contains(&second_hash));

    // 10. Close the needle so audit check passes (no STALE warning)
    Command::new(haystack_bin)
        .args(["needle", "close", "→001", "--reason", "test done"])
        .current_dir(repo_path)
        .output()
        .unwrap();

    // 11. Run audit check again — should pass now
    let final_check = Command::new(haystack_bin)
        .args(["os", "audit", "check"])
        .current_dir(repo_path)
        .output()
        .unwrap();
    
    let final_check_stdout = String::from_utf8_lossy(&final_check.stdout);
    println!("Final check output: {}", final_check_stdout);
    assert!(final_check.status.success(), "Audit check should pass after healing. Output: {}", final_check_stdout);
}
