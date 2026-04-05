/// Read elision (304 Not Modified) for the ostk kernel.
///
/// When an agent reads a file it has already read and the file hasn't changed,
/// the kernel returns a 5-token confirmation instead of the full file contents.
///
/// Two-layer token defense:
/// 1. Digest suppression: current files absent from [files] digest — agent
///    doesn't bother reading (~0 tokens).
/// 2. 304 interception: redundant reads caught by kernel (~5 tokens).
///
/// Both layers are invisible to the agent. No protocol to learn.
///
/// stat-on-access bypass detection: on every read, compare file mtime against
/// the gen table's last-known mtime. If they differ, the file was modified
/// outside ostk — invalidate all agent hwms, bump gen, return full content.
use crate::kernel::gen_table::GenTable;
use crate::kernel::hwm::Hwm;
use std::fs;
use std::path::Path;
use std::time::UNIX_EPOCH;

/// Result of a file read with elision.
#[derive(Debug, Clone)]
pub enum ReadResult {
    /// Full file content returned (first read, stale hwm, or bypass).
    Full {
        /// File contents
        content: String,
        /// Current generation number
        generation: u64,
    },
    /// 304 Not Modified — file unchanged since agent's last read.
    /// ~5 tokens instead of ~800.
    NotModified {
        /// Current generation number (confirms identity, not just "unchanged")
        generation: u64,
    },
}

impl ReadResult {
    /// Format the result for inclusion in a tool response.
    ///
    /// Full: returns the file content (caller handles wrapping).
    /// NotModified: returns the 5-token 304 response.
    pub fn format(&self, path: &str) -> String {
        match self {
            ReadResult::Full {
                content,
                generation,
            } => {
                format!("[200] {path}:gen={generation}\n{content}")
            }
            ReadResult::NotModified { generation } => {
                format!("[304] {path}:gen={generation} (current)")
            }
        }
    }

    /// Returns true if this is a 304 (elided) response.
    pub fn is_not_modified(&self) -> bool {
        matches!(self, ReadResult::NotModified { .. })
    }

    /// Returns the generation number regardless of variant.
    pub fn generation(&self) -> u64 {
        match self {
            ReadResult::Full { generation, .. } | ReadResult::NotModified { generation } => {
                *generation
            }
        }
    }
}

/// Get the current mtime of a file as seconds since epoch.
fn file_mtime_secs(path: &Path) -> Option<u64> {
    let metadata = fs::metadata(path).ok()?;
    let mtime = metadata.modified().ok()?;
    let dur = mtime.duration_since(UNIX_EPOCH).ok()?;
    Some(dur.as_secs())
}

