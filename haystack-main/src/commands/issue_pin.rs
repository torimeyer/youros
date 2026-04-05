//! `ostk pin issue` — issue a child OS pin under @ostk.prime.
//!
//! A pin is a minimal OS package:
//!   - .ostk/pins/{name}/pin.boot  — 8-line tack init script
//!   - .ostk/pins/{name}/pin.caps  — capability declaration
//!
//! After issuing, the operator signs pin.boot with a child GPG key
//! endorsed by @ostk.prime (kernel key from .ostk/kernel.key).
//!
//! See docs/spec/pin.md for the full spec.

use std::fs;
use std::path::PathBuf;

use crate::find_project_root;

/// Default capability set for a new pin.
/// Grants read access to .ostk/ and .language.
/// Grants write only to the pin's own store directory.
/// Execution is read-only shell only.
/// Kernel writes and governance modifications are always denied.
const DEFAULT_CAPS: &str = "read write execute";

/// The 8-line tack boot script template.
fn pin_boot_template(name: &str, caps: &str) -> String {
    format!(
        "# pin: {name} — child of @ostk.prime\n\
         # sign: gpg --sign this file with child key\n\
         :verify parent-key\n\
         :init @{name}\n\
         :caps {caps}\n\
         :load .language\n\
         :boot → :work\n\
         :trust parent-key → :execution\n"
    )
}

/// The capability file template.
fn pin_caps_template(name: &str) -> String {
    format!(
        "read: .ostk/ .language\n\
         write: .ostk/store/{name}/\n\
         execute: shell(readonly)\n\
         deny: write-kernel modify-governance\n"
    )
}

pub fn run_issue(name: &str, caps: Option<&str>) -> Result<(), String> {
    let root = find_project_root()?;
    let caps_str = caps.unwrap_or(DEFAULT_CAPS);

    // Create pin directory
    let pin_dir: PathBuf = crate::state_dir(&root).join("pins").join(name);
    if pin_dir.exists() {
        return Err(format!(
            "pin '{}' already exists at {}",
            name,
            pin_dir.display()
        ));
    }
    fs::create_dir_all(&pin_dir)
        .map_err(|e| format!("failed to create pin directory: {e}"))?;

    // Write pin.boot
    let boot_path = pin_dir.join("pin.boot");
    let boot_content = pin_boot_template(name, caps_str);
    fs::write(&boot_path, &boot_content)
        .map_err(|e| format!("failed to write pin.boot: {e}"))?;

    // Write pin.caps
    let caps_path = pin_dir.join("pin.caps");
    let caps_content = pin_caps_template(name);
    fs::write(&caps_path, &caps_content)
        .map_err(|e| format!("failed to write pin.caps: {e}"))?;

    println!("pin {} issued", name);
    println!();
    println!("  {}", boot_path.display());
    println!("  {}", caps_path.display());
    println!();
    println!("sign with:");
    println!(
        "  gpg --detach-sign {}",
        boot_path.display()
    );
    println!();
    // →773: Resolve kernel key via centralized sign::resolve_kernel_key()
    let ostk_dir = crate::state_dir(&root);
    let kernel_key_id = crate::commands::sign::resolve_kernel_key(&ostk_dir);

    println!("GPG chain:");
    println!("  @ostk.prime {kernel_key_id} → @{name} child key → pin.boot.asc");
    println!();
    println!("next: generate child key, sign with @ostk.prime, then sign pin.boot");

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pin_boot_template_has_8_lines() {
        let tmpl = pin_boot_template("test-agent", "read write execute");
        let lines: Vec<&str> = tmpl.lines().collect();
        assert_eq!(lines.len(), 8, "pin.boot must be exactly 8 lines");
    }

    #[test]
    fn test_pin_boot_template_contains_name() {
        let tmpl = pin_boot_template("my-agent", "read write execute");
        assert!(tmpl.contains("my-agent"));
        assert!(tmpl.contains(":init @my-agent"));
    }

    #[test]
    fn test_pin_boot_template_contains_caps() {
        let tmpl = pin_boot_template("my-agent", "read write execute");
        assert!(tmpl.contains(":caps read write execute"));
    }

    #[test]
    fn test_pin_boot_template_verify_parent_key() {
        let tmpl = pin_boot_template("my-agent", "read write");
        assert!(tmpl.contains(":verify parent-key"));
    }

    #[test]
    fn test_pin_boot_template_trust_line() {
        let tmpl = pin_boot_template("my-agent", "read");
        assert!(tmpl.contains(":trust parent-key → :execution"));
    }

    #[test]
    fn test_pin_caps_template_contains_name() {
        let caps = pin_caps_template("my-agent");
        assert!(caps.contains(".ostk/store/my-agent/"));
    }

    #[test]
    fn test_pin_caps_template_has_deny_line() {
        let caps = pin_caps_template("my-agent");
        assert!(caps.contains("deny: write-kernel modify-governance"));
    }

    #[test]
    fn test_pin_caps_template_readonly_execute() {
        let caps = pin_caps_template("my-agent");
        assert!(caps.contains("execute: shell(readonly)"));
    }
}
