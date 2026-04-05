//! fcp driver registry — the kernel's table of registered device drivers.
//!
//! Each fcp-* driver encodes the protocol between the CPUs (human + LLM) and a
//! domain device.  The registry tracks which drivers are loaded, what file
//! extensions they claim, and how to invoke them (internal function call vs.
//! external subprocess).
//!
//! Boot sequence: `register_internal_screen()` is called during TUI init to
//! register fcp-screen as an internal driver.  External drivers register via
//! `fcp.register` at runtime.

use std::collections::HashMap;

// ---------------------------------------------------------------------------
// Driver mode + entry
// ---------------------------------------------------------------------------

/// How the driver is invoked.
#[derive(Debug, Clone)]
pub enum DriverMode {
    /// Compiled into the binary — direct function calls, no IPC.
    Internal,
    /// External process — persistent subprocess, stdio JSON-RPC.
    External { pid: Option<u32> },
}

/// A registered fcp-* device driver.
#[derive(Debug, Clone)]
pub struct DriverEntry {
    pub handler_name: String,
    pub extensions: Vec<String>,      // file extensions this driver claims
    pub capabilities: Vec<String>,    // "ops", "query", "session", "display", "input"
    pub mode: DriverMode,
}

// ---------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------

/// The kernel's driver registry.
pub struct DriverRegistry {
    drivers: HashMap<String, DriverEntry>,   // keyed by handler_name
    extension_map: HashMap<String, String>,  // ext -> handler_name (for routing)
}

impl Default for DriverRegistry {
    fn default() -> Self {
        Self::new()
    }
}

impl DriverRegistry {
    pub fn new() -> Self {
        Self {
            drivers: HashMap::new(),
            extension_map: HashMap::new(),
        }
    }

    /// Register a driver (called on `fcp.register` or at boot for internal drivers).
    ///
    /// If a previous driver claimed the same extension, the new registration wins
    /// (last-writer-wins).  The old driver entry remains but loses the extension route.
    pub fn register(&mut self, entry: DriverEntry) {
        // Wire extension routes
        for ext in &entry.extensions {
            self.extension_map
                .insert(ext.clone(), entry.handler_name.clone());
        }
        self.drivers.insert(entry.handler_name.clone(), entry);
    }

    /// Find the driver that claims a file extension.
    pub fn driver_for_ext(&self, ext: &str) -> Option<&DriverEntry> {
        let name = self.extension_map.get(ext)?;
        self.drivers.get(name)
    }

    /// Find a driver by name.
    pub fn get(&self, name: &str) -> Option<&DriverEntry> {
        self.drivers.get(name)
    }

    /// List all registered drivers (deterministic order: sorted by name).
    pub fn list(&self) -> Vec<&DriverEntry> {
        let mut entries: Vec<&DriverEntry> = self.drivers.values().collect();
        entries.sort_by_key(|e| &e.handler_name);
        entries
    }

    /// Boot registration: register fcp-screen as internal driver.
    pub fn register_internal_screen(&mut self) {
        self.register(DriverEntry {
            handler_name: "fcp-screen".into(),
            extensions: vec!["tty".into()],
            capabilities: vec!["display".into(), "input".into()],
            mode: DriverMode::Internal,
        });
    }

    /// Boot registration: register fcp-web as internal driver.
    pub fn register_internal_web(&mut self) {
        self.register(DriverEntry {
            handler_name: "fcp-web".into(),
            extensions: vec![],
            capabilities: vec!["read".into(), "links".into(), "status".into()],
            mode: DriverMode::Internal,
        });
    }

    /// Boot registration: register fcp-llm as internal driver.
    pub fn register_internal_llm(&mut self) {
        self.register(DriverEntry {
            handler_name: "fcp-llm".into(),
            extensions: vec![],
            capabilities: vec!["query".into()],
            mode: DriverMode::Internal,
        });
    }

