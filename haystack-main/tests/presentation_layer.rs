use std::fs;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::net::UnixStream;
use serde_json::Value;
use ostk::serve::socket::{run_socket_server, socket_path};

#[tokio::test]
async fn test_broadcast_on_mutation() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().to_path_buf();
    let hs_dir = root.join(".ostk");
    fs::create_dir_all(hs_dir.join("needles")).unwrap();

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

    // 3. Trigger a mutation via post_mutation
    let root_clone = root.clone();
    tokio::spawn(async move {
        // Wait for connection to be registered
        tokio::time::sleep(std::time::Duration::from_millis(200)).await;
        ostk::post_mutation(&root_clone, &ostk::MutationEvent::HayFiled).unwrap();
    });

    // 4. Read broadcast from socket
    let mut line = String::new();
    reader.read_line(&mut line).await.unwrap();
    
    let msg: Value = serde_json::from_str(&line).unwrap();
    assert_eq!(msg["event"], "filesystem.mutated");
    assert!(msg["index"]["hay"]["pending"].as_u64().unwrap() >= 1);
}
