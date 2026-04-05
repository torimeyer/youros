use clap::{Parser, Subcommand};

mod commands_use {
    pub use ostk::commands::*;
}

#[derive(Parser)]
#[command(
    version,
    about = ostk::strings::cli::ABOUT
)]
struct Cli {
    /// Print agent usage guide (patterns for LLM tool use)
    #[arg(long)]
    agents: bool,

    /// Launch TUI with debug split-screen panel showing kernel internals
    #[arg(long)]
    debug: bool,

    #[command(subcommand)]
    command: Option<Commands>,
}

#[derive(Subcommand)]
enum AuditCommands {
    /// Check audit trail for gaps
    Check,
    /// Backfill missing audit events from git history
    Backfill {
        /// Show what would be added without writing
        #[arg(long)]
        dry_run: bool,
        /// Detect and fix phantom hashes by searching git log for replacement commits
        #[arg(long)]
        fix_rewrites: bool,
    },
    /// Record a commit hash remap in audit.jsonl
    Remap {
        /// Old commit hash
        #[arg(long)]
        old: String,
        /// New commit hash
        #[arg(long)]
        new: String,
        /// What triggered the rewrite: rebase, amend, backfill, squash
        #[arg(long)]
        cause: String,
    },
}

#[derive(Subcommand)]
enum OsCommands {
    /// Check daemon health
    Status,
    /// Session delta since last boot
    Diff,
    /// Show OS time and identity
    Clock,
    /// Show token savings and performance
    Metrics,
    /// Chronological audit trail for any needle or topic
    History {
        /// Needle ID (→NNN) or search term
        target: Option<String>,
        /// Show last N events (no filter)
        #[arg(long)]
        last: Option<usize>,
    },
    /// Audit trail completeness check
    Audit {
        #[command(subcommand)]
        command: AuditCommands,
    },
}

#[derive(Subcommand)]
enum WorkCommands {
    /// Claim the next available needle and begin work
    Pull {
        /// Continuous mode: work needle, close it, pull next until queue is empty
        #[arg(long)]
        loop_mode: bool,
    },
    /// Record a thought into the hay pile, or list/cluster existing hay
    Hay {
        /// Raw thought to capture (omit to list clustered hay)
        thought: Option<String>,
        /// Source attribution
        #[arg(long, default_value = "user")]
        source: String,
    },
    /// Triage hay into needles or keep as hay
    Compile {
        /// Only show what would be compiled (don't create needles)
        #[arg(long)]
        dry_run: bool,
    },
    /// Re-index hay and drafts for semantic search
    Index,
    /// Add a needle (verb + target + test)
    Add {
        /// Short title/verb target
        title: String,
        /// Priority: P0 (immediate), P1 (sprint), P2 (someday)
        #[arg(long, default_value = "P1")]
        priority: String,
        /// Milestone or track name
        #[arg(long)]
        milestone: Option<String>,
        /// Tags (comma-separated)
        #[arg(long)]
        tags: Option<String>,
        /// Acceptance criteria (shell command)
        #[arg(long)]
        ac: Option<String>,
        /// Longer description of the needle
        #[arg(long)]
        description: Option<String>,
        /// Comma-separated needle IDs this depends on (e.g. "→843,→844")
        #[arg(long)]
        depends_on: Option<String>,
        /// Comma-separated needle IDs this blocks (e.g. "→844,→846")
        #[arg(long)]
        blocks: Option<String>,
    },
    /// Close a needle with an optional reason
    Close {
        /// Needle ID to close (e.g. →576 or 576)
        id: String,
        /// Reason for closing
        #[arg(long)]
        reason: Option<String>,
    },
    /// Link needles or attach artifacts (spec, draft, agentfile)
    Link {
        /// Source needle ID (e.g. →843)
        source: String,
        /// Relation type: blocks, depends-on, spec, draft, agentfile
        relation: String,
        /// Target: needle ID for blocks/depends-on, file path for spec/draft/agentfile
        target: String,
    },
    /// List needles with optional filters
    List {
        /// Filter by status (open, closed, in_progress, shelved)
        #[arg(long)]
        status: Option<String>,
        /// Filter by priority (P0, P1, P2)
        #[arg(long)]
        priority: Option<String>,
        /// Only show count
        #[arg(long)]
        count: bool,
        /// Output as JSON
        #[arg(long)]
        json: bool,
    },
    /// Show next available task
    Next {
        /// Claim the task
        #[arg(long)]
        claim: bool,
        /// Assignee name
        #[arg(long)]
        assignee: Option<String>,
    },
    /// Cross-track dependency graph for a needle
    Depends {
        /// Needle ID (e.g. →576 or 576)
        id: Option<String>,
        /// Show the longest dependency chain across all needles
        #[arg(long)]
        critical_path: bool,
    },
    /// Validate, link, and assess needles — the quality pass
    Refine {
        /// Needle IDs to refine (e.g. →576 →577)
        ids: Vec<String>,
    },
    /// Add intent to a needle — make it denser and more connected
    Compound {
        /// Needle ID (e.g. →576)
        id: String,
        /// Intent text to compound onto the needle
        intent: String,
    },
    /// Expand from a point — show frontier rings for agent delegation
    Radiate {
        /// Needle ID to radiate from (default: highest-degree point)
        id: Option<String>,
    },
    /// Show sphere digest — members, joints, hay clusters, BFS topology
    Sphere {
        /// Needle ID to show sphere for
        id: String,
    },
    /// Find needles and hay by concept — neighborhood discovery
    Near {
        /// Concept term or keyword to search
        query: String,
    },
    /// Remove a joint between two needles
    Unlink {
        /// Source needle ID
        source: String,
        /// Target needle ID
        target: String,
    },
    /// Pre-flight briefing for working a needle — sphere, blockers, hay, neighbors
    Activate {
        /// Needle ID to activate
        id: String,
    },
}

