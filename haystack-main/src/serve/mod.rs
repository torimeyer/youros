//! MCP server for ostk.
//!
//! MCP server (originated in mish, ostk's experimental predecessor). Provides:
//! - types: JSON-RPC and MCP protocol types
//! - transport: stdio JSON-RPC line protocol
//! - dispatch: tool routing
//! - server: main event loop
//! - state: shared server state
//! - tools/: individual tool handlers

pub mod client;
pub mod dispatch;
pub mod server;
pub mod socket;
pub mod state;
pub mod tools;
pub mod transport;
pub mod types;
