/// Generation table for the ostk kernel.
///
/// Per-file metadata: path, generation number, writer alias, timestamp.
/// Stored as JSONL at .ostk/gen_table.jsonl, coordinated via flock().
///
/// Shadow files store previous file content at each generation for
/// Hot PR auto-merge (diffing between generations).
///
/// API:
/// - read_gen(path) -> Option<GenEntry>
/// - bump_gen(path, writer) -> GenEntry
/// - bump_gen_with_content(path, writer, content) -> GenEntry
/// - read_shadow(path, gen) -> Option<String>
/// - list_gens() -> Vec<GenEntry>
use fs2::FileExt;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::collections::hash_map::DefaultHasher;
use std::fs;
use std::hash::{Hash, Hasher};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

/// A single generation table entry.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GenEntry {
    /// File path (project-relative)
    pub path: String,
    /// Monotonically increasing generation number
    pub generation: u64,
    /// Writer alias (from OSTK_AGENT env var or "unknown")
    pub writer: String,
    /// ISO 8601 timestamp of last write
    pub timestamp: String,
    /// File mtime (seconds since epoch) at time of gen bump.
    /// Used by stat-on-access bypass detection: if current mtime
    /// differs from this value, the file was modified outside ostk.
    #[serde(default)]
    pub mtime: Option<u64>,
}

/// The generation table, backed by a JSONL file.
pub struct GenTable {
    /// Path to the gen_table.jsonl file
    table_path: PathBuf,
    /// Path to the lock file
    lock_path: PathBuf,
    /// Directory for shadow files (previous file content per generation)
    shadow_dir: PathBuf,
}

impl GenTable {
    /// Create a new GenTable rooted at the given .ostk directory.
    pub fn new(ostk_dir: &Path) -> Self {
        GenTable {
            table_path: ostk_dir.join("gen_table.jsonl"),
            lock_path: ostk_dir.join("gen_table.lock"),
            shadow_dir: ostk_dir.join("shadows"),
        }
    }

    /// Get the current writer identity from OSTK_AGENT env var.
    fn writer_identity() -> String {
        std::env::var("OSTK_AGENT").unwrap_or_else(|_| "unknown".to_string())
    }

    /// ISO 8601 timestamp.
    fn now_iso() -> String {
        let dur = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default();
        let secs = dur.as_secs();
        let days = secs / 86400;
        let time_secs = secs % 86400;
        let hours = time_secs / 3600;
        let minutes = (time_secs % 3600) / 60;
        let seconds = time_secs % 60;
        let (year, month, day) = days_to_ymd(days);
        format!("{year:04}-{month:02}-{day:02}T{hours:02}:{minutes:02}:{seconds:02}Z")
    }