#[derive(Subcommand)]
enum DocCommands {
    /// Create a new draft document
    Draft {
        /// Title of the document
        title: String,
    },
    /// Promote a draft to spec
    Promote {
        /// Path to the draft file
        path: String,
    },
    /// Decompose a spec or needle into sub-needles
    Decompose {
        /// Path to the spec file, or needle ID (→NNN, ->NNN, or bare number)
        path: String,
        /// Skip review gate — create sub-needles without confirmation
        #[arg(long)]
        auto: bool,
    },
}

#[derive(Subcommand)]
enum KernelCommands {
    /// Initialize a ostk project
    Init,
    /// Alias for init (deprecated)
    Install {
        #[arg(long, hide = true)]
        symlinks: bool,
        #[arg(long, hide = true)]
        no_symlinks: bool,
        #[arg(long, hide = true)]
        import: bool,
    },
    /// Run the MCP kernel server on stdio
    Serve,
    /// Spawn a new agent worker
    Spawn {
        /// Agent name (ignored when --firecracker is set)
        name: String,
        /// Model to use
        #[arg(long, default_value = "sonnet")]
        model: String,
        /// Budget cap in USD
        #[arg(long, default_value = "2")]
        budget: String,
        /// Prompt for the agent (ignored when --firecracker is set)
        prompt: Option<String>,
        /// Launch in a Firecracker/QEMU microVM using the given Agentfile
        #[arg(long)]
        firecracker: bool,
    },
    /// Block until a spawned agent finishes
    Await {
        /// Agent name to wait for
        name: String,
        /// Maximum time to wait in seconds (default: 300)
        #[arg(long, default_value = "300")]
        timeout: u64,
    },
    /// Reap dead agents from the process table
    Reap,
    /// Show active agents
    Ps,
    /// Clean session shutdown
    Shutdown {
        /// Commit message
        #[arg(short, long)]
        message: Option<String>,
        /// Agent name
        #[arg(long, default_value = "orchestrator")]
        agent: String,
    },
    /// Catch-all for unrecognized kernel subcommands
    #[command(external_subcommand)]
    Unknown(Vec<String>),
}

