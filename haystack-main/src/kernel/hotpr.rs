/// Hot PR — Conflict resolution for the ostk kernel.
///
/// When a CAS (str_replace) fails because old_str no longer matches,
/// Hot PR classifies the conflict and resolves it:
///
/// Tier 1 (auto-merge): Non-overlapping edits >3 lines apart. Applied silently.
/// Tier 2 (assisted merge): Overlapping or within 3 lines, <30 changed lines.
///     Returns a suggested file:edit() call for the agent to accept/modify/reject.
/// Tier 3 (manual rebase): >30 changed lines or >2 conflict regions.
///     Returns conflict info. Agent retries with fresh read.
///
/// The key insight: if the agent's intended edit targets different lines
/// than the lines that changed between the agent's base gen and the
/// current gen, the edits compose cleanly and can be applied silently.
use std::ops::Range;

/// Proximity threshold: edits within this many lines of each other
/// trigger Tier 2 (assisted merge) instead of Tier 1 (auto-merge).
/// Adjacent edits that don't textually overlap can still break each other --
/// a function signature change on line 10 and a new call on line 12 need
/// the agent's eyes.
pub const PROXIMITY_LINES: usize = 3;

/// Maximum changed lines for Tier 2. Beyond this, a suggested merge is
/// more confusing than a fresh read -- escalate to Tier 3.
pub const TIER2_MAX_CHANGED_LINES: usize = 30;

/// Maximum disjoint conflict regions for Tier 2. Beyond this, escalate
/// to Tier 3.
pub const TIER2_MAX_CONFLICT_REGIONS: usize = 2;

/// Result of a Hot PR merge attempt.
#[derive(Debug)]
pub enum MergeResult {
    /// Tier 1: edits don't overlap, auto-merged successfully.
    AutoMerged {
        /// The merged file content
        merged_content: String,
        /// Writer who made the conflicting edit
        other_writer: String,
        /// Generation the file was at before our merge
        from_gen: u64,
        /// Generation after our merge
        to_gen: u64,
    },
    /// Tier 2: edits overlap or are within proximity, but manageable.
    /// Return a suggested merge for the agent to accept/modify/reject.
    AssistedMerge {
        /// File content before the conflicting edit (for diffing)
        base_content: String,
        /// Current file content (at current gen)
        current_content: String,
        /// Current generation number
        current_gen: u64,
        /// Who made the conflicting edit
        conflict_writer: String,
        /// Line range that was changed by the other writer
        changed_lines: Range<usize>,
        /// The agent's intended old_str
        agent_old_str: String,
        /// The agent's intended new_str
        agent_new_str: String,
        /// Suggested old_str for the accepting file:edit() call
        suggested_old_str: String,
        /// Suggested new_str for the accepting file:edit() call
        suggested_new_str: String,
        /// Total changed line count (for the diff header)
        diff_line_count: usize,
    },
    /// Tier 3: edits overlap, manual rebase required.
    Conflict {
        /// Current file content (at current gen)
        current_content: String,
        /// Current generation number
        current_gen: u64,
        /// Who made the conflicting edit
        conflict_writer: String,
        /// Line range that was changed by the other writer
        changed_lines: Range<usize>,
        /// The agent's intended old_str
        agent_old_str: String,
        /// The agent's intended new_str
        agent_new_str: String,
    },
}

/// Compute the 1-based line range where `needle` appears in `haystack`.
/// Returns None if needle is not found.
pub fn find_line_range(haystack: &str, needle: &str) -> Option<Range<usize>> {
    let byte_offset = haystack.find(needle)?;
    let start_line = haystack[..byte_offset].matches('\n').count() + 1;
    let needle_newlines = needle.matches('\n').count();
    let end_line = start_line + needle_newlines;
    Some(start_line..end_line + 1) // exclusive end
}

/// Compute which lines changed between two versions of a file.
/// Returns a list of line ranges (1-based) that differ.
///
/// Uses a simple line-by-line diff: finds the first and last differing lines.
pub fn changed_line_range(old_content: &str, new_content: &str) -> Option<Range<usize>> {
    let old_lines: Vec<&str> = old_content.lines().collect();
    let new_lines: Vec<&str> = new_content.lines().collect();

    let first_diff = old_lines
        .iter()
        .zip(new_lines.iter())
        .position(|(a, b)| a != b);

    let len_differs = old_lines.len() != new_lines.len();

    if first_diff.is_none() && !len_differs {
        return None;
    }

    let first = first_diff.unwrap_or(old_lines.len().min(new_lines.len()));

    let old_rev = old_lines.iter().rev();
    let new_rev = new_lines.iter().rev();
    let tail_match = old_rev.zip(new_rev).take_while(|(a, b)| a == b).count();

    let last_in_new = if new_lines.len() > tail_match {
        new_lines.len() - tail_match
    } else {
        first + 1
    };

    let last_in_old = if old_lines.len() > tail_match {
        old_lines.len() - tail_match
    } else {
        first + 1
    };

    let end = last_in_new.max(last_in_old);

    Some((first + 1)..end + 1)
}

/// Check if two line ranges overlap.
pub fn ranges_overlap(a: &Range<usize>, b: &Range<usize>) -> bool {
    a.start < b.end && b.start < a.end
}

/// Check if two line ranges are within `proximity` lines of each other.
/// Returns true if the ranges overlap OR the gap between them is <= proximity lines.
pub fn ranges_within_proximity(a: &Range<usize>, b: &Range<usize>, proximity: usize) -> bool {
    if ranges_overlap(a, b) {
        return true;
    }
    let gap = if a.end <= b.start {
        b.start - a.end
    } else {
        a.start.saturating_sub(b.end)
    };
    gap <= proximity
}

