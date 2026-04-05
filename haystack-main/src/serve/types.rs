//! MCP protocol types for ostk serve.
//!
//! MCP protocol types (originated in mish, ostk's experimental predecessor). Trimmed to the subset we need.
//! Tool INPUT schemas stay identical (agents use the same JSON params).

use serde::{Deserialize, Serialize};

// ----- JSON-RPC 2.0 base types -----

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct JsonRpcRequest {
    pub jsonrpc: String,
    #[serde(default)]
    pub id: serde_json::Value,
    pub method: String,
    pub params: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Serialize)]
pub struct JsonRpcResponse {
    pub jsonrpc: String,
    pub id: serde_json::Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<JsonRpcError>,
}

#[derive(Debug, Clone, Serialize)]
pub struct JsonRpcError {
    pub code: i32,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<serde_json::Value>,
}

// ----- MCP Protocol Messages -----

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InitializeParams {
    pub protocol_version: String,
    pub capabilities: ClientCapabilities,
    pub client_info: ClientInfo,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ClientCapabilities {}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClientInfo {
    pub name: String,
    pub version: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InitializeResult {
    pub protocol_version: String,
    pub capabilities: ServerCapabilities,
    pub server_info: ServerInfo,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub instructions: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ServerCapabilities {
    pub tools: ToolsCapability,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ToolsCapability {
    pub list_changed: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct ServerInfo {
    pub name: String,
    pub version: String,
}

// ----- Tool Schemas -----

#[derive(Debug, Clone, Serialize)]
pub struct ToolDefinition {
    pub name: String,
    pub description: String,
    #[serde(rename = "inputSchema")]
    pub input_schema: serde_json::Value,
}

// ----- Tool Parameter Types -----

#[derive(Debug, Clone, Deserialize)]
pub struct ShRunParams {
    pub cmd: String,
    pub timeout: Option<u64>,
    #[serde(default)]
    pub raw: Option<bool>,
    /// →879: Optional working directory override. Defaults to project root.
    pub cwd: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ShSpawnParams {
    pub alias: String,
    pub cmd: String,
    pub wait_for: Option<String>,
    pub timeout: Option<u64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InteractAction {
    ReadTail,
    SendInput,
    SendSignal,
    Kill,
    Status,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ShInteractParams {
    pub alias: String,
    pub action: InteractAction,
    pub input: Option<String>,
    pub lines: Option<usize>,
    pub timeout: Option<u64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionAction {
    Create,
    List,
    Close,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ShSessionParams {
    pub action: SessionAction,
    pub name: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ShLockParams {
    pub action: String,
    pub name: String,
    #[serde(default)]
    pub timeout: Option<u64>,
}

// ----- Kernel Tool Parameter Types -----

#[derive(Debug, Clone, Deserialize)]
pub struct FsOpsParams {
    /// Quick mode: target file path
    pub path: Option<String>,
    /// Quick edit: text to find. Omit for file creation.
    pub old_str: Option<String>,
    /// Quick edit: replacement text. Create mode: full file content.
    pub new_str: Option<String>,
    /// Quick mode: replace every occurrence (default: false, errors if >1 match)
    pub replace_all: Option<bool>,
    /// Batch mode: operations array of JSON objects.
    /// Each op: {"method": "file.str_replace"|"file.read"|"file.write", "path": "...", ...}
    pub ops: Option<Vec<serde_json::Value>>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct FsReadParams {
    pub action: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct TackParams {
    pub input: String,
}

// ----- Tool Response Types -----

#[derive(Debug, Clone, Serialize)]
pub struct ShRunResponse {
    pub exit_code: i32,
    pub duration_ms: u64,
    pub output: String,
    pub lines: LineCount,
}

#[derive(Debug, Clone, Serialize)]
pub struct LineCount {
    pub total: u64,
    pub shown: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct ShSpawnResponse {
    pub alias: String,
    pub pid: u32,
    pub state: String,
    pub wait_matched: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub match_line: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output_tail: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ShInteractResponse {
    pub alias: String,
    pub action: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub lines_returned: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bytes_written: Option<usize>,
    pub state: String,
}

pub type ShSessionInfo = SessionInfo;
#[derive(Debug, Clone, Serialize)]
pub struct SessionInfo {
    pub session: String,
    pub cwd: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct ShSessionListResponse {
    pub sessions: Vec<SessionInfo>,
}

// ----- Session Wire Protocol (→840) -----

/// Request to dispatch user input to a named agent session.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionDispatchParams {
    pub session_name: String,
    pub input: String,
}

/// Request to drain pending events from a named agent session.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionEventsParams {
    pub session_name: String,
}

/// Request to switch the active session (creates on demand).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionSwitchParams {
    pub session_name: String,
}

/// Request to clear a session's messages.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionClearParams {
    pub session_name: String,
}

/// Request to reboot a session (fresh boot context).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionRebootParams {
    pub session_name: String,
}

/// Info about an agent session (distinct from ShSessionInfo which is PTY sessions).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentSessionInfo {
    pub name: String,
    pub message_count: usize,
    pub is_busy: bool,
}

/// Response listing all agent sessions.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentSessionListResponse {
    pub sessions: Vec<AgentSessionInfo>,
}

// ----- Process Table Digest (simplified) -----

#[derive(Debug, Serialize, Clone)]
pub struct ProcessDigestEntry {
    pub alias: String,
    pub state: String,
    pub pid: u32,
    pub elapsed_ms: u64,
}

// ----- MCP Tool Response Wrapper -----

#[derive(Debug, Clone, Serialize)]
pub struct ToolResponse {
    pub result: serde_json::Value,
    pub processes: Vec<ProcessDigestEntry>,
}

// ----- Tool Error -----

#[derive(Debug, Clone)]
pub struct ToolError {
    pub code: i32,
    pub message: String,
}

impl ToolError {
    pub fn new(code: i32, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }

    pub fn invalid_params(message: impl Into<String>) -> Self {
        Self::new(ERR_INVALID_PARAMS, message)
    }

    pub fn internal(message: impl Into<String>) -> Self {
        Self::new(ERR_INTERNAL, message)
    }
}

impl std::fmt::Display for ToolError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "[{}] {}", self.code, self.message)
    }
}

impl std::error::Error for ToolError {}

// ----- Application Error Codes -----

pub const ERR_SHELL_ERROR: i32 = -32000;
pub const ERR_ALIAS_NOT_FOUND: i32 = -32003;
pub const ERR_ALIAS_IN_USE: i32 = -32004;

pub const ERR_PARSE_ERROR: i32 = -32700;
pub const ERR_INVALID_REQUEST: i32 = -32600;
pub const ERR_METHOD_NOT_FOUND: i32 = -32601;
pub const ERR_INVALID_PARAMS: i32 = -32602;
pub const ERR_INTERNAL: i32 = -32603;

#[cfg(test)]
mod tests {
    use super::*;

    // ── ToolError ──

    #[test]
    fn tool_error_new() {
        let err = ToolError::new(ERR_SHELL_ERROR, "command failed");
        assert_eq!(err.code, ERR_SHELL_ERROR);
        assert_eq!(err.message, "command failed");
    }

    #[test]
    fn tool_error_invalid_params() {
        let err = ToolError::invalid_params("bad input");
        assert_eq!(err.code, ERR_INVALID_PARAMS);
        assert_eq!(err.message, "bad input");
    }

    #[test]
    fn tool_error_internal() {
        let err = ToolError::internal("panic");
        assert_eq!(err.code, ERR_INTERNAL);
        assert_eq!(err.message, "panic");
    }

    #[test]
    fn tool_error_display() {
        let err = ToolError::new(-32000, "test error");
        let display = format!("{}", err);
        assert!(display.contains("-32000"));
        assert!(display.contains("test error"));
    }

    #[test]
    fn tool_error_is_error() {
        let err = ToolError::new(ERR_SHELL_ERROR, "x");
        // Verify it implements std::error::Error
        let _: &dyn std::error::Error = &err;
    }

    // ── JsonRpcRequest deserialization ──

    #[test]
    fn jsonrpc_request_deserialize() {
        let json = r#"{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"name":"sh_run"}}"#;
        let req: JsonRpcRequest = serde_json::from_str(json).unwrap();
        assert_eq!(req.jsonrpc, "2.0");
        assert_eq!(req.id, serde_json::json!(1));
        assert_eq!(req.method, "tools/list");
        assert!(req.params.is_some());
    }

    #[test]
    fn jsonrpc_request_no_params() {
        let json = r#"{"jsonrpc":"2.0","id":2,"method":"ping"}"#;
        let req: JsonRpcRequest = serde_json::from_str(json).unwrap();
        assert!(req.params.is_none());
    }

    #[test]
    fn jsonrpc_request_null_id() {
        let json = r#"{"jsonrpc":"2.0","method":"notifications/initialized"}"#;
        let req: JsonRpcRequest = serde_json::from_str(json).unwrap();
        assert_eq!(req.id, serde_json::Value::Null);
    }

    // ── JsonRpcResponse serialization ──

    #[test]
    fn jsonrpc_response_serialize_result() {
        let resp = JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            id: serde_json::json!(1),
            result: Some(serde_json::json!({"ok": true})),
            error: None,
        };
        let json = serde_json::to_string(&resp).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed["jsonrpc"], "2.0");
        assert_eq!(parsed["result"]["ok"], true);
        // error should be skipped when None
        assert!(!json.contains("error"));
    }

    #[test]
    fn jsonrpc_response_serialize_error() {
        let resp = JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            id: serde_json::json!(2),
            result: None,
            error: Some(JsonRpcError {
                code: ERR_METHOD_NOT_FOUND,
                message: "not found".to_string(),
                data: None,
            }),
        };
        let json = serde_json::to_string(&resp).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed["error"]["code"], ERR_METHOD_NOT_FOUND);
        assert_eq!(parsed["error"]["message"], "not found");
        // result should be skipped when None
        assert!(!json.contains("result"));
    }

    // ── Tool param types deserialization ──

    #[test]
    fn sh_run_params_deserialize() {
        let json = r#"{"cmd":"ls -la","timeout":30,"raw":true}"#;
        let params: ShRunParams = serde_json::from_str(json).unwrap();
        assert_eq!(params.cmd, "ls -la");
        assert_eq!(params.timeout, Some(30));
        assert_eq!(params.raw, Some(true));
    }

    #[test]
    fn sh_run_params_minimal() {
        let json = r#"{"cmd":"echo hi"}"#;
        let params: ShRunParams = serde_json::from_str(json).unwrap();
        assert_eq!(params.cmd, "echo hi");
        assert!(params.timeout.is_none());
        assert!(params.raw.is_none());
    }

    #[test]
    fn sh_spawn_params_deserialize() {
        let json = r#"{"alias":"server","cmd":"npm start","wait_for":"ready","timeout":60}"#;
        let params: ShSpawnParams = serde_json::from_str(json).unwrap();
        assert_eq!(params.alias, "server");
        assert_eq!(params.cmd, "npm start");
        assert_eq!(params.wait_for.unwrap(), "ready");
        assert_eq!(params.timeout.unwrap(), 60);
    }

    #[test]
    fn sh_interact_params_deserialize() {
        let json = r#"{"alias":"srv","action":"read_tail","lines":20}"#;
        let params: ShInteractParams = serde_json::from_str(json).unwrap();
        assert_eq!(params.alias, "srv");
        assert_eq!(params.action, InteractAction::ReadTail);
        assert_eq!(params.lines, Some(20));
    }

    #[test]
    fn interact_action_variants() {
        let cases = vec![
            (r#""read_tail""#, InteractAction::ReadTail),
            (r#""send_input""#, InteractAction::SendInput),
            (r#""send_signal""#, InteractAction::SendSignal),
            (r#""kill""#, InteractAction::Kill),
            (r#""status""#, InteractAction::Status),
        ];
        for (json, expected) in cases {
            let action: InteractAction = serde_json::from_str(json).unwrap();
            assert_eq!(action, expected);
        }
    }

    #[test]
    fn session_action_variants() {
        let cases = vec![
            (r#""create""#, SessionAction::Create),
            (r#""list""#, SessionAction::List),
            (r#""close""#, SessionAction::Close),
        ];
        for (json, expected) in cases {
            let action: SessionAction = serde_json::from_str(json).unwrap();
            assert_eq!(action, expected);
        }
    }

    #[test]
    fn sh_lock_params_deserialize() {
        let json = r#"{"action":"create","name":"build","timeout":300}"#;
        let params: ShLockParams = serde_json::from_str(json).unwrap();
        assert_eq!(params.action, "create");
        assert_eq!(params.name, "build");
        assert_eq!(params.timeout, Some(300));
    }

    #[test]
    fn fs_ops_params_quick_mode() {
        let json = r#"{"path":"src/main.rs","old_str":"foo","new_str":"bar"}"#;
        let params: FsOpsParams = serde_json::from_str(json).unwrap();
        assert_eq!(params.path.unwrap(), "src/main.rs");
        assert_eq!(params.old_str.unwrap(), "foo");
        assert_eq!(params.new_str.unwrap(), "bar");
        assert!(params.ops.is_none());
    }

    #[test]
    fn fs_ops_params_batch_mode() {
        let json = r#"{"ops":[{"method":"file.read","path":"a.rs"}]}"#;
        let params: FsOpsParams = serde_json::from_str(json).unwrap();
        assert!(params.path.is_none());
        assert_eq!(params.ops.unwrap().len(), 1);
    }

    #[test]
    fn fs_read_params_deserialize() {
        let json = r#"{"action":"read src/main.rs"}"#;
        let params: FsReadParams = serde_json::from_str(json).unwrap();
        assert_eq!(params.action, "read src/main.rs");
    }

    // ── Response types serialization ──

    #[test]
    fn sh_run_response_serialize() {
        let resp = ShRunResponse {
            exit_code: 0,
            duration_ms: 150,
            output: "hello world".to_string(),
            lines: LineCount {
                total: 1,
                shown: 1,
            },
        };
        let json = serde_json::to_value(&resp).unwrap();
        assert_eq!(json["exit_code"], 0);
        assert_eq!(json["duration_ms"], 150);
        assert_eq!(json["output"], "hello world");
        assert_eq!(json["lines"]["total"], 1);
    }

    #[test]
    fn sh_spawn_response_serialize() {
        let resp = ShSpawnResponse {
            alias: "server".to_string(),
            pid: 1234,
            state: "running".to_string(),
            wait_matched: true,
            match_line: Some("Server ready".to_string()),
            output_tail: None,
            reason: None,
        };
        let json = serde_json::to_value(&resp).unwrap();
        assert_eq!(json["alias"], "server");
        assert_eq!(json["pid"], 1234);
        assert_eq!(json["wait_matched"], true);
        assert_eq!(json["match_line"], "Server ready");
        // output_tail and reason should be absent (skip_serializing_if)
        assert!(json.get("output_tail").is_none());
        assert!(json.get("reason").is_none());
    }

    #[test]
    fn sh_interact_response_serialize() {
        let resp = ShInteractResponse {
            alias: "bg".to_string(),
            action: "read_tail".to_string(),
            output: Some("line1\nline2".to_string()),
            lines_returned: Some(2),
            bytes_written: None,
            state: "running".to_string(),
        };
        let json = serde_json::to_value(&resp).unwrap();
        assert_eq!(json["alias"], "bg");
        assert_eq!(json["lines_returned"], 2);
        assert!(json.get("bytes_written").is_none());
    }

    #[test]
    fn session_info_serialize() {
        let resp = ShSessionListResponse {
            sessions: vec![SessionInfo {
                session: "main".to_string(),
                cwd: "/home/user".to_string(),
            }],
        };
        let json = serde_json::to_value(&resp).unwrap();
        assert_eq!(json["sessions"][0]["session"], "main");
    }

    #[test]
    fn process_digest_entry_serialize() {
        let entry = ProcessDigestEntry {
            alias: "build".to_string(),
            state: "running".to_string(),
            pid: 999,
            elapsed_ms: 5000,
        };
        let json = serde_json::to_value(&entry).unwrap();
        assert_eq!(json["alias"], "build");
        assert_eq!(json["elapsed_ms"], 5000);
    }

    // ── Error code constants ──

    #[test]
    #[allow(clippy::assertions_on_constants)]
    fn error_codes_are_negative() {
        assert!(ERR_SHELL_ERROR < 0);
        assert!(ERR_ALIAS_NOT_FOUND < 0);
        assert!(ERR_ALIAS_IN_USE < 0);
        assert!(ERR_PARSE_ERROR < 0);
        assert!(ERR_INVALID_REQUEST < 0);
        assert!(ERR_METHOD_NOT_FOUND < 0);
        assert!(ERR_INVALID_PARAMS < 0);
        assert!(ERR_INTERNAL < 0);
    }

    // ── InitializeResult serialization ──

    #[test]
    fn initialize_result_serialize() {
        let result = InitializeResult {
            protocol_version: "2024-11-05".to_string(),
            capabilities: ServerCapabilities {
                tools: ToolsCapability {
                    list_changed: false,
                },
            },
            server_info: ServerInfo {
                name: "ostk".to_string(),
                version: "0.1.0".to_string(),
            },
            instructions: Some("test instructions".to_string()),
        };
        let json = serde_json::to_value(&result).unwrap();
        assert_eq!(json["protocolVersion"], "2024-11-05");
        assert_eq!(json["serverInfo"]["name"], "ostk");
        assert_eq!(json["capabilities"]["tools"]["listChanged"], false);
    }

    // →840: Session wire protocol serde roundtrips

    #[test]
    fn session_dispatch_params_serde() {
        let params = super::SessionDispatchParams {
            session_name: "scheduler".into(),
            input: "hello world".into(),
        };
        let json = serde_json::to_string(&params).unwrap();
        let parsed: super::SessionDispatchParams = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.session_name, "scheduler");
        assert_eq!(parsed.input, "hello world");
    }

    #[test]
    fn session_events_params_serde() {
        let params = super::SessionEventsParams {
            session_name: "worker-1".into(),
        };
        let json = serde_json::to_string(&params).unwrap();
        let parsed: super::SessionEventsParams = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.session_name, "worker-1");
    }

    #[test]
    fn agent_session_info_serde() {
        let info = super::AgentSessionInfo {
            name: "scheduler".into(),
            message_count: 42,
            is_busy: true,
        };
        let json = serde_json::to_string(&info).unwrap();
        let parsed: super::AgentSessionInfo = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.name, "scheduler");
        assert_eq!(parsed.message_count, 42);
        assert!(parsed.is_busy);
    }

    #[test]
    fn agent_session_list_response_serde() {
        let resp = super::AgentSessionListResponse {
            sessions: vec![
                super::AgentSessionInfo { name: "scheduler".into(), message_count: 10, is_busy: false },
                super::AgentSessionInfo { name: "worker".into(), message_count: 5, is_busy: true },
            ],
        };
        let json = serde_json::to_string(&resp).unwrap();
        let parsed: super::AgentSessionListResponse = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.sessions.len(), 2);
        assert_eq!(parsed.sessions[0].name, "scheduler");
        assert!(parsed.sessions[1].is_busy);
    }

    #[test]
    fn session_switch_params_serde() {
        let params = super::SessionSwitchParams { session_name: "debug".into() };
        let json = serde_json::to_string(&params).unwrap();
        let parsed: super::SessionSwitchParams = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.session_name, "debug");
    }

    #[test]
    fn initialize_result_no_instructions() {
        let result = InitializeResult {
            protocol_version: "2024-11-05".to_string(),
            capabilities: ServerCapabilities {
                tools: ToolsCapability {
                    list_changed: false,
                },
            },
            server_info: ServerInfo {
                name: "ostk".to_string(),
                version: "0.1.0".to_string(),
            },
            instructions: None,
        };
        let json_str = serde_json::to_string(&result).unwrap();
        // instructions should be absent when None
        assert!(!json_str.contains("instructions"));
    }
}
