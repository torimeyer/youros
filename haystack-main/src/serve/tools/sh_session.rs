//! session (alias: sh_session) — session lifecycle management tool.
//!
//! Provides list and basic session information.

use crate::serve::state::ServerState;
use crate::serve::types::{
    ERR_INVALID_PARAMS, SessionAction, ShSessionInfo, ShSessionListResponse, ShSessionParams,
    ToolError,
};

/// Handle a session tool call.
pub async fn handle(
    params: ShSessionParams,
    state: &ServerState,
) -> Result<serde_json::Value, ToolError> {
    match params.action {
        SessionAction::List => handle_list(state).await,
        SessionAction::Create => {
            let name = params.name.as_deref().ok_or_else(|| {
                ToolError::new(ERR_INVALID_PARAMS, "name is required for create action")
            })?;
            // For now, sessions are implicit (one PTY per shell call).
            // Return success with the requested name.
            let cwd = std::env::current_dir()
                .map(|p| p.display().to_string())
                .unwrap_or_else(|_| "/".to_string());
            Ok(serde_json::json!({
                "session": name,
                "cwd": cwd,
                "ready": true,
            }))
        }
        SessionAction::Close => {
            let name = params.name.as_deref().ok_or_else(|| {
                ToolError::new(ERR_INVALID_PARAMS, "name is required for close action")
            })?;
            Ok(serde_json::json!({
                "session": name,
                "closed": true,
            }))
        }
    }
}

async fn handle_list(state: &ServerState) -> Result<serde_json::Value, ToolError> {
    let cwd = std::env::current_dir()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|_| "/".to_string());

    let processes = state.processes.read().await;
    let _active_count = processes
        .values()
        .filter(|e| !e.state.is_terminal())
        .count();

    // Return a single "main" session representing the server.
    let sessions = vec![ShSessionInfo {
        session: "main".to_string(),
        cwd,
    }];

    let resp = ShSessionListResponse { sessions };

    serde_json::to_value(&resp).map_err(|e| ToolError::internal(format!("serialize error: {e}")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::serve::types::{SessionAction, ShSessionParams};

    // ── list ──

    #[tokio::test]
    async fn session_list_returns_main() {
        let state = ServerState::new();
        let params = ShSessionParams {
            action: SessionAction::List,
            name: None,
        };
        let result = handle(params, &state).await.unwrap();
        let sessions = result["sessions"].as_array().unwrap();
        assert_eq!(sessions.len(), 1);
        assert_eq!(sessions[0]["session"], "main");
        assert!(!sessions[0]["cwd"].as_str().unwrap().is_empty());
    }

    // ── create ──

    #[tokio::test]
    async fn session_create_with_name() {
        let state = ServerState::new();
        let params = ShSessionParams {
            action: SessionAction::Create,
            name: Some("worker-1".to_string()),
        };
        let result = handle(params, &state).await.unwrap();
        assert_eq!(result["session"], "worker-1");
        assert_eq!(result["ready"], true);
        assert!(!result["cwd"].as_str().unwrap().is_empty());
    }

    #[tokio::test]
    async fn session_create_without_name() {
        let state = ServerState::new();
        let params = ShSessionParams {
            action: SessionAction::Create,
            name: None,
        };
        let err = handle(params, &state).await.unwrap_err();
        assert_eq!(err.code, ERR_INVALID_PARAMS);
        assert!(err.message.contains("name is required"));
    }

    // ── close ──

    #[tokio::test]
    async fn session_close_with_name() {
        let state = ServerState::new();
        let params = ShSessionParams {
            action: SessionAction::Close,
            name: Some("sess-x".to_string()),
        };
        let result = handle(params, &state).await.unwrap();
        assert_eq!(result["session"], "sess-x");
        assert_eq!(result["closed"], true);
    }

    #[tokio::test]
    async fn session_close_without_name() {
        let state = ServerState::new();
        let params = ShSessionParams {
            action: SessionAction::Close,
            name: None,
        };
        let err = handle(params, &state).await.unwrap_err();
        assert_eq!(err.code, ERR_INVALID_PARAMS);
        assert!(err.message.contains("name is required"));
    }
}