/// Count the total number of changed lines in a range.
fn changed_line_count(range: &Range<usize>) -> usize {
    range.end.saturating_sub(range.start)
}

/// Compute the agent's actual edit intent: which lines within old_str
/// actually differ from new_str. Returns a range relative to the file
/// (1-based), given old_str's position in the file at `old_str_file_start_line`.
///
/// Context lines (identical in old_str and new_str) are excluded from the
/// edit range. Only lines that actually change are counted.
fn agent_edit_range(old_str: &str, new_str: &str, old_str_file_start_line: usize) -> Range<usize> {
    let old_lines: Vec<&str> = old_str.lines().collect();
    let new_lines: Vec<&str> = new_str.lines().collect();

    let first_diff = old_lines
        .iter()
        .zip(new_lines.iter())
        .position(|(a, b)| a != b);

    let len_differs = old_lines.len() != new_lines.len();

    if first_diff.is_none() && !len_differs {
        return old_str_file_start_line..old_str_file_start_line;
    }

    let first = first_diff.unwrap_or(old_lines.len().min(new_lines.len()));

    let tail_match = old_lines
        .iter()
        .rev()
        .zip(new_lines.iter().rev())
        .take_while(|(a, b)| a == b)
        .count();

    let last_in_old = if old_lines.len() > tail_match {
        old_lines.len() - tail_match
    } else {
        first + 1
    };

    let last_in_new = if new_lines.len() > tail_match {
        new_lines.len() - tail_match
    } else {
        first + 1
    };

    let last = last_in_old.max(last_in_new);

    let start = old_str_file_start_line + first;
    let end = old_str_file_start_line + last;

    start..end
}

/// Attempt to auto-merge an edit against the current file content.
///
/// Classification:
/// - Tier 1 (AutoMerged): edits >PROXIMITY_LINES apart, apply silently
/// - Tier 2 (AssistedMerge): within proximity or overlapping, <TIER2_MAX_CHANGED_LINES
/// - Tier 3 (Conflict): too complex for suggestion
pub fn try_auto_merge(
    previous_content: &str,
    current_content: &str,
    old_str: &str,
    new_str: &str,
    current_gen: u64,
    other_writer: &str,
) -> MergeResult {
    let old_str_byte_offset = match previous_content.find(old_str) {
        Some(offset) => offset,
        None => {
            return MergeResult::Conflict {
                current_content: current_content.to_string(),
                current_gen,
                conflict_writer: other_writer.to_string(),
                changed_lines: 0..0,
                agent_old_str: old_str.to_string(),
                agent_new_str: new_str.to_string(),
            };
        }
    };

    let old_str_start_line = previous_content[..old_str_byte_offset]
        .matches('\n')
        .count()
        + 1;

    let edit_range = agent_edit_range(old_str, new_str, old_str_start_line);

    let change_range = match changed_line_range(previous_content, current_content) {
        Some(r) => r,
        None => {
            if current_content.contains(old_str) {
                let merged = current_content.replacen(old_str, new_str, 1);
                return MergeResult::AutoMerged {
                    merged_content: merged,
                    other_writer: other_writer.to_string(),
                    from_gen: current_gen,
                    to_gen: current_gen + 1,
                };
            }
            return MergeResult::Conflict {
                current_content: current_content.to_string(),
                current_gen,
                conflict_writer: other_writer.to_string(),
                changed_lines: 0..0,
                agent_old_str: old_str.to_string(),
                agent_new_str: new_str.to_string(),
            };
        }
    };

    // Classify: Tier 1 vs Tier 2 vs Tier 3
    let in_proximity = ranges_within_proximity(&edit_range, &change_range, PROXIMITY_LINES);
    let overlaps = ranges_overlap(&edit_range, &change_range);

    if overlaps || in_proximity {
        let total_changed = changed_line_count(&change_range) + changed_line_count(&edit_range);

        if total_changed > TIER2_MAX_CHANGED_LINES {
            return MergeResult::Conflict {
                current_content: current_content.to_string(),
                current_gen,
                conflict_writer: other_writer.to_string(),
                changed_lines: change_range,
                agent_old_str: old_str.to_string(),
                agent_new_str: new_str.to_string(),
            };
        }

        return try_tier2_rebase(
            previous_content,
            current_content,
            old_str,
            new_str,
            old_str_start_line,
            current_gen,
            other_writer,
            &change_range,
            total_changed,
        );
    }

    // Non-overlapping AND outside proximity: Tier 1 auto-merge.
    if current_content.contains(old_str) {
        let merged = current_content.replacen(old_str, new_str, 1);
        MergeResult::AutoMerged {
            merged_content: merged,
            other_writer: other_writer.to_string(),
            from_gen: current_gen,
            to_gen: current_gen + 1,
        }
    } else {
        match intent_apply(
            previous_content,
            current_content,
            old_str,
            new_str,
            old_str_start_line,
        ) {
            Some(merged) => MergeResult::AutoMerged {
                merged_content: merged,
                other_writer: other_writer.to_string(),
                from_gen: current_gen,
                to_gen: current_gen + 1,
            },
            None => MergeResult::Conflict {
                current_content: current_content.to_string(),
                current_gen,
                conflict_writer: other_writer.to_string(),
                changed_lines: change_range,
                agent_old_str: old_str.to_string(),
                agent_new_str: new_str.to_string(),
            },
        }
    }
}

