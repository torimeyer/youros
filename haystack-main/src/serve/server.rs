//! MCP Server — main event loop for `ostk serve`.
//!
//! Provides the async server that reads JSON-RPC from a transport,
//! dispatches to tool handlers, and writes responses.

use std::fmt;
use std::sync::Arc;

use tokio::io::{AsyncBufRead, AsyncWrite};

use crate::serve::dispatch::McpDispatcher;
use crate::serve::state::ServerState;
use crate::serve::transport::{StdioTransport, TransportError};

// ---------------------------------------------------------------------------
// ServerError
// ---------------------------------------------------------------------------

#[derive(Debug)]
pub enum ServerError {
    Transport(String),
    Io(std::io::Error),
}

impl fmt::Display for ServerError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ServerError::Transport(e) => write!(f, "transport error: {e}"),
            ServerError::Io(e) => write!(f, "I/O error: {e}"),
        }
    }
}

impl std::error::Error for ServerError {}

impl From<std::io::Error> for ServerError {
    fn from(e: std::io::Error) -> Self {
        ServerError::Io(e)
    }
}

// ---------------------------------------------------------------------------
// McpServer
// ---------------------------------------------------------------------------

pub struct McpServer {
    _state: Arc<ServerState>,
    dispatcher: Arc<McpDispatcher>,
}

impl Default for McpServer {
    fn default() -> Self {
        Self::new()
    }
}

impl McpServer {
    pub fn new() -> Self {
        let state = Arc::new(ServerState::new());
        let dispatcher = Arc::new(McpDispatcher::new(state.clone()));
        Self {
            _state: state,
            dispatcher,
        }
    }

    /// Create a server with a custom state.
    /// Used by tests for isolated kernel dirs and by the socket server for
    /// shared state across connections.
    pub fn with_state(state: Arc<ServerState>) -> Self {
        let dispatcher = Arc::new(McpDispatcher::new(state.clone()));
        Self {
            _state: state,
            dispatcher,
        }
    }

    /// Alias for `with_state` — accepts an already-Arc-wrapped state.
    /// Used by the Unix socket server to share state across connections.
    pub fn with_state_arc(state: Arc<ServerState>) -> Self {
        Self::with_state(state)
    }

