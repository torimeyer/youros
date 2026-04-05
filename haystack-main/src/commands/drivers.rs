//! `ostk drivers` — list registered fcp device drivers.
//!
//! Builds a boot-time driver registry snapshot and prints the table.
//! Internal drivers are always present; external drivers from HUMANFILE
//! `drivers:` section are shown if their PID file exists.

use crate::kernel::drivers::{DriverEntry, DriverMode, DriverRegistry};

/// Run `ostk drivers` — print the driver registry table.
pub fn run() -> Result<(), String> {
    let mut reg = DriverRegistry::new();

    // Internal drivers (always present)
    reg.register_internal_screen();

    // →787: Load external drivers declared in HUMANFILE (directive parser)
    if let Ok(root) = crate::find_project_root() {
        let ostk_dir = crate::state_dir(&root);
        let load_result = crate::humanfile::load(&ostk_dir);
        let drivers: std::collections::HashMap<String, String> =
            load_result.humanfile.drivers.into_iter().collect();
        let drivers_dir = ostk_dir.join("drivers");

        for name in drivers.keys() {
            let pid_path = drivers_dir.join(format!("{name}.pid"));
            let sock_path = drivers_dir.join(format!("{name}.sock"));

            let pid = std::fs::read_to_string(&pid_path)
                .ok()
                .and_then(|s| s.trim().parse::<u32>().ok());

            // Only show as registered if socket exists (driver is running)
            let alive = sock_path.exists()
                && std::os::unix::net::UnixStream::connect(&sock_path).is_ok();

            if alive {
                reg.register(DriverEntry {
                    handler_name: name.clone(),
                    extensions: super::boot::extensions_for_driver(name),
                    capabilities: vec!["query".into(), "session".into()],
                    mode: DriverMode::External { pid },
                });
            }
        }
    }

    println!("{}", reg.format_table());
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// →797: Verify that the driver wiring in run() builds a well-formed
    /// registry table when external drivers are registered.
    #[test]
    fn test_registry_with_external_driver_format() {
        let mut reg = DriverRegistry::new();
        reg.register_internal_screen();

        // Simulate what run() does for an alive external driver
        let name = "fcp-rust";
        reg.register(DriverEntry {
            handler_name: name.to_string(),
            extensions: super::super::boot::extensions_for_driver(name),
            capabilities: vec!["query".into(), "session".into()],
            mode: DriverMode::External { pid: Some(42) },
        });

        let table = reg.format_table();
        assert!(table.starts_with("[drivers]"), "table should start with header");
        assert!(table.contains("fcp-rust"), "table should list fcp-rust");
        assert!(table.contains("external(pid 42)"), "table should show pid");
        assert!(table.contains("[rs, toml]"), "table should show rust extensions");
        assert!(table.contains("fcp-screen"), "table should still list internal screen");
    }

    /// Verify format_table output for internal-only (no external drivers).
    #[test]
    fn test_registry_internal_only_format() {
        let mut reg = DriverRegistry::new();
        reg.register_internal_screen();

        let table = reg.format_table();
        assert!(table.contains("fcp-screen"));
        assert!(table.contains("internal"));
        assert!(!table.contains("external"));
    }

    /// Verify that extensions_for_driver feeds correct data into registry routes.
    #[test]
    fn test_extension_routing_via_driver_wiring() {
        let mut reg = DriverRegistry::new();
        reg.register_internal_screen();

        for name in &["fcp-rust", "fcp-python", "fcp-js", "fcp-go"] {
            reg.register(DriverEntry {
                handler_name: name.to_string(),
                extensions: super::super::boot::extensions_for_driver(name),
                capabilities: vec!["query".into(), "session".into()],
                mode: DriverMode::External { pid: None },
            });
        }

        assert_eq!(reg.driver_for_ext("rs").unwrap().handler_name, "fcp-rust");
        assert_eq!(reg.driver_for_ext("toml").unwrap().handler_name, "fcp-rust");
        assert_eq!(reg.driver_for_ext("py").unwrap().handler_name, "fcp-python");
        assert_eq!(reg.driver_for_ext("js").unwrap().handler_name, "fcp-js");
        assert_eq!(reg.driver_for_ext("ts").unwrap().handler_name, "fcp-js");
        assert_eq!(reg.driver_for_ext("tsx").unwrap().handler_name, "fcp-js");
        assert_eq!(reg.driver_for_ext("go").unwrap().handler_name, "fcp-go");
        // tty still routed to internal screen
        assert_eq!(reg.driver_for_ext("tty").unwrap().handler_name, "fcp-screen");
    }
}
