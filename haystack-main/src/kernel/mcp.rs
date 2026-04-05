/// Minimal MCP server for the ostk kernel.
///
/// JSON-RPC 2.0 over stdio. Implements just enough MCP to:
/// - Handle initialize/initialized handshake
/// - List one tool: kernel_shell (alias: kernel_sh_run)
/// - Execute kernel_shell via the PTY subsystem
/// - Return JSON-RPC responses
///
/// This is NOT a full MCP implementation. It's the minimum needed
/// to prove tool dispatch works and unblock Track 2 and Track 3.
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::io::{self, BufRead, Write};

use crate::kernel::pty;

// ---------------------------------------------------------------------------
// JSON-RPC types
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct JsonRpcRequest {
    pub jsonrpc: String,
    #[serde(default)]
    pub id: Value,
    pub method: String,
    pub params: Option<Value>,
}

#[derive(Debug, Serialize)]
pub struct JsonRpcResponse {
    pub jsonrpc: String,
    pub id: Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<JsonRpcError>,
}

#[derive(Debug, Serialize)]
pub struct JsonRpcError {
    pub code: i32,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<Value>,
}

// ---------------------------------------------------------------------------
// MCP protocol types
// ---------------------------------------------------------------------------

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct InitializeResult {
    protocol_version: String,
    capabilities: ServerCapabilities,
    server_info: ServerInfo,
}

#[derive(Debug, Serialize)]
struct ServerCapabilities {
    tools: ToolsCapability,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ToolsCapability {
    list_changed: bool,
}

#[derive(Debug, Serialize)]
struct ServerInfo {
    name: String,
    version: String,
}

#[derive(Debug, Serialize)]
struct ToolInfo {
    name: String,
    description: String,
    #[serde(rename = "inputSchema")]
    input_schema: Value,
}

#[derive(Debug, Serialize)]
struct ToolContent {
    #[serde(rename = "type")]
    content_type: String,
    text: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ToolResult {
    content: Vec<ToolContent>,
    is_error: bool,
}

// ---------------------------------------------------------------------------
// Tool dispatch
// ---------------------------------------------------------------------------

fn handle_sh_run(params: &Value) -> Result<Value, String> {
    let cmd = params
        .get("arguments")
        .and_then(|a| a.get("cmd"))
        .and_then(|c| c.as_str())
        .ok_or_else(|| "missing 'cmd' argument".to_string())?;

    let command = vec!["/bin/sh".to_string(), "-c".to_string(), cmd.to_string()];

    match pty::run_command(&command) {
        Ok((status, output)) => {
            let output_str = String::from_utf8_lossy(&output).to_string();
            let exit_code = status.code.unwrap_or(-1);
            let text = format!("+ exit:{exit_code}\n{output_str}");

            let result = ToolResult {
                content: vec![ToolContent {
                    content_type: "text".to_string(),
                    text,
                }],
                is_error: !status.success(),
            };
            serde_json::to_value(result).map_err(|e| e.to_string())
        }
        Err(e) => {
            let result = ToolResult {
                content: vec![ToolContent {
                    content_type: "text".to_string(),
                    text: format!("PTY error: {e}"),
                }],
                is_error: true,
            };
            serde_json::to_value(result).map_err(|e| e.to_string())
        }
    }
}

fn tools_list() -> Value {
    let tools = vec![ToolInfo {
        name: "kernel_sh_run".to_string(),
        description: "Run a shell command and return output".to_string(),
        input_schema: serde_json::json!({
            "type": "object",
            "properties": {
                "cmd": {
                    "type": "string",
                    "description": "Shell command to execute"
                }
            },
            "required": ["cmd"]
        }),
    }];

    serde_json::json!({ "tools": tools })
}

// ---------------------------------------------------------------------------
// Request dispatch
// ---------------------------------------------------------------------------

fn dispatch(req: &JsonRpcRequest) -> JsonRpcResponse {
    match req.method.as_str() {
        "initialize" => JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            id: req.id.clone(),
            result: Some(
                serde_json::to_value(InitializeResult {
                    protocol_version: "2024-11-05".to_string(),
                    capabilities: ServerCapabilities {
                        tools: ToolsCapability {
                            list_changed: false,
                        },
                    },
                    server_info: ServerInfo {
                        name: "ostk-kernel".to_string(),
                        version: env!("CARGO_PKG_VERSION").to_string(),
                    },
                })
                .unwrap(),
            ),
            error: None,
        },

        "notifications/initialized" => {
            // Notification — no response needed, but we return one anyway
            // since the caller may expect it. Actually MCP says notifications
            // don't get responses. We'll handle this by not sending a response
            // for notifications (id is null).
            JsonRpcResponse {
                jsonrpc: "2.0".to_string(),
                id: Value::Null,
                result: None,
                error: None,
            }
        }

        "tools/list" => JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            id: req.id.clone(),
            result: Some(tools_list()),
            error: None,
        },