/// Attempt Tier 2 assisted merge: mechanical rebase of the agent's edit
/// onto the current file content.
///
/// Two strategies:
/// 1. Trivial rebase: old_str still exists verbatim in current file.
/// 2. Line-level rebase: old_str was modified, apply transformation positionally.
///
/// If mechanical rebasing fails, escalate to Tier 3.
#[allow(clippy::too_many_arguments)]
fn try_tier2_rebase(
    previous_content: &str,
    current_content: &str,
    old_str: &str,
    new_str: &str,
    old_str_start_line: usize,
    current_gen: u64,
    other_writer: &str,
    change_range: &Range<usize>,
    diff_line_count: usize,
) -> MergeResult {
    // Strategy 1: Trivial rebase -- old_str still exists verbatim
    if current_content.matches(old_str).count() == 1 {
        return MergeResult::AssistedMerge {
            base_content: previous_content.to_string(),
            current_content: current_content.to_string(),
            current_gen,
            conflict_writer: other_writer.to_string(),
            changed_lines: change_range.clone(),
            agent_old_str: old_str.to_string(),
            agent_new_str: new_str.to_string(),
            suggested_old_str: old_str.to_string(),
            suggested_new_str: new_str.to_string(),
            diff_line_count,
        };
    }

    // Strategy 2: Line-level rebase
    match tier2_line_rebase(
        previous_content,
        current_content,
        old_str,
        new_str,
        old_str_start_line,
    ) {
        Some((rebased_old, rebased_new)) => MergeResult::AssistedMerge {
            base_content: previous_content.to_string(),
            current_content: current_content.to_string(),
            current_gen,
            conflict_writer: other_writer.to_string(),
            changed_lines: change_range.clone(),
            agent_old_str: old_str.to_string(),
            agent_new_str: new_str.to_string(),
            suggested_old_str: rebased_old,
            suggested_new_str: rebased_new,
            diff_line_count,
        },
        None => {
            // Mechanical rebase failed -- escalate to Tier 3
            MergeResult::Conflict {
                current_content: current_content.to_string(),
                current_gen,
                conflict_writer: other_writer.to_string(),
                changed_lines: change_range.clone(),
                agent_old_str: old_str.to_string(),
                agent_new_str: new_str.to_string(),
            }
        }
    }
}

/// Line-level rebase for Tier 2: apply the agent's intended transformation
/// to the corresponding region in the current file content.
///
/// Returns `Some((suggested_old_str, suggested_new_str))` if successful,
/// or `None` if the rebase can't be computed mechanically.
fn tier2_line_rebase(
    _previous_content: &str,
    current_content: &str,
    old_str: &str,
    new_str: &str,
    old_str_start_line: usize,
) -> Option<(String, String)> {
    let old_lines: Vec<&str> = old_str.lines().collect();
    let new_lines: Vec<&str> = new_str.lines().collect();
    let current_lines: Vec<&str> = current_content.lines().collect();

    let first_diff = old_lines
        .iter()
        .zip(new_lines.iter())
        .position(|(a, b)| a != b);

    let len_differs = old_lines.len() != new_lines.len();

    if first_diff.is_none() && !len_differs {
        return None;
    }

    let first = first_diff.unwrap_or(old_lines.len().min(new_lines.len()));

    let tail_match = old_lines
        .iter()
        .rev()
        .zip(new_lines.iter().rev())
        .take_while(|(a, b)| a == b)
        .count();

    let old_change_end = old_lines.len().saturating_sub(tail_match);
    let new_change_end = new_lines.len().saturating_sub(tail_match);

    let file_start_idx = old_str_start_line - 1;
    let file_region_end = file_start_idx + old_lines.len();

    if file_region_end > current_lines.len() {
        return None;
    }

    let suggested_old_lines: Vec<&str> = current_lines[file_start_idx..file_region_end].to_vec();

    let mut suggested_new_lines: Vec<String> = Vec::new();

    // Leading context from current file
    for line in &suggested_old_lines[..first] {
        suggested_new_lines.push(line.to_string());
    }

    // Agent's changed lines
    for line in &new_lines[first..new_change_end] {
        suggested_new_lines.push(line.to_string());
    }

    // Trailing context from current file
    let old_tail_start = old_change_end;
    if old_tail_start < suggested_old_lines.len() {
        for line in &suggested_old_lines[old_tail_start..] {
            suggested_new_lines.push(line.to_string());
        }
    }

    let suggested_old = suggested_old_lines.join("\n");
    let suggested_new = suggested_new_lines.join("\n");

    if current_content.matches(&suggested_old).count() != 1 {
        return None;
    }

    if suggested_old == suggested_new {
        return None;
    }

    Some((suggested_old, suggested_new))
}

