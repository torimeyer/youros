//! Socket transport integration test (→805).
//!
//! Validates the Unix domain socket transport end-to-end:
//! 1. Starts `haystack listen` in the background
//! 2. Connects via UnixStream to .ostk/haystack.sock
//! 3. Sends JSON-RPC initialize + notifications/initialized + tools/call
//! 4. Reads responses and verifies the shell tool returns "hello"
//! 5. Kills the daemon

#[cfg(unix)]
mod socket_tests {
    use std::path::PathBuf;
    use std::time::Duration;

    use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
    use tokio::net::UnixStream;

    /// Find the project root by looking for Cargo.toml.
    fn project_root() -> PathBuf {
        let mut dir = std::env::current_dir().unwrap();
        loop {
            if dir.join("Cargo.toml").exists() {
                return dir;
            }
            if !dir.pop() {
                panic!("Could not find project root (no Cargo.toml found)");
            }
        }
    }

    /// Send a JSON-RPC line and flush.
    async fn send_line(stream: &mut tokio::io::WriteHalf<UnixStream>, line: &str) {
        stream.write_all(line.as_bytes()).await.unwrap();
        stream.write_all(b"\n").await.unwrap();
        stream.flush().await.unwrap();
    }

    /// Read a single JSON-RPC response line.
    async fn read_line(reader: &mut BufReader<tokio::io::ReadHalf<UnixStream>>) -> String {
        let mut line = String::new();
        tokio::time::timeout(Duration::from_secs(15), reader.read_line(&mut line))
            .await
            .expect("timed out reading response")
            .expect("I/O error reading response");
        line
    }

    #[tokio::test]
    async fn socket_transport_shell_echo() {
        let root = project_root();
        let haystack_dir = root.join(".ostk");
        let sock_path = haystack_dir.join("haystack.sock");
        let pid_path = haystack_dir.join("kernel.pid");

        // Build the binary first so we have a fresh ostk
        let ostk = root.join("target/debug/ostk");

        // Spawn `ostk listen` as a background daemon
        let mut daemon = tokio::process::Command::new(&ostk)
            .arg("listen")
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .expect("failed to spawn ostk listen");

        // Wait for the socket to appear (up to 5 seconds)
        let start = std::time::Instant::now();
        while !sock_path.exists() {
            if start.elapsed() > Duration::from_secs(5) {
                // Read stderr for diagnostics
                let _ = daemon.kill().await;
                panic!(
                    "Socket {} did not appear within 5 seconds",
                    sock_path.display()
                );
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }

        // Small extra delay to ensure the listener is accepting
        tokio::time::sleep(Duration::from_millis(200)).await;

        // Connect to the socket
        let stream = UnixStream::connect(&sock_path)
            .await
            .expect("failed to connect to haystack.sock");

        let (read_half, mut write_half) = tokio::io::split(stream);
        let mut reader = BufReader::new(read_half);

        // Step 1: Send initialize
        send_line(
            &mut write_half,
            r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"socket-test","version":"0.1"}}}"#,
        )
        .await;

        let init_resp = read_line(&mut reader).await;
        let init_json: serde_json::Value = serde_json::from_str(init_resp.trim())
            .expect("failed to parse initialize response");
        assert_eq!(init_json["id"], 1);
        assert_eq!(init_json["result"]["serverInfo"]["name"], "haystack");

        // Step 2: Send notifications/initialized (no response expected)
        send_line(
            &mut write_half,
            r#"{"jsonrpc":"2.0","id":null,"method":"notifications/initialized"}"#,
        )
        .await;

        // Small delay for the notification to be processed
        tokio::time::sleep(Duration::from_millis(50)).await;

        // Step 3: Send tools/call for "shell" with command "echo hello"
        send_line(
            &mut write_half,
            r#"{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"shell","arguments":{"cmd":"echo hello"}}}"#,
        )
        .await;

        let call_resp = read_line(&mut reader).await;
        let call_json: serde_json::Value = serde_json::from_str(call_resp.trim())
            .expect("failed to parse tools/call response");

        // Step 4: Verify response contains "hello"
        assert_eq!(call_json["id"], 2);
        assert!(
            call_json["error"].is_null(),
            "tools/call returned error: {}",
            call_json
        );

        let text = call_json["result"]["content"][0]["text"]
            .as_str()
            .expect("missing text in response");
        assert!(
            text.contains("hello"),
            "Response should contain 'hello', got: {text}"
        );
        assert!(
            text.contains("exit:0"),
            "Response should show exit:0, got: {text}"
        );

        // Step 5: Kill the daemon
        daemon.kill().await.expect("failed to kill daemon");

        // Clean up socket and pid files (daemon may have already removed them)
        let _ = std::fs::remove_file(&sock_path);
        let _ = std::fs::remove_file(&pid_path);
    }
}
