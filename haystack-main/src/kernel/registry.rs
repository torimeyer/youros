//! OS-to-OS registry — ~/.ostk/registry.jsonl (or OSTK_REGISTRY_DIR)
//!
//! Each ostk OS instance registers itself here on `ostk install`.
//! Cross-OS nudges resolve target names via this registry.
//!
//! ## Isolation
//!
//! When `OSTK_REGISTRY_DIR` is set, that directory is used instead of
//! `~/.ostk/`. CI sets this to a per-job tempdir to prevent concurrent
//! write corruption across parallel build targets. Tests set it per-test.

use fs2::FileExt;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

/// One entry in ~/.ostk/registry.jsonl
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RegistryEntry {
    pub path: String,
    pub name: String,
    pub last_boot: String,
}

/// Return the directory that holds registry.jsonl and registry.lock.
///
/// Priority:
/// 1. `OSTK_REGISTRY_DIR` env var — used by CI and tests for isolation.
/// 2. `~/.ostk/` — global default.
fn registry_dir() -> Result<PathBuf, String> {
    if let Ok(dir) = std::env::var("OSTK_REGISTRY_DIR")
        && !dir.is_empty() {
            return Ok(PathBuf::from(dir));
        }
    let home = std::env::var("HOME").map_err(|_| "HOME not set".to_string())?;
    Ok(PathBuf::from(home).join(".ostk"))
}

/// Return the path to the registry file.
fn registry_path() -> Result<PathBuf, String> {
    Ok(registry_dir()?.join("registry.jsonl"))
}

/// Return the path to the registry lock file.
fn registry_lock_path() -> Result<PathBuf, String> {
    Ok(registry_dir()?.join("registry.lock"))
}

/// Execute a closure under an exclusive flock on registry.lock.
/// All read-modify-write operations on registry.jsonl must go through this.
fn with_registry_lock<F, R>(f: F) -> Result<R, String>
where
    F: FnOnce() -> Result<R, String>,
{
    let lock_path = registry_lock_path()?;
    if let Some(parent) = lock_path.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("failed to create registry dir: {e}"))?;
    }
    let lock_file = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(&lock_path)
        .map_err(|e| format!("open registry lock: {e}"))?;
    lock_file
        .lock_exclusive()
        .map_err(|e| format!("flock registry: {e}"))?;
    let result = f();
    drop(lock_file); // releases flock
    result
}

/// Read all entries from a specific registry file.
fn read_registry_at(path: &Path) -> Result<Vec<RegistryEntry>, String> {
    if !path.exists() {
        return Ok(vec![]);
    }
    let content = fs::read_to_string(path)
        .map_err(|e| format!("failed to read registry: {e}"))?;
    let mut entries = Vec::new();
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if let Ok(entry) = serde_json::from_str::<RegistryEntry>(line) {
            entries.push(entry);
        }
    }
    Ok(entries)
}

/// Write all entries back to a specific registry file.
fn write_registry_at(path: &Path, entries: &[RegistryEntry]) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("failed to create registry dir: {e}"))?;
    }
    let mut content = String::new();
    for entry in entries {
        let line = serde_json::to_string(entry).map_err(|e| e.to_string())?;
        content.push_str(&line);
        content.push('\n');
    }
    fs::write(path, content)
        .map_err(|e| format!("failed to write registry: {e}"))?;
    Ok(())
}

/// Core registration logic operating on an explicit registry path (no lock — caller holds it or
/// this is a test path). Internal use only.
fn register_os_instance_at(registry: &Path, root: &Path) -> Result<(), String> {
    let abs_path = fs::canonicalize(root)
        .unwrap_or_else(|_| root.to_path_buf());
    let path_str = abs_path.to_string_lossy().to_string();
    let name = abs_path
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| "unknown".to_string());
    let now = crate::now_iso();

    let mut entries = read_registry_at(registry)?;

    let mut found = false;
    for entry in &mut entries {
        if entry.path == path_str {
            entry.name = name.clone();
            entry.last_boot = now.clone();
            found = true;
            break;
        }
    }
    if !found {
        entries.push(RegistryEntry {
            path: path_str,
            name,
            last_boot: now,
        });
    }

    write_registry_at(registry, &entries)
}

