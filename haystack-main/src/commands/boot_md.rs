//! boot.md generation — the kernel's init script.
//!
//! Extracted from install.rs. Used by init, boot, and shutdown.

use std::fs;
use std::path::Path;

/// Generate boot.md if it doesn't exist.
pub fn generate(root: &Path) -> Result<bool, String> {
    generate_inner(root, false)
}

/// Regenerate boot.md (force overwrite). Called by boot and shutdown.
pub fn regenerate(root: &Path) -> Result<bool, String> {
    generate_inner(root, true)
}

fn generate_inner(root: &Path, force: bool) -> Result<bool, String> {
    let boot_path = crate::state_dir(root).join("boot.md");
    if boot_path.exists() && !force {
        return Ok(false);
    }

    // →651: harness detection — tell the agent how to talk to the kernel
    let harness = crate::commands::boot::detect_harness();
    let tool_hint = match harness {
        "claude-code" => crate::strings::boot::TOOL_HINT_CLAUDE_CODE,
        "ostk-serve" => crate::strings::boot::TOOL_HINT_SERVE,
        "ci" => crate::strings::boot::TOOL_HINT_CI,
        _ => crate::strings::boot::TOOL_HINT_DEFAULT,
    };

    let content = format!(
        "# boot.md — @ostk.prime\n\
         # harness: {}\n\
         # tool pattern: {}\n\
         \n\
         :verify .primefile\n\
         :post\n\
         :clock\n\
         :reap\n\
         \n\
         :init @ostk.prime+N\n\
         :load .language\n\
         :load fcp-tack\n\
         \n\
         :boot\n\
         :refine\n\
         :compile\n\
         :coordinate\n\
         :work\n\
         \n\
         # Orientation\n\
         :guide\n",
        harness, tool_hint
    );

    fs::write(&boot_path, content)
        .map_err(|e| format!("failed to write boot.md: {e}"))?;

    Ok(true)
}
