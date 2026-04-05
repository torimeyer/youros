//! `ostk mcp` — userspace MCP server proxy.
//!
//! Connects to external MCP servers (stdio transport) and exposes their
//! tools as CLI subcommands. Agents call `shell("ostk mcp call linear list-issues")`
//! and get compressed, audited output. The kernel never touches MCP protocol bytes.
//!
//! MCP server configuration lives in `.ostk/HUMANFILE`:
//! ```text
//! mcp:
//!   linear: npx -y @anthropic/linear-mcp-server
//!   github: gh mcp-server
//! ```
//!
//! Authorized secrets from the `secrets:` section are resolved via the
//! platform keychain and injected into the server's environment.
//!
//! ## Persistent driver subprocesses (→751)
//!
//! Servers with `fcp-*` prefixed names are treated as **persistent drivers**.
//! Instead of spawning a fresh process per call (which loses LSP workspace state),
//! the proxy spawns a relay daemon that keeps the subprocess alive between calls.
//!
//! The daemon listens on `.ostk/drivers/{name}.sock` (Unix domain socket).
//! Each `mcp call` connects to the socket, sends one JSON-RPC request, reads the
//! response, and disconnects. The daemon multiplexes requests to the subprocess
//! stdin/stdout with a mutex. PID written to `.ostk/drivers/{name}.pid`.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::AtomicU64;

use serde_json::{json, Value};

use crate::commands::secret::resolve_secret;

/// An MCP server parsed from HUMANFILE.
struct McpServer {
    name: String,
    command: String,
    args: Vec<String>,
}

/// Parse the `mcp:` section from HUMANFILE content.
///
/// Format (same indented-key pattern as `secrets:`):
/// ```text
/// mcp:
///   linear: npx -y @anthropic/linear-mcp-server
///   github: gh mcp-server
/// ```
fn parse_mcp_servers(content: &str) -> Vec<McpServer> {
    let mut servers = Vec::new();
    let mut in_mcp = false;

    for line in content.lines() {
        if line.trim_start() == "mcp:" {
            in_mcp = true;
            continue;
        }

        if in_mcp {
            // End of section: non-empty line that doesn't start with whitespace
            if !line.is_empty() && !line.starts_with(' ') && !line.starts_with('\t') {
                break;
            }

            let trimmed = line.trim();
            if trimmed.is_empty() || trimmed.starts_with('#') {
                continue;
            }

            // Parse "name: command args..."
            if let Some((name, cmd_str)) = trimmed.split_once(':') {
                let name = name.trim().to_string();
                let cmd_str = cmd_str.trim();
                if cmd_str.is_empty() {
                    continue;
                }

                let parts: Vec<&str> = cmd_str.split_whitespace().collect();
                let command = parts[0].to_string();
                let args: Vec<String> = parts[1..].iter().map(|s| s.to_string()).collect();

                servers.push(McpServer { name, command, args });
            }
        }
    }

    servers
}

/// Parse the `secrets:` section from HUMANFILE to find authorized env var names.
fn parse_authorized_secrets(content: &str) -> Vec<String> {
    let mut keys = Vec::new();
    let mut in_secrets = false;

    for line in content.lines() {
        if line.trim_start() == "secrets:" {
            in_secrets = true;
            continue;
        }

        if in_secrets {
            if !line.is_empty() && !line.starts_with(' ') && !line.starts_with('\t') {
                break;
            }

            let trimmed = line.trim();
            if trimmed.is_empty() || trimmed.starts_with('#') {
                continue;
            }

            if let Some((key, _)) = trimmed.split_once(':') {
                keys.push(key.trim().to_string());
            }
        }
    }

    keys
}

/// Load HUMANFILE content from the ostk directory.
fn load_humanfile(ostk_dir: &Path) -> Result<String, String> {
    let path = ostk_dir.join("HUMANFILE");
    std::fs::read_to_string(&path)
        .map_err(|e| format!("failed to read HUMANFILE: {e}"))
}