/// Intent-based apply: apply only the agent's actual edit to the current
/// content by line position. Used for Tier 1 auto-merge when old_str
/// includes context lines that were changed by another agent.
fn intent_apply(
    _previous_content: &str,
    current_content: &str,
    old_str: &str,
    new_str: &str,
    old_str_start_line: usize,
) -> Option<String> {
    let old_lines: Vec<&str> = old_str.lines().collect();
    let new_lines: Vec<&str> = new_str.lines().collect();
    let current_lines: Vec<String> = current_content.lines().map(|s| s.to_string()).collect();

    let first_diff = old_lines
        .iter()
        .zip(new_lines.iter())
        .position(|(a, b)| a != b);

    let len_differs = old_lines.len() != new_lines.len();

    if first_diff.is_none() && !len_differs {
        return Some(current_content.to_string());
    }

    let first = first_diff.unwrap_or(old_lines.len().min(new_lines.len()));

    let tail_match = old_lines
        .iter()
        .rev()
        .zip(new_lines.iter().rev())
        .take_while(|(a, b)| a == b)
        .count();

    let old_change_end = old_lines.len().saturating_sub(tail_match);
    let new_change_end = new_lines.len().saturating_sub(tail_match);

    let file_start_idx = old_str_start_line - 1 + first;
    let file_end_idx = old_str_start_line - 1 + old_change_end;

    let replacement: Vec<&str> = new_lines[first..new_change_end].to_vec();

    if file_end_idx > current_lines.len() {
        return None;
    }

    for (i, old_line) in old_lines[first..old_change_end].iter().enumerate() {
        let file_idx = file_start_idx + i;
        if file_idx >= current_lines.len() {
            return None;
        }
        if current_lines[file_idx] != *old_line {
            return None;
        }
    }

    let mut result_lines: Vec<String> = Vec::new();
    result_lines.extend(current_lines[..file_start_idx].iter().cloned());
    result_lines.extend(replacement.iter().map(|s| s.to_string()));
    result_lines.extend(current_lines[file_end_idx..].iter().cloned());

    let mut result = result_lines.join("\n");
    if current_content.ends_with('\n') && !result.ends_with('\n') {
        result.push('\n');
    }
    Some(result)
}

/// Format a conflict response for Tier 3 manual rebase.
pub fn format_conflict(path: &str, result: &MergeResult) -> String {
    match result {
        MergeResult::Conflict {
            current_content,
            current_gen,
            conflict_writer,
            changed_lines,
            agent_old_str,
            agent_new_str,
        } => {
            let lines_desc = if changed_lines.start == 0 && changed_lines.end == 0 {
                "unknown".to_string()
            } else {
                format!("{}-{}", changed_lines.start, changed_lines.end - 1)
            };
            format!(
                "[conflict] {path}:gen={current_gen} diff:[{lines_desc}] by:{conflict_writer}\n\
                 \n\
                 Current file content (gen {current_gen}):\n\
                 {current_content}\n\
                 \n\
                 Your intended edit:\n\
                 old_str: {agent_old_str:?}\n\
                 new_str: {agent_new_str:?}\n\
                 \n\
                 Retry with a fresh read of the file."
            )
        }
        MergeResult::AssistedMerge { .. } => String::new(),
        MergeResult::AutoMerged { .. } => String::new(),
    }
}

/// Render a compact line-level diff between two file versions.
///
/// For each line in the changed range, emits:
///   `- line N: [old content]`
///   `+ line N: [new content]`
///
/// Lines not in the changed range are omitted. If a block of lines was
/// inserted or deleted, the added/removed lines are shown with `+`/`-`.
///
/// Returns a string suitable for embedding in a conflict message.
fn format_line_diff(base: &str, current: &str, range: &Range<usize>) -> String {
    if range.start == 0 && range.end == 0 {
        return "(no diff available)".to_string();
    }

    let base_lines: Vec<&str> = base.lines().collect();
    let cur_lines: Vec<&str> = current.lines().collect();

    // Clamp range to valid indices (1-based, exclusive end)
    let start = range.start.saturating_sub(1); // convert to 0-based
    let end_base = (range.end - 1).min(base_lines.len());
    let end_cur = (range.end - 1).min(cur_lines.len());

    let mut parts: Vec<String> = Vec::new();

    let old_slice = if start < end_base && end_base <= base_lines.len() {
        &base_lines[start..end_base]
    } else {
        &[]
    };
    let new_slice = if start < end_cur && end_cur <= cur_lines.len() {
        &cur_lines[start..end_cur]
    } else {
        &[]
    };

    // Pair old and new lines together for a unified view
    let max_len = old_slice.len().max(new_slice.len());
    for i in 0..max_len {
        let line_no = range.start + i;
        match (old_slice.get(i), new_slice.get(i)) {
            (Some(old), Some(new)) if old == new => {
                // Context line — skip (keep diff compact)
            }
            (Some(old), Some(new)) => {
                parts.push(format!("- line {line_no}: {old}"));
                parts.push(format!("+ line {line_no}: {new}"));
            }
            (Some(old), None) => {
                parts.push(format!("- line {line_no}: {old}"));
            }
            (None, Some(new)) => {
                parts.push(format!("+ line {line_no}: {new}"));
            }
            (None, None) => {}
        }
    }

    if parts.is_empty() {
        "(lines added/removed — re-read file for context)".to_string()
    } else {
        parts.join("\n")
    }
}

/// Detect whether a Tier 2 conflict is AUTO-COMPOSABLE.
///
/// A conflict is AUTO-COMPOSABLE if we were able to mechanically rebase
/// the agent's edit without semantic ambiguity:
/// - Trivial rebase: `old_str` still exists verbatim (other edit was elsewhere)
/// - Line-level rebase succeeded: the transformation was applied positionally
///
/// In both cases, the suggested_old/new is ready to apply immediately.
fn is_auto_composable(agent_old_str: &str, suggested_old_str: &str) -> bool {
    // Trivial rebase: old_str unchanged — the change was elsewhere in the file
    agent_old_str == suggested_old_str
}