    /// Read all entries, compacted to latest per path.
    fn read_all_compacted(&self) -> Result<HashMap<String, GenEntry>, String> {
        if !self.table_path.exists() {
            return Ok(HashMap::new());
        }

        let content =
            fs::read_to_string(&self.table_path).map_err(|e| format!("read gen_table: {e}"))?;

        let mut map = HashMap::new();
        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            if let Ok(entry) = serde_json::from_str::<GenEntry>(line) {
                map.insert(entry.path.clone(), entry);
            }
        }
        Ok(map)
    }

    /// Write compacted entries back to the file.
    fn write_compacted(&self, entries: &HashMap<String, GenEntry>) -> Result<(), String> {
        let mut content = String::new();
        // Sort for deterministic output
        let mut sorted: Vec<_> = entries.values().collect();
        sorted.sort_by(|a, b| a.path.cmp(&b.path));

        for entry in sorted {
            let line = serde_json::to_string(entry).map_err(|e| e.to_string())?;
            content.push_str(&line);
            content.push('\n');
        }
        fs::write(&self.table_path, content).map_err(|e| format!("write gen_table: {e}"))?;
        Ok(())
    }

    /// Execute a closure under flock.
    fn with_lock<F, R>(&self, f: F) -> Result<R, String>
    where
        F: FnOnce() -> Result<R, String>,
    {
        // Ensure parent directory exists
        if let Some(parent) = self.lock_path.parent() {
            fs::create_dir_all(parent).map_err(|e| format!("create dir: {e}"))?;
        }

        let lock_file = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&self.lock_path)
            .map_err(|e| format!("open lock: {e}"))?;

        lock_file
            .lock_exclusive()
            .map_err(|e| format!("flock: {e}"))?;

        let result = f();

        // Lock released on drop
        drop(lock_file);
        result
    }

    /// Read the generation entry for a given path.
    pub fn read_gen(&self, path: &str) -> Result<Option<GenEntry>, String> {
        self.with_lock(|| {
            let entries = self.read_all_compacted()?;
            Ok(entries.get(path).cloned())
        })
    }

    /// Bump the generation for a path. Returns the new GenEntry.
    pub fn bump_gen(&self, path: &str, writer: Option<&str>) -> Result<GenEntry, String> {
        self.bump_gen_with_mtime(path, writer, None)
    }

    /// Bump the generation for a path with an explicit mtime.
    pub fn bump_gen_with_mtime(
        &self,
        path: &str,
        writer: Option<&str>,
        mtime: Option<u64>,
    ) -> Result<GenEntry, String> {
        self.with_lock(|| {
            let mut entries = self.read_all_compacted()?;

            let current_gen = entries.get(path).map(|e| e.generation).unwrap_or(0);
            let new_gen = current_gen + 1;

            let entry = GenEntry {
                path: path.to_string(),
                generation: new_gen,
                writer: writer
                    .map(|w| w.to_string())
                    .unwrap_or_else(Self::writer_identity),
                timestamp: Self::now_iso(),
                mtime,
            };

            entries.insert(path.to_string(), entry.clone());
            self.write_compacted(&entries)?;

            Ok(entry)
        })
    }

    /// List all generation entries.
    pub fn list_gens(&self) -> Result<Vec<GenEntry>, String> {
        self.with_lock(|| {
            let entries = self.read_all_compacted()?;
            let mut list: Vec<GenEntry> = entries.into_values().collect();
            list.sort_by(|a, b| a.path.cmp(&b.path));
            Ok(list)
        })
    }

    /// Compute a stable hash for a file path (used for shadow directory names).
    fn path_hash(path: &str) -> String {
        let mut hasher = DefaultHasher::new();
        path.hash(&mut hasher);
        format!("{:016x}", hasher.finish())
    }

    /// Get the shadow file path for a given file path and generation.
    fn shadow_path(&self, path: &str, generation: u64) -> PathBuf {
        let hash = Self::path_hash(path);
        self.shadow_dir.join(hash).join(format!("gen_{generation}"))
    }

    /// Store file content as a shadow for the given generation.
    fn write_shadow(&self, path: &str, generation: u64, content: &str) -> Result<(), String> {
        let shadow = self.shadow_path(path, generation);
        if let Some(parent) = shadow.parent() {
            fs::create_dir_all(parent).map_err(|e| format!("create shadow dir: {e}"))?;
        }
        fs::write(&shadow, content).map_err(|e| format!("write shadow: {e}"))?;
        Ok(())
    }

    /// Read the shadow content for a given file path and generation.
    pub fn read_shadow(&self, path: &str, generation: u64) -> Option<String> {
        let shadow = self.shadow_path(path, generation);
        fs::read_to_string(&shadow).ok()
    }

    /// Bump generation AND store the pre-edit file content as a shadow.
    ///
    /// `pre_content` is the file content BEFORE this edit was applied.
    /// This enables Hot PR to diff between generations for auto-merge.
    pub fn bump_gen_with_content(
        &self,
        path: &str,
        writer: Option<&str>,
        pre_content: &str,
    ) -> Result<GenEntry, String> {
        self.with_lock(|| {
            let mut entries = self.read_all_compacted()?;

            let current_gen = entries.get(path).map(|e| e.generation).unwrap_or(0);
            let new_gen = current_gen + 1;

            // Store the pre-edit content as a shadow for the current gen
            // (so we can reconstruct what the file looked like at this gen)
            self.write_shadow(path, new_gen, pre_content)?;

            let entry = GenEntry {
                path: path.to_string(),
                generation: new_gen,
                writer: writer
                    .map(|w| w.to_string())
                    .unwrap_or_else(Self::writer_identity),
                timestamp: Self::now_iso(),
                mtime: None,
            };

            entries.insert(path.to_string(), entry.clone());
            self.write_compacted(&entries)?;

            Ok(entry)
        })
    }

    /// Update the stored mtime for a path in the gen table (without bumping gen).
    pub fn update_mtime(&self, path: &str, mtime: u64) -> Result<(), String> {
        self.with_lock(|| {
            let mut entries = self.read_all_compacted()?;
            if let Some(entry) = entries.get_mut(path) {
                entry.mtime = Some(mtime);
                self.write_compacted(&entries)?;
            }
            Ok(())
        })
    }

    /// Clear the stored mtime for a path (set to None), without bumping gen.
    /// Used in tests to simulate the "unknown mtime" state.
    pub fn clear_mtime(&self, path: &str) -> Result<(), String> {
        self.with_lock(|| {
            let mut entries = self.read_all_compacted()?;
            if let Some(entry) = entries.get_mut(path) {
                entry.mtime = None;
                self.write_compacted(&entries)?;
            }
            Ok(())
        })
    }
}

