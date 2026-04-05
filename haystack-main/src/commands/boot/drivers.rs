//! Driver spawning for `ostk boot`.
//!
//! Handles spawning external FCP drivers declared in HUMANFILE and
//! registering them in the driver registry.

use std::fs;
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::process::Command;

// ── HUMANFILE driver spawning (->787, ->826) ───────────────────────────────

/// ->826: Spawn drivers from the merged Humanfile struct.
///
/// Accepts pre-parsed driver tuples (name, command) from the canonical
/// Humanfile (directive parser) rather than re-parsing the file.
pub(super) fn spawn_humanfile_drivers_from(ostk_dir: &Path, humanfile_drivers: &[(String, String)]) {
    // Always register fcp-screen as internal display driver
    let root = ostk_dir.parent().unwrap_or(ostk_dir);
    let _ = crate::language::register_capability(
        root,
        "fcp-screen",
        "device",
        "internal",
        "(event) -> (render)",
        "display driver",
        true,
    );

    if !humanfile_drivers.is_empty() {
        let drivers: Vec<(String, String)> = humanfile_drivers.to_vec();
        spawn_drivers(ostk_dir, &drivers);
    }
}

/// Internal: spawn a list of drivers and register them.
fn spawn_drivers(ostk_dir: &Path, drivers: &[(String, String)]) {
    if drivers.is_empty() {
        return;
    }

    let drivers_dir = ostk_dir.join("drivers");
    let _ = fs::create_dir_all(&drivers_dir);

    let mut registry = crate::kernel::drivers::DriverRegistry::new();
    registry.register_internal_screen();
    registry.register_internal_llm();

    let root = ostk_dir.parent().unwrap_or(ostk_dir);

    for (name, cmd_str) in drivers {
        // Determine the canonical driver name (fcp-prefixed)
        let fcp_name = if name.starts_with("fcp-") {
            name.clone()
        } else {
            format!("fcp-{name}")
        };

        // Use fcp-prefixed name for socket path — the MCP server creates fcp-{name}.sock
        let sock_path = drivers_dir.join(format!("{fcp_name}.sock"));
        let log_path = drivers_dir.join(format!("{name}.log"));
        let pid_path = drivers_dir.join(format!("{name}.pid"));
        // The relay daemon (ostk mcp serve) writes {fcp_name}.pid, boot writes {name}.pid.
        // Check both to avoid spawning duplicates.
        let fcp_pid_path = drivers_dir.join(format!("{fcp_name}.pid"));

        // Check if already alive — check both PID files (boot vs relay naming).
        let mut found_alive = false;
        for check_path in [&pid_path, &fcp_pid_path] {
            if let Ok(pid_str) = fs::read_to_string(check_path)
                && let Ok(pid) = pid_str.trim().parse::<u32>() {
                    let alive = unsafe { libc::kill(pid as i32, 0) } == 0;
                    if alive {
                        registry.register(crate::kernel::drivers::DriverEntry {
                            handler_name: name.clone(),
                            extensions: extensions_for_driver(name),
                            capabilities: vec!["query".into(), "session".into()],
                            mode: crate::kernel::drivers::DriverMode::External { pid: Some(pid) },
                        });
                        let _ = crate::language::register_capability(
                            root,
                            &fcp_name,
                            "device",
                            &format!(".ostk/drivers/{fcp_name}.sock"),
                            "(query) -> (result)",
                            &format!("{name} device driver"),
                            true,
                        );
                        println!("driver {name}: alive (pid {pid})");
                        found_alive = true;
                        break;
                    }
                }
        }
        if found_alive {
            continue;
        }
        // Stale socket/pid — clean up before respawning (both naming conventions)
        let _ = fs::remove_file(&sock_path);
        let _ = fs::remove_file(&pid_path);
        let _ = fs::remove_file(&fcp_pid_path);

        // Parse command string into program + args (shell-style split)
        let parts: Vec<&str> = cmd_str.split_whitespace().collect();
        if parts.is_empty() {
            eprintln!("driver {name}: empty command, skipping");
            continue;
        }
        let program = parts[0];
        let args = &parts[1..];

        // Open log file for stdout/stderr redirection
        let log_file = match fs::File::create(&log_path) {
            Ok(f) => f,
            Err(e) => {
                eprintln!("driver {name}: failed to create log: {e}");
                continue;
            }
        };
        let stderr_log = match log_file.try_clone() {
            Ok(f) => f,
            Err(e) => {
                eprintln!("driver {name}: failed to clone log handle: {e}");
                continue;
            }
        };

        // Spawn the driver process
        let child = match Command::new(program)
            .args(args)
            .stdout(std::process::Stdio::from(log_file))
            .stderr(std::process::Stdio::from(stderr_log))
            .spawn()
        {
            Ok(c) => c,
            Err(e) => {
                eprintln!("driver {name}: spawn failed: {e}");
                continue;
            }
        };

        let child_pid = child.id();
        let _ = fs::write(&pid_path, child_pid.to_string());

        // Wait up to 5s for the socket to appear
        let start = std::time::Instant::now();
        let timeout = std::time::Duration::from_secs(5);
        let mut connected = false;
        loop {
            if sock_path.exists()
                && UnixStream::connect(&sock_path).is_ok() {
                    connected = true;
                    break;
                }
            if start.elapsed() > timeout {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(50));
        }

        if connected {
            registry.register(crate::kernel::drivers::DriverEntry {
                handler_name: name.clone(),
                extensions: extensions_for_driver(name),
                capabilities: vec!["query".into(), "session".into()],
                mode: crate::kernel::drivers::DriverMode::External { pid: Some(child_pid) },
            });
            // Register in .language as alive
            let _ = crate::language::register_capability(
                root,
                &fcp_name,
                "device",
                &format!(".ostk/drivers/{name}.sock"),
                "(query) -> (result)",
                &format!("{name} device driver"),
                true,
            );
            println!("driver {name}: started (pid {child_pid})");
        } else {
            // Register in .language as dead (socket did not connect)
            let _ = crate::language::register_capability(
                root,
                &fcp_name,
                "device",
                &format!(".ostk/drivers/{name}.sock"),
                "(query) -> (result)",
                &format!("{name} device driver"),
                false,
            );
            eprintln!("driver {name}: socket did not appear within 5s");
        }
    }

    // Print driver table
    if registry.list().len() > 1 {
        println!("{}", registry.format_table());
    }
}