#[derive(Subcommand)]
enum Commands {
    /// OS operational commands (status, diff, audit, metrics)
    Os {
        #[command(subcommand)]
        command: OsCommands,
    },
    /// Work orchestration commands (pull, compile, hay)
    Work {
        #[command(subcommand)]
        command: WorkCommands,
    },
    /// Documentation and spec commands (draft, promote, decompose)
    Doc {
        #[command(subcommand)]
        command: DocCommands,
    },
    /// Kernel lifecycle commands (serve, spawn, init, install)
    Kernel {
        #[command(subcommand)]
        command: KernelCommands,
    },
    /// Commit with spec attribution and needle tracking
    Commit {
        /// Commit message description
        #[arg(short, long)]
        message: String,
        /// Spec name (file in docs/spec/, without .md)
        #[arg(long)]
        spec: Option<String>,
        /// Section within the spec
        #[arg(long)]
        section: Option<String>,
        /// Needle ID to attribute
        #[arg(long)]
        bead: Option<String>,
        /// Needle ID to attribute (alias for --bead)
        #[arg(long)]
        needle: Option<String>,
        /// Agent name
        #[arg(long, default_value = "orchestrator")]
        agent: String,
    },
    /// Initialize an ostk project (alias for kernel init)
    Init {
        /// Interactive guided setup with step-by-step configuration
        #[arg(long)]
        guided: bool,
        /// Import existing repo: scan git history, README, issues into needles
        #[arg(long)]
        import: bool,
    },
    /// Read .ostk/boot.md and report state
    Boot {
        /// Fail-fast: exit non-zero if POST check fails or boot.md is stale
        #[arg(long)]
        bail: bool,
        /// Update the continuation prompt from kernel state
        #[arg(long)]
        update_prompt: bool,
    },
    /// Manage the embedding model for semantic search
    Embeddings {
        /// Action: download, status
        #[arg(default_value = "status")]
        action: String,
    },
    /// Launch the TUI — fleet, nudges, work, tack input
    Tui,
    /// Universal query — show anything by name or keyword
    Show {
        /// What to show: needle ID (→NNN), needles, hay, threads, status, clock
        target: String,
        /// Output as JSON
        #[arg(long)]
        json: bool,
    },
    /// Group related needles into a thread
    Thread {
        #[command(subcommand)]
        command: ThreadCommands,
    },
    /// Alias for work (backward compat)
    #[command(hide = true)]
    Needle {
        #[command(subcommand)]
        command: Option<WorkCommands>,
    },
    /// Secret management — keys never enter LLM context
    Secret {
        #[command(subcommand)]
        command: SecretCommands,
    },
    /// Privilege escalation — approve or deny requests
    Grant {
        #[command(subcommand)]
        command: GrantCommands,
    },
    /// Trace attribution chain for a needle, commit, or spec
    Trace {
        /// Needle ID, commit hash, or spec path
        id: String,
    },
    /// Search file contents — ripgrep-style recursive search
    Search {
        /// Search query (regex or literal string)
        query: String,
        /// Limit search to this path
        #[arg(long)]
        path: Option<String>,
        /// Use semantic search (vector embedding)
        #[arg(long)]
        semantic: bool,
    },
    /// Run an agent from an Agentfile
    Run {
        /// Path to the Agentfile
        agentfile: String,
        /// Inject an authorized secret into agent env
        #[arg(long = "env-passthrough", value_name = "KEY")]
        env_passthrough: Vec<String>,
        /// Runtime: "host" or "qemu"
        #[arg(long, default_value = "host")]
        runtime: String,
        /// Only show what would be done
        #[arg(long)]
        dry_run: bool,
    },
    /// Single-turn LLM call for intent-based tack
    Ask {
        /// The tack expression to send to the LLM
        expression: String,
        /// Max tokens for the response
        #[arg(long, default_value = "1024")]
        max_tokens: u64,
    },
    /// Resolve tack grammar to ostk commands
    Tack {
        /// Tack expression (e.g. ':compile', '.? status', '→437')
        input: String,
        /// Output as JSON
        #[arg(long)]
        json: bool,
    },
    /// Show the agent orientation guide
    Guide,
    /// Short alias — `hs <cmd>` = `ostk <cmd>`
    #[command(name = "hs")]
    Hs {
        /// ostk subcommand and args (passed through)
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Check daemon health (legacy alias)
    #[command(hide = true)]
    Status,
    /// Alias for init (deprecated)
    #[command(hide = true)]
    Install {
        #[arg(long, hide = true)]
        no_symlinks: bool,
    },
    /// Block until a spawned agent finishes (legacy alias)
    #[command(hide = true)]
    Await {
        /// Agent name to wait for
        name: String,
        /// Maximum time to wait in seconds
        #[arg(long, default_value = "300")]
        timeout: u64,
    },
    /// Reap dead agents (legacy alias)
    #[command(hide = true)]
    Reap,
    /// Signed, portable OS package — pack, unpack, verify
    Bail {
        #[command(subcommand)]
        command: BailCommands,
    },
    /// File a bug or feature request
    Should {
        /// Skip AI refinement — use dumb label inference only
        #[arg(long)]
        no_refine: bool,
        /// What ostk should do (natural language)
        description: Vec<String>,
    },
    /// Execute intent — resolve a :verb through .language and dispatch
    Do {
        /// Tack verb or command to execute (e.g. compile, :boot, spawn)
        verb: String,
        /// Arguments passed to the resolved command
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// MCP server proxy (userspace)
    Mcp {
        #[command(subcommand)]
        command: McpCommands,
    },
    /// Start the kernel daemon on a Unix domain socket
    Listen,
    /// List registered fcp device drivers
    Drivers,
    /// Sign or re-sign the HUMANFILE with your GPG key
    Sign,
    /// Mount FUSE filesystem overlay (requires --features fuse)
    Mount {
        /// Mount point (default: .ostk/mount/)
        path: Option<String>,
    },
    /// Show highest-compounding work — what unblocks the most other needles
    Compounds,
    /// Interactive REPL — plain stdin/stdout, no TUI
    Repl {},
    /// Run needle-bench benchmarks (cargo tests, Docker scenarios, leaderboard)
    Bench {
        /// Specific scenario name to run
        scenario: Option<String>,
        /// List all available scenarios
        #[arg(long, short = 'l')]
        list: bool,
        /// Run cargo tests only
        #[arg(long)]
        cargo_only: bool,
        /// Model name for Docker scenarios
        #[arg(long, short = 'm', default_value = "claude-sonnet-4-6")]
        model: String,
        /// Run Docker scenarios only
        #[arg(long, short = 'd')]
        docker: bool,
        /// Render leaderboard from existing results
        #[arg(long, short = 's')]
        score: bool,
        /// Experiment arm: native, kernel, or both (default: both)
        #[arg(long, short = 'a', default_value = "both")]
        arm: String,
        /// Run all Docker scenarios
        #[arg(long)]
        all: bool,
        /// Use locally cross-compiled ostk binary for kernel arm (skip download)
        #[arg(long)]
        local: bool,
        /// Driver for kernel arm: openrouter (default, consistent API) or cpu (optimized native drivers)
        #[arg(long, default_value = "openrouter")]
        driver: String,
        /// Keep containers after run for investigation (skip cleanup)
        #[arg(long)]
        keep: bool,
    },
}

#[derive(Subcommand)]
enum ThreadCommands {
    /// Create a thread grouping needles
    Create {
        /// Thread name
        name: String,
        /// Needle IDs to group
        #[arg(long, num_args = 1..)]
        needles: Vec<String>,
        /// Skip needle validation (allow missing/closed)
        #[arg(long)]
        force: bool,
    },
    /// List all threads
    List,
}

#[derive(Subcommand)]
enum SecretCommands {
    /// Store a secret in the platform keychain
    Set {
        /// Key name (e.g. ANTHROPIC_API_KEY)
        key: String,
        /// Value (if omitted, prompts interactively)
        #[arg(long)]
        value: Option<String>,
    },
    /// Retrieve a secret (stdout only, for subprocess capture)
    Get {
        /// Key name
        key: String,
    },
    /// List available keys (names only, never values)
    List,
    /// Print export statement for shell eval
    Env {
        /// Key name
        key: String,
    },
}

#[derive(Subcommand)]
enum GrantCommands {
    /// Show pending (or all) privilege requests
    List {
        /// Filter by status: pending, granted, denied, revoked (default: pending)
        #[arg(long)]
        status: Option<String>,
        /// Output as JSON
        #[arg(long)]
        json: bool,
    },
    /// Approve a privilege request
    Approve {
        /// Request ID from `ostk grant list`
        id: String,
        /// TTL in seconds (0 = session-scoped, no expiry)
        #[arg(long, default_value = "0")]
        ttl: u64,
        /// Scope override (default: request target)
        #[arg(long)]
        scope: Option<String>,
    },
    /// Deny a privilege request
    Deny {
        /// Request ID
        id: String,
        /// Reason for denial
        #[arg(long, default_value = "not permitted")]
        reason: String,
    },
    /// Show details for a specific request
    Show {
        /// Request ID
        id: String,
    },
    /// List available request types
    Types,
}

#[derive(Subcommand)]
enum BailCommands {
    /// Pack a bail: signed portable OS package
    Pack {
        /// Public mode: boot.md + .boot/INIT + .primefile only
        #[arg(long)]
        public: bool,
    },
    /// Unpack a bail: verify signature, apply state to current project
    Unpack {
        /// Path to the .bail file
        path: String,
    },
    /// Verify the GPG signature on a bail without unpacking
    Verify {
        /// Path to the .bail file
        path: String,
    },
}

#[derive(Subcommand)]
enum McpCommands {
    /// List configured MCP servers
    List,
    /// Call a tool on an MCP server
    Call {
        /// Server name (from ostk.toml)
        server: String,
        /// Tool name
        tool: String,
        /// Arguments as key=value pairs
        args: Vec<String>,
    },
    /// Run an MCP server on a Unix domain socket (driver mode)
    Serve {
        /// Driver name (creates .ostk/drivers/<name>.sock)
        name: String,
    },
}

fn dispatch_command(command: Commands) -> Result<(), String> {
    match command {
        Commands::Os { command } => match command {
            OsCommands::Status => commands_use::fleet::run_status(),
            OsCommands::Diff => commands_use::diff::run(),
            OsCommands::Clock => commands_use::clock::run(),
            OsCommands::Metrics => commands_use::metrics::run(),
            OsCommands::History { target, last } => {
                commands_use::history::run(target.as_deref(), last)
            }
            OsCommands::Audit { command } => match command {
                AuditCommands::Check => commands_use::audit::run_check(),
                AuditCommands::Backfill { dry_run, fix_rewrites } => {
                    commands_use::audit::run_backfill(dry_run, fix_rewrites)
                }
                AuditCommands::Remap { old, new, cause } => {
                    commands_use::audit::run_remap(&old, &new, &cause)
                }
            },
        },
        Commands::Work { command } => match command {
            WorkCommands::Pull { loop_mode } => commands_use::work::run_pull(loop_mode),
            WorkCommands::Hay { thought: Some(thought), source } => commands_use::work::run_hay(&thought, &source),
            WorkCommands::Hay { thought: None, .. } => commands_use::work::run_hay_list(),
            WorkCommands::Compile { dry_run } => commands_use::work::run_compile(dry_run),
            WorkCommands::Index => commands_use::index::run_reindex(),
            WorkCommands::Add {
                title,
                priority,
                milestone,
                tags,
                ac,
                description,
                depends_on,
                blocks,
            } => commands_use::work::run_add(
                &title, &priority, &milestone, tags.as_deref(),
                ac.as_deref(), description.as_deref(),
                depends_on.as_deref(), blocks.as_deref(),
            ),
            WorkCommands::Link { source, relation, target } => {
                commands_use::work::run_link(&source, &relation, &target)
            }
            WorkCommands::Close { id, reason } => {
                commands_use::work::run_close(&id, reason.as_deref())
            }
            WorkCommands::List {
                status,
                priority,
                count,
                json,
            } => commands_use::work::run_list(status.as_deref(), priority.as_deref(), count, json),
            WorkCommands::Next { claim, assignee } => {
                commands_use::work::run_next(claim, assignee.as_deref())
            }
            WorkCommands::Depends { id, critical_path } => {
                commands_use::depends::run(id.as_deref(), critical_path)
            }
            WorkCommands::Refine { ids } => {
                commands_use::refine::run_refine(&ids)
            }
            WorkCommands::Compound { id, intent } => {
                commands_use::refine::run_compound(&id, &intent)
            }
            WorkCommands::Radiate { id } => {
                commands_use::refine::run_radiate(id.as_deref())
            }
            WorkCommands::Sphere { id } => {
                commands_use::refine::run_sphere(&id)
            }
            WorkCommands::Near { query } => {
                commands_use::refine::run_near(&query)
            }
            WorkCommands::Unlink { source, target } => {
                commands_use::refine::run_unlink(&source, &target)
            }
            WorkCommands::Activate { id } => {
                commands_use::refine::run_activate(&id)
            }
        },
        Commands::Doc { command } => match command {
            DocCommands::Draft { title } => commands_use::draft::run(&title),
            DocCommands::Promote { path } => commands_use::promote::run(&path),
            DocCommands::Decompose { path, auto } => commands_use::decompose::run(&path, auto),
        },
        Commands::Kernel { command } => match command {
            KernelCommands::Serve => {
                let rt = tokio::runtime::Runtime::new().map_err(|e| e.to_string());
                match rt {
                    Ok(rt) => rt
                        .block_on(ostk::serve::server::run_server())
                        .map_err(|e| e.to_string()),
                    Err(e) => Err(e),
                }
            }
            KernelCommands::Spawn { name, model, budget, prompt, firecracker } => {
                if firecracker {
                    commands_use::firecracker::run_vm(&name)
                } else {
                    let prompt = prompt.unwrap_or_default();
                    commands_use::fleet::run_spawn(&name, &model, &budget, &prompt)
                }
            }
            KernelCommands::Reap => commands_use::reap::run(),
            KernelCommands::Ps => commands_use::fleet::run_ps(),
            KernelCommands::Init => commands_use::init::run(),
            KernelCommands::Install { symlinks: _, no_symlinks: _, import: _ } => {
                // install is now an alias for init
                commands_use::init::run()
            }
            KernelCommands::Await { name, timeout } => {
                commands_use::fleet::run_await(&name, timeout)
            }
            KernelCommands::Shutdown { message, agent } => {
                commands_use::shutdown::run(message.as_deref(), &agent)
            }
            KernelCommands::Unknown(args) => {
                let sub = args.first().map(|s| s.as_str()).unwrap_or("?");
                Err(format!(
                    "unknown kernel command '{}'\n\n\
                     Available kernel commands:\n\
                     {}\n\
                     {}\n\
                     {}\n\
                     {}\n\
                     {}\n\
                     {}\n\
                     {}\n\
                     {}",
                    sub,
                    "  init      — initialize a ostk project",
                    "  install   — alias for init (deprecated)",
                    "  serve     — run MCP kernel server on stdio",
                    "  spawn     — spawn a new agent worker",
                    "  await     — block until a spawned agent finishes",
                    "  reap      — reap dead agents from the process table",
                    "  ps        — show active agents",
                    "  shutdown  — clean session shutdown",
                ))
            }
        },
        Commands::Commit { message, spec, section, bead, needle, agent } => {
            // --needle is an alias for --bead; --needle wins if both provided
            let effective_bead = needle.or(bead);
            commands_use::commit::run(&message, spec.as_deref(), section.as_deref(), effective_bead.as_deref(), &agent)
        }
        Commands::Init { guided, import } => {
            commands_use::init::run()?;
            if import {
                commands_use::import::run_local()?;
            }
            if guided {
                commands_use::onboarding::run_guided()?;
            }
            Ok(())
        }
        Commands::Embeddings { action } => {
            match action.as_str() {
                "download" => commands_use::embeddings::run_download()?,
                "status" | _ => commands_use::embeddings::run_status()?,
            }
            Ok(())
        }
        Commands::Boot { bail, update_prompt } => {
            // →813: Set env var so boot.rs can detect --update-prompt.
            if update_prompt {
                // SAFETY: Single-threaded CLI dispatch — no threads spawned yet.
                unsafe { std::env::set_var("OSTK_UPDATE_PROMPT", "1"); }
            }
            let result = commands_use::boot::run();
            if bail
                && let Err(e) = commands_use::post::run() {
                    eprintln!("{}", ostk::strings::errors::BOOT_BAIL_POST_FAIL.replacen("{}", &e, 1));
                    return Err(ostk::strings::errors::BOOT_BAIL_UNHEALTHY.replacen("{}", &e, 1));
                }
            result
        }
        Commands::Tui => {
            let root = ostk::find_project_root()?;
            ostk::fcp_screen::app::run(root, false)
        }
        Commands::Show { target, json } => commands_use::show::run(&target, json),
        Commands::Thread { command } => match command {
            ThreadCommands::Create { name, needles, force } => run_thread_create(&name, &needles, force),
            ThreadCommands::List => run_thread_list(),
        },
        Commands::Needle { command } => match command {
            Some(WorkCommands::Add {
                title,
                priority,
                milestone,
                tags,
                ac,
                description,
                depends_on,
                blocks,
            }) => commands_use::work::run_add(
                &title, &priority, &milestone, tags.as_deref(),
                ac.as_deref(), description.as_deref(),
                depends_on.as_deref(), blocks.as_deref(),
            ),
            Some(WorkCommands::Link { source, relation, target }) => {
                commands_use::work::run_link(&source, &relation, &target)
            }
            Some(WorkCommands::Close { id, reason }) => {
                commands_use::work::run_close(&id, reason.as_deref())
            }
            Some(WorkCommands::List {
                status,
                priority,
                count,
                json,
            }) => commands_use::work::run_list(status.as_deref(), priority.as_deref(), count, json),
            Some(WorkCommands::Next { claim, assignee }) => {
                commands_use::work::run_next(claim, assignee.as_deref())
            }
            Some(WorkCommands::Depends { id, critical_path }) => {
                commands_use::depends::run(id.as_deref(), critical_path)
            }
            Some(WorkCommands::Pull { loop_mode }) => commands_use::work::run_pull(loop_mode),
            Some(WorkCommands::Hay { thought: Some(thought), source }) => commands_use::work::run_hay(&thought, &source),
            Some(WorkCommands::Hay { thought: None, .. }) => commands_use::work::run_hay_list(),
            Some(WorkCommands::Compile { dry_run }) => commands_use::work::run_compile(dry_run),
            Some(WorkCommands::Index) => commands_use::index::run_reindex(),
            Some(WorkCommands::Refine { ids }) => {
                commands_use::refine::run_refine(&ids)
            }
            Some(WorkCommands::Compound { id, intent }) => {
                commands_use::refine::run_compound(&id, &intent)
            }
            Some(WorkCommands::Radiate { id }) => {
                commands_use::refine::run_radiate(id.as_deref())
            }
            Some(WorkCommands::Sphere { id }) => {
                commands_use::refine::run_sphere(&id)
            }
            Some(WorkCommands::Near { query }) => {
                commands_use::refine::run_near(&query)
            }
            Some(WorkCommands::Unlink { source, target }) => {
                commands_use::refine::run_unlink(&source, &target)
            }
            Some(WorkCommands::Activate { id }) => {
                commands_use::refine::run_activate(&id)
            }
            None => commands_use::work::run_list(None, None, false, false),
        },
        Commands::Secret { command } => match command {
            SecretCommands::Set { key, value } => commands_use::secret::run_set(&key, value.as_deref()),
            SecretCommands::Get { key } => commands_use::secret::run_get(&key),
            SecretCommands::List => commands_use::secret::run_list(),
            SecretCommands::Env { key } => commands_use::secret::run_env(&key),
        },
        Commands::Grant { command } => match command {
            GrantCommands::List { status, json } => commands_use::grant::run_list(status.as_deref(), json),
            GrantCommands::Approve { id, ttl, scope } => commands_use::grant::run_approve(&id, ttl, scope.as_deref()),
            GrantCommands::Deny { id, reason } => commands_use::grant::run_deny(&id, &reason),
            GrantCommands::Show { id } => commands_use::grant::run_show(&id),
            GrantCommands::Types => commands_use::grant::run_types(),
        },
        Commands::Trace { id } => commands_use::trace::run(&id),
        Commands::Search { query, path, semantic } => {
            commands_use::search::run(&query, path.as_deref(), semantic)
        }
        Commands::Run { agentfile, env_passthrough: _, runtime, dry_run: _ } => {
            match runtime.as_str() {
                "qemu" => commands_use::firecracker::run_vm(&agentfile),
                // →798: All paths route through kernel AgentSession + CpuDriver
                _ => commands_use::run::run_kernel(&agentfile),
            }
        }
        Commands::Ask { expression, max_tokens } => {
            commands_use::ask::ask(&expression, max_tokens)
        }
        Commands::Tack { input, json } => {
            let root = ostk::find_project_root()?;
            let hs_dir = ostk::state_dir(&root);
            match ostk::fcp::tack::resolve_tack(&input, &hs_dir) {
                Some(r) => {
                    // →596: Record resolution event for confidence gradient
                    let _ = ostk::append_audit(&root, &serde_json::json!({
                        "event": "tack.resolved",
                        "input": input,
                        "verb": r.verb,
                        "tier": r.tier,
                        "source": r.source.as_str(),
                        "resolved": r.resolved,
                        "timestamp": ostk::now_iso()
                    }));

                    if json {
                        let j = serde_json::json!({
                            "resolved": r.resolved,
                            "verb": r.verb,
                            "intent": r.intent.as_str(),
                            "command": r.command,
                            "args": r.args,
                            "source": r.source.as_str(),
                            "tier": r.tier,
                            "confidence": r.confidence,
                            "suggestions": r.suggestions,
                        });
                        println!("{}", serde_json::to_string_pretty(&j).unwrap_or_default());
                    } else if r.resolved {
                        let cmd = r.command.as_deref().unwrap_or("?");
                        let args_str = if r.args.is_empty() {
                            String::new()
                        } else {
                            format!(" {}", r.args.join(" "))
                        };
                        println!("{}", ostk::strings::tack::RESOLVED_FMT
                            .replacen("{}", &r.verb, 1)
                            .replacen("{}", cmd, 1)
                            .replacen("{}", &args_str, 1)
                            .replacen("{}", r.source.as_str(), 1)
                        );
                    } else {
                        eprint!("{}", ostk::strings::tack::UNRECOGNIZED_FMT.replacen("{}", &r.verb, 1));
                        if !r.suggestions.is_empty() {
                            eprint!("{}", ostk::strings::tack::DID_YOU_MEAN.replacen("{}", &r.suggestions.join(", "), 1));
                        }
                        eprintln!();
                        return Err(ostk::strings::errors::UNRECOGNIZED_TACK.to_string());
                    }
                    Ok(())
                }
                None => Err(ostk::strings::errors::NOT_A_TACK.replacen("{}", &input, 1)),
            }
        }
        Commands::Guide => {
            println!("{}", ostk::cli::agents::AGENT_GUIDE);
            Ok(())
        }
        Commands::Hs { args } => {
            let exe = std::env::current_exe().unwrap_or_else(|_| "ostk".into());
            let status = std::process::Command::new(&exe)
                .args(&args)
                .status()
                .map_err(|e| format!("hs passthrough failed: {e}"))?;
            std::process::exit(status.code().unwrap_or(1));
        }
        Commands::Status => commands_use::fleet::run_status(),
        Commands::Install { no_symlinks: _ } => {
            // install is now an alias for init
            commands_use::init::run()
        }
        Commands::Await { name, timeout } => commands_use::fleet::run_await(&name, timeout),
        Commands::Reap => commands_use::reap::run(),
        Commands::Bail { command } => match command {
            BailCommands::Pack { public } => commands_use::bail::run_pack(public),
            BailCommands::Unpack { path } => commands_use::bail::run_unpack(&path),
            BailCommands::Verify { path } => commands_use::bail::run_verify(&path),
        },
        Commands::Should { no_refine, description } => {
            let joined = description.join(" ");
            commands_use::should::run(&joined, no_refine)
        }
        Commands::Mcp { command } => match command {
            McpCommands::List => commands_use::mcp_proxy::run_list(),
            McpCommands::Call { server, tool, args } => {
                commands_use::mcp_proxy::run_call(&server, &tool, &args)
            }
            McpCommands::Serve { name } => commands_use::mcp_proxy::run_serve(&name),
        },
        Commands::Do { verb, args } => run_do(&verb, &args),
        Commands::Listen => commands_use::listen::run(),
        Commands::Drivers => commands_use::drivers::run(),
        Commands::Sign => commands_use::sign::run(),
        Commands::Mount { path } => commands_use::mount::run(path.as_deref()),
        Commands::Compounds => commands_use::compounds::run(),
        Commands::Repl {} => commands_use::repl::run(),
        Commands::Bench { scenario, list, cargo_only, model, docker, score, arm, all, local, driver, keep } => {
            commands_use::bench::run(scenario.as_deref(), list, cargo_only, &model, docker, score, &arm, all, local, &driver, keep)
        }
    }
}

/// Run a command capturing stdout, strip VT100 escape codes, write to stdout.
#[allow(dead_code)] // VTE pipeline variant, wiring pending
fn run_with_vt100_strip(args: &[String]) -> i32 {
    use std::io::{Read, Write};
    use std::process::{Command, Stdio};

    let mut child = match Command::new(&args[0])
        .args(&args[1..])
        .stdin(Stdio::inherit())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
    {
        Ok(c) => c,
        Err(_) => return exec_real_shell(args),
    };

    let mut raw = Vec::new();
    if let Some(ref mut stdout) = child.stdout {
        let _ = stdout.read_to_end(&mut raw);
    }

    let status = child.wait().map(|s| s.code().unwrap_or(1)).unwrap_or(1);

    let stripped = ostk::squasher::vte_strip::strip_vt100_bytes(&raw);
    let _ = std::io::stdout().write_all(&stripped);
    let _ = std::io::stdout().flush();

    status
}

/// Exec the real shell with no ostk processing.
fn exec_real_shell(args: &[String]) -> i32 {
    use std::ffi::CString;
    use std::process::Command;

    let c_args: Vec<CString> = args
        .iter()
        .map(|a| CString::new(a.as_str()).unwrap())
        .collect();
    let c_ptrs: Vec<*const libc::c_char> = c_args
        .iter()
        .map(|a| a.as_ptr())
        .chain(std::iter::once(std::ptr::null()))
        .collect();

    unsafe {
        libc::execvp(c_ptrs[0], c_ptrs.as_ptr());
    }

    // execvp failed — fall back to Command
    let status = Command::new(&args[0])
        .args(&args[1..])
        .status();

    match status {
        Ok(s) => s.code().unwrap_or(1),
        Err(_) => 1,
    }
}

fn main() {
    // If we're PID 1 inside a VM, run init sequence (never returns)
    if std::process::id() == 1 {
        ostk::commands::vm_init::run_as_init();
    }

    // Fallback: if invoked as "ostk-init" symlink, run init sequence
    let bin_name = std::env::args().next().unwrap_or_default();
    if bin_name.ends_with("ostk-init") {
        ostk::commands::vm_init::run_as_init();
    }

    // →819: Initialize structured debug logging (RUST_LOG=debug to activate)
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .with_target(false)
        .with_writer(std::io::stderr)
        .init();

    let os_name = detect_os_name();
    let state_dir = os_state_dir(&os_name);
    // SAFETY: These run at the very start of main(), before any threads are
    // spawned. They are set once and never mutated again.
    if std::env::var("OSTK_OS_NAME").is_err() {
        unsafe { std::env::set_var("OSTK_OS_NAME", &os_name) };
    }
    if std::env::var("OSTK_STATE_DIR").is_err() {
        unsafe { std::env::set_var("OSTK_STATE_DIR", &state_dir) };
    }

    use clap::{CommandFactory, FromArgMatches};
    let bin_name: &'static str = match os_name.as_str() {
        "ostk" => "ostk",
        "hs"   => "hs",
        _      => "ostk",
    };
    let matches = Cli::command().name(bin_name).get_matches();
    let cli = Cli::from_arg_matches(&matches).unwrap_or_else(|e: clap::Error| e.exit());

    if cli.agents { print_agents_guide(); }

    let debug_mode = cli.debug;
    let command = match cli.command {
        Some(cmd) => cmd,
        None => {
            use std::io::IsTerminal;

            let root = ostk::find_project_root();
            let has_state = root.as_ref().is_ok_and(|r| {
                ostk::state_dir(r).is_dir()
            });

            if !has_state {
                // No .ostk/ — can we auto-bootstrap?
                let has_api_key = ostk::kernel::defaults::detect_api_key().is_some();
                let is_tty = std::io::stdin().is_terminal();

                if has_api_key && is_tty {
                    // Silent auto-bootstrap → proceed to TUI
                    let cwd = std::env::current_dir().unwrap_or_default();
                    if let Err(e) = ostk::commands::init::run_quiet(&cwd) {
                        eprintln!("bootstrap error: {e}");
                        std::process::exit(1);
                    }
                    // →911: After bootstrap, check trust tier. If T3 (no GPG),
                    // offer guided setup instead of dropping into a broken TUI.
                    let state_dir = cwd.join(".ostk");
                    let (tier, _) = ostk::kernel::identity::determine_trust_tier(&state_dir);
                    if matches!(tier, ostk::kernel::identity::TrustTier::T3) {
                        println!();
                        println!("  \x1b[33m⚠\x1b[0m  No GPG key found — the OS needs one to write files.");
                        println!();
                        println!("  Options:");
                        println!("    \x1b[32mostk init --guided\x1b[0m    interactive setup (can generate a key for you)");
                        println!("    \x1b[32mgpg --full-generate-key\x1b[0m  then run \x1b[32mostk\x1b[0m again");
                        println!();
                        // Still open TUI so they can explore read-only
                    }
                } else if is_tty {
                    // No API key, interactive → show welcome menu
                    if let Err(e) = ostk::commands::onboarding::run() {
                        eprintln!("error: {e}");
                    }
                    std::process::exit(0);
                } else {
                    // Non-interactive, no state → hard error
                    eprintln!("error: no .ostk/ directory — run `ostk init`");
                    std::process::exit(1);
                }
            }

            let root = ostk::find_project_root().unwrap();
            if let Err(e) = ostk::fcp_screen::app::run(root, debug_mode) {
                eprintln!("{}", ostk::strings::shell::TUI_ERROR.replacen("{}", &e, 1));
                std::process::exit(1);
            }
            std::process::exit(0);
        }
    };

    let result = dispatch_command(command);
    if let Err(e) = result {
        eprintln!("error: {e}");
        std::process::exit(1);
    }
}

fn detect_os_name() -> String {
    std::env::args().next()
        .and_then(|a| std::path::Path::new(&a).file_name().map(|f| f.to_string_lossy().into_owned()))
        .unwrap_or_else(|| "ostk".to_string())
}

fn os_state_dir(os_name: &str) -> String {
    match os_name {
        "ostk" | "hs" => ".ostk".to_string(),
        other => format!(".{other}"),
    }
}

fn print_agents_guide() -> ! {
    println!("{}", ostk::cli::agents::AGENT_GUIDE);
    std::process::exit(0);
}

// ---------------------------------------------------------------------------
// Thread commands
// ---------------------------------------------------------------------------

fn run_do(verb: &str, extra_args: &[String]) -> Result<(), String> {
    let root = ostk::find_project_root()?;
    let ostk_dir = ostk::state_dir(&root);

    // Use the unified fcp-tack resolution pipeline.
    // If resolution fails or returns None, fall through to the scheduling agent.
    let tack_input = if verb.starts_with(':') { verb.to_string() } else { format!(":{}", verb) };
    let resolved = ostk::fcp::tack::resolve_tack(&tack_input, &ostk_dir)
        .filter(|r| r.resolved);

    if resolved.is_none() {
        // →752: Free text — route through kernel agent loop (do_cmd).
        let mut prompt_parts = vec![verb.to_string()];
        prompt_parts.extend_from_slice(extra_args);
        let prompt = prompt_parts.join(" ");
        return commands_use::do_cmd::run_oneshot(&prompt);
    }
    let res = resolved.unwrap();

    let command = res.command.ok_or_else(|| format!("verb resolved but no command found: {}", verb))?;

    // Split the resolved command (it might be multiple words like "needle add")
    let parts: Vec<String> = command.split_whitespace().map(|s| s.to_string()).collect();
    if parts.is_empty() {
        return Err(format!("empty command resolved for verb: {}", verb));
    }

    let mut cmd_args = Vec::new();
    // Add any args that were part of the resolution (e.g. "add" in "needle add")
    if parts.len() > 1 {
        cmd_args.extend_from_slice(&parts[1..]);
    }
    // Add args that were passed with the tack command (e.g. `:boot --bail` → "--bail")
    cmd_args.extend_from_slice(&res.args);
    // Add extra args passed directly to run_do
    cmd_args.extend_from_slice(extra_args);

    let exe = std::env::current_exe().unwrap_or_else(|_| "ostk".into());
    let status = std::process::Command::new(exe)
        .arg(&parts[0])
        .args(&cmd_args)
        .status()
        .map_err(|e| format!("failed to execute {}: {}", parts[0], e))?;

    if status.success() {
        Ok(())
    } else {
        Err(format!("command failed: {} {}", parts[0], cmd_args.join(" ")))
    }
}

fn run_thread_create(_name: &str, _needles: &[String], _force: bool) -> Result<(), String> { Ok(()) }
fn run_thread_list() -> Result<(), String> { Ok(()) }