/// List configured MCP servers from HUMANFILE.
pub fn run_list() -> Result<(), String> {
    let root = crate::find_project_root()?;
    let content = load_humanfile(&crate::state_dir(&root))?;
    let servers = parse_mcp_servers(&content);

    if servers.is_empty() {
        println!("no MCP servers configured");
        println!();
        println!("add to .ostk/HUMANFILE:");
        println!();
        println!("  mcp:");
        println!("    linear: npx -y @anthropic/linear-mcp-server");
        println!("    github: gh mcp-server");
        return Ok(());
    }

    for srv in &servers {
        let cmd_line = if srv.args.is_empty() {
            srv.command.clone()
        } else {
            format!("{} {}", srv.command, srv.args.join(" "))
        };
        println!("{:<14}{}", srv.name, cmd_line);
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Persistent driver paths
// ---------------------------------------------------------------------------

fn drivers_dir(ostk_dir: &Path) -> PathBuf {
    ostk_dir.join("drivers")
}

fn driver_sock_path(ostk_dir: &Path, name: &str) -> PathBuf {
    drivers_dir(ostk_dir).join(format!("{name}.sock"))
}

fn driver_pid_path(ostk_dir: &Path, name: &str) -> PathBuf {
    drivers_dir(ostk_dir).join(format!("{name}.pid"))
}

fn driver_state_path(ostk_dir: &Path) -> PathBuf {
    ostk_dir.join("drivers.json")
}

/// Check if a process with the given PID is still alive.
fn process_alive(pid: u32) -> bool {
    unsafe { libc::kill(pid as i32, 0) == 0 }
}

/// Returns true if the server name indicates a persistent driver (fcp-* prefix).
fn is_persistent_driver(name: &str) -> bool {
    name.starts_with("fcp-")
}

// ---------------------------------------------------------------------------
// run_serve — socket-mode MCP server (→787)
// ---------------------------------------------------------------------------

/// Run an MCP server on a Unix domain socket instead of stdio.
///
/// Creates `.ostk/drivers/<name>.sock` and listens for connections.
/// Each connection gets one JSON-RPC request-response cycle relayed to
/// the underlying MCP subprocess (resolved from HUMANFILE mcp: section).
///
/// This is the entry point for `ostk mcp serve <name>`.
pub fn run_serve(name: &str) -> Result<(), String> {
    let root = crate::find_project_root()?;
    let ostk_dir = crate::state_dir(&root);
    let content = load_humanfile(&ostk_dir)?;
    let servers = parse_mcp_servers(&content);

    // →826: Fall back to DRIVER directives when mcp: section doesn't have the server.
    // DRIVER rust ostk mcp serve fcp-rust → run_serve("fcp-rust") needs to resolve
    // the actual binary. Convention: fcp-{name} binary for stdio proxy, or match the
    // DRIVER name (with/without fcp- prefix) and use the fcp-prefixed binary directly.
    let driver_fallback: Option<McpServer>;
    let srv = if let Some(s) = servers.iter().find(|s| s.name == name) {
        s
    } else {
        // Check directive-parsed HUMANFILE for a matching DRIVER
        let hf = crate::humanfile::load(&ostk_dir);
        let bare_name = name.strip_prefix("fcp-").unwrap_or(name);
        let found = hf.humanfile.drivers.iter().any(|(n, _)| {
            n == name || n == bare_name
        });
        if found {
            // The binary is the fcp-prefixed name itself (on PATH via stdio transport)
            driver_fallback = Some(McpServer {
                name: name.to_string(),
                command: name.to_string(),
                args: vec![],
            });
            driver_fallback.as_ref().unwrap()
        } else {
            return Err(format!("MCP server '{}' not configured in HUMANFILE", name));
        }
    };

    // Resolve authorized secrets — try directive-parsed secrets first, fall back to legacy
    let hf_result = crate::humanfile::load(&ostk_dir);
    let authorized_keys = if !hf_result.humanfile.secrets.is_empty() {
        hf_result.humanfile.secrets.clone()
    } else {
        parse_authorized_secrets(&content)
    };
    let mut env: HashMap<String, String> = HashMap::new();
    for key in &authorized_keys {
        if let Ok(val) = resolve_secret(key) {
            env.insert(key.clone(), val);
        }
    }

    // Ensure drivers directory exists
    let dir = drivers_dir(&ostk_dir);
    std::fs::create_dir_all(&dir)
        .map_err(|e| format!("failed to create drivers dir: {e}"))?;

    let sock_path = driver_sock_path(&ostk_dir, name);
    let pid_path = driver_pid_path(&ostk_dir, name);

    // Clean up stale socket
    let _ = std::fs::remove_file(&sock_path);

    // Spawn the MCP subprocess with stdin/stdout piped
    let mut child = Command::new(&srv.command)
        .args(&srv.args)
        .envs(&env)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|e| format!("failed to spawn MCP server '{}': {e}", name))?;

    let mut child_stdin = child.stdin.take().ok_or("failed to open server stdin")?;
    let child_stdout = child.stdout.take().ok_or("failed to open server stdout")?;
    let mut child_reader = BufReader::new(child_stdout);

    // Perform JSON-RPC initialize handshake
    driver_initialize(&mut child_stdin, &mut child_reader)?;

    // Write PID file
    let daemon_pid = std::process::id();
    std::fs::write(&pid_path, daemon_pid.to_string())
        .map_err(|e| format!("failed to write PID file: {e}"))?;

    // Bind Unix domain socket
    let listener = std::os::unix::net::UnixListener::bind(&sock_path)
        .map_err(|e| format!("failed to bind socket {}: {e}", sock_path.display()))?;

    // Restrict socket to owner-only
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(&sock_path, std::fs::Permissions::from_mode(0o600))
        .map_err(|e| format!("chmod socket: {e}"))?;

    eprintln!(
        "[ostk] mcp serve '{}': listening on {} (pid {})",
        name,
        sock_path.display(),
        daemon_pid
    );

    let next_id = AtomicU64::new(3);

    // Accept loop — one request-response per connection
    for stream in listener.incoming() {
        // Check if subprocess is still alive
        match child.try_wait() {
            Ok(Some(status)) => {
                eprintln!("[ostk] mcp serve '{}': subprocess exited ({status})", name);
                break;
            }
            Err(e) => {
                eprintln!("[ostk] mcp serve '{}': wait error: {e}", name);
                break;
            }
            Ok(None) => {} // still running
        }

        let mut stream = match stream {
            Ok(s) => s,
            Err(e) => {
                eprintln!("[ostk] mcp serve '{}': accept error: {e}", name);
                continue;
            }
        };

        // Read request from client
        let mut client_reader = BufReader::new(&stream);
        let mut request_line = String::new();
        if client_reader.read_line(&mut request_line).is_err() {
            continue;
        }

        let request_line = request_line.trim();
        if request_line.is_empty() {
            continue;
        }

        // Parse client request, rewrite ID for subprocess
        let client_req: Value = match serde_json::from_str(request_line) {
            Ok(v) => v,
            Err(e) => {
                let err_resp = json!({
                    "jsonrpc": "2.0",
                    "id": null,
                    "error": {"code": -32700, "message": format!("parse error: {e}")}
                });
                let _ = writeln!(stream, "{}", serde_json::to_string(&err_resp).unwrap());
                continue;
            }
        };

        let client_id = client_req.get("id").cloned().unwrap_or(Value::Null);
        let rpc_id = next_id.fetch_add(1, std::sync::atomic::Ordering::Relaxed);

        let mut relay_req = client_req.clone();
        relay_req["id"] = json!(rpc_id);

        // Send to subprocess
        let relay_str = serde_json::to_string(&relay_req).unwrap();
        if child_stdin.write_all(relay_str.as_bytes()).is_err()
            || child_stdin.write_all(b"\n").is_err()
            || child_stdin.flush().is_err()
        {
            let err_resp = json!({
                "jsonrpc": "2.0",
                "id": client_id,
                "error": {"code": -32603, "message": "failed to write to driver subprocess"}
            });
            let _ = writeln!(stream, "{}", serde_json::to_string(&err_resp).unwrap());
            break;
        }

        // Read response from subprocess
        let response = match read_subprocess_response(&mut child_reader, rpc_id) {
            Ok(mut resp) => {
                resp["id"] = client_id;
                resp
            }
            Err(e) => {
                json!({
                    "jsonrpc": "2.0",
                    "id": client_id,
                    "error": {"code": -32603, "message": format!("driver read error: {e}")}
                })
            }
        };

        let resp_str = serde_json::to_string(&response).unwrap();
        let _ = writeln!(stream, "{}", resp_str);
        let _ = stream.flush();
    }

    // Cleanup
    let _ = child.kill();
    let _ = child.wait();
    let _ = std::fs::remove_file(&sock_path);
    let _ = std::fs::remove_file(&pid_path);
    eprintln!("[ostk] mcp serve '{}': exiting", name);

    Ok(())
}

// ---------------------------------------------------------------------------
// run_call — main entry point
// ---------------------------------------------------------------------------

/// Call a tool on an MCP server.
///
/// For `fcp-*` drivers: connects to a persistent subprocess via Unix socket,
/// spawning the relay daemon if not already running. State persists across calls.
///
/// For other servers: spawns a fresh process per call (original behavior).
pub fn run_call(server: &str, tool: &str, args: &[String]) -> Result<(), String> {
    let root = crate::find_project_root()?;
    let ostk_dir = crate::state_dir(&root);
    let content = load_humanfile(&ostk_dir)?;
    let servers = parse_mcp_servers(&content);

    let srv = servers
        .iter()
        .find(|s| s.name == server)
        .ok_or_else(|| format!("MCP server '{}' not configured in HUMANFILE", server))?;

    // Parse key=value args into JSON object
    let arguments = parse_args(args)?;

    // Resolve authorized secrets and inject as env vars
    let authorized_keys = parse_authorized_secrets(&content);
    let mut env: HashMap<String, String> = HashMap::new();
    for key in &authorized_keys {
        if let Ok(val) = resolve_secret(key) {
            env.insert(key.clone(), val);
        }
    }

    if is_persistent_driver(server) {
        run_persistent_call(&ostk_dir, server, srv, tool, &arguments, &env)
    } else {
        run_ephemeral_call(srv, tool, &arguments, &env)
    }
}

// ---------------------------------------------------------------------------
// Persistent driver calls (→751)
// ---------------------------------------------------------------------------

/// Connect to a running driver daemon or spawn one, then send the tool call.
fn run_persistent_call(
    ostk_dir: &Path,
    name: &str,
    srv: &McpServer,
    tool: &str,
    arguments: &Value,
    env: &HashMap<String, String>,
) -> Result<(), String> {
    let sock = driver_sock_path(ostk_dir, name);
    let pid_file = driver_pid_path(ostk_dir, name);

    // Check if daemon is already running
    let daemon_alive = (if pid_file
        .exists() { {
            std::fs::read_to_string(&pid_file)
                .ok()
                .and_then(|s| s.trim().parse::<u32>().ok())
                .map(process_alive)
                .unwrap_or(false)
        } } else { false })
        && sock.exists();

    if !daemon_alive {
        // Clean up stale socket/pid files
        let _ = std::fs::remove_file(&sock);
        let _ = std::fs::remove_file(&pid_file);

        // Spawn the relay daemon as a detached child process
        spawn_relay_daemon(ostk_dir, name, srv, env)?;

        // Wait for socket to appear (max 10s, poll every 50ms)
        let start = std::time::Instant::now();
        let timeout = std::time::Duration::from_secs(10);
        loop {
            if sock.exists() {
                // Try connecting — socket file may exist before accept() is ready
                if UnixStream::connect(&sock).is_ok() {
                    break;
                }
            }
            if start.elapsed() > timeout {
                return Err(format!(
                    "driver daemon for '{}' did not start within 10s (socket: {})",
                    name,
                    sock.display()
                ));
            }
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
    }

    // Connect to daemon and send the tool call
    let mut stream = UnixStream::connect(&sock)
        .map_err(|e| format!("failed to connect to driver '{}': {e}", name))?;

    // Set read/write timeouts
    stream
        .set_read_timeout(Some(std::time::Duration::from_secs(60)))
        .map_err(|e| format!("failed to set read timeout: {e}"))?;
    stream
        .set_write_timeout(Some(std::time::Duration::from_secs(10)))
        .map_err(|e| format!("failed to set write timeout: {e}"))?;

    // Send the tool call request as a single JSON line
    let call_req = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool,
            "arguments": arguments
        }
    });

    let serialized = serde_json::to_string(&call_req)
        .map_err(|e| format!("failed to serialize request: {e}"))?;
    stream
        .write_all(serialized.as_bytes())
        .map_err(|e| format!("failed to write to driver socket: {e}"))?;
    stream
        .write_all(b"\n")
        .map_err(|e| format!("failed to write newline to driver socket: {e}"))?;
    stream
        .flush()
        .map_err(|e| format!("failed to flush driver socket: {e}"))?;

    // Read the response (single JSON line)
    let mut reader = BufReader::new(&stream);
    let mut line = String::new();
    reader
        .read_line(&mut line)
        .map_err(|e| format!("failed to read from driver socket: {e}"))?;

    if line.trim().is_empty() {
        return Err(format!("driver '{}' returned empty response", name));
    }

    let resp: Value = serde_json::from_str(line.trim())
        .map_err(|e| format!("invalid JSON from driver '{}': {e}", name))?;

    if let Some(error) = resp.get("error") {
        return Err(format!("driver tool call failed: {}", error));
    }

    if let Some(result) = resp.get("result") {
        print_result(result);
    }

    Ok(())
}