/// Map driver name to default file extensions it claims.
pub fn extensions_for_driver(name: &str) -> Vec<String> {
    match name {
        "rust" | "fcp-rust" => vec!["rs".into(), "toml".into()],
        "python" | "fcp-python" => vec!["py".into()],
        "js" | "fcp-js" => vec!["js".into(), "ts".into(), "jsx".into(), "tsx".into()],
        "go" | "fcp-go" => vec!["go".into()],
        _ => vec![],
    }
}

// ->797: extensions_for_driver tests
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extensions_for_driver_rust() {
        let exts = extensions_for_driver("rust");
        assert_eq!(exts, vec!["rs".to_string(), "toml".to_string()]);
    }

    #[test]
    fn test_extensions_for_driver_fcp_rust() {
        let exts = extensions_for_driver("fcp-rust");
        assert_eq!(exts, vec!["rs".to_string(), "toml".to_string()]);
    }

    #[test]
    fn test_extensions_for_driver_python() {
        let exts = extensions_for_driver("python");
        assert_eq!(exts, vec!["py".to_string()]);
    }

    #[test]
    fn test_extensions_for_driver_fcp_python() {
        let exts = extensions_for_driver("fcp-python");
        assert_eq!(exts, vec!["py".to_string()]);
    }

    #[test]
    fn test_extensions_for_driver_js() {
        let exts = extensions_for_driver("js");
        assert_eq!(exts, vec!["js".to_string(), "ts".to_string(), "jsx".to_string(), "tsx".to_string()]);
    }

    #[test]
    fn test_extensions_for_driver_fcp_js() {
        let exts = extensions_for_driver("fcp-js");
        assert_eq!(exts, vec!["js".to_string(), "ts".to_string(), "jsx".to_string(), "tsx".to_string()]);
    }

    #[test]
    fn test_extensions_for_driver_go() {
        let exts = extensions_for_driver("go");
        assert_eq!(exts, vec!["go".to_string()]);
    }

    #[test]
    fn test_extensions_for_driver_fcp_go() {
        let exts = extensions_for_driver("fcp-go");
        assert_eq!(exts, vec!["go".to_string()]);
    }

    #[test]
    fn test_extensions_for_driver_unknown_returns_empty() {
        let exts = extensions_for_driver("unknown-driver");
        assert!(exts.is_empty());
    }

    #[test]
    fn test_extensions_for_driver_empty_name_returns_empty() {
        let exts = extensions_for_driver("");
        assert!(exts.is_empty());
    }
}
