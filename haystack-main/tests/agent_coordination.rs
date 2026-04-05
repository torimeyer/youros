use std::fs;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::net::UnixStream;
use serde_json::Value;
use ostk::serve::socket::{run_socket_server, socket_path};
use ostk::kernel::pty::run_command;

#[tokio::test]
async fn test_agent_handoff_interception() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().to_path_buf();
    let hs_dir = root.join(".ostk");
    fs::create_dir_all(hs_dir.join("needles")).unwrap();
    fs::write(hs_dir.join("needles/counter"), "0").unwrap();
    fs::write(hs_dir.join("needles/issues.jsonl"), "").unwrap();
    fs::write(hs_dir.join("audit.jsonl"), "").unwrap();

    // 1. Start socket server
    let hs_dir_clone = hs_dir.clone();
    tokio::spawn(async move {
        let _ = run_socket_server(hs_dir_clone, None).await;
    });

    // Wait for socket to be ready
    let sock = socket_path(&hs_dir);
    let mut retries = 0;
    while !sock.exists() && retries < 10 {
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
        retries += 1;
    }

    // 2. Connect mock presentation layer
    let stream = UnixStream::connect(&sock).await.unwrap();
    let mut reader = BufReader::new(stream);

    // 3. Run a mock agent command that emits a Tack signal
    // Set CWD so pty can find .ostk
    let orig_cwd = std::env::current_dir().unwrap();
    std::env::set_current_dir(&root).unwrap();
    
    tokio::spawn(async move {
        let _ = run_command(&["/bin/sh".to_string(), "-c".to_string(), "echo ':handoff @tester →576'".to_string()]);
    });

    // 4. Read broadcast from socket — should see fcp.resolved
    let mut line = String::new();
    // We might get filesystem.mutated first from audit appends, so look for fcp.resolved
    loop {
        line.clear();
        reader.read_line(&mut line).await.unwrap();
        if line.is_empty() { break; }
        
        let msg: Value = serde_json::from_str(&line).unwrap();
        if msg["event"] == "fcp.resolved" {
            assert_eq!(msg["verb"], "handoff");
            assert!(msg["command"].as_str().unwrap().contains("run"));
            assert!(msg["input"].as_str().unwrap().contains(":handoff @tester →576"));
            break;
        }
    }

    std::env::set_current_dir(orig_cwd).unwrap();
}