/// Spawn the relay daemon as a detached child process.
///
/// The daemon:
/// 1. Spawns the MCP subprocess with stdin/stdout piped
/// 2. Performs the JSON-RPC initialize handshake
/// 3. Listens on a Unix domain socket
/// 4. For each connection: reads one request, relays to subprocess, returns response
/// 5. Exits when subprocess dies
fn spawn_relay_daemon(
    ostk_dir: &Path,
    name: &str,
    srv: &McpServer,
    env: &HashMap<String, String>,
) -> Result<(), String> {
    // Ensure drivers directory exists
    let dir = drivers_dir(ostk_dir);
    std::fs::create_dir_all(&dir)
        .map_err(|e| format!("failed to create drivers dir: {e}"))?;

    let sock_path = driver_sock_path(ostk_dir, name);
    let pid_path = driver_pid_path(ostk_dir, name);
    let state_path = driver_state_path(ostk_dir);
    let driver_name = name.to_string();

    // Build the daemon command — re-exec ourselves with a hidden subcommand
    // Actually, fork directly and run the daemon in the child process.
    // Using fork avoids needing to serialize all state to args.

    let command = srv.command.clone();
    let args = srv.args.clone();
    let env = env.clone();

    // Fork a child process for the daemon
    match unsafe { libc::fork() } {
        -1 => {
            Err(format!(
                "fork failed: {}",
                std::io::Error::last_os_error()
            ))
        }
        0 => {
            // ── Child process (daemon) ──

            // Detach from parent session
            unsafe {
                libc::setsid();
            }

            // Spawn the MCP subprocess
            let mut child = match Command::new(&command)
                .args(&args)
                .envs(&env)
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::null())
                .spawn()
            {
                Ok(c) => c,
                Err(e) => {
                    eprintln!("[ostk] driver daemon: failed to spawn '{}': {e}", driver_name);
                    std::process::exit(1);
                }
            };

            let child_pid = child.id();
            let mut child_stdin = child.stdin.take().expect("stdin piped");
            let child_stdout = child.stdout.take().expect("stdout piped");
            let mut child_reader = BufReader::new(child_stdout);

            // Perform JSON-RPC initialize handshake
            if let Err(e) = driver_initialize(&mut child_stdin, &mut child_reader) {
                eprintln!("[ostk] driver daemon: initialize failed for '{}': {e}", driver_name);
                let _ = child.kill();
                std::process::exit(1);
            }

            // Write PID file
            let daemon_pid = std::process::id();
            if let Err(e) = std::fs::write(&pid_path, daemon_pid.to_string()) {
                eprintln!("[ostk] driver daemon: failed to write PID file: {e}");
                let _ = child.kill();
                std::process::exit(1);
            }

            // Write state to drivers.json
            let _ = write_driver_state(&state_path, &driver_name, daemon_pid, child_pid);

            // Clean up stale socket
            let _ = std::fs::remove_file(&sock_path);

            // Bind Unix domain socket
            let listener = match std::os::unix::net::UnixListener::bind(&sock_path) {
                Ok(l) => l,
                Err(e) => {
                    eprintln!("[ostk] driver daemon: failed to bind socket: {e}");
                    let _ = child.kill();
                    std::process::exit(1);
                }
            };

            // Restrict socket to owner-only (security: prevent other users from connecting)
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&sock_path, std::fs::Permissions::from_mode(0o600))
                .map_err(|e| format!("chmod socket: {e}"))?;

            eprintln!(
                "[ostk] driver daemon '{}' started: daemon_pid={} child_pid={} sock={}",
                driver_name, daemon_pid, child_pid, sock_path.display()
            );

            // Use an atomic counter for JSON-RPC request IDs (we already used 1 and 2 in init)
            let next_id = AtomicU64::new(3);

            // Set a non-blocking accept timeout so we can check if subprocess is alive
            listener
                .set_nonblocking(false)
                .expect("set_nonblocking");

            // Accept connections and relay requests
            // Each connection = one request-response cycle
            for stream in listener.incoming() {
                // Check if subprocess is still alive
                match child.try_wait() {
                    Ok(Some(status)) => {
                        eprintln!(
                            "[ostk] driver daemon '{}': subprocess exited ({status}), shutting down",
                            driver_name
                        );
                        break;
                    }
                    Err(e) => {
                        eprintln!(
                            "[ostk] driver daemon '{}': wait error: {e}, shutting down",
                            driver_name
                        );
                        break;
                    }
                    Ok(None) => {} // still running
                }

                let mut stream = match stream {
                    Ok(s) => s,
                    Err(e) => {
                        eprintln!("[ostk] driver daemon '{}': accept error: {e}", driver_name);
                        continue;
                    }
                };

                // Read request from client
                let mut client_reader = BufReader::new(&stream);
                let mut request_line = String::new();
                if client_reader.read_line(&mut request_line).is_err() {
                    continue;
                }

                let request_line = request_line.trim();
                if request_line.is_empty() {
                    continue;
                }

                // Parse client request, rewrite the ID for the subprocess
                let client_req: Value = match serde_json::from_str(request_line) {
                    Ok(v) => v,
                    Err(e) => {
                        let err_resp = json!({
                            "jsonrpc": "2.0",
                            "id": null,
                            "error": {"code": -32700, "message": format!("parse error: {e}")}
                        });
                        let _ = writeln!(stream, "{}", serde_json::to_string(&err_resp).unwrap());
                        continue;
                    }
                };

                let client_id = client_req.get("id").cloned().unwrap_or(Value::Null);
                let rpc_id = next_id.fetch_add(1, std::sync::atomic::Ordering::Relaxed);

                // Rewrite request with our sequential ID
                let mut relay_req = client_req.clone();
                relay_req["id"] = json!(rpc_id);

                // Send to subprocess stdin
                let relay_str = serde_json::to_string(&relay_req).unwrap();
                if child_stdin.write_all(relay_str.as_bytes()).is_err()
                    || child_stdin.write_all(b"\n").is_err()
                    || child_stdin.flush().is_err()
                {
                    let err_resp = json!({
                        "jsonrpc": "2.0",
                        "id": client_id,
                        "error": {"code": -32603, "message": "failed to write to driver subprocess"}
                    });
                    let _ = writeln!(stream, "{}", serde_json::to_string(&err_resp).unwrap());
                    break; // subprocess likely dead
                }

                // Read response from subprocess (skip notifications)
                let response = match read_subprocess_response(&mut child_reader, rpc_id) {
                    Ok(mut resp) => {
                        // Rewrite response ID back to client's original ID
                        resp["id"] = client_id;
                        resp
                    }
                    Err(e) => {
                        json!({
                            "jsonrpc": "2.0",
                            "id": client_id,
                            "error": {"code": -32603, "message": format!("driver read error: {e}")}
                        })
                    }
                };

                let resp_str = serde_json::to_string(&response).unwrap();
                let _ = writeln!(stream, "{}", resp_str);
                let _ = stream.flush();
            }

            // Cleanup
            let _ = child.kill();
            let _ = child.wait();
            let _ = std::fs::remove_file(&sock_path);
            let _ = std::fs::remove_file(&pid_path);
            eprintln!("[ostk] driver daemon '{}' exiting", driver_name);
            std::process::exit(0);
        }
        _parent_pid => {
            // ── Parent process ──
            // Daemon is spawning in the background, return to caller.
            // The caller will poll for the socket to appear.
            Ok(())
        }
    }
}

