pub mod audit;
pub mod frontmatter;
pub mod index;
pub mod needles;
pub mod paths;
pub mod time;

// Re-export all public items so callers can use `crate::util::function_name`
// or, via lib.rs re-exports, `crate::function_name`.

/// Truncate a string to at most `max` bytes, appending "..." if truncated.
/// Uses `floor_char_boundary` to avoid panicking on multi-byte UTF-8.
pub fn safe_truncate(s: &str, max: usize) -> String {
    if s.len() <= max {
        s.to_string()
    } else {
        let end = max.saturating_sub(3);
        let safe_end = s.floor_char_boundary(end);
        format!("{}...", &s[..safe_end])
    }
}

pub use audit::{append_audit, read_audit_events, resolve_remaps};
pub use frontmatter::{parse_frontmatter, write_with_frontmatter};
pub use index::{post_mutation, verify_os_integrity, MutationEvent};
pub use needles::{
    next_needle_id, parse_needle_ids, parse_spec_refs, read_needles, with_needles_locked,
    write_needles,
};
pub use paths::{find_project_root, normalize_spec_path, state_dir, STATE_DIR};
pub use time::now_iso;
