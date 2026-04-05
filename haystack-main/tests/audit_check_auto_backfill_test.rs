use std::fs;
use std::process::Command;
use tempfile::TempDir;

#[test]
fn test_audit_check_auto_backfill() {
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

    // 3. Create a commit with bead ID
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

    // 4. Backfill to get it in audit
    Command::new(haystack_bin)
        .args(["os", "audit", "backfill"])
        .current_dir(repo_path)
        .output()
        .unwrap();

    // 5. Manually add a phantom hash to audit.jsonl
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

    // 6. Run audit check — should detect phantom and run backfill
    // Since →001 exists in first_hash, it should remap fake_hash -> first_hash
    let check_out = Command::new(haystack_bin)
        .args(["os", "audit", "check"])
        .current_dir(repo_path)
        .output()
        .unwrap();

    let check_stdout = String::from_utf8_lossy(&check_out.stdout);
    println!("Check output: {}", check_stdout);

    assert!(check_stdout.contains("triggering automatic backfill recovery"));
    assert!(check_stdout.contains("REMAPPED: deadbeefdeadbeefdeadbeefdeadbeefdeadbeef ->"));

    // 7. Verify audit.jsonl has the remap
    let audit_after = fs::read_to_string(repo_path.join(".ostk/audit.jsonl")).unwrap();
    assert!(audit_after.contains("commit.remapped"));
    assert!(audit_after.contains(fake_hash));
    assert!(audit_after.contains(&first_hash));

    // 8. Close needle and run check again to ensure it passes
    Command::new(haystack_bin)
        .args(["work", "close", "→001", "--reason", "done"])
        .current_dir(repo_path)
        .output()
        .unwrap();

    let final_check = Command::new(haystack_bin)
        .args(["os", "audit", "check"])
        .current_dir(repo_path)
        .output()
        .unwrap();
    assert!(final_check.status.success());
    }