/// Perform the JSON-RPC initialize handshake with the MCP subprocess.
fn driver_initialize(
    stdin: &mut std::process::ChildStdin,
    reader: &mut BufReader<std::process::ChildStdout>,
) -> Result<(), String> {
    // 1. Send initialize
    let init_req = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "ostk",
                "version": env!("CARGO_PKG_VERSION")
            }
        }
    });
    send_message_child(stdin, &init_req)?;

    // Read initialize response
    let init_resp = read_message_child(reader)?;
    if init_resp.get("error").is_some() {
        return Err(format!("MCP initialize failed: {}", init_resp["error"]));
    }

    // 2. Send initialized notification
    let notif = json!({
        "jsonrpc": "2.0",
        "method": "notifications/initialized"
    });
    send_message_child(stdin, &notif)?;

    Ok(())
}

/// Read a response from the subprocess, matching the expected ID.
/// Skips notifications (no "id" field) and responses with wrong IDs.
fn read_subprocess_response(
    reader: &mut BufReader<std::process::ChildStdout>,
    expected_id: u64,
) -> Result<Value, String> {
    loop {
        let mut line = String::new();
        let bytes_read = reader
            .read_line(&mut line)
            .map_err(|e| format!("read error: {e}"))?;

        if bytes_read == 0 {
            return Err("subprocess closed stdout".into());
        }

        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }

        let parsed: Value = serde_json::from_str(trimmed)
            .map_err(|e| format!("invalid JSON: {e}"))?;

        // Skip notifications (no "id")
        if parsed.get("id").is_none() || parsed["id"].is_null() {
            continue;
        }

        // Check if this is the response we expect
        if parsed["id"].as_u64() == Some(expected_id) {
            return Ok(parsed);
        }

        // Wrong ID — skip (shouldn't happen in normal flow but be defensive)
    }
}