/// Read a file with 304 elision and bypass detection.
///
/// Logic:
/// 1. Stat the file, compare mtime against gen table's last-known mtime.
/// 2. If mtime differs → bypass detected:
///    - Invalidate all agent hwms for this file
///    - Bump gen with writer="external"
///    - Log `[bypass]` event
///    - Return Full content (force re-read)
/// 3. Get current gen from gen table
/// 4. Get agent's hwm for this file
/// 5. If hwm == current gen → return NotModified (the 304)
/// 6. If hwm < current gen OR no hwm → read file, update hwm, return Full
///
/// `file_path` is the absolute path to the file.
/// `gen_path` is the project-relative path (key in gen table).
/// `agent` is the agent alias requesting the read.
/// `ostk_dir` is the .ostk directory path.
pub fn read_file_with_elision(
    file_path: &Path,
    gen_path: &str,
    agent: &str,
    ostk_dir: &Path,
) -> Result<ReadResult, String> {
    let gen_table = GenTable::new(ostk_dir);
    let hwm = Hwm::new(ostk_dir);

    // Step 1: stat-on-access bypass detection
    let current_mtime = file_mtime_secs(file_path);

    if let Some(gen_entry) = gen_table.read_gen(gen_path).map_err(|e| e.to_string())? {
        // If we can't confirm file is unchanged (missing mtime on either side),
        // treat as bypass — return full content as a safe default.
        let bypass_detected = match (gen_entry.mtime, current_mtime) {
            (Some(stored), Some(disk)) => stored != disk,
            (None, Some(_)) => true, // No stored mtime, file exists — assume changed
            _ => false,
        };

        if bypass_detected {
            // Bypass detected: file modified outside ostk.
            // Invalidate all agent hwms for this file.
            hwm.invalidate_file(gen_path)?;

            // Bump gen with writer="external" and new mtime
            let new_entry = gen_table
                .bump_gen_with_mtime(gen_path, Some("external"), current_mtime)
                .map_err(|e| e.to_string())?;

            // Log the bypass
            log_bypass(gen_path, gen_entry.generation, new_entry.generation);

            // Read and return full content
            let content = fs::read_to_string(file_path)
                .map_err(|e| format!("read file {}: {e}", file_path.display()))?;

            // Record the hwm for this agent
            hwm.record_read(agent, gen_path, new_entry.generation)?;

            return Ok(ReadResult::Full {
                content,
                generation: new_entry.generation,
            });
        }
    }

    // Step 2: Get current gen from gen table
    let gen_entry = gen_table.read_gen(gen_path).map_err(|e| e.to_string())?;

    match gen_entry {
        Some(entry) => {
            // Step 3: Check agent's hwm
            let agent_hwm = hwm.get_hwm(agent, gen_path)?;

            match agent_hwm {
                Some(hwm_gen) if hwm_gen >= entry.generation => {
                    // hwm == current gen → 304 Not Modified
                    // (use >= to handle edge cases where hwm somehow exceeds gen)
                    Ok(ReadResult::NotModified {
                        generation: entry.generation,
                    })
                }
                _ => {
                    // hwm < current gen OR no hwm → full read
                    let content = fs::read_to_string(file_path)
                        .map_err(|e| format!("read file {}: {e}", file_path.display()))?;

                    // Update hwm
                    hwm.record_read(agent, gen_path, entry.generation)?;

                    // Update stored mtime if we have it
                    if let Some(mtime) = current_mtime {
                        let _ = gen_table.update_mtime(gen_path, mtime);
                    }

                    Ok(ReadResult::Full {
                        content,
                        generation: entry.generation,
                    })
                }
            }
        }
        None => {
            // No gen table entry — first time this file is tracked.
            // Read file, create gen entry, set hwm.
            let content = fs::read_to_string(file_path)
                .map_err(|e| format!("read file {}: {e}", file_path.display()))?;

            // Bump gen to 1 with mtime
            let entry = gen_table
                .bump_gen_with_mtime(gen_path, Some("initial"), current_mtime)
                .map_err(|e| e.to_string())?;

            // Record hwm
            hwm.record_read(agent, gen_path, entry.generation)?;

            Ok(ReadResult::Full {
                content,
                generation: entry.generation,
            })
        }
    }
}