/// Register an OS instance. Creates or updates the entry for the given path.
/// Protected by an exclusive flock on registry.lock to prevent concurrent corruption.
pub fn register_os_instance(root: &Path) -> Result<(), String> {
    let path = registry_path()?;
    with_registry_lock(|| register_os_instance_at(&path, root))
}

/// Core timestamp update logic operating on an explicit registry path.
fn update_registry_timestamp_at(registry: &Path, root: &Path) -> Result<(), String> {
    let abs_path = fs::canonicalize(root)
        .unwrap_or_else(|_| root.to_path_buf());
    let path_str = abs_path.to_string_lossy().to_string();
    let now = crate::now_iso();

    let mut entries = read_registry_at(registry)?;

    for entry in &mut entries {
        if entry.path == path_str {
            entry.last_boot = now;
            return write_registry_at(registry, &entries);
        }
    }

    // Not registered yet — silently skip
    Ok(())
}

/// Update the last_boot timestamp for an OS instance.
/// Protected by an exclusive flock on registry.lock to prevent concurrent corruption.
pub fn update_registry_timestamp(root: &Path) -> Result<(), String> {
    let path = registry_path()?;
    with_registry_lock(|| update_registry_timestamp_at(&path, root))
}

/// Core resolve logic operating on an explicit registry path.
fn resolve_instance_at(registry: &Path, name: &str) -> Result<Option<RegistryEntry>, String> {
    let entries = read_registry_at(registry)?;
    Ok(entries.into_iter().find(|e| e.name == name))
}

