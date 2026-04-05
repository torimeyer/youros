use std::fmt::Write;

use crate::find_project_root;
use crate::kernel::nudge;
use crate::kernel::registry;
use crate::kernel::verb_ctx::VerbCtx;
use std::path::PathBuf;

/// CLI entry point.
pub fn run(agent: &str, message: &str) -> Result<(), String> {
    let root = crate::find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_verb(&mut ctx, agent, message)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation — writes to VerbCtx (→1157).
pub fn run_verb(ctx: &mut VerbCtx, agent: &str, message: &str) -> Result<(), String> {
    if let Some(target_name) = agent.strip_prefix('@') {
        // Cross-OS nudge: resolve target via registry
        run_cross_os_verb(ctx, target_name, message)
    } else {
        // Local nudge: existing behavior
        let ostk_dir = ctx.ostk_dir();
        nudge::push_nudge(&ostk_dir, agent, message)?;
        writeln!(ctx, "nudged {}: {}", agent, message).unwrap();
        Ok(())
    }
}

/// Resolve a cross-OS nudge target and write the nudge to the remote OS instance.
fn run_cross_os_verb(ctx: &mut VerbCtx, target_name: &str, message: &str) -> Result<(), String> {
    let entry = registry::resolve_instance(target_name)?
        .ok_or_else(|| {
            format!(
                "unknown OS instance: @{target_name}. \
                 Run 'ostk trust list' to see registered instances."
            )
        })?;

    deliver_cross_os_nudge(ctx, &entry, target_name, message)
}

/// Deliver a nudge to a resolved OS instance. Separated for testability.
fn deliver_cross_os_nudge(
    ctx: &mut VerbCtx,
    entry: &registry::RegistryEntry,
    target_name: &str,
    message: &str,
) -> Result<(), String> {
    let target_path = PathBuf::from(&entry.path);
    let target_ostk = crate::state_dir(&target_path);

    if !target_ostk.is_dir() {
        return Err(format!(
            "OS instance @{target_name} at {} has no .ostk/ directory",
            entry.path
        ));
    }

    // Determine source name from current project
    let source_name = match find_project_root() {
        Ok(root) => root
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_else(|| "unknown".to_string()),
        Err(_) => "unknown".to_string(),
    };

    // Write nudge to the target OS instance's nudges directory
    nudge::push_nudge(&target_ostk, &source_name, message)?;
    writeln!(ctx, "nudged @{}: {}", target_name, message).unwrap();
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kernel::registry::RegistryEntry;
    use std::fs;
    use std::path::Path;
    use std::sync::atomic::{AtomicU32, Ordering};

    static TEST_COUNTER: AtomicU32 = AtomicU32::new(0);
    fn test_dir(prefix: &str) -> std::path::PathBuf {
        let n = TEST_COUNTER.fetch_add(1, Ordering::SeqCst);
        let pid = std::process::id();
        std::env::temp_dir().join(format!("ostk_nudge_test_{prefix}_{pid}_{n}"))
    }

    #[test]
    fn test_cross_os_nudge_unknown_target_error_message() {
        // Test the error message format directly
        let target_name = "nonexistent";
        let err = format!(
            "unknown OS instance: @{target_name}. \
             Run 'ostk trust list' to see registered instances."
        );
        assert!(err.contains("unknown OS instance: @nonexistent"));
        assert!(err.contains("ostk trust list"));
    }

    #[test]
    fn test_cross_os_nudge_delivers_to_target() {
        let target_proj = test_dir("deliver_target");
        let _ = fs::remove_dir_all(&target_proj);
        fs::create_dir_all(target_proj.join(".ostk")).unwrap();

        let entry = RegistryEntry {
            path: target_proj.to_string_lossy().to_string(),
            name: "test_target".to_string(),
            last_boot: "2026-03-09T00:00:00Z".to_string(),
        };

        let root = Path::new("/tmp/nudge_test_root");
        let input = serde_json::json!({});
        let mut ctx = VerbCtx::new(root, &input);

        let result = deliver_cross_os_nudge(&mut ctx, &entry, "test_target", "check tests");
        assert!(result.is_ok(), "delivery should succeed: {:?}", result);

        // Verify nudge was written
        let nudge_dir = target_proj.join(".ostk/nudges");
        assert!(nudge_dir.is_dir(), "nudges dir should exist at target");

        let entries: Vec<_> = fs::read_dir(&nudge_dir)
            .unwrap()
            .filter_map(|e| e.ok())
            .collect();
        assert!(!entries.is_empty(), "should have at least one nudge file");

        let mut found = false;
        for e in &entries {
            let content = fs::read_to_string(e.path()).unwrap_or_default();
            if content.contains("check tests") {
                found = true;
            }
        }
        assert!(found, "nudge message should be in target's nudges");

        let output = ctx.into_output();
        assert!(output.contains("nudged @test_target: check tests"));

        let _ = fs::remove_dir_all(&target_proj);
    }

    #[test]
    fn test_cross_os_nudge_target_missing_ostk_dir() {
        let target_proj = test_dir("missing_ostk");
        let _ = fs::remove_dir_all(&target_proj);
        fs::create_dir_all(&target_proj).unwrap();
        // No .ostk/ directory

        let entry = RegistryEntry {
            path: target_proj.to_string_lossy().to_string(),
            name: "broken_target".to_string(),
            last_boot: "2026-03-09T00:00:00Z".to_string(),
        };

        let root = Path::new("/tmp/nudge_test_root");
        let input = serde_json::json!({});
        let mut ctx = VerbCtx::new(root, &input);

        let result = deliver_cross_os_nudge(&mut ctx, &entry, "broken_target", "hello");
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(err.contains("no .ostk/ directory"), "got: {err}");

        let _ = fs::remove_dir_all(&target_proj);
    }
}
