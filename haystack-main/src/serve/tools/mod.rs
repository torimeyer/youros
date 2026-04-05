//! Tool handlers for the MCP server.
//!
//! Each module handles one MCP tool (originated in mish, ostk's experimental
//! predecessor), now using ostk's kernel primitives.

pub mod sh_interact;
pub mod sh_lock;
pub mod sh_run;
pub mod sh_session;
pub mod sh_spawn;
pub mod fs_ops;
pub mod fs_read;
pub mod tack;
pub mod pitchfork;
pub mod context_search;
pub mod context_release;
