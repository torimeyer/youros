//! `ostk clock` — OS time display.
//!
//! Shows wall time, session uptime, kernel version, gen activity,
//! identity counter, and audit depth. The OS knows what time it is.

use std::fmt::Write;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::kernel::verb_ctx::VerbCtx;

/// CLI entry point — creates a VerbCtx, runs, prints output.
pub fn run() -> Result<(), String> {
    let root = crate::find_project_root()
        .unwrap_or_else(|_| std::env::current_dir().unwrap_or_default());
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_verb(&mut ctx)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation — writes to VerbCtx (→1157).
pub fn run_verb(ctx: &mut VerbCtx) -> Result<(), String> {
    let hs_dir = ctx.ostk_dir();

    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let wall = format_wall(now);
    let version = env!("CARGO_PKG_VERSION");
    let identity = crate::kernel::identity::read_counter(&hs_dir);
    let session_uptime = session_uptime_secs(&hs_dir, now);
    let audit_events = count_lines(&hs_dir.join("audit.jsonl")).unwrap_or(0);
    let last_gen = file_age_secs(&hs_dir.join("gen_table.jsonl"), now);
    let swap_age = file_age_secs(&hs_dir.join("boot.md"), now);
    let swap_status = if swap_age < 3600 { "✓ fresh" } else { "~ stale" };
    let focus = std::fs::read_to_string(hs_dir.join("focus"))
        .ok()
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|| "—".to_string());

    writeln!(ctx, "ostk clock").unwrap();
    writeln!(ctx, "──────────────────────────────").unwrap();
    writeln!(ctx, "  wall      {wall}").unwrap();
    writeln!(ctx, "  session   {}", format_duration(session_uptime)).unwrap();
    writeln!(ctx, "  kernel    v{version} (@prime+{identity})").unwrap();
    writeln!(ctx, "  swap      {swap_status} ({})", format_duration(swap_age)).unwrap();
    writeln!(ctx, "  last gen  {}",
        if last_gen < 60 { format!("{}s ago", last_gen) } else { format_duration(last_gen) }
    ).unwrap();
    writeln!(ctx, "  audit     {} events", audit_events).unwrap();
    writeln!(ctx, "  focus     {focus}").unwrap();
    writeln!(ctx, "──────────────────────────────").unwrap();

    Ok(())
}

fn format_wall(ts: u64) -> String {
    // Simple UTC formatting without chrono dependency
    let secs = ts % 60;
    let mins = (ts / 60) % 60;
    let hours = (ts / 3600) % 24;
    let days = ts / 86400;
    // Days since Unix epoch → approximate date
    let year = 1970 + days / 365;
    let day_of_year = days % 365;
    let month = day_of_year / 30 + 1;
    let day = day_of_year % 30 + 1;
    format!("{year}-{month:02}-{day:02}T{hours:02}:{mins:02}:{secs:02}Z")
}

fn format_duration(secs: u64) -> String {
    if secs < 60 {
        format!("{secs}s")
    } else if secs < 3600 {
        format!("{}m{}s", secs / 60, secs % 60)
    } else {
        format!("{}h{}m", secs / 3600, (secs % 3600) / 60)
    }
}

fn count_lines(path: &Path) -> Option<usize> {
    std::fs::read_to_string(path)
        .ok()
        .map(|s| s.lines().filter(|l| !l.trim().is_empty()).count())
}

fn file_age_secs(path: &Path, now: u64) -> u64 {
    path.metadata()
        .ok()
        .and_then(|m| m.modified().ok())
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| now.saturating_sub(d.as_secs()))
        .unwrap_or(u64::MAX)
}

fn session_uptime_secs(hs_dir: &Path, now: u64) -> u64 {
    // Find the youngest .jsonl session file (most recent agent start)
    let sessions_dir = hs_dir.join("sessions");
    let mut youngest = u64::MAX;
    if let Ok(entries) = std::fs::read_dir(&sessions_dir) {
        for entry in entries.flatten() {
            if entry.path().extension().and_then(|e| e.to_str()) == Some("jsonl") {
                let age = file_age_secs(&entry.path(), now);
                if age < youngest {
                    youngest = age;
                }
            }
        }
    }
    if youngest == u64::MAX { 0 } else { youngest }
}
