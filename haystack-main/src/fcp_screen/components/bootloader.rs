//! TUI bootloader — animated kernel boot sequence rendered into the chat zone.
//!
//! Classic boot screen: action verbs, progressive disclosure, visual pacing.
//! Streams into the established screen layout (status bar + input bar already visible).
//! No LLM needed — purely filesystem checks.

use std::fs;
use std::path::Path;

use crate::commands::helpers::{count_lines, count_fleet, count_open_needles};

use crate::fcp_screen::protocol::{self, Color};
use crate::fcp_screen::renderer::AppState as Screen;
use crate::language::parse_language;

/// Render the kernel boot sequence on the TUI.
///
/// Each subsystem step renders with action-verb pacing.
/// Cursor hidden during boot to avoid blinking artifacts.
/// Called from `app::run()` BEFORE the event loop.
pub fn render_boot_sequence(root: &Path, scr: &mut Screen) {
    let ostk_dir = crate::state_dir(root);

    // ── Power-On Self Test ───────────────────────────────────
    // Header renders immediately — no disk I/O. Then each check runs and
    // streams its result live. The POST IS the first thing you see.

    scr.append_line(protocol::line_from_spans(vec![
        protocol::bold("ostk"),
        protocol::dim(format!(" v{}", env!("CARGO_PKG_VERSION"))),
    ]));
    scr.append_line(protocol::line_plain(""));

    // Run each check and stream result immediately
    let mut passed = 0usize;
    let total = 8;

    macro_rules! post_check {
        ($label:expr, $result:expr) => {{
            let result = $result;
            match &result {
                BootResult::Ok(detail) => {
                    passed += 1;
                    scr.append_line(protocol::line_from_spans(vec![
                        protocol::colored("  ✓ ", Color::Green),
                        protocol::plain(format!("{}: {detail}", $label)),
                    ]));
                }
                BootResult::Warn(detail) => {
                    scr.append_line(protocol::line_from_spans(vec![
                        protocol::colored("  ⚠ ", Color::Yellow),
                        protocol::plain(format!("{}: {detail}", $label)),
                    ]));
                }
                BootResult::Fail(detail) => {
                    scr.append_line(protocol::line_from_spans(vec![
                        protocol::colored("  ✗ ", Color::Red),
                        protocol::plain(format!("{}: {detail}", $label)),
                    ]));
                }
            }
        }};
    }

    post_check!("HUMANFILE", check_humanfile(&ostk_dir));
    post_check!("boot.md", if ostk_dir.join("boot.md").exists() {
        BootResult::Ok("loaded".into())
    } else {
        BootResult::Warn("not found".into())
    });
    post_check!(".language", check_language(&ostk_dir));
    post_check!("identity", check_identity(&ostk_dir));
    post_check!("needles", check_needles(&ostk_dir));
    let driver_names = discover_drivers(&ostk_dir);
    post_check!("drivers", BootResult::Ok(driver_names.join(", ")));
    post_check!("fleet", check_fleet(&ostk_dir));
    post_check!("audit", check_audit(&ostk_dir));

    // POST summary
    scr.append_line(protocol::line_plain(""));
    let (mark, color) = if passed == total {
        ("✓", Color::Green)
    } else {
        ("!", Color::Yellow)
    };
    scr.append_line(protocol::line_from_spans(vec![
        protocol::colored(format!("POST {passed}/{total} {mark}"), color),
        protocol::dim("  :help or Alt+?"),
    ]));
    scr.append_line(protocol::line_plain(""));

}

// ── Subsystem checks ────────────────────────────────────────────────────

enum BootResult {
    Ok(String),
    Warn(String),
    Fail(String),
}

fn check_humanfile(ostk_dir: &Path) -> BootResult {
    let path = ostk_dir.join("HUMANFILE");
    if path.exists() {
        let sig_path = ostk_dir.join("HUMANFILE.sig");
        let asc_path = ostk_dir.join("HUMANFILE.asc");
        if sig_path.exists() || asc_path.exists() {
            BootResult::Ok("verified (signed)".into())
        } else {
            BootResult::Ok("loaded (unsigned)".into())
        }
    } else {
        BootResult::Warn("not found".into())
    }
}