    /// Format the driver table for boot output / CLI display.
    ///
    /// ```text
    /// [drivers]
    ///   fcp-screen  internal  [tty]  display, input
    /// ```
    pub fn format_table(&self) -> String {
        let entries = self.list();
        if entries.is_empty() {
            return "[drivers] (none)".to_string();
        }
        let mut out = String::from("[drivers]");
        for entry in entries {
            let mode_str = match &entry.mode {
                DriverMode::Internal => "internal".to_string(),
                DriverMode::External { pid: Some(p) } => format!("external(pid {})", p),
                DriverMode::External { pid: None } => "external".to_string(),
            };
            let exts = format!("[{}]", entry.extensions.join(", "));
            let caps = entry.capabilities.join(", ");
            out.push_str(&format!(
                "\n  {:<14}{:<18}{:<10}{}",
                entry.handler_name, mode_str, exts, caps
            ));
        }
        out
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn register_internal_and_query_by_extension() {
        let mut reg = DriverRegistry::new();
        reg.register_internal_screen();

        let driver = reg.driver_for_ext("tty").expect("tty should resolve");
        assert_eq!(driver.handler_name, "fcp-screen");
        assert!(matches!(driver.mode, DriverMode::Internal));
        assert_eq!(driver.capabilities, vec!["display", "input"]);
    }

    #[test]
    fn register_external_driver_and_verify_extension_map() {
        let mut reg = DriverRegistry::new();
        reg.register(DriverEntry {
            handler_name: "fcp-rust".into(),
            extensions: vec!["rs".into(), "toml".into()],
            capabilities: vec!["ops".into(), "query".into(), "session".into()],
            mode: DriverMode::External { pid: Some(12345) },
        });

        let driver = reg.driver_for_ext("rs").expect("rs should resolve");
        assert_eq!(driver.handler_name, "fcp-rust");
        assert!(matches!(driver.mode, DriverMode::External { pid: Some(12345) }));

        let toml_driver = reg.driver_for_ext("toml").expect("toml should resolve");
        assert_eq!(toml_driver.handler_name, "fcp-rust");
    }

    #[test]
    fn extension_conflict_last_wins() {
        let mut reg = DriverRegistry::new();

        // First driver claims "rs"
        reg.register(DriverEntry {
            handler_name: "fcp-rust-v1".into(),
            extensions: vec!["rs".into()],
            capabilities: vec!["ops".into()],
            mode: DriverMode::Internal,
        });

        // Second driver also claims "rs" — should win
        reg.register(DriverEntry {
            handler_name: "fcp-rust-v2".into(),
            extensions: vec!["rs".into()],
            capabilities: vec!["ops".into(), "query".into()],
            mode: DriverMode::External { pid: None },
        });

        let driver = reg.driver_for_ext("rs").expect("rs should resolve");
        assert_eq!(driver.handler_name, "fcp-rust-v2");

        // Both drivers should still be in the registry
        assert!(reg.get("fcp-rust-v1").is_some());
        assert!(reg.get("fcp-rust-v2").is_some());
    }

    #[test]
    fn list_returns_all_drivers_sorted() {
        let mut reg = DriverRegistry::new();
        reg.register_internal_screen();
        reg.register(DriverEntry {
            handler_name: "fcp-rust".into(),
            extensions: vec!["rs".into()],
            capabilities: vec!["ops".into()],
            mode: DriverMode::External { pid: None },
        });
        reg.register(DriverEntry {
            handler_name: "fcp-drawio".into(),
            extensions: vec!["drawio".into()],
            capabilities: vec!["session".into()],
            mode: DriverMode::External { pid: Some(999) },
        });

        let all = reg.list();
        assert_eq!(all.len(), 3);
        // Sorted by handler_name
        assert_eq!(all[0].handler_name, "fcp-drawio");
        assert_eq!(all[1].handler_name, "fcp-rust");
        assert_eq!(all[2].handler_name, "fcp-screen");
    }

    #[test]
    fn driver_for_ext_returns_none_for_unclaimed() {
        let mut reg = DriverRegistry::new();
        reg.register_internal_screen();

        assert!(reg.driver_for_ext("rs").is_none());
        assert!(reg.driver_for_ext("py").is_none());
        assert!(reg.driver_for_ext("").is_none());
    }

    #[test]
    fn get_by_name() {
        let mut reg = DriverRegistry::new();
        reg.register_internal_screen();

        assert!(reg.get("fcp-screen").is_some());
        assert!(reg.get("nonexistent").is_none());
    }

    #[test]
    fn empty_registry() {
        let reg = DriverRegistry::new();
        assert!(reg.list().is_empty());
        assert!(reg.driver_for_ext("tty").is_none());
        assert!(reg.get("fcp-screen").is_none());
    }

    #[test]
    fn format_table_with_drivers() {
        let mut reg = DriverRegistry::new();
        reg.register_internal_screen();

        let table = reg.format_table();
        assert!(table.starts_with("[drivers]"));
        assert!(table.contains("fcp-screen"));
        assert!(table.contains("internal"));
        assert!(table.contains("[tty]"));
        assert!(table.contains("display, input"));
    }

    #[test]
    fn format_table_empty() {
        let reg = DriverRegistry::new();
        assert_eq!(reg.format_table(), "[drivers] (none)");
    }
}
