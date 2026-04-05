/// High-water mark tracking for staleness signals.
///
/// Tracks per-agent read high-water marks in .ostk/hwm.jsonl.
/// On file read: record the gen the agent saw.
/// On any response: if file gen > agent's hwm, flag `[stale] path:gen=N yours=M`.
use fs2::FileExt;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

/// A high-water mark entry: tracks the last generation an agent read for a file.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HwmEntry {
    /// Agent alias
    pub agent: String,
    /// File path (project-relative)
    pub path: String,
    /// Generation number the agent last read
    pub generation: u64,
}

/// A staleness signal for a single file.
#[derive(Debug, Clone)]
pub struct StaleFile {
    /// File path
    pub path: String,
    /// Current generation of the file
    pub current_gen: u64,
    /// Generation the agent last read
    pub agent_gen: u64,
    /// Who last wrote the file
    pub writer: String,
}

impl StaleFile {
    /// Format as a stale signal: `[stale] path:gen=N yours=M behind=K`
    pub fn format(&self) -> String {
        let behind = self.current_gen - self.agent_gen;
        format!(
            "[stale] {}:gen={} yours={} behind={}",
            self.path, self.current_gen, self.agent_gen, behind
        )
    }
}

/// High-water mark tracker.
pub struct Hwm {
    /// Path to .ostk directory
    ostk_dir: PathBuf,
}

impl Hwm {
    /// Create a new Hwm tracker rooted at the given .ostk directory.
    pub fn new(ostk_dir: &Path) -> Self {
        Hwm {
            ostk_dir: ostk_dir.to_path_buf(),
        }
    }

    fn hwm_path(&self) -> PathBuf {
        self.ostk_dir.join("hwm.jsonl")
    }

    fn lock_path(&self) -> PathBuf {
        self.ostk_dir.join("hwm.lock")
    }

    /// Execute a closure under flock.
    fn with_lock<F, R>(&self, f: F) -> Result<R, String>
    where
        F: FnOnce() -> Result<R, String>,
    {
        fs::create_dir_all(&self.ostk_dir).map_err(|e| format!("create .ostk dir: {e}"))?;

        let lock_file = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(self.lock_path())
            .map_err(|e| format!("open hwm lock: {e}"))?;

        lock_file
            .lock_exclusive()
            .map_err(|e| format!("flock hwm: {e}"))?;

        let result = f();
        drop(lock_file);
        result
    }