    /// Run the server event loop on the given transport.
    pub async fn run<R, W>(&self, transport: &mut StdioTransport<R, W>) -> Result<(), ServerError>
    where
        R: AsyncBufRead + Unpin,
        W: AsyncWrite + Unpin,
    {
        loop {
            match transport.read_request().await {
                Ok(Some(request)) => {
                    if let Some(response) = self.dispatcher.dispatch(request).await {
                        transport
                            .write_response(response)
                            .await
                            .map_err(|e| ServerError::Transport(e.to_string()))?;
                    }
                }
                Ok(None) => break,
                Err(TransportError::Eof) => break,
                Err(e) => return Err(ServerError::Transport(e.to_string())),
            }
        }
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// run_server — entry point for `ostk serve`
// ---------------------------------------------------------------------------

/// Entry point for `ostk serve`.
///
/// Full lifecycle:
/// 1. Create server
/// 2. Start background reaper for dead PTY processes
/// 3. Run transport loop on stdin/stdout
/// 4. Clean exit on EOF
pub async fn run_server() -> Result<(), Box<dyn std::error::Error>> {
    let state = Arc::new(ServerState::new());
    let dispatcher = Arc::new(McpDispatcher::new(state.clone()));
    let transport = StdioTransport::new();
    let (mut reader, writer) = transport.into_split();

    // Start background reaper — checks for dead processes every 5 seconds.
    // This prevents PTY fd exhaustion in long-running sessions.
    let reaper_state = state.clone();
    let reaper_handle = tokio::spawn(async move {
        let mut interval = tokio::time::interval(std::time::Duration::from_secs(5));
        // Purge terminal entries older than 30 minutes
        let purge_age = std::time::Duration::from_secs(30 * 60);
        loop {
            interval.tick().await;
            let reaped = reaper_state.reap_dead_processes().await;
            let purged = reaper_state.purge_stale_entries(purge_age).await;
            if reaped > 0 || purged > 0 {
                eprintln!(
                    "[ostk] reaper: reaped={reaped} purged={purged}"
                );
            }
        }
    });

    loop {
        match reader.read_request().await {
            Ok(Some(request)) => {
                let d = dispatcher.clone();
                let w = writer.clone();
                if let Some(response) = d.dispatch(request).await
                    && let Err(e) = w.write_response(response).await
                {
                    eprintln!("[ostk] write error: {e}");
                    break;
                }
            }
            Ok(None) => break,
            Err(TransportError::Eof) => break,
            Err(e) => {
                eprintln!("[ostk] transport error: {e}");
                reaper_handle.abort();
                return Err(e.to_string().into());
            }
        }
    }

    // Clean shutdown — stop the reaper
    reaper_handle.abort();
    Ok(())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn make_transport(
        input: &str,
    ) -> StdioTransport<tokio::io::BufReader<std::io::Cursor<Vec<u8>>>, Vec<u8>> {
        let reader = tokio::io::BufReader::new(std::io::Cursor::new(input.as_bytes().to_vec()));
        let writer = Vec::new();
        StdioTransport::with_io(reader, writer)
    }

    fn get_output<R>(transport: StdioTransport<R, Vec<u8>>) -> String {
        let (_reader, writer) = transport.into_parts();
        String::from_utf8(writer).unwrap()
    }

    // Test 1: Server processes initialize request

    #[tokio::test]
    async fn test_server_initialize() {
        let server = McpServer::new();
        let input = concat!(
            r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test"}}}"#,
            "\n",
        );
        let mut transport = make_transport(input);
        server.run(&mut transport).await.unwrap();

        let output = get_output(transport);
        let parsed: serde_json::Value = serde_json::from_str(output.trim()).unwrap();
        assert_eq!(parsed["jsonrpc"], "2.0");
        assert_eq!(parsed["id"], 1);
        assert_eq!(parsed["result"]["serverInfo"]["name"], "ostk");
        assert_eq!(parsed["result"]["protocolVersion"], "2024-11-05");
    }

    // Test 2: Server exits cleanly on empty input (EOF)

    #[tokio::test]
    async fn test_server_eof() {
        let server = McpServer::new();
        let mut transport = make_transport("");
        let result = server.run(&mut transport).await;
        assert!(result.is_ok(), "Server should exit cleanly on EOF");
    }

    // Test 3: Server processes tools/list and returns 6 tools

    #[tokio::test]
    async fn test_server_tools_list() {
        let server = McpServer::new();
        let input = concat!(r#"{"jsonrpc":"2.0","id":2,"method":"tools/list"}"#, "\n",);
        let mut transport = make_transport(input);
        server.run(&mut transport).await.unwrap();

        let output = get_output(transport);
        let parsed: serde_json::Value = serde_json::from_str(output.trim()).unwrap();
        let tools = parsed["result"]["tools"].as_array().unwrap();
        // →1157: tools now generated from .language (dynamic count)
        assert!(tools.len() >= 7, "should have at least core tools, got {}", tools.len());

        let names: Vec<&str> = tools.iter().map(|t| t["name"].as_str().unwrap()).collect();
        for expected in ["shell", "spawn", "interact", "session", "lock", "help"] {
            assert!(names.contains(&expected), "missing core tool: {expected}");
        }
        assert!(names.contains(&"tack"));
        assert!(names.contains(&"web_read"));
        assert!(names.contains(&"web_links"));
        assert!(names.contains(&"web_status"));
        assert!(names.contains(&"ostk_pitchfork"));
        assert!(names.contains(&"ostk_context_search"));
        assert!(names.contains(&"ostk_context_release"));
    }

    // Test 4: Notifications produce no output

    #[tokio::test]
    async fn test_server_notification_no_output() {
        let server = McpServer::new();
        let input = concat!(
            r#"{"jsonrpc":"2.0","id":null,"method":"notifications/initialized"}"#,
            "\n",
        );
        let mut transport = make_transport(input);
        server.run(&mut transport).await.unwrap();

        let output = get_output(transport);
        assert!(
            output.trim().is_empty(),
            "Notification should produce no output, got: {output}"
        );
    }

    // Test 5: Multiple requests processed sequentially

    #[tokio::test]
    async fn test_server_multiple_requests() {
        let server = McpServer::new();
        let input = concat!(
            r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test"}}}"#,
            "\n",
            r#"{"jsonrpc":"2.0","id":2,"method":"tools/list"}"#,
            "\n",
        );
        let mut transport = make_transport(input);
        server.run(&mut transport).await.unwrap();

        let output = get_output(transport);
        let lines: Vec<&str> = output.trim().lines().collect();
        assert_eq!(lines.len(), 2, "Should have 2 responses");

        let resp1: serde_json::Value = serde_json::from_str(lines[0]).unwrap();
        assert_eq!(resp1["id"], 1);
        assert_eq!(resp1["result"]["serverInfo"]["name"], "ostk");

        let resp2: serde_json::Value = serde_json::from_str(lines[1]).unwrap();
        assert_eq!(resp2["id"], 2);
        assert!(resp2["result"]["tools"].is_array());
    }

    // Test 6: Full MCP lifecycle — init + notification + tools/list + help

    #[tokio::test]
    async fn test_server_full_mcp_lifecycle() {
        let server = McpServer::new();
        let input = concat!(
            r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test"}}}"#,
            "\n",
            r#"{"jsonrpc":"2.0","id":null,"method":"notifications/initialized"}"#,
            "\n",
            r#"{"jsonrpc":"2.0","id":2,"method":"tools/list"}"#,
            "\n",
            r#"{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"help","arguments":{}}}"#,
            "\n",
        );
        let mut transport = make_transport(input);
        server.run(&mut transport).await.unwrap();

        let output = get_output(transport);
        let lines: Vec<&str> = output.trim().lines().collect();
        // 3 responses: initialize, tools/list, tools/call (notification has none)
        assert_eq!(lines.len(), 3, "Expected 3 responses, got: {lines:?}");

        let resp1: serde_json::Value = serde_json::from_str(lines[0]).unwrap();
        assert_eq!(resp1["id"], 1);
        assert_eq!(resp1["result"]["serverInfo"]["name"], "ostk");

        let resp2: serde_json::Value = serde_json::from_str(lines[1]).unwrap();
        assert_eq!(resp2["id"], 2);
        assert!(resp2["result"]["tools"].as_array().unwrap().len() >= 7);

        let resp3: serde_json::Value = serde_json::from_str(lines[2]).unwrap();
        assert_eq!(resp3["id"], 3);
        let text = resp3["result"]["content"][0]["text"].as_str().unwrap();
        assert!(
            text.contains("ostk reference card"),
            "help should return reference card: {text}"
        );
    }

    // Test 7: tools/call before initialization returns error

    #[tokio::test]
    async fn test_server_tools_call_before_init() {
        let server = McpServer::new();
        let input = concat!(
            r#"{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"help","arguments":{}}}"#,
            "\n",
        );
        let mut transport = make_transport(input);
        server.run(&mut transport).await.unwrap();

        let output = get_output(transport);
        let parsed: serde_json::Value = serde_json::from_str(output.trim()).unwrap();
        assert!(
            parsed["error"].is_object(),
            "Should return error before init"
        );
        assert!(
            parsed["error"]["message"]
                .as_str()
                .unwrap()
                .contains("not initialized")
        );
    }

    // Test 8: Unknown tool returns error

    #[tokio::test]
    async fn test_server_unknown_tool_error() {
        let server = McpServer::new();
        let input = concat!(
            r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test"}}}"#,
            "\n",
            r#"{"jsonrpc":"2.0","id":null,"method":"notifications/initialized"}"#,
            "\n",
            r#"{"jsonrpc":"2.0","id":20,"method":"tools/call","params":{"name":"bogus","arguments":{}}}"#,
            "\n",
        );
        let mut transport = make_transport(input);
        server.run(&mut transport).await.unwrap();

        let output = get_output(transport);
        let lines: Vec<&str> = output.trim().lines().collect();
        assert_eq!(lines.len(), 2, "Expected 2 responses, got: {lines:?}");

        let parsed: serde_json::Value = serde_json::from_str(lines[1]).unwrap();
        assert!(parsed["error"].is_object());
        assert!(
            parsed["error"]["message"]
                .as_str()
                .unwrap()
                .contains("Unknown tool")
        );
    }

    // Test 9: Full-stack shell through server

    #[tokio::test]
    async fn test_server_sh_run() {
        let server = McpServer::new();
        let input = concat!(
            r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test"}}}"#,
            "\n",
            r#"{"jsonrpc":"2.0","id":null,"method":"notifications/initialized"}"#,
            "\n",
            r#"{"jsonrpc":"2.0","id":100,"method":"tools/call","params":{"name":"shell","arguments":{"cmd":"echo ostk_serve_test"}}}"#,
            "\n",
        );
        let mut transport = make_transport(input);
        server.run(&mut transport).await.unwrap();

        let output = get_output(transport);
        let lines: Vec<&str> = output.trim().lines().collect();
        assert_eq!(lines.len(), 2, "Expected 2 responses, got: {lines:?}");

        let parsed: serde_json::Value = serde_json::from_str(lines[1]).unwrap();
        assert!(parsed["error"].is_null(), "Expected success, got: {parsed}");

        let text = parsed["result"]["content"][0]["text"].as_str().unwrap();
        assert!(text.contains("exit:0"), "should show exit:0: {text}");
        assert!(
            text.contains("ostk_serve_test"),
            "should contain output: {text}"
        );
    }

    // ── Kernel integration tests ──

    use crate::kernel::identity::Identity;

    fn temp_ostk_dir(name: &str) -> std::path::PathBuf {
        // Ensure trust tier is T0 for tests — without this, CI environments
        // without GPG resolve to T3 (deny-all-writes), failing every write test.
        // Must be set before the OnceLock in kernel::policy is initialized.
        unsafe { std::env::set_var("OSTK_TRUST_TIER", "T0") };
        let root = std::env::temp_dir()
            .join("ostk_test_serve_kernel")
            .join(name);
        let _ = std::fs::remove_dir_all(&root);
        let ostk = root.join(".ostk");
        std::fs::create_dir_all(&ostk).unwrap();
        // When OSTK_PIN is set (running tests inside an ostk session),
        // create a permissive pin.caps so find_ostk_dir + load_active_pin_caps
        // resolves correctly for files under this root.
        if let Ok(pin_name) = std::env::var("OSTK_PIN") {
            if !pin_name.is_empty() {
                let pin_dir = ostk.join("pins").join(&pin_name);
                std::fs::create_dir_all(&pin_dir).unwrap();
                std::fs::write(
                    pin_dir.join("pin.caps"),
                    "read: *\nwrite: *\nexecute: *\ndeny:\n",
                ).unwrap();
            }
        }
        ostk
    }

    fn make_server_with_dir(ostk_dir: std::path::PathBuf) -> McpServer {
        let state = Arc::new(ServerState::with_ostk_dir(ostk_dir));
        McpServer::with_state(state)
    }

    // Test 10: Initialize assigns kernel identity
    #[tokio::test]
    async fn test_kernel_initialize_assigns_identity() {
        let hs_dir = temp_ostk_dir("init_identity");
        let server = make_server_with_dir(hs_dir.clone());
        let input = concat!(
            r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test"}}}"#,
            "\n",
        );
        let mut transport = make_transport(input);
        server.run(&mut transport).await.unwrap();

        let output = get_output(transport);
        let parsed: serde_json::Value = serde_json::from_str(output.trim()).unwrap();

        // Initialize should succeed
        assert_eq!(parsed["result"]["serverInfo"]["name"], "ostk");

        // Identity should be registered in agents.jsonl
        let identity = Identity::new(&hs_dir);
        let agents = identity.read_agents().unwrap();
        assert!(
            !agents.is_empty(),
            "Should have at least one registered agent"
        );
        assert_eq!(agents[0].alias, "agent-1");
        assert_eq!(agents[0].status, "active");

        let _ = std::fs::remove_dir_all(&hs_dir);
    }

    // Test 11: Tool call records heartbeat
    #[tokio::test]
    async fn test_kernel_tool_call_records_heartbeat() {
        let hs_dir = temp_ostk_dir("heartbeat");
        let server = make_server_with_dir(hs_dir.clone());
        let input = concat!(
            r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test"}}}"#,
            "\n",
            r#"{"jsonrpc":"2.0","id":null,"method":"notifications/initialized"}"#,
            "\n",
            r#"{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"help","arguments":{}}}"#,
            "\n",
        );
        let mut transport = make_transport(input);
        server.run(&mut transport).await.unwrap();

        let output = get_output(transport);
        let lines: Vec<&str> = output.trim().lines().collect();
        assert_eq!(lines.len(), 2, "Expected 2 responses, got: {lines:?}");

        // After the tool call, the agent should still be active in heartbeat
        let health = crate::kernel::heartbeat::check_health(&hs_dir).unwrap();
        assert!(!health.is_empty(), "Agent should appear in health check");
        assert_eq!(health[0].alias, "agent-1");
        assert_eq!(
            health[0].status,
            crate::kernel::heartbeat::HealthStatus::Active
        );

        let _ = std::fs::remove_dir_all(&hs_dir);
    }

    // Test 12: Tool response includes digest
    #[tokio::test]
    async fn test_kernel_tool_response_includes_digest() {
        let hs_dir = temp_ostk_dir("digest");
        let server = make_server_with_dir(hs_dir.clone());
        let input = concat!(
            r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test"}}}"#,
            "\n",
            r#"{"jsonrpc":"2.0","id":null,"method":"notifications/initialized"}"#,
            "\n",
            r#"{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"help","arguments":{}}}"#,
            "\n",
        );
        let mut transport = make_transport(input);
        server.run(&mut transport).await.unwrap();

        let output = get_output(transport);
        let lines: Vec<&str> = output.trim().lines().collect();
        assert_eq!(lines.len(), 2, "Expected 2 responses, got: {lines:?}");

        let parsed: serde_json::Value = serde_json::from_str(lines[1]).unwrap();
        let text = parsed["result"]["content"][0]["text"].as_str().unwrap();

        // Tool response should contain [procs] digest line
        assert!(
            text.contains("[procs]"),
            "Tool response should include [procs] digest: {text}"
        );
        assert!(
            text.contains("a1:"), // HSCP G2: agent-1 → a1:
            "Digest should mention the agent (HSCP G2 format): {text}"
        );

        let _ = std::fs::remove_dir_all(&hs_dir);
    }

    // Test 13: file:edit through Hot PR succeeds
    #[tokio::test]
    async fn test_kernel_ss_edit_hotpr() {
        let hs_dir = temp_ostk_dir("ss_edit");
        // Create a test file under the project root (parent of .ostk/)
        // so find_ostk_dir walks up and finds this test's .ostk/ dir.
        let project_root = hs_dir.parent().unwrap();
        let test_file = project_root.join("ss_edit_file.txt");
        std::fs::write(&test_file, "hello world\nfoo bar\nbaz\n").unwrap();

        let server = make_server_with_dir(hs_dir.clone());
        let edit_cmd = format!(
            r#"{{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{{"name":"file:edit","arguments":{{"path":"{}","old_str":"foo bar","new_str":"FOO BAR"}}}}}}"#,
            test_file.display()
        );
        let input = format!(
            "{}\n{}\n{}\n",
            r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test"}}}"#,
            r#"{"jsonrpc":"2.0","id":null,"method":"notifications/initialized"}"#,
            edit_cmd,
        );
        let mut transport = make_transport(&input);
        server.run(&mut transport).await.unwrap();

        let output = get_output(transport);
        let lines: Vec<&str> = output.trim().lines().collect();
        assert_eq!(lines.len(), 2, "Expected 2 responses, got: {lines:?}");

        let parsed: serde_json::Value = serde_json::from_str(lines[1]).unwrap();
        assert!(
            parsed["error"].is_null(),
            "file:edit should succeed, got: {parsed}"
        );

        let text = parsed["result"]["content"][0]["text"].as_str().unwrap();
        assert!(text.contains("+ file:edit"), "should show success: {text}");
        assert!(text.contains("gen="), "should show generation: {text}");

        // Verify file was actually edited
        let content = std::fs::read_to_string(&test_file).unwrap();
        assert_eq!(
            content, "hello world\nFOO BAR\nbaz\n",
            "File should be edited"
        );

        let _ = std::fs::remove_file(&test_file);
        let _ = std::fs::remove_dir_all(&hs_dir);
    }

    // Test 14: file:read returns 304 on second read
    #[tokio::test]
    async fn test_kernel_fs_read_304() {
        let hs_dir = temp_ostk_dir("fs_read_304");
        // Create a test file to read
        let test_file = std::env::temp_dir()
            .join("ostk_test_serve_kernel")
            .join("fs_read_file.txt");
        std::fs::write(&test_file, "test content\n").unwrap();

        let server = make_server_with_dir(hs_dir.clone());
        let read_cmd = format!(
            r#"{{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{{"name":"file:read","arguments":{{"action":"read {}"}}}}}}"#,
            test_file.display()
        );
        let read_cmd2 = format!(
            r#"{{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{{"name":"file:read","arguments":{{"action":"read {}"}}}}}}"#,
            test_file.display()
        );
        let input = format!(
            "{}\n{}\n{}\n{}\n",
            r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test"}}}"#,
            r#"{"jsonrpc":"2.0","id":null,"method":"notifications/initialized"}"#,
            read_cmd,
            read_cmd2,
        );
        let mut transport = make_transport(&input);
        server.run(&mut transport).await.unwrap();

        let output = get_output(transport);
        let lines: Vec<&str> = output.trim().lines().collect();
        assert_eq!(lines.len(), 3, "Expected 3 responses, got: {lines:?}");

        // First read: should return full content [200]
        let resp1: serde_json::Value = serde_json::from_str(lines[1]).unwrap();
        assert!(
            resp1["error"].is_null(),
            "First read should succeed: {resp1}"
        );
        let text1 = resp1["result"]["content"][0]["text"].as_str().unwrap();
        assert!(
            text1.contains("[200]"),
            "First read should return [200]: {text1}"
        );
        assert!(
            text1.contains("test content"),
            "First read should include file content: {text1}"
        );

        // Second read: should return 304
        let resp2: serde_json::Value = serde_json::from_str(lines[2]).unwrap();
        assert!(
            resp2["error"].is_null(),
            "Second read should succeed: {resp2}"
        );
        let text2 = resp2["result"]["content"][0]["text"].as_str().unwrap();
        assert!(
            text2.contains("[304]"),
            "Second read should return [304]: {text2}"
        );
        assert!(
            !text2.contains("test content"),
            "304 should NOT include file content: {text2}"
        );

        let _ = std::fs::remove_file(&test_file);
        let _ = std::fs::remove_dir_all(&hs_dir);
    }
}
