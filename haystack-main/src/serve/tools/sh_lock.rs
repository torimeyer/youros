//! lock (alias: sh_lock) — coordination locks for agent orchestration.
//!
//! Two-layer design:
//! - **In-memory**: `ServerState.locks` HashMap with `tokio::sync::Notify` for
//!   instant wake on release. Agents blocking on `watch` wake immediately when
//!   the holder releases — no polling.
//! - **Filesystem**: `.ostk/locks/{name}.lock` files persist across daemon
//!   restarts. On daemon startup, stale lock files can be recovered.
//!
//! "create"  — acquire the lock (fails if already held)
//! "release" — release the lock, wake all waiters instantly
//! "watch"   — block until the lock is released (with timeout)
//! "status"  — check whether the lock is held and by whom

use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Instant, SystemTime};

use crate::serve::state::{LockEntry, ServerState};
use crate::serve::types::{ERR_ALIAS_NOT_FOUND, ERR_INVALID_PARAMS, ShLockParams, ToolError};

/// Resolve the path for a named lock file (persistence layer).
fn lock_path(state: &ServerState, name: &str) -> PathBuf {
    state.ostk_dir.join("locks").join(format!("{name}.lock"))
}

/// Handle a lock tool call.
pub async fn handle(
    params: ShLockParams,
    state: &ServerState,
) -> Result<serde_json::Value, ToolError> {
    let path = lock_path(state, &params.name);

    match params.action.as_str() {
        "create" => {
            // Ensure the locks directory exists.
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent).map_err(|e| {
                    ToolError::new(ERR_INVALID_PARAMS, format!("failed to create locks dir: {e}"))
                })?;
            }

            let owner = {
                let alias = state.agent_alias.read().await;
                alias.clone().unwrap_or_else(|| "unknown".to_string())
            };

            // Check in-memory state first (authoritative when daemon is alive)
            {
                let locks = state.locks.read().await;
                if locks.contains_key(&params.name) {
                    return Err(ToolError::new(
                        ERR_INVALID_PARAMS,
                        format!("lock '{}' already held", params.name),
                    ));
                }
            }

            // Write filesystem lock (persistence)
            let now = SystemTime::now()
                .duration_since(SystemTime::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs();
            let content = serde_json::json!({ "owner": owner, "created": now }).to_string();

            use std::io::Write;
            match std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&path)
            {
                Ok(mut f) => {
                    f.write_all(content.as_bytes()).map_err(|e| {
                        ToolError::new(ERR_INVALID_PARAMS, format!("failed to write lock: {e}"))
                    })?;
                }
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                    return Err(ToolError::new(
                        ERR_INVALID_PARAMS,
                        format!("lock '{}' already held", params.name),
                    ));
                }
                Err(e) => {
                    return Err(ToolError::new(
                        ERR_INVALID_PARAMS,
                        format!("failed to create lock: {e}"),
                    ));
                }
            }

            // Register in-memory entry with Notify
            let entry = LockEntry {
                name: params.name.clone(),
                holder: owner.clone(),
                created: Instant::now(),
                notify: Arc::new(tokio::sync::Notify::new()),
            };
            state.locks.write().await.insert(params.name.clone(), entry);

            Ok(serde_json::json!({"lock": params.name, "status": "created", "holder": owner}))
        }

        "release" => {
            // Remove in-memory entry and notify all waiters
            let notify = {
                let mut locks = state.locks.write().await;
                locks.remove(&params.name).map(|e| e.notify)
            };

            // Check if anyone actually held this lock
            let file_existed = match std::fs::remove_file(&path) {
                Ok(()) => true,
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => false,
                Err(e) => return Err(ToolError::new(
                    ERR_INVALID_PARAMS,
                    format!("failed to release lock: {e}"),
                )),
            };

            if notify.is_none() && !file_existed {
                return Err(ToolError::new(
                    ERR_ALIAS_NOT_FOUND,
                    format!("lock '{}' not found", params.name),
                ));
            }

            // Wake all waiters instantly
            if let Some(n) = notify {
                n.notify_waiters();
            }

            Ok(serde_json::json!({"lock": params.name, "status": "released"}))
        }

        "watch" => {
            let timeout_secs = params.timeout.unwrap_or(300);
            let timeout = std::time::Duration::from_secs(timeout_secs);

            // Get the Notify handle if the lock exists in memory
            let notify = {
                let locks = state.locks.read().await;
                locks.get(&params.name).map(|e| Arc::clone(&e.notify))
            };

            match notify {
                Some(n) => {
                    // Block until notified or timeout — instant wake, no polling
                    match tokio::time::timeout(timeout, n.notified()).await {
                        Ok(()) => Ok(serde_json::json!({
                            "lock": params.name,
                            "status": "released"
                        })),
                        Err(_) => Ok(serde_json::json!({
                            "lock": params.name,
                            "status": "timeout"
                        })),
                    }
                }
                None => {
                    // Lock not in memory — check filesystem (stale from previous daemon)
                    if path.try_exists().unwrap_or(false) {
                        // Stale filesystem lock — fall back to brief poll
                        let deadline = tokio::time::Instant::now() + timeout;
                        loop {
                            if !path.try_exists().unwrap_or(true) {
                                return Ok(serde_json::json!({
                                    "lock": params.name, "status": "released"
                                }));
                            }
                            if tokio::time::Instant::now() >= deadline {
                                return Ok(serde_json::json!({
                                    "lock": params.name, "status": "timeout"
                                }));
                            }
                            tokio::time::sleep(std::time::Duration::from_millis(200)).await;
                        }
                    } else {
                        // Lock doesn't exist at all — return immediately
                        Ok(serde_json::json!({
                            "lock": params.name,
                            "status": "released"
                        }))
                    }
                }
            }
        }

        "status" => {
            // Check in-memory first (authoritative)
            let locks = state.locks.read().await;
            if let Some(entry) = locks.get(&params.name) {
                let elapsed = entry.created.elapsed().as_secs();
                return Ok(serde_json::json!({
                    "lock": params.name,
                    "state": "held",
                    "holder": entry.holder,
                    "held_for_secs": elapsed,
                }));
            }
            drop(locks);

            // Fall back to filesystem check (stale from previous daemon)
            let state_str = if path.try_exists().unwrap_or(false) {
                "held"  // stale filesystem lock
            } else {
                "not_found"
            };
            Ok(serde_json::json!({"lock": params.name, "state": state_str}))
        }

        _ => Err(ToolError::new(
            ERR_INVALID_PARAMS,
            format!("Unknown lock action: {}", params.action),
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::serve::state::ServerState;
    use crate::serve::types::ShLockParams;

    /// Create a ServerState backed by a unique temp .ostk directory.
    fn make_state(test_name: &str) -> ServerState {
        let dir = std::env::temp_dir()
            .join("ostk_sh_lock_tests")
            .join(test_name);
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        ServerState::with_ostk_dir(dir)
    }

    fn make_params(action: &str, name: &str, timeout: Option<u64>) -> ShLockParams {
        ShLockParams {
            action: action.to_string(),
            name: name.to_string(),
            timeout,
        }
    }

    // ── create ──

    #[tokio::test]
    async fn create_lock() {
        let state = make_state("create_lock");
        let params = make_params("create", "build-lock", None);
        let result = handle(params, &state).await.unwrap();
        assert_eq!(result["lock"], "build-lock");
        assert_eq!(result["status"], "created");

        // Verify lock file exists on disk
        let path = lock_path(&state, "build-lock");
        assert!(path.exists(), "lock file should exist on disk");
    }

    #[tokio::test]
    async fn create_lock_already_held() {
        let state = make_state("create_already_held");
        handle(make_params("create", "dup", None), &state)
            .await
            .unwrap();

        // Try to create again while held
        let err = handle(make_params("create", "dup", None), &state)
            .await
            .unwrap_err();
        assert_eq!(err.code, ERR_INVALID_PARAMS);
        assert!(err.message.contains("already held"));
    }

    #[tokio::test]
    async fn create_lock_after_release() {
        let state = make_state("create_after_release");
        handle(make_params("create", "reuse", None), &state)
            .await
            .unwrap();
        handle(make_params("release", "reuse", None), &state)
            .await
            .unwrap();

        // Should be able to create again after release
        let result = handle(make_params("create", "reuse", None), &state)
            .await
            .unwrap();
        assert_eq!(result["status"], "created");
    }

    // ── release ──

    #[tokio::test]
    async fn release_lock() {
        let state = make_state("release_lock");
        handle(make_params("create", "rel-test", None), &state)
            .await
            .unwrap();

        let result = handle(make_params("release", "rel-test", None), &state)
            .await
            .unwrap();
        assert_eq!(result["lock"], "rel-test");
        assert_eq!(result["status"], "released");

        // Verify the lock file is gone
        let path = lock_path(&state, "rel-test");
        assert!(!path.exists(), "lock file should be deleted on release");
    }

    #[tokio::test]
    async fn release_nonexistent_lock() {
        let state = make_state("release_nonexistent");
        let err = handle(make_params("release", "ghost", None), &state)
            .await
            .unwrap_err();
        assert_eq!(err.code, ERR_ALIAS_NOT_FOUND);
        assert!(err.message.contains("not found"));
    }

    // ── status ──

    #[tokio::test]
    async fn status_held() {
        let state = make_state("status_held");
        handle(make_params("create", "stat-test", None), &state)
            .await
            .unwrap();

        let result = handle(make_params("status", "stat-test", None), &state)
            .await
            .unwrap();
        assert_eq!(result["lock"], "stat-test");
        assert_eq!(result["state"], "held");
    }

    #[tokio::test]
    async fn status_released() {
        let state = make_state("status_released");
        handle(make_params("create", "stat-rel", None), &state)
            .await
            .unwrap();
        handle(make_params("release", "stat-rel", None), &state)
            .await
            .unwrap();

        // After release, file is deleted — status returns "not_found"
        // (there is no "released" state on disk, the file is simply gone)
        let result = handle(make_params("status", "stat-rel", None), &state)
            .await
            .unwrap();
        assert_eq!(result["state"], "not_found");
    }

    #[tokio::test]
    async fn status_not_found() {
        let state = make_state("status_not_found");
        let result = handle(make_params("status", "nope", None), &state)
            .await
            .unwrap();
        assert_eq!(result["state"], "not_found");
    }

    // ── watch ──

    #[tokio::test]
    async fn watch_already_released() {
        let state = make_state("watch_already_released");
        handle(make_params("create", "watch-rel", None), &state)
            .await
            .unwrap();
        handle(make_params("release", "watch-rel", None), &state)
            .await
            .unwrap();

        // File is gone — watch should return immediately
        let result = handle(make_params("watch", "watch-rel", Some(5)), &state)
            .await
            .unwrap();
        assert_eq!(result["status"], "released");
    }

    #[tokio::test]
    async fn watch_not_found() {
        let state = make_state("watch_not_found");
        // Lock was never created — file absent, returns released immediately
        let result = handle(make_params("watch", "no-such-lock", Some(1)), &state)
            .await
            .unwrap();
        assert_eq!(result["status"], "released");
    }

    #[tokio::test]
    async fn watch_timeout() {
        let state = make_state("watch_timeout");
        handle(make_params("create", "watch-timeout", None), &state)
            .await
            .unwrap();

        // Watch with 1 second timeout — lock is held, should timeout
        let result = handle(make_params("watch", "watch-timeout", Some(1)), &state)
            .await
            .unwrap();
        assert_eq!(result["status"], "timeout");
    }

    // ── unknown action ──

    #[tokio::test]
    async fn unknown_action() {
        let state = make_state("unknown_action");
        let err = handle(make_params("destroy", "x", None), &state)
            .await
            .unwrap_err();
        assert_eq!(err.code, ERR_INVALID_PARAMS);
        assert!(err.message.contains("Unknown lock action"));
    }
}