    /// Read all HWM entries, compacted to latest per (agent, path) pair.
    fn read_all(&self) -> Result<HashMap<(String, String), HwmEntry>, String> {
        let path = self.hwm_path();
        if !path.exists() {
            return Ok(HashMap::new());
        }

        let content = fs::read_to_string(&path).map_err(|e| format!("read hwm.jsonl: {e}"))?;

        let mut map = HashMap::new();
        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            if let Ok(entry) = serde_json::from_str::<HwmEntry>(line) {
                map.insert((entry.agent.clone(), entry.path.clone()), entry);
            }
        }
        Ok(map)
    }

    /// Write all entries back (compacted).
    fn write_all(&self, entries: &HashMap<(String, String), HwmEntry>) -> Result<(), String> {
        let path = self.hwm_path();
        let mut content = String::new();
        let mut sorted: Vec<_> = entries.values().collect();
        sorted.sort_by(|a, b| (&a.agent, &a.path).cmp(&(&b.agent, &b.path)));

        for entry in sorted {
            let line = serde_json::to_string(entry).map_err(|e| e.to_string())?;
            content.push_str(&line);
            content.push('\n');
        }
        fs::write(&path, content).map_err(|e| format!("write hwm.jsonl: {e}"))?;
        Ok(())
    }

    /// Record that an agent read a file at a specific generation.
    pub fn record_read(&self, agent: &str, file_path: &str, generation: u64) -> Result<(), String> {
        self.with_lock(|| {
            let mut entries = self.read_all()?;
            let key = (agent.to_string(), file_path.to_string());

            // Only update if this generation is higher than what we've seen
            let should_update = match entries.get(&key) {
                Some(existing) => generation > existing.generation,
                None => true,
            };

            if should_update {
                entries.insert(
                    key,
                    HwmEntry {
                        agent: agent.to_string(),
                        path: file_path.to_string(),
                        generation,
                    },
                );
                self.write_all(&entries)?;
            }

            Ok(())
        })
    }

    /// Get the high-water mark for an agent on a specific file.
    pub fn get_hwm(&self, agent: &str, file_path: &str) -> Result<Option<u64>, String> {
        self.with_lock(|| {
            let entries = self.read_all()?;
            let key = (agent.to_string(), file_path.to_string());
            Ok(entries.get(&key).map(|e| e.generation))
        })
    }

    /// Get all HWM entries for an agent.
    pub fn get_agent_hwms(&self, agent: &str) -> Result<Vec<HwmEntry>, String> {
        self.with_lock(|| {
            let entries = self.read_all()?;
            Ok(entries.into_values().filter(|e| e.agent == agent).collect())
        })
    }

    /// Invalidate all agent HWMs for a specific file.
    ///
    /// Used when external modification is detected (bypass): all agents
    /// must re-read the file, so their hwms are removed.
    pub fn invalidate_file(&self, file_path: &str) -> Result<(), String> {
        self.with_lock(|| {
            let mut entries = self.read_all()?;
            let keys_to_remove: Vec<(String, String)> = entries
                .keys()
                .filter(|(_, p)| p == file_path)
                .cloned()
                .collect();
            for key in keys_to_remove {
                entries.remove(&key);
            }
            self.write_all(&entries)?;
            Ok(())
        })
    }

    /// Check staleness: compare an agent's HWMs against the gen table.
    /// Returns a list of files where the current gen exceeds the agent's last read.
    pub fn check_staleness(
        &self,
        agent: &str,
        gen_table: &crate::kernel::gen_table::GenTable,
    ) -> Result<Vec<StaleFile>, String> {
        let all_gens = gen_table.list_gens()?;
        self.check_staleness_with_gens(agent, &all_gens)
    }

    /// Check staleness against a pre-fetched gen snapshot.
    /// Use this when you need to read gen_table once and pass the same snapshot
    /// to multiple consumers (e.g., digest generation) for atomicity.
    pub fn check_staleness_with_gens(
        &self,
        agent: &str,
        all_gens: &[crate::kernel::gen_table::GenEntry],
    ) -> Result<Vec<StaleFile>, String> {
        let agent_hwms = self.get_agent_hwms(agent)?;

        let gen_map: HashMap<&str, &crate::kernel::gen_table::GenEntry> =
            all_gens.iter().map(|e| (e.path.as_str(), e)).collect();

        let mut stale = Vec::new();
        for hwm in &agent_hwms {
            if let Some(gen_entry) = gen_map.get(hwm.path.as_str())
                && gen_entry.generation > hwm.generation
            {
                stale.push(StaleFile {
                    path: hwm.path.clone(),
                    current_gen: gen_entry.generation,
                    agent_gen: hwm.generation,
                    writer: gen_entry.writer.clone(),
                });
            }
        }

        Ok(stale)
    }
}

