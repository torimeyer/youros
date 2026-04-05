//! Background daemon event poller — decouples daemon I/O from the render loop.
//!
//! The daemon client uses synchronous Unix socket reads with a 5s timeout.
//! Running this on the main thread blocks input handling during tool execution.
//! This module spawns a dedicated thread that polls the daemon and feeds
//! events through the unified AppMessage channel.

use std::sync::mpsc::{self, Sender};
use std::thread;
use std::time::Duration;

use serde_json::Value;

/// Events sent from the daemon poll thread to the main event loop.
pub enum DaemonEvent {
    /// Batch of events from session/events endpoint.
    Events(Value),
    /// Daemon connection lost — caller should drop daemon and switch to embedded.
    Lost,
}

/// Request sent from the main loop to the poll thread.
enum PollRequest {
    /// Poll events for a session.
    Poll { session_name: String },
    /// Stop polling (agent went idle).
    Stop,
    /// Shut down the thread.
    Shutdown,
}

/// Handle to the background poll thread.
pub struct DaemonPoller {
    request_tx: Sender<PollRequest>,
}

impl DaemonPoller {
    /// Spawn the background poller. Sends `AppMessage::Daemon(DaemonEvent::*)`
    /// through the unified channel instead of maintaining its own event_rx.
    pub(crate) fn spawn(
        ostk_dir: &std::path::Path,
        app_tx: Sender<super::app::AppMessage>,
    ) -> Option<Self> {
        let mut client = crate::serve::client::DaemonClient::new(ostk_dir);
        if client.connect().is_err() {
            return None;
        }
        // Initialize the poll connection (separate from the main client's connection)
        let _ = client.send_request("initialize", serde_json::json!({
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ostk-poller"},
        }));
        let _ = client.send_request("notifications/initialized", serde_json::json!({}));

        let (req_tx, req_rx) = mpsc::channel::<PollRequest>();

        thread::Builder::new()
            .name("daemon-poller".into())
            .spawn(move || {
                let mut active_session: Option<String> = None;

                loop {
                    // Check for commands from main thread
                    match req_rx.recv_timeout(Duration::from_millis(50)) {
                        Ok(PollRequest::Poll { session_name }) => {
                            active_session = Some(session_name);
                        }
                        Ok(PollRequest::Stop) => {
                            active_session = None;
                        }
                        Ok(PollRequest::Shutdown) => break,
                        Err(mpsc::RecvTimeoutError::Disconnected) => break,
                        Err(mpsc::RecvTimeoutError::Timeout) => {}
                    }

                    // Poll if we have an active session
                    if let Some(ref name) = active_session {
                        match client.poll_events(name) {
                            Ok(val) => {
                                if app_tx.send(super::app::AppMessage::Daemon(
                                    DaemonEvent::Events(val),
                                )).is_err() {
                                    break; // main thread dropped receiver
                                }
                            }
                            Err(_) => {
                                let _ = app_tx.send(super::app::AppMessage::Daemon(
                                    DaemonEvent::Lost,
                                ));
                                break;
                            }
                        }
                    }
                }
            })
            .ok()?;

        Some(Self {
            request_tx: req_tx,
        })
    }

    /// Tell the poller to start polling for a session.
    pub fn start_polling(&self, session_name: &str) {
        let _ = self.request_tx.send(PollRequest::Poll {
            session_name: session_name.to_string(),
        });
    }

    /// Tell the poller to stop polling (agent went idle).
    pub fn stop_polling(&self) {
        let _ = self.request_tx.send(PollRequest::Stop);
    }
}

impl Drop for DaemonPoller {
    fn drop(&mut self) {
        let _ = self.request_tx.send(PollRequest::Shutdown);
    }
}