/// Send a JSON-RPC message to a child process stdin.
fn send_message_child(stdin: &mut std::process::ChildStdin, msg: &Value) -> Result<(), String> {
    let serialized = serde_json::to_string(msg)
        .map_err(|e| format!("serialize error: {e}"))?;
    stdin
        .write_all(serialized.as_bytes())
        .map_err(|e| format!("write error: {e}"))?;
    stdin
        .write_all(b"\n")
        .map_err(|e| format!("write newline error: {e}"))?;
    stdin.flush().map_err(|e| format!("flush error: {e}"))?;
    Ok(())
}

/// Read a JSON-RPC response from a child process stdout.
fn read_message_child(reader: &mut BufReader<std::process::ChildStdout>) -> Result<Value, String> {
    loop {
        let mut line = String::new();
        let bytes_read = reader
            .read_line(&mut line)
            .map_err(|e| format!("read error: {e}"))?;

        if bytes_read == 0 {
            return Err("subprocess closed stdout unexpectedly".into());
        }

        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }

        let parsed: Value = serde_json::from_str(trimmed)
            .map_err(|e| format!("invalid JSON from subprocess: {e}"))?;

        if parsed.get("id").is_some() && !parsed["id"].is_null() {
            return Ok(parsed);
        }
    }
}

/// Write driver state to `.ostk/drivers.json`.
fn write_driver_state(
    state_path: &Path,
    name: &str,
    daemon_pid: u32,
    child_pid: u32,
) -> Result<(), String> {
    // Read existing state or start fresh
    let mut state: Value = state_path
        .exists()
        .then(|| {
            std::fs::read_to_string(state_path)
                .ok()
                .and_then(|s| serde_json::from_str(&s).ok())
        })
        .flatten()
        .unwrap_or_else(|| json!({}));

    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);

    state[name] = json!({
        "daemon_pid": daemon_pid,
        "child_pid": child_pid,
        "started": now
    });

    std::fs::write(state_path, serde_json::to_string_pretty(&state).unwrap())
        .map_err(|e| format!("failed to write drivers.json: {e}"))
}

// ---------------------------------------------------------------------------
// Ephemeral calls (original behavior for non-fcp-* servers)
// ---------------------------------------------------------------------------