/// Format multiple stale signals into a single string.
pub fn format_stale_signals(stale_files: &[StaleFile]) -> String {
    if stale_files.is_empty() {
        return String::new();
    }
    stale_files
        .iter()
        .map(|s| s.format())
        .collect::<Vec<_>>()
        .join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kernel::gen_table::GenTable;
    use std::fs;
    use std::path::PathBuf;

    fn temp_ostk_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join("ostk_test_hwm").join(name);
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn test_record_and_get_hwm() {
        let dir = temp_ostk_dir("record_get");
        let hwm = Hwm::new(&dir);

        // No HWM yet
        assert!(hwm.get_hwm("agent-1", "src/main.rs").unwrap().is_none());

        // Record a read
        hwm.record_read("agent-1", "src/main.rs", 5).unwrap();
        assert_eq!(hwm.get_hwm("agent-1", "src/main.rs").unwrap(), Some(5));

        // Record a higher gen
        hwm.record_read("agent-1", "src/main.rs", 7).unwrap();
        assert_eq!(hwm.get_hwm("agent-1", "src/main.rs").unwrap(), Some(7));

        // Lower gen should not update
        hwm.record_read("agent-1", "src/main.rs", 3).unwrap();
        assert_eq!(hwm.get_hwm("agent-1", "src/main.rs").unwrap(), Some(7));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_multiple_agents_files() {
        let dir = temp_ostk_dir("multi_agents");
        let hwm = Hwm::new(&dir);

        hwm.record_read("agent-1", "src/a.rs", 3).unwrap();
        hwm.record_read("agent-1", "src/b.rs", 5).unwrap();
        hwm.record_read("agent-2", "src/a.rs", 2).unwrap();

        assert_eq!(hwm.get_hwm("agent-1", "src/a.rs").unwrap(), Some(3));
        assert_eq!(hwm.get_hwm("agent-1", "src/b.rs").unwrap(), Some(5));
        assert_eq!(hwm.get_hwm("agent-2", "src/a.rs").unwrap(), Some(2));
        assert!(hwm.get_hwm("agent-2", "src/b.rs").unwrap().is_none());

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_staleness_detection() {
        let dir = temp_ostk_dir("staleness");
        let hwm = Hwm::new(&dir);
        let gt = GenTable::new(&dir);

        // Agent reads file at gen 5
        hwm.record_read("agent-1", "src/main.rs", 5).unwrap();

        // Another agent bumps gen to 7
        gt.bump_gen("src/main.rs", Some("agent-2")).unwrap(); // gen 1
        gt.bump_gen("src/main.rs", Some("agent-2")).unwrap(); // gen 2
        gt.bump_gen("src/main.rs", Some("agent-2")).unwrap(); // gen 3
        gt.bump_gen("src/main.rs", Some("agent-2")).unwrap(); // gen 4
        gt.bump_gen("src/main.rs", Some("agent-2")).unwrap(); // gen 5
        gt.bump_gen("src/main.rs", Some("agent-2")).unwrap(); // gen 6
        gt.bump_gen("src/main.rs", Some("agent-2")).unwrap(); // gen 7

        // Check staleness
        let stale = hwm.check_staleness("agent-1", &gt).unwrap();
        assert_eq!(stale.len(), 1);
        assert_eq!(stale[0].path, "src/main.rs");
        assert_eq!(stale[0].current_gen, 7);
        assert_eq!(stale[0].agent_gen, 5);
        assert_eq!(stale[0].writer, "agent-2");

        let signal = stale[0].format();
        assert_eq!(signal, "[stale] src/main.rs:gen=7 yours=5 behind=2");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_no_staleness_when_current() {
        let dir = temp_ostk_dir("no_staleness");
        let hwm = Hwm::new(&dir);
        let gt = GenTable::new(&dir);

        gt.bump_gen("src/main.rs", Some("agent-1")).unwrap(); // gen 1

        // Agent reads at current gen
        hwm.record_read("agent-1", "src/main.rs", 1).unwrap();

        let stale = hwm.check_staleness("agent-1", &gt).unwrap();
        assert!(stale.is_empty());

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_format_stale_signals() {
        let stale = vec![
            StaleFile {
                path: "src/main.rs".to_string(),
                current_gen: 7,
                agent_gen: 5,
                writer: "agent-2".to_string(),
            },
            StaleFile {
                path: "src/lib.rs".to_string(),
                current_gen: 3,
                agent_gen: 1,
                writer: "agent-3".to_string(),
            },
        ];
        let output = format_stale_signals(&stale);
        assert!(output.contains("[stale] src/main.rs:gen=7 yours=5 behind=2"));
        assert!(output.contains("[stale] src/lib.rs:gen=3 yours=1 behind=2"));
    }

    #[test]
    fn test_format_stale_empty() {
        let output = format_stale_signals(&[]);
        assert!(output.is_empty());
    }
}