        "tools/call" => {
            let params = req.params.as_ref();
            let tool_name = params
                .and_then(|p| p.get("name"))
                .and_then(|n| n.as_str())
                .unwrap_or("");

            match tool_name {
                "kernel_sh_run" => match handle_sh_run(params.unwrap_or(&Value::Null)) {
                    Ok(result) => JsonRpcResponse {
                        jsonrpc: "2.0".to_string(),
                        id: req.id.clone(),
                        result: Some(result),
                        error: None,
                    },
                    Err(e) => JsonRpcResponse {
                        jsonrpc: "2.0".to_string(),
                        id: req.id.clone(),
                        result: None,
                        error: Some(JsonRpcError {
                            code: -32603,
                            message: e,
                            data: None,
                        }),
                    },
                },
                _ => JsonRpcResponse {
                    jsonrpc: "2.0".to_string(),
                    id: req.id.clone(),
                    result: None,
                    error: Some(JsonRpcError {
                        code: -32601,
                        message: format!("unknown tool: {tool_name}"),
                        data: None,
                    }),
                },
            }
        }

        _ => JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            id: req.id.clone(),
            result: None,
            error: Some(JsonRpcError {
                code: -32601,
                message: format!("method not found: {}", req.method),
                data: None,
            }),
        },
    }
}

// ---------------------------------------------------------------------------
// Server loop
// ---------------------------------------------------------------------------

/// Run the MCP server on stdin/stdout.
///
/// Reads JSON-RPC requests line by line from stdin,
/// dispatches them, and writes responses to stdout.
pub fn serve() -> io::Result<()> {
    let stdin = io::stdin();
    let mut stdout = io::stdout();

    for line in stdin.lock().lines() {
        let line = line?;
        let line = line.trim();
        if line.is_empty() {
            continue;
        }

        let req: JsonRpcRequest = match serde_json::from_str(line) {
            Ok(r) => r,
            Err(e) => {
                let err_resp = JsonRpcResponse {
                    jsonrpc: "2.0".to_string(),
                    id: Value::Null,
                    result: None,
                    error: Some(JsonRpcError {
                        code: -32700,
                        message: format!("parse error: {e}"),
                        data: None,
                    }),
                };
                let resp_str = serde_json::to_string(&err_resp).unwrap();
                writeln!(stdout, "{resp_str}")?;
                stdout.flush()?;
                continue;
            }
        };

        // Notifications don't get responses
        let is_notification = req.id.is_null() || req.method.starts_with("notifications/");

        let resp = dispatch(&req);

        if !is_notification {
            let resp_str = serde_json::to_string(&resp).unwrap();
            writeln!(stdout, "{resp_str}")?;
            stdout.flush()?;
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_dispatch_initialize() {
        let req = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: Value::Number(1.into()),
            method: "initialize".to_string(),
            params: Some(serde_json::json!({
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": { "name": "test", "version": "1.0" }
            })),
        };

        let resp = dispatch(&req);
        assert!(resp.error.is_none());
        assert!(resp.result.is_some());
        let result = resp.result.unwrap();
        assert_eq!(
            result.get("protocolVersion").unwrap().as_str().unwrap(),
            "2024-11-05"
        );
    }

    #[test]
    fn test_dispatch_tools_list() {
        let req = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: Value::Number(2.into()),
            method: "tools/list".to_string(),
            params: None,
        };

        let resp = dispatch(&req);
        assert!(resp.error.is_none());
        let result = resp.result.unwrap();
        let tools = result.get("tools").unwrap().as_array().unwrap();
        assert_eq!(tools.len(), 1);
        assert_eq!(
            tools[0].get("name").unwrap().as_str().unwrap(),
            "kernel_sh_run"
        );
    }

    #[test]
    fn test_dispatch_sh_run() {
        let req = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: Value::Number(3.into()),
            method: "tools/call".to_string(),
            params: Some(serde_json::json!({
                "name": "kernel_sh_run",
                "arguments": { "cmd": "echo mcp_test_output" }
            })),
        };

        let resp = dispatch(&req);
        assert!(resp.error.is_none());
        let result = resp.result.unwrap();
        let content = result.get("content").unwrap().as_array().unwrap();
        let text = content[0].get("text").unwrap().as_str().unwrap();
        assert!(
            text.contains("mcp_test_output"),
            "expected 'mcp_test_output' in output, got: {text}"
        );
    }

    #[test]
    fn test_dispatch_unknown_tool() {
        let req = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: Value::Number(4.into()),
            method: "tools/call".to_string(),
            params: Some(serde_json::json!({
                "name": "nonexistent_tool",
                "arguments": {}
            })),
        };

        let resp = dispatch(&req);
        assert!(resp.error.is_some());
        assert_eq!(resp.error.unwrap().code, -32601);
    }

    #[test]
    fn test_dispatch_unknown_method() {
        let req = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: Value::Number(5.into()),
            method: "nonexistent/method".to_string(),
            params: None,
        };

        let resp = dispatch(&req);
        assert!(resp.error.is_some());
    }
}
