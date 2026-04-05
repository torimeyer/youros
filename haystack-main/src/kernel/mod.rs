/// ostk kernel modules.
///
/// The kernel provides coordination primitives:
/// - pty: PTY allocation, command execution
/// - file: CAS (str_replace) for file editing with Hot PR integration
/// - gen_table: Per-file generation tracking with shadow files
/// - hotpr: Conflict resolution (Tier 1 auto-merge, Tier 2 assisted merge, Tier 3 manual rebase)
/// - identity: Kernel-assigned agent aliases (monotonic counter)
/// - heartbeat: Agent health monitoring (active/stale/crashed)
/// - hwm: Per-agent read high-water marks for staleness signals
/// - digest: Dual digest ([procs] + [files]) on every tool response
/// - elision: Read elision (304 Not Modified) — redundant reads return ~5 tokens
/// - recovery: Grammar-compressed session logging and recovery summaries
/// - mcp: Minimal MCP server (JSON-RPC over stdio)
/// - policy: Pin capability enforcement at the write path
/// - approval: Kernel-mediated tool approval bus (→824)
pub mod approval;
pub mod destructor;
pub mod digest;
pub mod drivers;
pub mod dying;
pub mod quota;
pub mod elision;
pub mod file;
pub mod gen_table;
pub mod heartbeat;
pub mod hotpr;
pub mod hwm;
pub mod host_identity;
pub mod identity;
pub mod mcp;
pub mod nudge;
pub mod policy;
pub mod pty;
pub mod recovery;
pub mod registry;
/// - request: Unified privilege escalation (Secret, Budget, Tool, FileAccess, ModelUpgrade)
pub mod request;
/// - intelligence: Hay clustering — the pile organizes itself
pub mod intelligence;
/// - defaults: Sane defaults for zero-config operation
pub mod defaults;
/// - predispatch: →957 Pre-dispatch intent resolution — implicit kernel context injection
pub mod predispatch;
/// - handoff: →1157 Model switch lookup table — page table for new model
pub mod handoff;
/// - init: →1157 INIT parsing and validation — boot sequence spec
pub mod init;
/// - resolve: →1157 Internal verb resolution — eliminates fork+exec for kernel verbs
pub mod resolve;
/// - schema: →1157 Generate MCP tool schemas from .language signatures
pub mod schema;
/// - secrets: Secret masking — prevent secret values from entering LLM context
pub mod secrets;
/// - verb_ctx: →1157 Execution context for kernel verbs (fmt::Write based)
pub mod verb_ctx;