/// Resolve an OS instance name to its path. Returns None if not found.
/// Protected by an exclusive flock on registry.lock to prevent reading partially-written state.
pub fn resolve_instance(name: &str) -> Result<Option<RegistryEntry>, String> {
    let path = registry_path()?;
    with_registry_lock(|| resolve_instance_at(&path, name))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU32, Ordering};

    static TEST_COUNTER: AtomicU32 = AtomicU32::new(0);

    /// Create a unique temp registry file for test isolation.
    fn test_registry() -> PathBuf {
        let n = TEST_COUNTER.fetch_add(1, Ordering::SeqCst);
        let pid = std::process::id();
        let dir = std::env::temp_dir()
            .join(format!("ostk_registry_test_{pid}_{n}"));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir.join("registry.jsonl")
    }

    #[test]
    fn test_register_creates_file() {
        let reg = test_registry();
        let tmp = std::env::temp_dir().join("ostk_reg_test_project_create");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();

        register_os_instance_at(&reg, &tmp).unwrap();

        let entries = read_registry_at(&reg).unwrap();
        assert_eq!(entries.len(), 1);
        assert!(entries[0].path.contains("ostk_reg_test_project_create"));
        assert!(!entries[0].last_boot.is_empty());

        let _ = fs::remove_dir_all(&tmp);
        let _ = fs::remove_file(&reg);
    }

    #[test]
    fn test_register_updates_existing() {
        let reg = test_registry();
        let tmp = std::env::temp_dir().join("ostk_reg_test_project_update");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();

        register_os_instance_at(&reg, &tmp).unwrap();
        let entries1 = read_registry_at(&reg).unwrap();
        let _ts1 = entries1[0].last_boot.clone();

        // Small delay to get a different timestamp
        std::thread::sleep(std::time::Duration::from_millis(1100));

        register_os_instance_at(&reg, &tmp).unwrap();
        let entries2 = read_registry_at(&reg).unwrap();
        assert_eq!(entries2.len(), 1, "should update, not duplicate");
        assert!(!entries2[0].last_boot.is_empty());

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_boot_updates_timestamp() {
        let reg = test_registry();
        let tmp = std::env::temp_dir().join("ostk_reg_test_project_boot");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();

        // Register first
        register_os_instance_at(&reg, &tmp).unwrap();
        let entries1 = read_registry_at(&reg).unwrap();
        assert_eq!(entries1.len(), 1);
        let ts1 = entries1[0].last_boot.clone();

        std::thread::sleep(std::time::Duration::from_millis(1100));

        // Update timestamp (like boot would)
        update_registry_timestamp_at(&reg, &tmp).unwrap();
        let entries2 = read_registry_at(&reg).unwrap();
        assert_eq!(entries2.len(), 1);
        assert_ne!(entries2[0].last_boot, ts1, "timestamp should be updated");

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_resolve_instance() {
        let reg = test_registry();
        let tmp = std::env::temp_dir().join("ostk_reg_test_resolve_proj");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();

        register_os_instance_at(&reg, &tmp).unwrap();

        let found = resolve_instance_at(&reg, "ostk_reg_test_resolve_proj").unwrap();
        assert!(found.is_some(), "should resolve registered instance");

        let not_found = resolve_instance_at(&reg, "nonexistent").unwrap();
        assert!(not_found.is_none(), "should not resolve unknown instance");

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_multiple_instances() {
        let reg = test_registry();
        let tmp1 = std::env::temp_dir().join("ostk_reg_test_multi_a");
        let tmp2 = std::env::temp_dir().join("ostk_reg_test_multi_b");
        let _ = fs::remove_dir_all(&tmp1);
        let _ = fs::remove_dir_all(&tmp2);
        fs::create_dir_all(&tmp1).unwrap();
        fs::create_dir_all(&tmp2).unwrap();

        register_os_instance_at(&reg, &tmp1).unwrap();
        register_os_instance_at(&reg, &tmp2).unwrap();

        let entries = read_registry_at(&reg).unwrap();
        assert_eq!(entries.len(), 2);

        let _ = fs::remove_dir_all(&tmp1);
        let _ = fs::remove_dir_all(&tmp2);
    }

    #[test]
    fn test_update_unregistered_is_noop() {
        let reg = test_registry();
        let tmp = std::env::temp_dir().join("ostk_reg_test_unregistered");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();

        let result = update_registry_timestamp_at(&reg, &tmp);
        assert!(result.is_ok());

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_read_empty_registry() {
        let reg = test_registry();
        let entries = read_registry_at(&reg).unwrap();
        assert!(entries.is_empty());
    }

    /// Verify that OSTK_REGISTRY_DIR redirects the public API to an isolated
    /// directory instead of ~/.ostk/. This is the mechanism CI uses to prevent
    /// parallel-job corruption.
    ///
    /// Note: std::env::set_var is not thread-safe in multi-threaded tests. This test
    /// uses a unique temp dir and cleans up the env var after, which is safe as long
    /// as no other test in this module simultaneously exercises the same env var. The
    /// _at variants used by all other tests in this module bypass registry_dir()
    /// entirely, so there is no interference.
    #[test]
    fn test_registry_dir_env_var_isolation() {
        let n = TEST_COUNTER.fetch_add(1, Ordering::SeqCst);
        let pid = std::process::id();
        let isolated_dir = std::env::temp_dir()
            .join(format!("ostk_reg_envvar_test_{pid}_{n}"));
        let _ = fs::remove_dir_all(&isolated_dir);
        fs::create_dir_all(&isolated_dir).unwrap();

        let proj = std::env::temp_dir().join(format!("ostk_reg_envvar_proj_{pid}_{n}"));
        let _ = fs::remove_dir_all(&proj);
        fs::create_dir_all(&proj).unwrap();

        // Set OSTK_REGISTRY_DIR to the isolated dir
        // SAFETY: no other test in this module reads OSTK_REGISTRY_DIR via the
        // public functions (all others use _at variants directly).
        unsafe { std::env::set_var("OSTK_REGISTRY_DIR", &isolated_dir) };

        let reg_result = register_os_instance(&proj);
        unsafe { std::env::remove_var("OSTK_REGISTRY_DIR") };

        reg_result.unwrap();

        // The registry entry must be in the isolated dir, not ~/.ostk/
        let reg_file = isolated_dir.join("registry.jsonl");
        assert!(reg_file.exists(), "registry.jsonl should exist in isolated dir");
        let entries = read_registry_at(&reg_file).unwrap();
        assert_eq!(entries.len(), 1, "one entry in isolated registry");

        let _ = fs::remove_dir_all(&isolated_dir);
        let _ = fs::remove_dir_all(&proj);
    }
}