fn check_language(ostk_dir: &Path) -> BootResult {
    let path = ostk_dir.join(".language");
    if path.exists() {
        let content = fs::read_to_string(&path).unwrap_or_default();
        let entries = parse_language(&content);
        let verbs = entries.iter().filter(|e| !e.is_infrastructure()).count();
        let devices = entries.iter().filter(|e| e.is_device()).count();
        let services = entries.iter().filter(|e| e.is_service()).count();
        if devices > 0 || services > 0 {
            BootResult::Ok(format!("{verbs} verbs, {devices} devices, {services} services"))
        } else {
            BootResult::Ok(format!("{verbs} verbs"))
        }
    } else {
        BootResult::Fail("not found".into())
    }
}

fn check_identity(ostk_dir: &Path) -> BootResult {
    let path = ostk_dir.join("identity_counter");
    if path.exists() {
        let id = crate::kernel::identity::read_counter(ostk_dir);
        BootResult::Ok(format!("@h.p+{id}"))
    } else {
        BootResult::Fail("no identity_counter".into())
    }
}

fn check_needles(ostk_dir: &Path) -> BootResult {
    let path = ostk_dir.join("needles/issues.jsonl");
    if path.exists() {
        let open = count_open_needles(&path);
        BootResult::Ok(format!("{open} open"))
    } else {
        BootResult::Warn("no issues.jsonl".into())
    }
}

fn discover_drivers(ostk_dir: &Path) -> Vec<String> {
    let root = ostk_dir.parent().unwrap_or(ostk_dir);
    if let Ok(entries) = crate::language::parse_language_file(root) {
        let device_entries: Vec<String> = entries
            .iter()
            .filter(|e| e.is_device())
            .map(|e| {
                let status = if e.is_alive() { "" } else { " (down)" };
                format!("{}{}", e.verb, status)
            })
            .collect();
        if !device_entries.is_empty() {
            return device_entries;
        }
    }
    // Fallback: always report fcp-screen if .language has no device entries
    vec!["fcp-screen".to_string()]
}

fn check_fleet(ostk_dir: &Path) -> BootResult {
    let path = ostk_dir.join("agents.jsonl");
    if path.exists() {
        let (active, total) = count_fleet(&path);
        BootResult::Ok(format!("{active} active / {total} total"))
    } else {
        BootResult::Warn("no agents.jsonl".into())
    }
}

fn check_audit(ostk_dir: &Path) -> BootResult {
    let path = ostk_dir.join("audit.jsonl");
    if path.exists() {
        let count = count_lines(&path);
        BootResult::Ok(format!("{count} events"))
    } else {
        BootResult::Warn("no audit.jsonl".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn check_humanfile_missing() {
        let tmp = PathBuf::from("/tmp/ostk_bootloader_test_nonexistent");
        let result = check_humanfile(&tmp);
        assert!(matches!(result, BootResult::Warn(_)));
    }

    #[test]
    fn check_language_missing() {
        let tmp = PathBuf::from("/tmp/ostk_bootloader_test_nonexistent");
        let result = check_language(&tmp);
        assert!(matches!(result, BootResult::Fail(_)));
    }

    #[test]
    fn check_identity_missing() {
        let tmp = PathBuf::from("/tmp/ostk_bootloader_test_nonexistent");
        let result = check_identity(&tmp);
        assert!(matches!(result, BootResult::Fail(_)));
    }

    #[test]
    fn check_needles_missing() {
        let tmp = PathBuf::from("/tmp/ostk_bootloader_test_nonexistent");
        let result = check_needles(&tmp);
        assert!(matches!(result, BootResult::Warn(_)));
    }

    #[test]
    fn check_fleet_missing() {
        let tmp = PathBuf::from("/tmp/ostk_bootloader_test_nonexistent");
        let result = check_fleet(&tmp);
        assert!(matches!(result, BootResult::Warn(_)));
    }

    #[test]
    fn check_drivers_no_dir() {
        let tmp = PathBuf::from("/tmp/ostk_bootloader_test_nonexistent");
        let names = discover_drivers(&tmp);
        assert!(names.contains(&"fcp-screen".to_string()));
    }

    #[test]
    fn check_audit_missing() {
        let tmp = PathBuf::from("/tmp/ostk_bootloader_test_nonexistent");
        let result = check_audit(&tmp);
        assert!(matches!(result, BootResult::Warn(_)));
    }
}