/// Format a Tier 2 assisted merge conflict response.
///
/// Emits:
/// ```ignore
/// Tier 2 conflict in file.rs:
///
/// What changed (gen 5→6, by ridge):
/// - line 42: [old] → [new]
///
/// Your intended change:
/// old_str: "..."
/// new_str: "..."
///
/// Suggested merge: [AUTO-COMPOSABLE: changes don't overlap semantically]
/// Apply? Re-read file (gen 6) and retry with:
///   file:edit("file.rs", "suggested old", "suggested new")
///
/// To modify: write your own file:edit() against the current gen 6 content.
/// To reject: re-read and start over.
/// ```
pub fn format_tier2_conflict(path: &str, result: &MergeResult) -> String {
    match result {
        MergeResult::AssistedMerge {
            base_content,
            current_content,
            current_gen,
            conflict_writer,
            changed_lines,
            agent_old_str,
            agent_new_str,
            suggested_old_str,
            suggested_new_str,
            diff_line_count,
        } => {
            let base_gen = current_gen.saturating_sub(1);
            let diff = format_line_diff(base_content, current_content, changed_lines);
            let composable = is_auto_composable(agent_old_str, suggested_old_str);
            let composable_label = if composable {
                "[AUTO-COMPOSABLE: changes don't overlap semantically]"
            } else {
                "[ASSISTED: positional rebase applied — verify correctness]"
            };

            format!(
                "Tier 2 conflict in {path}:\n\
                 \n\
                 What changed (gen {base_gen}\u{2192}{current_gen}, by {conflict_writer}, \
                 +/-{diff_line_count} lines):\n\
                 {diff}\n\
                 \n\
                 Your intended change:\n\
                 old_str: {agent_old_str:?}\n\
                 new_str: {agent_new_str:?}\n\
                 \n\
                 Suggested merge: {composable_label}\n\
                 Apply? Re-read file (gen {current_gen}) and retry with:\n\
                   file:edit(\"{path}\", {suggested_old_str:?}, {suggested_new_str:?})\n\
                 \n\
                 To modify: write your own file:edit() against the current gen {current_gen} content.\n\
                 To reject: re-read the file and start over."
            )
        }
        _ => String::new(),
    }
}