/// Log a bypass detection event.
fn log_bypass(path: &str, old_gen: u64, new_gen: u64) {
    eprintln!("[bypass] {path} externally modified, gen {old_gen}->{new_gen}");
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kernel::gen_table::GenTable;
    use std::fs;
    use std::path::PathBuf;
    use std::thread;
    use std::time::Duration;

    fn temp_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir()
            .join("ostk_test_elision")
            .join(name);
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn temp_project(name: &str) -> (PathBuf, PathBuf) {
        let base = temp_dir(name);
        let project = base.join("project");
        let ostk_state = base.join(".ostk");
        fs::create_dir_all(&project).unwrap();
        fs::create_dir_all(&ostk_state).unwrap();
        (project, ostk_state)
    }

    fn write_test_file(project: &Path, rel_path: &str, content: &str) -> PathBuf {
        let full = project.join(rel_path);
        if let Some(parent) = full.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(&full, content).unwrap();
        full
    }

    // --- Test 1: First read returns Full ---
    #[test]
    fn test_first_read_returns_full() {
        let (project, ostk_state) = temp_project("first_read");
        let file = write_test_file(&project, "src/main.rs", "fn main() {}\n");

        let result = read_file_with_elision(&file, "src/main.rs", "agent-1", &ostk_state).unwrap();

        match result {
            ReadResult::Full {
                content,
                generation,
            } => {
                assert_eq!(content, "fn main() {}\n");
                assert_eq!(generation, 1); // First gen entry created
            }
            ReadResult::NotModified { .. } => panic!("expected Full on first read"),
        }

        let _ = fs::remove_dir_all(temp_dir("first_read"));
    }

    // --- Test 2: Same file, no changes → 304 ---
    #[test]
    fn test_second_read_no_changes_304() {
        let (project, ostk_state) = temp_project("second_read");
        let file = write_test_file(&project, "src/main.rs", "fn main() {}\n");

        // First read: Full
        let r1 = read_file_with_elision(&file, "src/main.rs", "agent-1", &ostk_state).unwrap();
        assert!(!r1.is_not_modified());

        // Second read: 304
        let r2 = read_file_with_elision(&file, "src/main.rs", "agent-1", &ostk_state).unwrap();
        assert!(r2.is_not_modified());
        assert_eq!(r2.generation(), 1);

        // Verify format is ~5 tokens
        let formatted = r2.format("src/main.rs");
        assert_eq!(formatted, "[304] src/main.rs:gen=1 (current)");

        let _ = fs::remove_dir_all(temp_dir("second_read"));
    }

    // --- Test 3: Another agent writes → first agent reads → Full ---
    #[test]
    fn test_stale_hwm_returns_full() {
        let (project, ostk_state) = temp_project("stale_hwm");
        let file = write_test_file(&project, "src/main.rs", "fn main() {}\n");

        // Agent-1 reads (Full, gen=1)
        let r1 = read_file_with_elision(&file, "src/main.rs", "agent-1", &ostk_state).unwrap();
        assert_eq!(r1.generation(), 1);

        // Another agent writes (bumps gen to 2).
        // Preserve mtime to simulate a proper ostk write (not external).
        let gt = GenTable::new(&ostk_state);
        let mtime = file_mtime_secs(&file);
        gt.bump_gen_with_mtime("src/main.rs", Some("agent-2"), mtime).unwrap();

        // Agent-1 reads again: hwm=1, gen=2 → Full
        let r2 = read_file_with_elision(&file, "src/main.rs", "agent-1", &ostk_state).unwrap();
        assert!(!r2.is_not_modified());
        assert_eq!(r2.generation(), 2);

        // Agent-1 reads once more: hwm=2, gen=2 → 304
        let r3 = read_file_with_elision(&file, "src/main.rs", "agent-1", &ostk_state).unwrap();
        assert!(r3.is_not_modified());
        assert_eq!(r3.generation(), 2);

        let _ = fs::remove_dir_all(temp_dir("stale_hwm"));
    }

    // --- Test 4: External modification → bypass detected → Full ---
    #[test]
    fn test_bypass_detection_external_modification() {
        let (project, ostk_state) = temp_project("bypass");
        let file = write_test_file(&project, "src/main.rs", "original content\n");

        // Agent-1 reads (Full, gen=1, stores mtime)
        let r1 = read_file_with_elision(&file, "src/main.rs", "agent-1", &ostk_state).unwrap();
        assert_eq!(r1.generation(), 1);

        // Agent-1 reads again → 304
        let r2 = read_file_with_elision(&file, "src/main.rs", "agent-1", &ostk_state).unwrap();
        assert!(r2.is_not_modified());

        // External modification: change file outside ostk
        // Wait a moment to ensure mtime changes (filesystem granularity)
        thread::sleep(Duration::from_millis(1100));
        fs::write(&file, "externally modified content\n").unwrap();

        // Agent-1 reads again: mtime changed → bypass → Full
        let r3 = read_file_with_elision(&file, "src/main.rs", "agent-1", &ostk_state).unwrap();
        assert!(!r3.is_not_modified());
        assert_eq!(r3.generation(), 2); // Gen bumped from 1 to 2

        match r3 {
            ReadResult::Full { content, .. } => {
                assert_eq!(content, "externally modified content\n");
            }
            _ => panic!("expected Full after bypass"),
        }

        // After bypass, agent-1 reads again → 304 (hwm updated)
        let r4 = read_file_with_elision(&file, "src/main.rs", "agent-1", &ostk_state).unwrap();
        assert!(r4.is_not_modified());

        let _ = fs::remove_dir_all(temp_dir("bypass"));
    }

    // --- Test 5: Multiple agents, independent 304s ---
    #[test]
    fn test_multiple_agents_independent_304s() {
        let (project, ostk_state) = temp_project("multi_agents");
        let file_a = write_test_file(&project, "src/a.rs", "file a content\n");
        let file_b = write_test_file(&project, "src/b.rs", "file b content\n");

        // Agent-1 reads file A (Full, gen=1)
        let r1 = read_file_with_elision(&file_a, "src/a.rs", "agent-1", &ostk_state).unwrap();
        assert_eq!(r1.generation(), 1);

        // Agent-2 reads file A (Full — agent-2 hasn't read it yet, but gen exists)
        let r2 = read_file_with_elision(&file_a, "src/a.rs", "agent-2", &ostk_state).unwrap();
        assert!(!r2.is_not_modified());
        assert_eq!(r2.generation(), 1);

        // Agent-1 reads file A again → 304
        let r3 = read_file_with_elision(&file_a, "src/a.rs", "agent-1", &ostk_state).unwrap();
        assert!(r3.is_not_modified());

        // Agent-2 reads file A again → 304
        let r4 = read_file_with_elision(&file_a, "src/a.rs", "agent-2", &ostk_state).unwrap();
        assert!(r4.is_not_modified());

        // Agent-1 reads file B (Full — first time)
        let r5 = read_file_with_elision(&file_b, "src/b.rs", "agent-1", &ostk_state).unwrap();
        assert!(!r5.is_not_modified());

        // Agent-1 reads file B again → 304
        let r6 = read_file_with_elision(&file_b, "src/b.rs", "agent-1", &ostk_state).unwrap();
        assert!(r6.is_not_modified());

        // Agent-2 reads file B → Full (first time for agent-2)
        let r7 = read_file_with_elision(&file_b, "src/b.rs", "agent-2", &ostk_state).unwrap();
        assert!(!r7.is_not_modified());

        let _ = fs::remove_dir_all(temp_dir("multi_agents"));
    }

    // --- Test 6: Bypass invalidates ALL agents ---
    #[test]
    fn test_bypass_invalidates_all_agents() {
        let (project, ostk_state) = temp_project("bypass_all");
        let file = write_test_file(&project, "src/main.rs", "original\n");

        // Both agents read → Full, gen=1
        let _ = read_file_with_elision(&file, "src/main.rs", "agent-1", &ostk_state).unwrap();
        let _ = read_file_with_elision(&file, "src/main.rs", "agent-2", &ostk_state).unwrap();

        // Both should get 304 now
        let r1 = read_file_with_elision(&file, "src/main.rs", "agent-1", &ostk_state).unwrap();
        assert!(r1.is_not_modified());
        let r2 = read_file_with_elision(&file, "src/main.rs", "agent-2", &ostk_state).unwrap();
        assert!(r2.is_not_modified());

        // External modification
        thread::sleep(Duration::from_millis(1100));
        fs::write(&file, "externally modified\n").unwrap();

        // Agent-1 reads → Full (bypass detected, gen bumped to 2)
        let r3 = read_file_with_elision(&file, "src/main.rs", "agent-1", &ostk_state).unwrap();
        assert!(!r3.is_not_modified());
        assert_eq!(r3.generation(), 2);

        // Agent-2 reads → Full (hwm was invalidated by the bypass)
        let r4 = read_file_with_elision(&file, "src/main.rs", "agent-2", &ostk_state).unwrap();
        assert!(!r4.is_not_modified());
        assert_eq!(r4.generation(), 2);

        let _ = fs::remove_dir_all(temp_dir("bypass_all"));
    }

    // --- Test 7: ReadResult format ---
    #[test]
    fn test_read_result_format() {
        let full = ReadResult::Full {
            content: "hello world\n".to_string(),
            generation: 5,
        };
        assert_eq!(
            full.format("src/main.rs"),
            "[200] src/main.rs:gen=5\nhello world\n"
        );

        let not_mod = ReadResult::NotModified { generation: 7 };
        assert_eq!(
            not_mod.format("src/main.rs"),
            "[304] src/main.rs:gen=7 (current)"
        );
    }

    // --- Test 8: 304 format is ~5 tokens ---
    #[test]
    fn test_304_token_count() {
        let not_mod = ReadResult::NotModified { generation: 42 };
        let formatted = not_mod.format("src/main.rs");
        // "[304] src/main.rs:gen=42 (current)" — count whitespace-separated tokens
        let tokens: Vec<&str> = formatted.split_whitespace().collect();
        assert!(
            tokens.len() <= 5,
            "304 response should be ~5 tokens, got {}: {:?}",
            tokens.len(),
            tokens
        );
    }

    // --- 779: same file read twice → 304 Not Modified ---
    #[test]
    fn test_same_file_read_twice_returns_304() {
        let (project, ostk_state) = temp_project("read_twice_304");
        let file = write_test_file(&project, "src/utils.rs", "pub fn util() {}\n");

        // First read: must be Full
        let r1 = read_file_with_elision(&file, "src/utils.rs", "agent-A", &ostk_state).unwrap();
        assert!(!r1.is_not_modified(), "first read should be Full");
        assert_eq!(r1.generation(), 1);

        // Second read: identical file → 304
        let r2 = read_file_with_elision(&file, "src/utils.rs", "agent-A", &ostk_state).unwrap();
        assert!(r2.is_not_modified(), "second read of unchanged file should be 304");
        assert_eq!(r2.generation(), 1);

        let _ = fs::remove_dir_all(temp_dir("read_twice_304"));
    }

    // --- 779: modified file between reads → full content ---
    #[test]
    fn test_modified_file_between_reads_returns_full() {
        let (project, ostk_state) = temp_project("modified_between");
        let file = write_test_file(&project, "src/config.rs", "v1 content\n");

        // First read: Full
        let r1 = read_file_with_elision(&file, "src/config.rs", "agent-A", &ostk_state).unwrap();
        assert!(!r1.is_not_modified());
        assert_eq!(r1.generation(), 1);

        // Another agent writes → bumps gen to 2.
        // Modify file content on disk first, then bump gen with the new mtime
        // to simulate a proper ostk write (not external).
        fs::write(&file, "v2 content\n").unwrap();
        let gt = GenTable::new(&ostk_state);
        let mtime = file_mtime_secs(&file);
        gt.bump_gen_with_mtime("src/config.rs", Some("agent-B"), mtime).unwrap();

        // Agent-A reads again: gen is now 2 but hwm is 1 → Full
        let r2 = read_file_with_elision(&file, "src/config.rs", "agent-A", &ostk_state).unwrap();
        assert!(!r2.is_not_modified(), "should return Full after gen bump");
        assert_eq!(r2.generation(), 2);

        match r2 {
            ReadResult::Full { content, .. } => {
                assert_eq!(content, "v2 content\n");
            }
            _ => panic!("expected Full with new content"),
        }

        // Now agent-A reads again → 304 (hwm caught up)
        let r3 = read_file_with_elision(&file, "src/config.rs", "agent-A", &ostk_state).unwrap();
        assert!(r3.is_not_modified(), "third read with no changes should be 304");

        let _ = fs::remove_dir_all(temp_dir("modified_between"));
    }

    // --- Test: None stored mtime with file on disk → Full (not 304) ---
    #[test]
    fn test_elision_none_mtime_returns_full() {
        let (project, ostk_state) = temp_project("none_mtime_full");
        let file = write_test_file(&project, "src/lib.rs", "pub fn lib() {}\n");

        // First read establishes gen entry with mtime
        let r1 = read_file_with_elision(&file, "src/lib.rs", "agent-1", &ostk_state).unwrap();
        assert_eq!(r1.generation(), 1);

        // Manually clear the stored mtime to simulate None state
        let gt = GenTable::new(&ostk_state);
        gt.clear_mtime("src/lib.rs").unwrap();

        // Next read: stored mtime is None, disk mtime exists → bypass → Full
        let r2 = read_file_with_elision(&file, "src/lib.rs", "agent-1", &ostk_state).unwrap();
        assert!(
            !r2.is_not_modified(),
            "should return Full when stored mtime is None (unknown state)"
        );

        let _ = fs::remove_dir_all(temp_dir("none_mtime_full"));
    }

    // --- Test: Both mtimes match → 304 is correct ---
    #[test]
    fn test_elision_both_mtime_match_returns_304() {
        let (project, ostk_state) = temp_project("both_mtime_304");
        let file = write_test_file(&project, "src/lib.rs", "pub fn lib() {}\n");

        // First read: Full, records mtime
        let r1 = read_file_with_elision(&file, "src/lib.rs", "agent-1", &ostk_state).unwrap();
        assert!(!r1.is_not_modified());
        assert_eq!(r1.generation(), 1);

        // Second read: mtimes match → 304
        let r2 = read_file_with_elision(&file, "src/lib.rs", "agent-1", &ostk_state).unwrap();
        assert!(
            r2.is_not_modified(),
            "should return 304 when both mtimes match"
        );
        assert_eq!(r2.generation(), 1);

        let _ = fs::remove_dir_all(temp_dir("both_mtime_304"));
    }

    // --- Test: Mtimes differ → Full content returned ---
    #[test]
    fn test_elision_mtime_mismatch_returns_full() {
        let (project, ostk_state) = temp_project("mtime_mismatch");
        let file = write_test_file(&project, "src/lib.rs", "original\n");

        // First read: Full
        let r1 = read_file_with_elision(&file, "src/lib.rs", "agent-1", &ostk_state).unwrap();
        assert_eq!(r1.generation(), 1);

        // Second read: 304
        let r2 = read_file_with_elision(&file, "src/lib.rs", "agent-1", &ostk_state).unwrap();
        assert!(r2.is_not_modified());

        // External modification with different mtime
        thread::sleep(Duration::from_millis(1100));
        fs::write(&file, "changed\n").unwrap();

        // Third read: mtime mismatch → Full
        let r3 = read_file_with_elision(&file, "src/lib.rs", "agent-1", &ostk_state).unwrap();
        assert!(
            !r3.is_not_modified(),
            "should return Full when mtimes differ"
        );
        assert_eq!(r3.generation(), 2);

        let _ = fs::remove_dir_all(temp_dir("mtime_mismatch"));
    }

    /// Full pipeline: first read = Full (200), second read = NotModified (304),
    /// verifying both the ReadResult variant and its formatted output.
    #[test]
    fn test_elision_full_then_not_modified_format() {
        let (project, ostk_state) = temp_project("full_then_304_fmt");
        let file = write_test_file(&project, "src/demo.rs", "fn demo() {}\n");

        // First read: must be Full / 200
        let r1 = read_file_with_elision(&file, "src/demo.rs", "agent-X", &ostk_state).unwrap();
        assert!(!r1.is_not_modified(), "first read must be Full");
        let f1 = r1.format("src/demo.rs");
        assert!(
            f1.starts_with("[200]"),
            "first read format must start with [200]: {f1}"
        );
        assert!(
            f1.contains("fn demo()"),
            "first read format must include file content: {f1}"
        );

        // Second read: must be NotModified / 304
        let r2 = read_file_with_elision(&file, "src/demo.rs", "agent-X", &ostk_state).unwrap();
        assert!(r2.is_not_modified(), "second read must be NotModified");
        let f2 = r2.format("src/demo.rs");
        assert!(
            f2.starts_with("[304]"),
            "second read format must start with [304]: {f2}"
        );
        assert!(
            !f2.contains("fn demo()"),
            "304 response must NOT include file content: {f2}"
        );
        // Both reads agree on generation.
        assert_eq!(r1.generation(), r2.generation());

        let _ = fs::remove_dir_all(temp_dir("full_then_304_fmt"));
    }
}