/// Original per-call spawn behavior for non-persistent MCP servers.
fn run_ephemeral_call(
    srv: &McpServer,
    tool: &str,
    arguments: &Value,
    env: &HashMap<String, String>,
) -> Result<(), String> {
    // Spawn MCP server process
    let mut child = Command::new(&srv.command)
        .args(&srv.args)
        .envs(env)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("failed to spawn MCP server '{}': {}", srv.name, e))?;

    let child_stdin = child.stdin.take().ok_or("failed to open server stdin")?;
    let child_stdout = child.stdout.take().ok_or("failed to open server stdout")?;

    let result = run_jsonrpc_session(child_stdin, child_stdout, tool, arguments);

    // Always try to kill the child on exit
    let _ = child.kill();
    let _ = child.wait();

    result
}

/// Run the JSON-RPC session: initialize, call tool, print result.
fn run_jsonrpc_session(
    mut stdin: std::process::ChildStdin,
    stdout: std::process::ChildStdout,
    tool: &str,
    arguments: &Value,
) -> Result<(), String> {
    let mut reader = BufReader::new(stdout);

    // 1. Send initialize
    let init_req = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "ostk",
                "version": env!("CARGO_PKG_VERSION")
            }
        }
    });
    send_message(&mut stdin, &init_req)?;

    // Read initialize response
    let init_resp = read_message(&mut reader)?;
    if init_resp.get("error").is_some() {
        return Err(format!(
            "MCP initialize failed: {}",
            init_resp["error"]
        ));
    }

    // 2. Send initialized notification
    let initialized_notif = json!({
        "jsonrpc": "2.0",
        "method": "notifications/initialized"
    });
    send_message(&mut stdin, &initialized_notif)?;

    // 3. Call the tool
    let call_req = json!({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": tool,
            "arguments": arguments
        }
    });
    send_message(&mut stdin, &call_req)?;

    // Read tool call response
    let call_resp = read_message(&mut reader)?;

    if let Some(error) = call_resp.get("error") {
        return Err(format!("MCP tool call failed: {}", error));
    }

    // Extract and print result content
    if let Some(result) = call_resp.get("result") {
        print_result(result);
    }

    Ok(())
}

/// Send a JSON-RPC message (line-delimited) to the server's stdin.
fn send_message(stdin: &mut std::process::ChildStdin, msg: &Value) -> Result<(), String> {
    let serialized = serde_json::to_string(msg)
        .map_err(|e| format!("failed to serialize JSON-RPC message: {e}"))?;
    stdin
        .write_all(serialized.as_bytes())
        .map_err(|e| format!("failed to write to MCP server: {e}"))?;
    stdin
        .write_all(b"\n")
        .map_err(|e| format!("failed to write newline: {e}"))?;
    stdin
        .flush()
        .map_err(|e| format!("failed to flush MCP server stdin: {e}"))?;
    Ok(())
}

/// Read a JSON-RPC response (line-delimited) from the server's stdout.
/// Skips notification messages (no "id" field) to find the next response.
fn read_message(reader: &mut BufReader<std::process::ChildStdout>) -> Result<Value, String> {
    loop {
        let mut line = String::new();
        let bytes_read = reader
            .read_line(&mut line)
            .map_err(|e| format!("failed to read from MCP server: {e}"))?;

        if bytes_read == 0 {
            return Err("MCP server closed stdout unexpectedly".into());
        }

        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }

        let parsed: Value = serde_json::from_str(trimmed)
            .map_err(|e| format!("invalid JSON from MCP server: {e}"))?;

        // Skip notifications (messages without an "id" field)
        if parsed.get("id").is_some() {
            return Ok(parsed);
        }
    }
}

/// Print the result content from a tools/call response.
fn print_result(result: &Value) {
    // MCP tools/call result has a "content" array with typed entries
    if let Some(content) = result.get("content").and_then(|c| c.as_array()) {
        for item in content {
            match item.get("type").and_then(|t| t.as_str()) {
                Some("text") => {
                    if let Some(text) = item.get("text").and_then(|t| t.as_str()) {
                        println!("{}", text);
                    }
                }
                Some(other) => {
                    // For non-text content, dump as compact JSON
                    println!("[{}] {}", other, item);
                }
                None => {
                    println!("{}", item);
                }
            }
        }
    } else {
        // Fallback: print the whole result as pretty JSON
        if let Ok(pretty) = serde_json::to_string_pretty(result) {
            println!("{}", pretty);
        }
    }
}