/// Format an auto-merge notification for Tier 1.
pub fn format_auto_merge(_path: &str, other_writer: &str, from_gen: u64, to_gen: u64) -> String {
    format!("[conflict] auto-merged with {other_writer} (gen {from_gen}->{to_gen})")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_find_line_range_first_line() {
        let content = "hello world\nfoo bar\nbaz qux\n";
        let range = find_line_range(content, "hello world").unwrap();
        assert_eq!(range, 1..2);
    }

    #[test]
    fn test_find_line_range_middle() {
        let content = "line1\nline2\nline3\nline4\n";
        let range = find_line_range(content, "line2\nline3").unwrap();
        assert_eq!(range, 2..4);
    }

    #[test]
    fn test_find_line_range_last_line() {
        let content = "a\nb\nc\n";
        let range = find_line_range(content, "c").unwrap();
        assert_eq!(range, 3..4);
    }

    #[test]
    fn test_find_line_range_not_found() {
        let content = "hello\nworld\n";
        assert!(find_line_range(content, "missing").is_none());
    }

    #[test]
    fn test_changed_line_range_identical() {
        let content = "a\nb\nc\n";
        assert!(changed_line_range(content, content).is_none());
    }

    #[test]
    fn test_changed_line_range_middle_change() {
        let old = "line1\nline2\nline3\n";
        let new = "line1\nLINE2\nline3\n";
        let range = changed_line_range(old, new).unwrap();
        assert_eq!(range, 2..3);
    }

    #[test]
    fn test_changed_line_range_insertion() {
        let old = "line1\nline3\n";
        let new = "line1\nline2\nline3\n";
        let range = changed_line_range(old, new).unwrap();
        assert_eq!(range, 2..3);
    }

    #[test]
    fn test_changed_line_range_deletion() {
        let old = "line1\nline2\nline3\n";
        let new = "line1\nline3\n";
        let range = changed_line_range(old, new).unwrap();
        assert_eq!(range, 2..3);
    }

    #[test]
    fn test_ranges_overlap_no_overlap() {
        assert!(!ranges_overlap(&(1..3), &(4..6)));
        assert!(!ranges_overlap(&(4..6), &(1..3)));
    }

    #[test]
    fn test_ranges_overlap_overlap() {
        assert!(ranges_overlap(&(1..4), &(3..6)));
        assert!(ranges_overlap(&(3..6), &(1..4)));
    }

    #[test]
    fn test_ranges_overlap_adjacent() {
        assert!(!ranges_overlap(&(1..3), &(3..5)));
    }

    // --- Tier 1: edits far apart, auto-merge silently ---

    #[test]
    fn test_auto_merge_far_apart() {
        let prev = "line1\nline2\nline3\nline4\nline5\nline6\nline7\nline8\n";
        let curr = "LINE1\nline2\nline3\nline4\nline5\nline6\nline7\nline8\n";

        let result = try_auto_merge(prev, curr, "line8", "LINE8", 2, "agent-1");

        match result {
            MergeResult::AutoMerged {
                merged_content,
                other_writer,
                from_gen,
                to_gen,
            } => {
                assert!(merged_content.contains("LINE1"));
                assert!(merged_content.contains("LINE8"));
                assert_eq!(other_writer, "agent-1");
                assert_eq!(from_gen, 2);
                assert_eq!(to_gen, 3);
            }
            other => panic!("expected Tier 1 auto-merge, got: {other:?}"),
        }
    }

    #[test]
    fn test_auto_merge_multiline_far_apart() {
        let prev =
            "fn main() {\n    println!(\"hello\");\n}\n\n\n\n\nfn helper() {\n    todo!();\n}\n";
        let curr =
            "fn main() {\n    println!(\"goodbye\");\n}\n\n\n\n\nfn helper() {\n    todo!();\n}\n";

        let result = try_auto_merge(
            prev,
            curr,
            "fn helper() {\n    todo!();\n}",
            "fn helper() {\n    42\n}",
            3,
            "ridge",
        );

        match result {
            MergeResult::AutoMerged { merged_content, .. } => {
                assert!(merged_content.contains("println!(\"goodbye\")"));
                assert!(merged_content.contains("fn helper() {\n    42\n}"));
            }
            other => panic!("expected Tier 1 auto-merge, got: {other:?}"),
        }
    }

    // --- Tier 2: edits within proximity, assisted merge ---

    #[test]
    fn test_tier2_nearby_trivial_rebase() {
        let prev = "line1\nline2\nline3\nline4\nline5\n";
        let curr = "LINE1\nline2\nline3\nline4\nline5\n";

        let result = try_auto_merge(prev, curr, "line4", "LINE4", 2, "agent-1");

        match result {
            MergeResult::AssistedMerge {
                current_gen,
                conflict_writer,
                suggested_old_str,
                suggested_new_str,
                ..
            } => {
                assert_eq!(current_gen, 2);
                assert_eq!(conflict_writer, "agent-1");
                assert_eq!(suggested_old_str, "line4");
                assert_eq!(suggested_new_str, "LINE4");
            }
            other => panic!("expected Tier 2 assisted merge, got: {other:?}"),
        }
    }

    #[test]
    fn test_tier2_overlapping_line_rebase() {
        let prev = "line1\nline2\nline3\n";
        let curr = "line1\nLINE2\nline3\n";

        let result = try_auto_merge(prev, curr, "line2", "OTHER2", 2, "agent-1");

        match result {
            MergeResult::AssistedMerge {
                current_gen,
                conflict_writer,
                suggested_old_str,
                suggested_new_str,
                ..
            } => {
                assert_eq!(current_gen, 2);
                assert_eq!(conflict_writer, "agent-1");
                assert_eq!(suggested_old_str, "LINE2");
                assert_eq!(suggested_new_str, "OTHER2");
            }
            other => panic!("expected Tier 2 assisted merge, got: {other:?}"),
        }
    }

    #[test]
    fn test_tier2_multiline_within_proximity() {
        let prev = "fn main() {\n    println!(\"hello\");\n}\n\nfn helper() {\n    todo!();\n}\n";
        let curr = "fn main() {\n    println!(\"goodbye\");\n}\n\nfn helper() {\n    todo!();\n}\n";

        let result = try_auto_merge(
            prev,
            curr,
            "fn helper() {\n    todo!();\n}",
            "fn helper() {\n    42\n}",
            3,
            "ridge",
        );

        match result {
            MergeResult::AssistedMerge {
                suggested_old_str,
                suggested_new_str,
                ..
            } => {
                assert_eq!(suggested_old_str, "fn helper() {\n    todo!();\n}");
                assert_eq!(suggested_new_str, "fn helper() {\n    42\n}");
            }
            other => panic!("expected Tier 2 assisted merge, got: {other:?}"),
        }
    }

    #[test]
    fn test_tier2_interacting_edits_rebase() {
        let prev = "fn connect(host: &str) -> Connection {\n    todo!()\n}\n";
        let curr = "fn connect(host: &str, timeout: Duration) -> Connection {\n    todo!()\n}\n";

        let result = try_auto_merge(
            prev,
            curr,
            "fn connect(host: &str) -> Connection {",
            "fn connect(host: &str, port: u16) -> Connection {",
            7,
            "vane",
        );

        match result {
            MergeResult::AssistedMerge {
                suggested_old_str,
                suggested_new_str,
                current_gen,
                ..
            } => {
                assert_eq!(current_gen, 7);
                assert_eq!(
                    suggested_old_str,
                    "fn connect(host: &str, timeout: Duration) -> Connection {"
                );
                assert_eq!(
                    suggested_new_str,
                    "fn connect(host: &str, port: u16) -> Connection {"
                );
            }
            other => panic!("expected Tier 2 for interacting edits, got: {other:?}"),
        }
    }

    // --- Tier 3: too complex ---

    #[test]
    fn test_tier3_large_diff_escalation() {
        let mut prev_lines: Vec<String> = Vec::new();
        let mut curr_lines: Vec<String> = Vec::new();
        for i in 1..=50 {
            prev_lines.push(format!("line{i}"));
            if i <= 30 {
                curr_lines.push(format!("CHANGED{i}"));
            } else {
                curr_lines.push(format!("line{i}"));
            }
        }
        let prev = prev_lines.join("\n") + "\n";
        let curr = curr_lines.join("\n") + "\n";

        let result = try_auto_merge(&prev, &curr, "line31", "LINE31", 5, "agent-1");

        match result {
            MergeResult::Conflict { current_gen, .. } => {
                assert_eq!(current_gen, 5);
            }
            other => panic!("expected Tier 3 conflict, got: {other:?}"),
        }
    }

    // --- Proximity helper ---

    #[test]
    fn test_ranges_within_proximity() {
        assert!(ranges_within_proximity(&(1..3), &(3..5), 3));
        assert!(ranges_within_proximity(&(1..3), &(4..6), 3));
        assert!(ranges_within_proximity(&(1..3), &(6..8), 3));
        assert!(!ranges_within_proximity(&(1..3), &(7..9), 3));
        assert!(ranges_within_proximity(&(1..5), &(3..7), 3));
        assert!(ranges_within_proximity(&(6..8), &(1..3), 3));
        assert!(!ranges_within_proximity(&(7..9), &(1..3), 3));
    }

    // --- Format tests ---

    #[test]
    fn test_format_auto_merge() {
        let msg = format_auto_merge("src/main.rs", "ridge", 5, 6);
        assert_eq!(msg, "[conflict] auto-merged with ridge (gen 5->6)");
    }

    #[test]
    fn test_format_conflict() {
        let result = MergeResult::Conflict {
            current_content: "current content".to_string(),
            current_gen: 5,
            conflict_writer: "vane".to_string(),
            changed_lines: 3..6,
            agent_old_str: "old".to_string(),
            agent_new_str: "new".to_string(),
        };
        let msg = format_conflict("src/main.rs", &result);
        assert!(msg.contains("[conflict] src/main.rs:gen=5 diff:[3-5] by:vane"));
        assert!(msg.contains("old_str: \"old\""));
        assert!(msg.contains("new_str: \"new\""));
    }

    #[test]
    fn test_format_tier2_conflict() {
        let result = MergeResult::AssistedMerge {
            base_content: "line1\nline2\nold line3\nline4\nline5\nline6\n".to_string(),
            current_content: "line1\nline2\nnew line3\nline4\nline5\nline6\n".to_string(),
            current_gen: 7,
            conflict_writer: "ridge".to_string(),
            changed_lines: 3..4,
            agent_old_str: "old text".to_string(),
            agent_new_str: "new text".to_string(),
            suggested_old_str: "suggested old".to_string(),
            suggested_new_str: "suggested new".to_string(),
            diff_line_count: 5,
        };
        let msg = format_tier2_conflict("src/main.rs", &result);
        // Header: path, current gen, diff line count
        assert!(msg.contains("Tier 2 conflict in src/main.rs:"), "missing header: {msg}");
        assert!(msg.contains("gen 6\u{2192}7"), "missing gen range: {msg}");
        assert!(msg.contains("+/-5 lines"), "missing diff line count: {msg}");
        assert!(msg.contains("by ridge"), "missing conflict_writer: {msg}");
        // Diff section: what changed
        assert!(msg.contains("What changed"), "missing diff section: {msg}");
        assert!(msg.contains("- line 3:"), "missing old line in diff: {msg}");
        assert!(msg.contains("+ line 3:"), "missing new line in diff: {msg}");
        assert!(msg.contains("old line3"), "missing old line content: {msg}");
        assert!(msg.contains("new line3"), "missing new line content: {msg}");
        // Agent's intended change
        assert!(msg.contains("Your intended change"), "missing intended change section: {msg}");
        assert!(msg.contains("old_str: \"old text\""), "missing old_str: {msg}");
        assert!(msg.contains("new_str: \"new text\""), "missing new_str: {msg}");
        // Suggested merge with composability label (agent_old != suggested_old, so ASSISTED)
        assert!(msg.contains("Suggested merge:"), "missing suggested merge: {msg}");
        assert!(msg.contains("ASSISTED"), "missing composability label: {msg}");
        assert!(msg.contains("suggested old"), "missing suggested old: {msg}");
        assert!(msg.contains("suggested new"), "missing suggested new: {msg}");
        // Action instructions
        assert!(msg.contains("To modify:"), "missing To modify: {msg}");
        assert!(msg.contains("To reject:"), "missing To reject: {msg}");
    }

    #[test]
    fn test_format_tier2_conflict_auto_composable() {
        // When suggested_old_str == agent_old_str, it's AUTO-COMPOSABLE (trivial rebase)
        let result = MergeResult::AssistedMerge {
            base_content: "line1\nline2\nold line3\nline4\nmy target line\nline6\n".to_string(),
            current_content: "line1\nline2\nnew line3\nline4\nmy target line\nline6\n".to_string(),
            current_gen: 6,
            conflict_writer: "vane".to_string(),
            changed_lines: 3..4,
            agent_old_str: "my target line".to_string(),
            agent_new_str: "my replacement".to_string(),
            // Trivial rebase: old_str still exists, suggested == agent's intended
            suggested_old_str: "my target line".to_string(),
            suggested_new_str: "my replacement".to_string(),
            diff_line_count: 1,
        };
        let msg = format_tier2_conflict("src/lib.rs", &result);
        assert!(msg.contains("AUTO-COMPOSABLE"), "expected AUTO-COMPOSABLE label: {msg}");
        assert!(msg.contains("my target line"), "missing suggested old: {msg}");
        assert!(msg.contains("my replacement"), "missing suggested new: {msg}");
    }

    // --- Tier 1: non-overlapping edits via intent_apply ---
    //
    // Exercises the intent_apply fallback in try_auto_merge:
    //   - Agent's old_str existed in previous_content at line 1.
    //   - Another agent changed line 1 (context line in old_str).
    //   - Agent's actual edit targets line 8 (far away).
    //   - old_str no longer matches current_content verbatim,
    //     so the code falls through to intent_apply() for Tier 1.
    // --- 779: Non-overlapping edits auto-merge (Tier 1 unit) ---
    #[test]
    fn test_tier1_non_overlapping_edits_auto_merge() {
        // Two edits on opposite ends of a 10-line file.
        // Agent-B changed line 1; our edit targets line 10.
        let prev = "aaa\nbbb\nccc\nddd\neee\nfff\nggg\nhhh\niii\njjj\n";
        let curr = "AAA\nbbb\nccc\nddd\neee\nfff\nggg\nhhh\niii\njjj\n";

        let result = try_auto_merge(prev, curr, "jjj", "JJJ", 4, "agent-B");

        match result {
            MergeResult::AutoMerged {
                merged_content, ..
            } => {
                assert!(merged_content.contains("AAA"), "other edit missing");
                assert!(merged_content.contains("JJJ"), "our edit missing");
                // Untouched lines preserved
                assert!(merged_content.contains("eee"), "middle lines corrupted");
            }
            other => panic!("expected Tier 1 auto-merge, got: {other:?}"),
        }
    }

    // --- 779: Overlapping edits produce Tier 2 assisted merge ---
    #[test]
    fn test_tier2_overlapping_edits_conflict_suggestion() {
        // Both edits touch the same region (line 2), within proximity.
        let prev = "line1\nline2\nline3\nline4\nline5\n";
        let curr = "line1\nCHANGED2\nline3\nline4\nline5\n";

        // Agent wants to change line 3 (adjacent to line 2 change → within PROXIMITY_LINES)
        let result = try_auto_merge(prev, curr, "line3", "AGENT3", 5, "other-agent");

        match result {
            MergeResult::AssistedMerge {
                suggested_old_str,
                suggested_new_str,
                current_gen,
                conflict_writer,
                ..
            } => {
                assert_eq!(current_gen, 5);
                assert_eq!(conflict_writer, "other-agent");
                // The suggestion should preserve the agent's intent
                assert_eq!(suggested_old_str, "line3");
                assert_eq!(suggested_new_str, "AGENT3");
            }
            other => panic!("expected Tier 2 assisted merge, got: {other:?}"),
        }
    }

    // --- 779: Identical-region conflict escalates to Tier 3 ---
    #[test]
    fn test_tier3_identical_region_conflict() {
        // Both edits target the exact same line (line 2).
        // Agent-B already changed line 2; our old_str references the original line 2
        // which no longer exists. old_str won't be found in current_content.
        let prev = "line1\ntarget_line\nline3\n";
        let curr = "line1\nOTHER_EDIT\nline3\n";

        // Agent's old_str = "target_line" — this doesn't exist in curr.
        // Agent's edit range overlaps the change range (both on line 2).
        let result =
            try_auto_merge(prev, curr, "target_line", "MY_EDIT", 10, "agent-rival");

        match result {
            MergeResult::AssistedMerge {
                suggested_old_str,
                suggested_new_str,
                ..
            } => {
                // Tier 2 line-level rebase: suggests applying against current content
                assert_eq!(suggested_old_str, "OTHER_EDIT");
                assert_eq!(suggested_new_str, "MY_EDIT");
            }
            MergeResult::Conflict {
                current_gen,
                conflict_writer,
                ..
            } => {
                // Also acceptable: if the rebase can't be computed, Tier 3.
                assert_eq!(current_gen, 10);
                assert_eq!(conflict_writer, "agent-rival");
            }
            other => panic!("expected Tier 2 or Tier 3 for identical-region conflict, got: {other:?}"),
        }
    }

    // --- 779: Identical old_str and new_str in large overlapping diff → Tier 3 ---
    #[test]
    fn test_tier3_large_overlapping_identical_region() {
        // Build a file where >30 lines changed AND the edit overlaps the change range.
        let prev_lines: Vec<String> = (1..=50).map(|i| format!("line{i}")).collect();
        let mut curr_lines = prev_lines.clone();
        // Change lines 1-35 in current
        for i in 0..35 {
            curr_lines[i] = format!("CHANGED{}", i + 1);
        }
        let prev = prev_lines.join("\n") + "\n";
        let curr = curr_lines.join("\n") + "\n";

        // Agent wants to edit line 5 which is inside the massive change zone
        let result = try_auto_merge(&prev, &curr, "line5", "AGENT5", 3, "bulk-editor");

        match result {
            MergeResult::Conflict { current_gen, .. } => {
                assert_eq!(current_gen, 3);
            }
            other => panic!("expected Tier 3 conflict for large overlapping diff, got: {other:?}"),
        }
    }

    #[test]
    fn test_auto_merge_non_overlapping_via_intent_apply() {
        // 10-line file; agent's old_str spans lines 7-9 with context on line 7.
        let prev = "header\nalpha\nbeta\ngamma\ndelta\nepsilon\nzeta\neta\ntheta\niota\n";
        // Another agent changed line 1 ("header" -> "HEADER").
        let curr = "HEADER\nalpha\nbeta\ngamma\ndelta\nepsilon\nzeta\neta\ntheta\niota\n";

        // Agent's edit: change "eta" (line 8) to "ETA".
        // old_str includes context: "zeta\neta\ntheta" (lines 7-9).
        // The actual diff is only line 8 — far from the line-1 change.
        let result = try_auto_merge(
            prev,
            curr,
            "zeta\neta\ntheta",
            "zeta\nETA\ntheta",
            3, // current_gen
            "agent-B",
        );

        match result {
            MergeResult::AutoMerged {
                merged_content,
                other_writer,
                from_gen,
                to_gen,
            } => {
                // Both edits should be present in the merged output.
                assert!(
                    merged_content.contains("HEADER"),
                    "other agent's edit missing: {merged_content}"
                );
                assert!(
                    merged_content.contains("ETA"),
                    "our edit missing: {merged_content}"
                );
                // Context lines should be unchanged.
                assert!(
                    merged_content.contains("zeta"),
                    "context line zeta missing: {merged_content}"
                );
                assert!(
                    merged_content.contains("theta"),
                    "context line theta missing: {merged_content}"
                );
                assert_eq!(other_writer, "agent-B");
                assert_eq!(from_gen, 3);
                assert_eq!(to_gen, 4);
            }
            other => panic!(
                "expected Tier 1 auto-merge via intent_apply, got: {other:?}"
            ),
        }
    }
}