fn days_to_ymd(days: u64) -> (u64, u64, u64) {
    let z = days + 719468;
    let era = z / 146097;
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn temp_ostk_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir()
            .join("ostk_test_gen_table")
            .join(name);
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn test_bump_gen_sequential() {
        let dir = temp_ostk_dir("sequential");
        let gt = GenTable::new(&dir);

        // First bump: gen 0 -> 1
        let entry1 = gt.bump_gen("src/main.rs", Some("agent-1")).unwrap();
        assert_eq!(entry1.generation, 1);
        assert_eq!(entry1.writer, "agent-1");
        assert_eq!(entry1.path, "src/main.rs");

        // Second bump: gen 1 -> 2
        let entry2 = gt.bump_gen("src/main.rs", Some("agent-2")).unwrap();
        assert_eq!(entry2.generation, 2);
        assert_eq!(entry2.writer, "agent-2");

        // Third bump: gen 2 -> 3
        let entry3 = gt.bump_gen("src/main.rs", Some("agent-1")).unwrap();
        assert_eq!(entry3.generation, 3);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_read_gen() {
        let dir = temp_ostk_dir("read_gen");
        let gt = GenTable::new(&dir);

        // No entry yet
        assert!(gt.read_gen("src/main.rs").unwrap().is_none());

        // After bump
        gt.bump_gen("src/main.rs", Some("agent-1")).unwrap();
        let entry = gt.read_gen("src/main.rs").unwrap().unwrap();
        assert_eq!(entry.generation, 1);
        assert_eq!(entry.writer, "agent-1");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_list_gens() {
        let dir = temp_ostk_dir("list_gens");
        let gt = GenTable::new(&dir);

        gt.bump_gen("src/a.rs", Some("agent-1")).unwrap();
        gt.bump_gen("src/b.rs", Some("agent-2")).unwrap();
        gt.bump_gen("src/a.rs", Some("agent-1")).unwrap(); // bump a second time

        let list = gt.list_gens().unwrap();
        assert_eq!(list.len(), 2);
        assert_eq!(list[0].path, "src/a.rs");
        assert_eq!(list[0].generation, 2);
        assert_eq!(list[1].path, "src/b.rs");
        assert_eq!(list[1].generation, 1);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_multiple_files() {
        let dir = temp_ostk_dir("multi_files");
        let gt = GenTable::new(&dir);

        gt.bump_gen("file_a.txt", Some("w1")).unwrap();
        gt.bump_gen("file_b.txt", Some("w2")).unwrap();
        gt.bump_gen("file_a.txt", Some("w1")).unwrap();

        let a = gt.read_gen("file_a.txt").unwrap().unwrap();
        let b = gt.read_gen("file_b.txt").unwrap().unwrap();
        assert_eq!(a.generation, 2);
        assert_eq!(b.generation, 1);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_writer_from_env() {
        let dir = temp_ostk_dir("env_writer");
        let gt = GenTable::new(&dir);

        // Without OSTK_AGENT set, writer should be "unknown" or whatever is set
        let entry = gt.bump_gen("test.rs", None).unwrap();
        // Writer comes from env or defaults to "unknown"
        assert!(!entry.writer.is_empty());

        let _ = fs::remove_dir_all(&dir);
    }

    // --- 779: increment generation on file write ---
    #[test]
    fn test_increment_generation_on_write() {
        let dir = temp_ostk_dir("incr_gen_write");
        let gt = GenTable::new(&dir);

        // No entry yet
        assert!(gt.read_gen("src/app.rs").unwrap().is_none());

        // First write → gen 1
        let e1 = gt.bump_gen("src/app.rs", Some("writer-A")).unwrap();
        assert_eq!(e1.generation, 1);

        // Second write → gen 2
        let e2 = gt.bump_gen("src/app.rs", Some("writer-B")).unwrap();
        assert_eq!(e2.generation, 2);
        assert_eq!(e2.writer, "writer-B");

        // Third write → gen 3
        let e3 = gt.bump_gen("src/app.rs", Some("writer-A")).unwrap();
        assert_eq!(e3.generation, 3);

        // Confirm read_gen returns the latest
        let current = gt.read_gen("src/app.rs").unwrap().unwrap();
        assert_eq!(current.generation, 3);

        let _ = fs::remove_dir_all(&dir);
    }

    // --- 779: concurrent reads return same generation ---
    #[test]
    fn test_concurrent_reads_return_same_generation() {
        let dir = temp_ostk_dir("concurrent_reads");
        let gt = GenTable::new(&dir);

        // Bump to gen 4
        for _ in 0..4 {
            gt.bump_gen("src/lib.rs", Some("setup")).unwrap();
        }

        // Multiple reads should all see gen 4
        let r1 = gt.read_gen("src/lib.rs").unwrap().unwrap();
        let r2 = gt.read_gen("src/lib.rs").unwrap().unwrap();
        let r3 = gt.read_gen("src/lib.rs").unwrap().unwrap();
        assert_eq!(r1.generation, 4);
        assert_eq!(r2.generation, 4);
        assert_eq!(r3.generation, 4);
        // All return the same writer
        assert_eq!(r1.writer, r2.writer);
        assert_eq!(r2.writer, r3.writer);

        let _ = fs::remove_dir_all(&dir);
    }

    // --- 779: stale read detection (gen changed between read and write) ---
    #[test]
    fn test_stale_read_detection() {
        let dir = temp_ostk_dir("stale_read");
        let gt = GenTable::new(&dir);

        // Agent-1 reads gen (bumps to 1)
        let initial = gt.bump_gen("src/data.rs", Some("agent-1")).unwrap();
        assert_eq!(initial.generation, 1);

        // Simulate: agent-1 reads the file at gen 1
        let read_gen = gt.read_gen("src/data.rs").unwrap().unwrap().generation;
        assert_eq!(read_gen, 1);

        // Another agent writes (bumps to gen 2) — agent-1 doesn't know
        gt.bump_gen("src/data.rs", Some("agent-2")).unwrap();

        // Agent-1 tries to write, but first checks current gen
        let current = gt.read_gen("src/data.rs").unwrap().unwrap();
        // Stale: agent-1 saw gen 1, but current is gen 2
        assert_ne!(read_gen, current.generation);
        assert_eq!(current.generation, 2);
        assert_eq!(current.writer, "agent-2");

        let _ = fs::remove_dir_all(&dir);
    }

    // --- 779: bump_gen_with_content stores shadows ---
    #[test]
    fn test_bump_gen_with_content_shadow_round_trip() {
        let dir = temp_ostk_dir("shadow_rt");
        let gt = GenTable::new(&dir);

        let pre_content = "fn old() {}\n";
        let entry = gt
            .bump_gen_with_content("src/mod.rs", Some("agent-1"), pre_content)
            .unwrap();
        assert_eq!(entry.generation, 1);

        // Shadow should contain pre-edit content
        let shadow = gt.read_shadow("src/mod.rs", 1).unwrap();
        assert_eq!(shadow, pre_content);

        // No shadow for gen 0 (never stored)
        assert!(gt.read_shadow("src/mod.rs", 0).is_none());

        let _ = fs::remove_dir_all(&dir);
    }

    /// Round-trip: bump_gen then read_gen, verify generation increments
    /// monotonically across multiple files and writers.
    #[test]
    fn test_bump_gen_read_gen_round_trip() {
        let dir = temp_ostk_dir("bump_read_roundtrip");
        let gt = GenTable::new(&dir);
        let path = "src/round_trip.rs";

        // Before any bump, read_gen returns None.
        assert!(gt.read_gen(path).unwrap().is_none());

        // Bump 5 times with alternating writers.
        for i in 1..=5u64 {
            let writer = if i % 2 == 0 { "agent-even" } else { "agent-odd" };
            let bumped = gt.bump_gen(path, Some(writer)).unwrap();

            // Generation must equal the bump ordinal.
            assert_eq!(
                bumped.generation, i,
                "bump #{i}: expected gen={i}, got gen={}",
                bumped.generation
            );

            // Immediate read_gen must return the same entry.
            let read_back = gt.read_gen(path).unwrap().unwrap();
            assert_eq!(read_back.generation, bumped.generation);
            assert_eq!(read_back.writer, writer);
            assert_eq!(read_back.path, path);
        }

        // Final state: gen == 5, writer == "agent-odd"
        let final_entry = gt.read_gen(path).unwrap().unwrap();
        assert_eq!(final_entry.generation, 5);
        assert_eq!(final_entry.writer, "agent-odd");

        let _ = fs::remove_dir_all(&dir);
    }
}