/// Parse key=value argument pairs into a JSON object.
fn parse_args(args: &[String]) -> Result<Value, String> {
    let mut map = serde_json::Map::new();
    for arg in args {
        let (key, value) = arg
            .split_once('=')
            .ok_or_else(|| format!("invalid argument '{}': expected key=value", arg))?;

        // Try to parse value as JSON first (for numbers, booleans, arrays)
        let json_value = match serde_json::from_str::<Value>(value) {
            Ok(v) => v,
            Err(_) => Value::String(value.to_string()),
        };

        map.insert(key.to_string(), json_value);
    }
    Ok(Value::Object(map))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_mcp_servers() {
        let content = r#"# HUMANFILE

mcp:
  linear: npx -y @anthropic/linear-mcp-server
  github: gh mcp-server

secrets:
  ANTHROPIC_API_KEY: authorized
"#;
        let servers = parse_mcp_servers(content);
        assert_eq!(servers.len(), 2);

        assert_eq!(servers[0].name, "linear");
        assert_eq!(servers[0].command, "npx");
        assert_eq!(servers[0].args, vec!["-y", "@anthropic/linear-mcp-server"]);

        assert_eq!(servers[1].name, "github");
        assert_eq!(servers[1].command, "gh");
        assert_eq!(servers[1].args, vec!["mcp-server"]);
    }

    #[test]
    fn test_parse_mcp_servers_empty() {
        let content = "# HUMANFILE\nno mcp section here\n";
        let servers = parse_mcp_servers(content);
        assert!(servers.is_empty());
    }

    #[test]
    fn test_parse_mcp_servers_with_comments() {
        let content = "mcp:\n  # this is a comment\n  linear: npx -y @linear/server\n";
        let servers = parse_mcp_servers(content);
        assert_eq!(servers.len(), 1);
        assert_eq!(servers[0].name, "linear");
    }

    #[test]
    fn test_parse_authorized_secrets() {
        let content = r#"
secrets:
  LINEAR_API_KEY: authorized
  GITHUB_TOKEN: authorized

other stuff
"#;
        let keys = parse_authorized_secrets(content);
        assert_eq!(keys, vec!["LINEAR_API_KEY", "GITHUB_TOKEN"]);
    }

    #[test]
    fn test_parse_args_simple() {
        let args = vec!["team=engineering".to_string(), "limit=10".to_string()];
        let result = parse_args(&args).unwrap();
        assert_eq!(result["team"], "engineering");
        assert_eq!(result["limit"], 10); // parsed as number
    }

    #[test]
    fn test_parse_args_empty() {
        let args: Vec<String> = vec![];
        let result = parse_args(&args).unwrap();
        assert_eq!(result, json!({}));
    }

    #[test]
    fn test_parse_args_json_values() {
        let args = vec![
            "active=true".to_string(),
            "tags=[\"a\",\"b\"]".to_string(),
        ];
        let result = parse_args(&args).unwrap();
        assert_eq!(result["active"], true);
        assert_eq!(result["tags"], json!(["a", "b"]));
    }

    #[test]
    fn test_parse_args_invalid() {
        let args = vec!["no-equals-sign".to_string()];
        assert!(parse_args(&args).is_err());
    }

    // ── Persistent driver detection ──

    #[test]
    fn test_is_persistent_driver() {
        assert!(is_persistent_driver("fcp-rust"));
        assert!(is_persistent_driver("fcp-k8s"));
        assert!(is_persistent_driver("fcp-screen"));
        assert!(!is_persistent_driver("linear"));
        assert!(!is_persistent_driver("github"));
        assert!(!is_persistent_driver("fcprust")); // no hyphen
    }

    // ── Driver path helpers ──

    #[test]
    fn test_driver_paths() {
        let hs = PathBuf::from("/tmp/.ostk");
        assert_eq!(
            driver_sock_path(&hs, "fcp-rust"),
            PathBuf::from("/tmp/.ostk/drivers/fcp-rust.sock")
        );
        assert_eq!(
            driver_pid_path(&hs, "fcp-rust"),
            PathBuf::from("/tmp/.ostk/drivers/fcp-rust.pid")
        );
        assert_eq!(
            driver_state_path(&hs),
            PathBuf::from("/tmp/.ostk/drivers.json")
        );
    }

    // ── Driver state file ──

    #[test]
    fn test_write_driver_state() {
        let dir = std::env::temp_dir().join("ostk_test_driver_state");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();

        let state_path = dir.join("drivers.json");
        write_driver_state(&state_path, "fcp-rust", 1234, 5678).unwrap();

        let content = std::fs::read_to_string(&state_path).unwrap();
        let parsed: Value = serde_json::from_str(&content).unwrap();
        assert_eq!(parsed["fcp-rust"]["daemon_pid"], 1234);
        assert_eq!(parsed["fcp-rust"]["child_pid"], 5678);
        assert!(parsed["fcp-rust"]["started"].as_u64().unwrap() > 0);

        // Writing a second driver should merge, not overwrite
        write_driver_state(&state_path, "fcp-k8s", 9999, 8888).unwrap();
        let content2 = std::fs::read_to_string(&state_path).unwrap();
        let parsed2: Value = serde_json::from_str(&content2).unwrap();
        assert_eq!(parsed2["fcp-rust"]["daemon_pid"], 1234);
        assert_eq!(parsed2["fcp-k8s"]["daemon_pid"], 9999);

        let _ = std::fs::remove_dir_all(&dir);
    }

    // ── Unix socket relay integration test ──

    #[test]
    fn test_relay_daemon_echo_server() {
        // This test creates a mock "MCP server" (a simple echo script),
        // spawns the relay daemon, connects via socket, and verifies
        // the response round-trips correctly.

        use std::io::{BufRead, BufReader, Write};

        let dir = std::env::temp_dir().join("ostk_test_relay_daemon");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("drivers")).unwrap();

        // Create a mock MCP server script that:
        // 1. Reads initialize, responds with success
        // 2. Reads initialized notification (no response)
        // 3. For each subsequent request, echoes back a success response
        let script_path = dir.join("mock_mcp.sh");
        std::fs::write(
            &script_path,
            r#"#!/bin/bash
# Mock MCP server — reads JSON-RPC lines, responds appropriately
while IFS= read -r line; do
    method=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('method',''))" 2>/dev/null)
    id=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d.get('id')))" 2>/dev/null)

    if [ "$method" = "initialize" ]; then
        echo "{\"jsonrpc\":\"2.0\",\"id\":${id},\"result\":{\"protocolVersion\":\"2024-11-05\",\"capabilities\":{},\"serverInfo\":{\"name\":\"mock\",\"version\":\"0.1\"}}}"
    elif [ "$method" = "notifications/initialized" ]; then
        : # no response for notifications
    elif [ "$method" = "tools/call" ]; then
        echo "{\"jsonrpc\":\"2.0\",\"id\":${id},\"result\":{\"content\":[{\"type\":\"text\",\"text\":\"150 files, 128 symbols\"}]}}"
    fi
