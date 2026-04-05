use std::fs;
use std::path::PathBuf;
use std::process::Command;

/// atomic counter for unique temp dirs
static TEST_COUNTER: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);

fn setup_test_project() -> PathBuf {
    let n = TEST_COUNTER.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    let dir = PathBuf::from("/Users/scottmeyer/.gemini/tmp/haystack")
        .join("haystack_human_exp_tests")
        .join(format!("test_{}", n));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(dir.join(".ostk/needles")).unwrap();
    dir
}

fn haystack_bin() -> PathBuf {
    let local = std::env::current_dir().unwrap().join("target/debug/haystack");
    if local.exists() {
        return local;
    }
    PathBuf::from("haystack")
}

#[test]
fn test_hay_file_and_list() {
    let root = setup_test_project();
    let bin = haystack_bin();

    // 1. File some hay (using work hay)
    let status = Command::new(&bin)
        .args(["work", "hay", "thinking about semantic search"])
        .current_dir(&root)
        .status()
        .unwrap();
    assert!(status.success());

    // 2. Check audit.jsonl
    let audit_content = fs::read_to_string(root.join(".ostk/audit.jsonl")).unwrap();
    assert!(audit_content.contains("hay.filed"));
    assert!(audit_content.contains("thinking about semantic search"));
}

#[test]
fn test_compile_workflow() {
    let root = setup_test_project();
    let bin = haystack_bin();

    // 1. File hay that looks like a needle (starts with a verb)
    Command::new(&bin)
        .args(["work", "hay", ":status check"])
        .current_dir(&root)
        .status()
        .unwrap();

    // 2. Compile (dry-run)
    let output = Command::new(&bin)
        .args(["work", "compile", "--dry-run"])
        .current_dir(&root)
        .output()
        .unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("[NEEDLE CANDIDATE] :status check"));
}

#[test]
fn test_index_and_search_workflow() {
    let root = setup_test_project();
    let bin = haystack_bin();

    // 1. Create a draft
    let draft_dir = root.join("docs/draft");
    fs::create_dir_all(&draft_dir).unwrap();
    fs::write(draft_dir.join("tui-spec.md"), "# TUI Features\n- Tack bar\n- Quickline").unwrap();

    // 2. File some hay and a regular file
    Command::new(&bin)
        .args(["work", "hay", "remember to implement autocomplete"])
        .current_dir(&root)
        .status()
        .unwrap();
    fs::write(root.join("audit.txt"), "audit log: remember to implement autocomplete").unwrap();

    // 3. Index
    let status = Command::new(&bin)
        .args(["work", "index"])
        .current_dir(&root)
        .status()
        .unwrap();
    assert!(status.success());

    // 4. Verify index.json
    let index_content = fs::read_to_string(root.join(".ostk/index.json")).unwrap();
    assert!(index_content.contains("tui-spec"));
    assert!(index_content.contains("remember to implement autocomplete"));

    // 5. Basic search (ripgrep/grep)
    let output = Command::new(&bin)
        .args(["search", "autocomplete"])
        .current_dir(&root)
        .output()
        .unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout);
    println!("SEARCH STDOUT: '{}'", stdout);
    assert!(stdout.contains("audit.txt"));
    assert!(stdout.contains("remember to implement autocomplete"));
}

#[test]
fn test_needle_lifecycle() {
    let root = setup_test_project();
    let bin = haystack_bin();

    // 1. Add a needle (needle add <title> --priority <p>)
    let status = Command::new(&bin)
        .args(["needle", "add", "implement-tui", "--priority", "P0"])
        .current_dir(&root)
        .status()
        .unwrap();
    assert!(status.success());

    // 2. List needles
    let output = Command::new(&bin)
        .args(["needle", "list"])
        .current_dir(&root)
        .output()
        .unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("implement-tui"));
    assert!(stdout.contains("[P0]"));

    // 3. Claim next (dry run style list)
    let output = Command::new(&bin)
        .args(["needle", "next"])
        .current_dir(&root)
        .output()
        .unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("implement-tui"));

    // 4. Close needle
    let id_line = stdout.lines().next().unwrap();
    let id = id_line.split_whitespace().next().unwrap();
    
    let status = Command::new(&bin)
        .args(["needle", "close", id, "--reason", "done"])
        .current_dir(&root)
        .status()
        .unwrap();
    assert!(status.success());

    // 5. Verify closed in list
    let output = Command::new(&bin)
        .args(["needle", "list", "--status", "closed"])
        .current_dir(&root)
        .output()
        .unwrap();
    assert!(String::from_utf8_lossy(&output.stdout).contains("implement-tui"));
}