done
"#,
        )
        .unwrap();

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&script_path, std::fs::Permissions::from_mode(0o755))
                .unwrap();
        }

        // Verify the script works standalone first
        let mut test_child = Command::new("bash")
            .arg(&script_path)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();

        let mut test_stdin = test_child.stdin.take().unwrap();
        let test_stdout = test_child.stdout.take().unwrap();
        let mut test_reader = BufReader::new(test_stdout);

        // Send init
        writeln!(
            test_stdin,
            r#"{{"jsonrpc":"2.0","id":1,"method":"initialize","params":{{"protocolVersion":"2024-11-05"}}}}"#
        )
        .unwrap();
        test_stdin.flush().unwrap();

        let mut resp = String::new();
        test_reader.read_line(&mut resp).unwrap();
        assert!(
            resp.contains("mock"),
            "mock script should respond: {resp}"
        );

        let _ = test_child.kill();
        let _ = test_child.wait();

        // Now test through the relay daemon
        let sock_path = dir.join("drivers").join("fcp-test.sock");
        let pid_path = dir.join("drivers").join("fcp-test.pid");
        let state_path = dir.join("drivers.json");

        let srv = McpServer {
            name: "fcp-test".to_string(),
            command: "bash".to_string(),
            args: vec![script_path.to_string_lossy().to_string()],
        };

        // Spawn relay daemon
        spawn_relay_daemon(&dir, "fcp-test", &srv, &HashMap::new()).unwrap();

        // Wait for socket
        let start = std::time::Instant::now();
        while !sock_path.exists() || UnixStream::connect(&sock_path).is_err() {
            if start.elapsed() > std::time::Duration::from_secs(5) {
                panic!("relay daemon socket did not appear in 5s");
            }
            std::thread::sleep(std::time::Duration::from_millis(50));
        }

        // First call
        let mut stream = UnixStream::connect(&sock_path).unwrap();
        stream
            .set_read_timeout(Some(std::time::Duration::from_secs(5)))
            .unwrap();
        let req = json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "rust_session", "arguments": {"action": "open /tmp"}}
        });
        writeln!(stream, "{}", serde_json::to_string(&req).unwrap()).unwrap();
        stream.flush().unwrap();

        let mut reader = BufReader::new(&stream);
        let mut line = String::new();
        reader.read_line(&mut line).unwrap();
        let resp1: Value = serde_json::from_str(line.trim()).unwrap();
        assert_eq!(resp1["id"], 1, "response ID should match client request");
        assert!(
            resp1["result"]["content"][0]["text"]
                .as_str()
                .unwrap()
                .contains("150 files"),
            "first call should return data: {resp1}"
        );

        // Second call — same daemon, same subprocess
        let mut stream2 = UnixStream::connect(&sock_path).unwrap();
        stream2
            .set_read_timeout(Some(std::time::Duration::from_secs(5)))
            .unwrap();
        let req2 = json!({
            "jsonrpc": "2.0",
            "id": 42,
            "method": "tools/call",
            "params": {"name": "rust_query", "arguments": {"input": "map"}}
        });
        writeln!(stream2, "{}", serde_json::to_string(&req2).unwrap()).unwrap();
        stream2.flush().unwrap();

        let mut reader2 = BufReader::new(&stream2);
        let mut line2 = String::new();
        reader2.read_line(&mut line2).unwrap();
        let resp2: Value = serde_json::from_str(line2.trim()).unwrap();
        assert_eq!(resp2["id"], 42, "response ID should match client request");
        assert!(
            resp2["result"]["content"][0]["text"]
                .as_str()
                .unwrap()
                .contains("150 files"),
            "second call should also return data (same subprocess): {resp2}"
        );

        // Verify PID file exists and daemon is alive
        assert!(pid_path.exists(), "PID file should exist");
        let pid: u32 = std::fs::read_to_string(&pid_path)
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        assert!(process_alive(pid), "daemon should still be alive");

        // Verify drivers.json was written
        assert!(state_path.exists(), "drivers.json should exist");
        let state: Value =
            serde_json::from_str(&std::fs::read_to_string(&state_path).unwrap()).unwrap();
        assert!(
            state["fcp-test"]["daemon_pid"].as_u64().unwrap() > 0,
            "drivers.json should contain daemon_pid"
        );

        // Clean up: kill the daemon
        unsafe {
            libc::kill(pid as i32, libc::SIGTERM);
        }
        std::thread::sleep(std::time::Duration::from_millis(200));

        let _ = std::fs::remove_dir_all(&dir);
    }

    // ── process_alive ──

    #[test]
    fn test_process_alive_self() {
        assert!(process_alive(std::process::id()));
    }

    #[test]
    fn test_process_alive_invalid() {
        // PID 99999999 is almost certainly not running
        assert!(!process_alive(99_999_999));
    }

    // →797: Socket path construction tests for run_serve wiring

    #[test]
    fn test_driver_sock_path_matches_convention() {
        // run_serve() uses driver_sock_path() — verify it follows
        // the .ostk/drivers/<name>.sock convention
        let hs = PathBuf::from("/project/.ostk");
        assert_eq!(
            driver_sock_path(&hs, "fcp-rust"),
            PathBuf::from("/project/.ostk/drivers/fcp-rust.sock")
        );
        assert_eq!(
            driver_sock_path(&hs, "fcp-python"),
            PathBuf::from("/project/.ostk/drivers/fcp-python.sock")
        );
        assert_eq!(
            driver_sock_path(&hs, "fcp-screen"),
            PathBuf::from("/project/.ostk/drivers/fcp-screen.sock")
        );
    }

    #[test]
    fn test_driver_pid_path_matches_convention() {
        let hs = PathBuf::from("/project/.ostk");
        assert_eq!(
            driver_pid_path(&hs, "fcp-rust"),
            PathBuf::from("/project/.ostk/drivers/fcp-rust.pid")
        );
    }

    #[test]
    fn test_drivers_dir_canonical() {
        let hs = PathBuf::from("/project/.ostk");
        assert_eq!(
            drivers_dir(&hs),
            PathBuf::from("/project/.ostk/drivers")
        );
    }

    #[test]
    fn test_driver_sock_path_with_non_fcp_name() {
        // Non-fcp names should still get valid paths
        let hs = PathBuf::from("/project/.ostk");
        assert_eq!(
            driver_sock_path(&hs, "linear"),
            PathBuf::from("/project/.ostk/drivers/linear.sock")
        );
    }
}
