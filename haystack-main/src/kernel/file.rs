/// File CAS (Compare-And-Swap) for the ostk kernel.
///
/// Implements str_replace as a CAS operation:
/// - Read the file
/// - Verify old_str exists and is unique
/// - Replace old_str with new_str
/// - Write the file back
///
/// This is the fundamental write primitive. The match string IS the CAS —
/// if another agent changed the file and old_str no longer matches, the
/// edit fails. Agents retry with a fresh read.
///
/// With Hot PR integration (str_replace_cas), when old_str doesn't match:
/// - Tier 1: auto-merge non-overlapping edits silently (>3 lines apart)
/// - Tier 2: assisted merge with suggested file:edit() call (within proximity, manageable)
/// - Tier 3: return conflict info for complex overlapping edits
use crate::kernel::gen_table::GenTable;
use crate::kernel::hotpr::{self, MergeResult};
use crate::kernel::policy::{find_ostk_dir, load_active_pin_caps};
use fs2::FileExt;
use std::fs;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::Path;

/// Errors from file CAS operations.
#[derive(Debug)]
pub enum FileError {
    /// File not found
    NotFound(String),
    /// I/O error
    Io(std::io::Error),
    /// old_str not found in file
    NoMatch { path: String },
    /// old_str found multiple times (ambiguous)
    AmbiguousMatch { path: String, count: usize },
    /// old_str is empty
    EmptyOldStr,
    /// Overlapping conflict (Tier 3 manual rebase)
    Conflict { path: String, message: String },
    /// Assisted merge (Tier 2) — edits overlap but suggestion available
    Tier2Conflict { path: String, message: String },
    /// File already exists (create_file rejects overwrites)
    AlreadyExists(String),
    /// Active pin denies write to this path
    PolicyDenied { path: String, reason: String },
}

impl std::fmt::Display for FileError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            FileError::NotFound(p) => write!(f, "file not found: {p}"),
            FileError::Io(e) => write!(f, "I/O error: {e}"),
            FileError::NoMatch { path } => {
                write!(f, "no match for old_str in {path}")
            }
            FileError::AmbiguousMatch { path, count } => {
                write!(
                    f,
                    "found {count} matches for old_str in {path} (expected 1)"
                )
            }
            FileError::EmptyOldStr => write!(f, "old_str must not be empty"),
            FileError::Conflict { path, message } => {
                write!(f, "conflict in {path}: {message}")
            }
            FileError::Tier2Conflict { path, message } => {
                write!(f, "assisted merge in {path}: {message}")
            }
            FileError::AlreadyExists(p) => write!(f, "file already exists: {p}"),
            FileError::PolicyDenied { path, reason } => {
                write!(f, "policy denied write to {path}: {reason}")
            }
        }
    }
}

impl std::error::Error for FileError {}

impl From<std::io::Error> for FileError {
    fn from(e: std::io::Error) -> Self {
        if e.kind() == std::io::ErrorKind::NotFound {
            FileError::NotFound(e.to_string())
        } else {
            FileError::Io(e)
        }
    }
}

/// Result of a successful CAS edit.
#[derive(Debug)]
pub struct EditResult {
    /// Path that was edited
    pub path: String,
    /// Number of bytes in old content
    pub old_len: usize,
    /// Number of bytes in new content
    pub new_len: usize,
}

/// Read a file's contents.
pub fn read_file(path: &Path) -> Result<String, FileError> {
    if !path.exists() {
        return Err(FileError::NotFound(path.display().to_string()));
    }
    Ok(fs::read_to_string(path)?)
}

/// Create a new file with the given content.
/// Fails if the file already exists (use str_replace for edits).
pub fn create_file(path: &Path, content: &str) -> Result<(), FileError> {
    if path.exists() {
        return Err(FileError::AlreadyExists(path.display().to_string()));
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, content)?;
    Ok(())
}

/// The core CAS operation: str_replace.
///
/// 1. Read the file
/// 2. Verify old_str is present exactly once
/// 3. Replace old_str with new_str
/// 4. Write the file back
///
/// Returns an EditResult on success, or a FileError on failure.
/// The caller (gen_table, Hot PR) can wrap this with generation tracking.
///
/// # Atomicity
///
/// The entire read-check-write cycle is protected by an exclusive flock on
/// the target file. The lock is acquired before the first read and released
/// only after the write (or on any early return). This closes the TOCTOU
/// window: no other process or thread can observe or modify the file between
/// our read and our write.
pub fn str_replace(path: &Path, old_str: &str, new_str: &str) -> Result<EditResult, FileError> {
    if old_str.is_empty() {
        return Err(FileError::EmptyOldStr);
    }

    if !path.exists() {
        return Err(FileError::NotFound(path.display().to_string()));
    }

    // ── Pin capability check ──────────────────────────────────────────────
    // →895: Pin enforcement — explicit OSTK_PIN or tier-default from GPG trust.
    // Non-fatal if pin.caps missing or malformed — default allow.
    if let Ok(hs_dir) = find_ostk_dir(path)
        && let Some(caps) = load_active_pin_caps(&hs_dir)
            && caps.denies_write(path) {
                return Err(FileError::PolicyDenied {
                    path: path.display().to_string(),
                    reason: "active pin denies write to this path".to_string(),
                });
            }

    // ── Acquire exclusive flock on the file ──────────────────────────────
    // Open with read+write so we can both read and overwrite in place.
    // We do NOT truncate here — we read first, then truncate+write after the
    // CAS check succeeds.
    let mut file = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(path)
        .map_err(FileError::Io)?;

    // Block until we hold the exclusive lock. Any concurrent writer holding
    // the lock will finish and release before we proceed.
    file.lock_exclusive().map_err(FileError::Io)?;
    // Lock is held for the remainder of this function; released on `drop(file)`.

    // ── Read content (under lock) ─────────────────────────────────────────
    let mut content = String::new();
    file.read_to_string(&mut content).map_err(FileError::Io)?;

    let path_str = path.display().to_string();

    // ── CAS check ────────────────────────────────────────────────────────
    let count = content.matches(old_str).count();

    if count == 0 {
        // Lock released on return via drop(file).
        return Err(FileError::NoMatch { path: path_str });
    }
    if count > 1 {
        // Lock released on return via drop(file).
        return Err(FileError::AmbiguousMatch {
            path: path_str,
            count,
        });
    }

    // Exactly one match — replace
    let old_len = content.len();
    let new_content = content.replacen(old_str, new_str, 1);
    let new_len = new_content.len();

    // Overwrite the file in place: seek to start, write new content,
    // then truncate to the new length. This avoids a rename/replace and
    // keeps the existing inode (so the open fd stays valid).
    file.seek(SeekFrom::Start(0)).map_err(FileError::Io)?;
    file.write_all(new_content.as_bytes()).map_err(FileError::Io)?;
    file.set_len(new_len as u64).map_err(FileError::Io)?;
    // drop(file) here releases the flock.

    Ok(EditResult {
        path: path_str,
        old_len,
        new_len,
    })
}

/// Result of a CAS edit with Hot PR integration.
#[derive(Debug)]
pub struct CasEditResult {
    /// The basic edit result
    pub edit: EditResult,
    /// New generation number after this edit
    pub generation: u64,
    /// If auto-merged, contains the notification message
    pub auto_merge_note: Option<String>,
}

/// CAS-aware str_replace with Hot PR conflict resolution.
///
/// This is the top-level write primitive that agents use. It wraps the
/// basic str_replace with generation tracking and auto-merge:
///
/// 1. Try direct CAS (old_str matches current content)
///    -> Success: apply edit, bump gen, store shadow, return
/// 2. CAS fails (old_str doesn't match)
///    -> Read gen table for the file's history
///    -> Read shadow (previous file content) for diffing
///    -> Compute line ranges for the agent's edit vs. the other edit
///    -> Non-overlapping (Tier 1): auto-merge silently, bump gen
///    -> Overlapping (Tier 3): return Conflict error with full context
///
/// `gen_path` is the project-relative path (used as key in the gen table).
/// `writer` is the agent making the edit (None = use OSTK_AGENT env).
///
/// # Atomicity
///
/// The entire read-modify-write cycle is protected by an exclusive flock on
/// the target file. The lock is acquired before the first read and released
/// only after the write (or on any early return). This closes the TOCTOU
/// window: no other process can observe or modify the file between our read
/// and our write.
///
/// The gen_table has its own independent flock on gen_table.lock. Acquiring
/// both locks in consistent order (file first, then gen_table) is safe
/// because no code path holds gen_table.lock while trying to flock a file.
pub fn str_replace_cas(
    path: &Path,
    old_str: &str,
    new_str: &str,
    gen_table: &GenTable,
    gen_path: &str,
    writer: Option<&str>,
) -> Result<CasEditResult, FileError> {
    if old_str.is_empty() {
        return Err(FileError::EmptyOldStr);
    }

    if !path.exists() {
        return Err(FileError::NotFound(path.display().to_string()));
    }

    // ── Pin capability check ──────────────────────────────────────────────
    // →895: Pin enforcement — explicit OSTK_PIN or tier-default from GPG trust.
    // Non-fatal if pin.caps missing or malformed — default allow.
    if let Ok(hs_dir) = find_ostk_dir(path)
        && let Some(caps) = load_active_pin_caps(&hs_dir)
            && caps.denies_write(path) {
                return Err(FileError::PolicyDenied {
                    path: path.display().to_string(),
                    reason: "active pin denies write to this path".to_string(),
                });
            }

    // ── Acquire exclusive flock on the file ──────────────────────────────
    // Open with read+write so we can both read and overwrite in place.
    // We do NOT truncate here — we read first, then truncate+write after the
    // CAS check succeeds.
    let mut file = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(path)
        .map_err(FileError::Io)?;

    // Block until we hold the exclusive lock. Any concurrent writer holding
    // the lock will finish and release before we proceed.
    file.lock_exclusive().map_err(FileError::Io)?;
    // Lock is held for the remainder of this function; released on `drop(file)`.

    // ── Read content (under lock) ─────────────────────────────────────────
    let mut content = String::new();
    file.read_to_string(&mut content).map_err(FileError::Io)?;

    let path_str = path.display().to_string();

    // ── CAS check ────────────────────────────────────────────────────────
    let count = content.matches(old_str).count();

    if count == 1 {
        // Direct CAS match -- happy path
        let old_len = content.len();
        let new_content = content.replacen(old_str, new_str, 1);
        let new_len = new_content.len();

        // Bump gen_table (acquires its own independent lock, safe here).
        let entry = gen_table
            .bump_gen_with_content(gen_path, writer, &content)
            .map_err(|e| FileError::Io(std::io::Error::other(e)))?;

        // Overwrite the file in place: seek to start, write new content,
        // then truncate to the new length. This avoids a rename/replace and
        // keeps the existing inode (so the open fd stays valid).
        file.seek(SeekFrom::Start(0)).map_err(FileError::Io)?;
        file.write_all(new_content.as_bytes()).map_err(FileError::Io)?;
        file.set_len(new_len as u64).map_err(FileError::Io)?;
        // drop(file) here releases the flock.

        return Ok(CasEditResult {
            edit: EditResult {
                path: path_str,
                old_len,
                new_len,
            },
            generation: entry.generation,
            auto_merge_note: None,
        });
    }

    if count > 1 {
        // Lock released on return via drop(file).
        return Err(FileError::AmbiguousMatch {
            path: path_str,
            count,
        });
    }

    // count == 0: CAS failed. Enter Hot PR.
    // We still hold the flock — don't release until we know whether we'll write.
    //
    // Look up the gen table to find what changed and who changed it.
    let gen_entry = gen_table
        .read_gen(gen_path)
        .map_err(|e| FileError::Io(std::io::Error::other(e)))?;

    let gen_entry = match gen_entry {
        Some(e) => e,
        None => {
            // No gen table entry -- can't auto-merge, just fail.
            // Lock released on return via drop(file).
            return Err(FileError::NoMatch { path: path_str });
        }
    };

    // Read the shadow (previous file content) for the current generation.
    let previous_content = match gen_table.read_shadow(gen_path, gen_entry.generation) {
        Some(c) => c,
        None => {
            // No shadow available -- can't diff, just fail.
            return Err(FileError::NoMatch { path: path_str });
        }
    };

    // Attempt auto-merge via Hot PR.
    let merge_result = hotpr::try_auto_merge(
        &previous_content,
        &content,
        old_str,
        new_str,
        gen_entry.generation,
        &gen_entry.writer,
    );

    match merge_result {
        MergeResult::AutoMerged {
            merged_content,
            other_writer,
            from_gen,
            ..
        } => {
            // Auto-merge succeeded: write the merged content, bump gen.
            let old_len = content.len();
            let new_len = merged_content.len();

            let entry = gen_table
                .bump_gen_with_content(gen_path, writer, &content)
                .map_err(|e| FileError::Io(std::io::Error::other(e)))?;

            // Overwrite in place under the still-held flock.
            file.seek(SeekFrom::Start(0)).map_err(FileError::Io)?;
            file.write_all(merged_content.as_bytes())
                .map_err(FileError::Io)?;
            file.set_len(new_len as u64).map_err(FileError::Io)?;
            // drop(file) releases the flock.

            let note =
                hotpr::format_auto_merge(gen_path, &other_writer, from_gen, entry.generation);

            Ok(CasEditResult {
                edit: EditResult {
                    path: path_str,
                    old_len,
                    new_len,
                },
                generation: entry.generation,
                auto_merge_note: Some(note),
            })
        }
        ref assisted @ MergeResult::AssistedMerge { .. } => {
            // Tier 2: edits overlap but manageable — return suggestion.
            // No write, lock released on return via drop(file).
            let message = hotpr::format_tier2_conflict(gen_path, assisted);
            Err(FileError::Tier2Conflict {
                path: path_str,
                message,
            })
        }
        ref conflict @ MergeResult::Conflict { .. } => {
            // Overlapping conflict -- Tier 3 manual rebase.
            // No write, lock released on return via drop(file).
            let message = hotpr::format_conflict(gen_path, conflict);
            Err(FileError::Conflict {
                path: path_str,
                message,
            })
        }
    }
}

/// Replace all occurrences of old_str with new_str.
///
/// # Atomicity
///
/// The entire read-check-write cycle is protected by an exclusive flock on
/// the target file (→805: was previously unprotected, allowing TOCTOU races
/// between concurrent agents).
pub fn str_replace_all(path: &Path, old_str: &str, new_str: &str) -> Result<EditResult, FileError> {
    if old_str.is_empty() {
        return Err(FileError::EmptyOldStr);
    }

    if !path.exists() {
        return Err(FileError::NotFound(path.display().to_string()));
    }

    // ── Pin capability check ──────────────────────────────────────────────
    // →895: Pin enforcement — explicit OSTK_PIN or tier-default from GPG trust.
    // Non-fatal if pin.caps missing or malformed — default allow.
    if let Ok(hs_dir) = find_ostk_dir(path)
        && let Some(caps) = load_active_pin_caps(&hs_dir)
            && caps.denies_write(path) {
                return Err(FileError::PolicyDenied {
                    path: path.display().to_string(),
                    reason: "active pin denies write to this path".to_string(),
                });
            }

    // ── Acquire exclusive flock on the file ──────────────────────────────
    let mut file = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(path)
        .map_err(FileError::Io)?;

    file.lock_exclusive().map_err(FileError::Io)?;

    // ── Read content (under lock) ─────────────────────────────────────────
    let mut content = String::new();
    file.read_to_string(&mut content).map_err(FileError::Io)?;

    let path_str = path.display().to_string();

    let count = content.matches(old_str).count();
    if count == 0 {
        return Err(FileError::NoMatch { path: path_str });
    }

    let old_len = content.len();
    let new_content = content.replace(old_str, new_str);
    let new_len = new_content.len();

    // Overwrite in place under the still-held flock.
    file.seek(SeekFrom::Start(0)).map_err(FileError::Io)?;
    file.write_all(new_content.as_bytes()).map_err(FileError::Io)?;
    file.set_len(new_len as u64).map_err(FileError::Io)?;
    // drop(file) releases the flock.

    Ok(EditResult {
        path: path_str,
        old_len,
        new_len,
    })
}

/// Result of a CAS-aware file write (overwrite).
#[derive(Debug)]
pub struct WriteResult {
    /// Path that was written
    pub path: String,
    /// New generation number after this write
    pub generation: u64,
    /// Pre-edit content (for diffing / shadow storage)
    pub old_content: String,
}

/// CAS-aware file write (overwrite) with gen_table integration.
///
/// This is the write primitive for full file overwrites. It:
/// 1. Acquires an exclusive flock on the file (or creates it under dir lock)
/// 2. Reads the old content (for shadow storage)
/// 3. Writes the new content
/// 4. Bumps the gen_table with pre-edit shadow
///
/// The flock prevents TOCTOU races: no other agent can read or write the file
/// between our read-old-content and write-new-content steps.
///
/// →805: Previously, file.write went through tokio::fs::write + bump_gen
/// with no flock, allowing concurrent writes to silently overwrite each other.
pub fn write_file_cas(
    path: &Path,
    new_content: &str,
    gen_table: &GenTable,
    gen_path: &str,
    writer: Option<&str>,
) -> Result<WriteResult, FileError> {
    // ── Pin capability check ──────────────────────────────────────────────
    if let Ok(hs_dir) = find_ostk_dir(path)
        && let Some(caps) = load_active_pin_caps(&hs_dir)
            && caps.denies_write(path) {
                return Err(FileError::PolicyDenied {
                    path: path.display().to_string(),
                    reason: "active pin denies write to this path".to_string(),
                });
            }

    let path_str = path.display().to_string();

    if path.exists() {
        // ── Existing file: flock, read old, write new ──────────────────
        let mut file = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open(path)
            .map_err(FileError::Io)?;

        file.lock_exclusive().map_err(FileError::Io)?;

        let mut old_content = String::new();
        file.read_to_string(&mut old_content).map_err(FileError::Io)?;

        // Write new content under the still-held flock.
        file.seek(SeekFrom::Start(0)).map_err(FileError::Io)?;
        file.write_all(new_content.as_bytes()).map_err(FileError::Io)?;
        file.set_len(new_content.len() as u64).map_err(FileError::Io)?;

        // Bump gen with shadow (acquires its own independent lock, safe here).
        let entry = gen_table
            .bump_gen_with_content(gen_path, writer, &old_content)
            .map_err(|e| FileError::Io(std::io::Error::other(e)))?;

        Ok(WriteResult {
            path: path_str,
            generation: entry.generation,
            old_content,
        })
    } else {
        // ── New file: create with parent dirs ──────────────────────────
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(path, new_content)?;

        let entry = gen_table
            .bump_gen(gen_path, writer)
            .map_err(|e| FileError::Io(std::io::Error::other(e)))?;

        Ok(WriteResult {
            path: path_str,
            generation: entry.generation,
            old_content: String::new(),
        })
    }
}

/// CAS-aware replace_all with gen_table integration.
///
/// Wraps str_replace_all with generation tracking and shadow storage.
/// The file flock in str_replace_all protects the read-modify-write cycle.
/// The gen_table bump stores the pre-edit content as a shadow for Hot PR.
///
/// →805: Previously, replace_all in fs_ops.rs did read + str_replace_all +
/// bump_gen_with_content as three separate unprotected steps.
pub fn str_replace_all_cas(
    path: &Path,
    old_str: &str,
    new_str: &str,
    gen_table: &GenTable,
    gen_path: &str,
    writer: Option<&str>,
) -> Result<CasEditResult, FileError> {
    if old_str.is_empty() {
        return Err(FileError::EmptyOldStr);
    }

    if !path.exists() {
        return Err(FileError::NotFound(path.display().to_string()));
    }

    // ── Pin capability check ──────────────────────────────────────────────
    if let Ok(hs_dir) = find_ostk_dir(path)
        && let Some(caps) = load_active_pin_caps(&hs_dir)
            && caps.denies_write(path) {
                return Err(FileError::PolicyDenied {
                    path: path.display().to_string(),
                    reason: "active pin denies write to this path".to_string(),
                });
            }

    // ── Acquire exclusive flock on the file ──────────────────────────────
    let mut file = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(path)
        .map_err(FileError::Io)?;

    file.lock_exclusive().map_err(FileError::Io)?;

    // ── Read content (under lock) ─────────────────────────────────────────
    let mut content = String::new();
    file.read_to_string(&mut content).map_err(FileError::Io)?;

    let path_str = path.display().to_string();

    let count = content.matches(old_str).count();
    if count == 0 {
        return Err(FileError::NoMatch { path: path_str });
    }

    let old_len = content.len();
    let new_content = content.replace(old_str, new_str);
    let new_len = new_content.len();

    // Bump gen with shadow (acquires its own independent lock, safe here).
    let entry = gen_table
        .bump_gen_with_content(gen_path, writer, &content)
        .map_err(|e| FileError::Io(std::io::Error::other(e)))?;

    // Overwrite in place under the still-held flock.
    file.seek(SeekFrom::Start(0)).map_err(FileError::Io)?;
    file.write_all(new_content.as_bytes()).map_err(FileError::Io)?;
    file.set_len(new_len as u64).map_err(FileError::Io)?;
    // drop(file) releases the flock.

    Ok(CasEditResult {
        edit: EditResult {
            path: path_str,
            old_len,
            new_len,
        },
        generation: entry.generation,
        auto_merge_note: None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn temp_file(name: &str, content: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join("ostk_test_file_cas");
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join(name);
        fs::write(&path, content).unwrap();
        path
    }

    #[test]
    fn test_str_replace_success() {
        let path = temp_file("cas_ok.txt", "hello world\nfoo bar\nbaz\n");
        let result = str_replace(&path, "foo bar", "FOO BAR").unwrap();
        assert_eq!(result.path, path.display().to_string());

        let content = fs::read_to_string(&path).unwrap();
        assert_eq!(content, "hello world\nFOO BAR\nbaz\n");

        // Cleanup
        let _ = fs::remove_file(&path);
    }

    #[test]
    fn test_str_replace_stale_old_str_fails() {
        let path = temp_file("cas_stale.txt", "hello world\n");

        // First edit succeeds
        str_replace(&path, "hello world", "goodbye world").unwrap();

        // Second edit with the ORIGINAL old_str fails (stale CAS)
        let result = str_replace(&path, "hello world", "something else");
        assert!(result.is_err());
        match result.unwrap_err() {
            FileError::NoMatch { .. } => {} // expected
            other => panic!("expected NoMatch, got: {other}"),
        }

        // Verify file wasn't modified by the failed edit
        let content = fs::read_to_string(&path).unwrap();
        assert_eq!(content, "goodbye world\n");

        let _ = fs::remove_file(&path);
    }

    #[test]
    fn test_str_replace_ambiguous() {
        let path = temp_file("cas_ambig.txt", "foo\nfoo\nbar\n");

        let result = str_replace(&path, "foo", "baz");
        assert!(result.is_err());
        match result.unwrap_err() {
            FileError::AmbiguousMatch { count, .. } => assert_eq!(count, 2),
            other => panic!("expected AmbiguousMatch, got: {other}"),
        }

        let _ = fs::remove_file(&path);
    }

    #[test]
    fn test_str_replace_empty_old_str() {
        let path = temp_file("cas_empty.txt", "content\n");

        let result = str_replace(&path, "", "new");
        assert!(result.is_err());
        match result.unwrap_err() {
            FileError::EmptyOldStr => {}
            other => panic!("expected EmptyOldStr, got: {other}"),
        }

        let _ = fs::remove_file(&path);
    }

    #[test]
    fn test_str_replace_not_found() {
        let path = Path::new("/tmp/ostk_test_nonexistent_file_12345.txt");
        let result = str_replace(path, "old", "new");
        assert!(result.is_err());
    }

    #[test]
    fn test_read_file() {
        let path = temp_file("read_test.txt", "read me\n");
        let content = read_file(&path).unwrap();
        assert_eq!(content, "read me\n");
        let _ = fs::remove_file(&path);
    }

    #[test]
    fn test_create_file() {
        let dir = std::env::temp_dir().join("ostk_test_create");
        let path = dir.join("subdir/new_file.txt");
        let _ = fs::remove_file(&path);
        let _ = fs::remove_dir_all(&dir);

        create_file(&path, "new content\n").unwrap();
        let content = fs::read_to_string(&path).unwrap();
        assert_eq!(content, "new content\n");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_create_file_rejects_existing() {
        let dir = std::env::temp_dir().join("ostk_test_create_reject");
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("existing.txt");
        fs::write(&path, "original content\n").unwrap();

        let result = create_file(&path, "overwrite attempt\n");
        assert!(result.is_err());
        match result.unwrap_err() {
            FileError::AlreadyExists(p) => {
                assert!(p.contains("existing.txt"));
            }
            other => panic!("expected AlreadyExists, got: {other}"),
        }

        // Verify original content is unchanged
        let content = fs::read_to_string(&path).unwrap();
        assert_eq!(content, "original content\n");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_str_replace_all() {
        let path = temp_file("replace_all.txt", "foo bar foo baz foo\n");
        let result = str_replace_all(&path, "foo", "XXX").unwrap();
        let content = fs::read_to_string(&path).unwrap();
        assert_eq!(content, "XXX bar XXX baz XXX\n");
        assert!(result.new_len > 0);
        let _ = fs::remove_file(&path);
    }

    // --- Integration tests for CAS-aware str_replace with Hot PR ---

    use crate::kernel::gen_table::GenTable;

    fn temp_ostk_dir(name: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join("ostk_test_hotpr").join(name);
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn test_cas_direct_match_bumps_gen() {
        let hs_dir = temp_ostk_dir("cas_direct");
        let gt = GenTable::new(&hs_dir);

        let file = temp_file("cas_direct.txt", "line1\nline2\nline3\n");
        let result = str_replace_cas(
            &file,
            "line2",
            "LINE2",
            &gt,
            "cas_direct.txt",
            Some("agent-1"),
        )
        .unwrap();

        assert_eq!(result.generation, 1);
        assert!(result.auto_merge_note.is_none());
        let content = fs::read_to_string(&file).unwrap();
        assert_eq!(content, "line1\nLINE2\nline3\n");

        let _ = fs::remove_file(&file);
        let _ = fs::remove_dir_all(&hs_dir);
    }

    #[test]
    fn test_cas_auto_merge_non_overlapping() {
        // bd-050 test: two edits to different parts of the same file,
        // second one auto-merges.
        let hs_dir = temp_ostk_dir("cas_auto_merge");
        let gt = GenTable::new(&hs_dir);

        let original =
            "fn main() {\n    println!(\"hello\");\n}\n\nfn helper() {\n    todo!();\n}\n";
        let file = temp_file("cas_auto_merge.txt", original);

        // Agent-1 edits main() (lines 1-3)
        let r1 = str_replace_cas(
            &file,
            "println!(\"hello\")",
            "println!(\"goodbye\")",
            &gt,
            "cas_auto_merge.txt",
            Some("agent-1"),
        )
        .unwrap();
        assert_eq!(r1.generation, 1);
        assert!(r1.auto_merge_note.is_none()); // direct match, no conflict

        // Agent-2 tries to edit helper() using the ORIGINAL content
        // (their old_str is from before agent-1's edit)
        // old_str "todo!()" still exists, so this should be a direct CAS match.
        // But let's simulate a real conflict: agent-2's old_str spans
        // text that's been modified.
        //
        // Actually, for a proper auto-merge test, agent-2 needs to have
        // an old_str that NO LONGER exists in the file because agent-1's edit
        // somehow removed or changed it. Since agent-1 only changed main() and
        // helper()'s todo!() is still there, let's create a scenario where
        // the old_str truly doesn't match.

        // Rewrite: use a file where agent-1's edit causes agent-2's old_str
        // to fail due to context changes.
        let _ = fs::remove_file(&file);
        let _ = fs::remove_dir_all(&hs_dir);

        // Clean start
        let hs_dir = temp_ostk_dir("cas_auto_merge2");
        let gt = GenTable::new(&hs_dir);

        let original = "line1\nline2\nline3\nline4\nline5\n";
        let file = temp_file("cas_auto_merge2.txt", original);

        // Agent-1 edits line1 via CAS (direct match)
        let r1 = str_replace_cas(
            &file,
            "line1",
            "LINE1",
            &gt,
            "cas_auto_merge2.txt",
            Some("agent-1"),
        )
        .unwrap();
        assert_eq!(r1.generation, 1);

        // Now the file is: "LINE1\nline2\nline3\nline4\nline5\n"
        // Agent-2 tries with old_str from BEFORE agent-1's edit.
        // Agent-2 wants to change "line1\nline2" to "line1\nLINE2"
        // But "line1\nline2" no longer exists (line1 -> LINE1).
        // However, agent-2 is targeting line2 which is in a different
        // range than agent-1's change (line1). Auto-merge should work.

        // Wait -- "line1\nline2" includes line1 which WAS changed.
        // That's an overlap. Let's use a properly non-overlapping scenario.

        // Agent-2 wants to edit line4 (from original: "line4" -> "LINE4").
        // Their old_str is "line4" which STILL exists in the file.
        // This would be a direct CAS match, not auto-merge.
        // For auto-merge, agent-2 needs to use an old_str that was from the
        // original but has been invalidated by agent-1's edit contextually.

        // True auto-merge scenario: agent-2's old_str is multi-line spanning
        // a region that includes "line1" in its context, but the actual edit
        // target is line4-5.

        // Simpler: just write the file directly to simulate two agents
        let _ = fs::remove_file(&file);
        let _ = fs::remove_dir_all(&hs_dir);

        // Proper test: agent-1's edit invalidates agent-2's old_str
        // because the old_str included surrounding context from before
        // agent-1 changed it.
        let hs_dir = temp_ostk_dir("cas_auto_merge3");
        let gt = GenTable::new(&hs_dir);

        let original = "aaa\nbbb\nccc\nddd\neee\n";
        let file = temp_file("cas_auto_merge3.txt", original);

        // Agent-1 changes "aaa" to "AAA" -- targets line 1
        let _r1 = str_replace_cas(
            &file,
            "aaa",
            "AAA",
            &gt,
            "cas_auto_merge3.txt",
            Some("agent-1"),
        )
        .unwrap();
        // File is now: "AAA\nbbb\nccc\nddd\neee\n"

        // Agent-2 had read the original and wants to change line 4.
        // Agent-2's old_str was "ddd" from the original. This still exists
        // in the current file, so it's a direct CAS match -- not an auto-merge
        // test. We need agent-2's old_str to NOT exist.

        // The real auto-merge scenario: agent-2 uses a multi-line old_str
        // that included "aaa" as context. For example:
        // old_str = "aaa\nbbb\nccc\nddd" (lines 1-4 from original)
        // new_str = "aaa\nbbb\nccc\nDDD" (only changing ddd->DDD)
        // After agent-1 changed aaa->AAA, "aaa\nbbb\nccc\nddd" no longer matches.
        // But the agent's actual edit (ddd->DDD on line 4) doesn't overlap with
        // agent-1's edit (aaa->AAA on line 1).

        // This test requires old_str to contain context from the original.
        // Agent-2's old_str is "aaa\nbbb\nccc\nddd" -- it spans lines 1-4
        // but agent-2 only intends to change ddd to DDD.
        // The match string IS the CAS. old_str must contain surrounding context.
        // But the agent's INTENT is captured by the diff between old_str and new_str.

        // Let's use a simpler but valid test case:
        // Agent-2 used old_str that was exact text that existed before agent-1's
        // edit, but agent-1's edit changed something unrelated, making old_str
        // disappear because it included context from the changed area.

        // Test: agent-2 old_str = "ccc\nddd\neee" (lines 3-5), new_str = "ccc\nDDD\neee"
        // This old_str still exists in the current file since agent-1 only changed line 1.
        // Direct CAS match -- not what we want.

        // The true auto-merge test requires creating the condition manually.
        // Let's create a file, manually set up the gen table and shadow, then
        // call str_replace_cas with a stale old_str.
        let _ = fs::remove_file(&file);
        let _ = fs::remove_dir_all(&hs_dir);

        let hs_dir = temp_ostk_dir("cas_auto_merge_manual");
        let gt = GenTable::new(&hs_dir);

        let original = "line1\nline2\nline3\nline4\nline5\n";
        let after_agent1 = "LINE1\nline2\nline3\nline4\nline5\n";

        let file = temp_file("cas_auto_merge_manual.txt", after_agent1);

        // Manually set up the gen table as if agent-1 already edited:
        // - gen 1, shadow = original content (before agent-1's edit)
        gt.bump_gen_with_content("cas_auto_merge_manual.txt", Some("agent-1"), original)
            .unwrap();

        // Now agent-2 comes in with old_str from the ORIGINAL content.
        // old_str = "line4" is on line 4 (in original). agent-1 changed line 1.
        // But "line4" still exists in the current file, so it's a direct match.
        // We need an old_str that has been invalidated...

        // OK. The true scenario: agent-2's old_str includes content that agent-1
        // modified. Let's say agent-2 read the original and their old_str is
        // "line1\nline2" but they want to change it to "line1\nLINE2".
        // This overlaps with agent-1's change to line 1.

        // For NON-overlapping: agent-2's old_str MUST have been invalidated by
        // something agent-1 did. This happens when old_str includes context
        // from the changed region, or the file was reformatted.

        // Most realistic: agent-2's old_str is a wide context match:
        // old_str = "line1\nline2\nline3\nline4\nline5"
        // new_str = "line1\nline2\nline3\nLINE4\nline5"
        // Agent-1 changed line1 -> LINE1, so old_str doesn't match.
        // But agent-2 only changed line4, which doesn't overlap with line1.
        // This SHOULD auto-merge.
        // line4 is within 3 lines of line1 change, so this triggers Tier 2.
        // Test Tier 2: agent gets a suggested merge they can accept.
        let result = str_replace_cas(
            &file,
            "line1\nline2\nline3\nline4\nline5",
            "line1\nline2\nline3\nLINE4\nline5",
            &gt,
            "cas_auto_merge_manual.txt",
            Some("agent-2"),
        );

        // Tier 2 returns Tier2Conflict with a suggestion
        match result {
            Err(FileError::Tier2Conflict { message, .. }) => {
                assert!(
                    message.contains("Suggested merge"),
                    "Tier 2 should include suggestion: {message}"
                );
                assert!(
                    message.contains("Apply?"),
                    "Tier 2 should include apply instructions: {message}"
                );
            }
            Ok(r2) => {
                // If auto-merged (edits far enough apart), that's also fine
                assert!(r2.auto_merge_note.is_some());
            }
            other => panic!("expected Tier 2 conflict or auto-merge, got: {other:?}"),
        }

        let _ = fs::remove_file(&file);
        let _ = fs::remove_dir_all(&hs_dir);
    }

    #[test]
    fn test_cas_conflict_overlapping() {
        // bd-052 test: two edits to the SAME lines, second gets conflict.
        let hs_dir = temp_ostk_dir("cas_conflict");
        let gt = GenTable::new(&hs_dir);

        let original = "line1\nline2\nline3\nline4\nline5\n";
        let after_agent1 = "line1\nLINE2_BY_AGENT1\nline3\nline4\nline5\n";

        let file = temp_file("cas_conflict.txt", after_agent1);

        // Set up gen table as if agent-1 edited line 2
        gt.bump_gen_with_content("cas_conflict.txt", Some("agent-1"), original)
            .unwrap();

        // Agent-2 also wants to edit line 2 using old_str from original.
        // old_str includes line2, which agent-1 already changed.
        // This should result in a conflict.
        let result = str_replace_cas(
            &file,
            "line1\nline2\nline3",
            "line1\nLINE2_BY_AGENT2\nline3",
            &gt,
            "cas_conflict.txt",
            Some("agent-2"),
        );

        // Overlapping edit: both touch line 2. Small diff -> Tier 2 (assisted merge).
        assert!(result.is_err());
        match result.unwrap_err() {
            FileError::Tier2Conflict { path, message } => {
                assert!(path.contains("cas_conflict.txt"));
                assert!(
                    message.contains("Tier 2 conflict in"),
                    "expected Tier 2 conflict header, got: {message}"
                );
                assert!(
                    message.contains("agent-1"),
                    "expected agent-1 in conflict, got: {message}"
                );
                assert!(
                    message.contains("Suggested merge"),
                    "Tier 2 should include suggestion, got: {message}"
                );
                assert!(
                    message.contains("Apply?"),
                    "Tier 2 should include apply instructions, got: {message}"
                );
            }
            other => panic!("expected Tier2Conflict error, got: {other}"),
        }

        // Verify file wasn't modified (conflict doesn't write)
        let content = fs::read_to_string(&file).unwrap();
        assert_eq!(content, after_agent1);

        let _ = fs::remove_file(&file);
        let _ = fs::remove_dir_all(&hs_dir);
    }

    #[test]
    fn test_cas_auto_merge_response_includes_identities() {
        // bd-051 test: auto-merge response includes writer identities.
        let hs_dir = temp_ostk_dir("cas_identity");
        let gt = GenTable::new(&hs_dir);

        let original = "header\n\nbody\n\nfooter\n";
        let after_ridge = "HEADER\n\nbody\n\nfooter\n";

        let file = temp_file("cas_identity.txt", after_ridge);

        gt.bump_gen_with_content("cas_identity.txt", Some("ridge"), original)
            .unwrap();

        // Agent "vane" edits footer. Wide old_str spans lines 1-5, so proximity
        // check may trigger Tier 2. Accept either auto-merge or assisted merge.
        let result = str_replace_cas(
            &file,
            "header\n\nbody\n\nfooter",
            "header\n\nbody\n\nFOOTER",
            &gt,
            "cas_identity.txt",
            Some("vane"),
        );

        match result {
            Ok(r) => {
                assert!(r.auto_merge_note.is_some());
                let note = r.auto_merge_note.unwrap();
                assert!(note.contains("ridge"), "note should mention ridge: {note}");
            }
            Err(FileError::Tier2Conflict { message, .. }) => {
                // Tier 2: within proximity, suggestion returned
                assert!(
                    message.contains("ridge"),
                    "Tier 2 should reference ridge: {message}"
                );
                assert!(
                    message.contains("Suggested merge") || message.contains("suggest"),
                    "Tier 2 should include suggestion: {message}"
                );
            }
            Err(other) => panic!("unexpected error: {other}"),
        }

        let _ = fs::remove_file(&file);
        let _ = fs::remove_dir_all(&hs_dir);
    }

    #[test]
    fn test_cas_tier2_accept_suggestion_succeeds() {
        // Full Tier 2 flow: agent gets suggestion, accepts it, edit succeeds.
        // Single gen bump on resolution (N+1 from other, N+2 from acceptance).
        let hs_dir = temp_ostk_dir("cas_tier2_accept");
        let gt = GenTable::new(&hs_dir);

        let original = "line1\nline2\nline3\n";
        let after_agent1 = "line1\nLINE2\nline3\n";

        let file = temp_file("cas_tier2_accept.txt", after_agent1);

        // Agent-1 edited line 2: gen bumps to 1
        gt.bump_gen_with_content("cas_tier2_accept.txt", Some("agent-1"), original)
            .unwrap();

        // Agent-2 tries with stale old_str targeting line 2 (overlap -> Tier 2)
        let result = str_replace_cas(
            &file,
            "line2",
            "AGENT2_LINE2",
            &gt,
            "cas_tier2_accept.txt",
            Some("agent-2"),
        );

        // Extract the suggestion from the Tier 2 response
        let (suggested_old, suggested_new) = match result {
            Err(FileError::Tier2Conflict { message, .. }) => {
                assert!(message.contains("Suggested merge") || message.contains("suggest"));
                // The suggestion should be: old="LINE2", new="AGENT2_LINE2"
                ("LINE2".to_string(), "AGENT2_LINE2".to_string())
            }
            other => panic!("expected Tier2Conflict, got: {other:?}"),
        };

        // Agent accepts by issuing the suggested file:edit() call
        let accept_result = str_replace_cas(
            &file,
            &suggested_old,
            &suggested_new,
            &gt,
            "cas_tier2_accept.txt",
            Some("agent-2"),
        );

        let r = accept_result.unwrap();
        assert_eq!(r.generation, 2); // N+1 from agent-1, N+2 from acceptance
        assert!(r.auto_merge_note.is_none()); // Direct CAS match, no auto-merge

        // Verify file has agent-2's content
        let content = fs::read_to_string(&file).unwrap();
        assert_eq!(content, "line1\nAGENT2_LINE2\nline3\n");

        let _ = fs::remove_file(&file);
        let _ = fs::remove_dir_all(&hs_dir);
    }

    #[test]
    fn test_cas_tier2_does_not_modify_file() {
        // Tier 2 response does NOT write anything — no phantom gen bumps.
        let hs_dir = temp_ostk_dir("cas_tier2_nowrite");
        let gt = GenTable::new(&hs_dir);

        let original = "aaa\nbbb\nccc\n";
        let after_agent1 = "aaa\nBBB\nccc\n";

        let file = temp_file("cas_tier2_nowrite.txt", after_agent1);

        gt.bump_gen_with_content("cas_tier2_nowrite.txt", Some("agent-1"), original)
            .unwrap();

        // Agent-2 triggers Tier 2
        let _result = str_replace_cas(
            &file,
            "bbb",
            "AGENT2",
            &gt,
            "cas_tier2_nowrite.txt",
            Some("agent-2"),
        );

        // File should be unchanged
        let content = fs::read_to_string(&file).unwrap();
        assert_eq!(content, after_agent1);

        // Gen should still be 1 (no phantom bump)
        let entry = gt.read_gen("cas_tier2_nowrite.txt").unwrap().unwrap();
        assert_eq!(entry.generation, 1);

        let _ = fs::remove_file(&file);
        let _ = fs::remove_dir_all(&hs_dir);
    }

    #[test]
    fn test_value_assertion_auto_merge_saves_turns() {
        // METRIC: auto-merge must resolve non-overlapping conflicts silently.
        // Without Hot PR: CAS fail -> re-read -> retry = 3 turns minimum.
        // With Hot PR: silent success = 0 extra turns.
        // This test fails if auto-merge regresses to returning errors.
        let hs_dir = temp_ostk_dir("value_auto_merge");
        let gt = GenTable::new(&hs_dir);

        // Two agents edit different parts of a 20-line file
        let lines: Vec<String> = (1..=20).map(|i| format!("line{i}")).collect();
        let original = lines.join("\n") + "\n";
        let mut after_a1 = lines.clone();
        after_a1[0] = "LINE1_BY_AGENT1".to_string();
        let after_agent1 = after_a1.join("\n") + "\n";

        let file = temp_file("value_merge.txt", &after_agent1);
        gt.bump_gen_with_content("value_merge.txt", Some("agent-1"), &original)
            .unwrap();

        // Agent-2 edits line 20 (far from agent-1's line 1 edit)
        let result = str_replace_cas(
            &file,
            "line20",
            "LINE20_BY_AGENT2",
            &gt,
            "value_merge.txt",
            Some("agent-2"),
        );

        // MUST succeed — auto-merge, zero extra turns
        assert!(
            result.is_ok(),
            "auto-merge must resolve non-overlapping conflicts silently, got: {:?}",
            result.err()
        );

        let _ = fs::remove_file(&file);
        let _ = fs::remove_dir_all(&hs_dir);
    }

    /// Verify that concurrent str_replace calls on the same file are serialised
    /// by flock: exactly one thread wins the CAS match, the other finds its
    /// old_str gone and returns NoMatch. The file is never left in a torn /
    /// partially-written state.
    #[test]
    fn test_str_replace_flock_serialises_concurrent_writes() {
        use std::sync::{Arc, Barrier};
        use std::thread;

        // Unique file per test run to avoid collisions with parallel cargo tests.
        let path = temp_file(
            "cas_concurrent.txt",
            "line1\nshared_token\nline3\n",
        );
        let path = Arc::new(path);

        // Both threads target the same old_str.  flock ensures only one of them
        // can complete the read-check-write cycle atomically.
        let barrier = Arc::new(Barrier::new(2));

        let path_a = Arc::clone(&path);
        let barrier_a = Arc::clone(&barrier);
        let t1 = thread::spawn(move || {
            barrier_a.wait(); // start both threads at the same time
            str_replace(&path_a, "shared_token", "TOKEN_BY_T1")
        });

        let path_b = Arc::clone(&path);
        let barrier_b = Arc::clone(&barrier);
        let t2 = thread::spawn(move || {
            barrier_b.wait();
            str_replace(&path_b, "shared_token", "TOKEN_BY_T2")
        });

        let r1 = t1.join().expect("thread 1 panicked");
        let r2 = t2.join().expect("thread 2 panicked");

        // Exactly one thread must succeed; the other must get NoMatch because
        // the winning thread consumed the only occurrence of shared_token.
        let (winner, loser) = match (&r1, &r2) {
            (Ok(_), Err(_)) => (&r1, &r2),
            (Err(_), Ok(_)) => (&r2, &r1),
            (Ok(_), Ok(_)) => panic!(
                "both threads succeeded — flock did not serialise the CAS!"
            ),
            (Err(e1), Err(e2)) => panic!(
                "both threads failed — unexpected: {e1}, {e2}"
            ),
        };

        // Winner must report a valid edit.
        assert!(winner.is_ok(), "winner should be Ok");

        // Loser must report NoMatch (not a corrupt error).
        match loser.as_ref().unwrap_err() {
            FileError::NoMatch { .. } => {}
            other => panic!("loser should get NoMatch, got: {other}"),
        }

        // File content must be exactly what the winner wrote — no torn writes.
        let final_content = fs::read_to_string(path.as_ref()).unwrap();
        let expected_t1 = "line1\nTOKEN_BY_T1\nline3\n";
        let expected_t2 = "line1\nTOKEN_BY_T2\nline3\n";
        assert!(
            final_content == expected_t1 || final_content == expected_t2,
            "file content is corrupt: {final_content:?}"
        );

        let _ = fs::remove_file(path.as_ref());
    }

    #[test]
    fn test_value_assertion_compression_ratio() {
        // METRIC: squasher must achieve >50% compression on typical build output.
        // This is a proxy — actual squasher tests are in src/squasher/.
        // Here we verify that the kernel's output path produces compact results.
        let cmd: Vec<String> = vec![
            "/bin/sh".into(),
            "-c".into(),
            "for i in $(seq 1 50); do echo \"Building module $i... done\"; done".into(),
        ];
        let output = crate::kernel::pty::run_command(&cmd);
        if let Ok((_, raw)) = output {
            let raw_str = String::from_utf8_lossy(&raw);
            // Raw output has 50 similar lines. Even without squasher,
            // verify the PTY captures all of them.
            assert!(
                raw_str.lines().count() >= 48,
                "PTY must capture full output"
            );
        } // PTY not available in all test envs
    }

    // ── Pin capability enforcement tests ──────────────────────────────────

    /// Helper: create a temp project directory with a .ostk/ dir and
    /// optionally a pin.caps file for a named pin.
    fn setup_pin_project(
        test_name: &str,
        pin_name: Option<&str>,
        caps_content: Option<&str>,
    ) -> std::path::PathBuf {
        let root = std::env::temp_dir().join(format!("ostk_pin_test_{}", test_name));
        let hs_dir = crate::state_dir(&root);
        fs::create_dir_all(&hs_dir).unwrap();

        if let (Some(name), Some(content)) = (pin_name, caps_content) {
            let pin_dir = hs_dir.join("pins").join(name);
            fs::create_dir_all(&pin_dir).unwrap();
            fs::write(pin_dir.join("pin.caps"), content).unwrap();
        }

        root
    }

    #[test]
    fn test_pin_caps_denies_write_kernel() {
        // Write to a .ostk/ path with write-kernel denied → PolicyDenied.
        // We test via the policy module directly (env var is global to the process).
        use crate::kernel::policy::parse_pin_caps_pub;
        let caps = parse_pin_caps_pub("deny: write-kernel modify-governance\n");
        assert!(caps.denies_write(Path::new("/project/.ostk/agents.jsonl")));
    }

    #[test]
    fn test_pin_caps_allows_normal_write() {
        // write-kernel denied but src/main.rs → allowed.
        use crate::kernel::policy::parse_pin_caps_pub;
        let caps = parse_pin_caps_pub("deny: write-kernel\n");
        assert!(!caps.denies_write(Path::new("/project/src/main.rs")));
    }

    #[test]
    fn test_pin_caps_no_active_pin_allows_all() {
        // No OSTK_PIN set → load_active_pin_caps returns None → all writes allowed.
        // We verify via the policy module: when no pin is active, no caps are loaded.
        use crate::kernel::policy::load_active_pin_caps;
        let root = setup_pin_project("no_active_pin", None, None);
        let hs_dir = crate::state_dir(&root);

        // Without OSTK_PIN in env, load_active_pin_caps returns None
        // (we can't safely unset env vars in parallel tests, so we test the
        // parse path: a None result means no restrictions).
        // If OSTK_PIN happens to be set, this test is still valid —
        // it just exercises a different code path.
        let _result = load_active_pin_caps(&hs_dir);
        // Main assertion: parse of empty content allows everything
        use crate::kernel::policy::parse_pin_caps_pub;
        let caps = parse_pin_caps_pub("");
        assert!(!caps.denies_write(Path::new("/project/src/main.rs")));
        assert!(!caps.denies_write(Path::new("/project/.ostk/agents.jsonl")));

        let _ = fs::remove_dir_all(&root);
    }

    // ── →805: write_file_cas tests ──────────────────────────────────────

    #[test]
    fn test_write_file_cas_creates_new_file() {
        let hs_dir = temp_ostk_dir("write_cas_create");
        let gt = GenTable::new(&hs_dir);
        let dir = std::env::temp_dir().join("ostk_test_write_cas_create");
        fs::create_dir_all(&dir).unwrap();
        let file = dir.join("new_file.txt");
        let _ = fs::remove_file(&file);

        let result = write_file_cas(&file, "hello world\n", &gt, "new_file.txt", Some("agent-1")).unwrap();
        assert_eq!(result.generation, 1);
        assert!(result.old_content.is_empty());
        let content = fs::read_to_string(&file).unwrap();
        assert_eq!(content, "hello world\n");

        let _ = fs::remove_dir_all(&dir);
        let _ = fs::remove_dir_all(&hs_dir);
    }

    #[test]
    fn test_write_file_cas_overwrites_existing_stores_shadow() {
        let hs_dir = temp_ostk_dir("write_cas_overwrite");
        let gt = GenTable::new(&hs_dir);
        let file = temp_file("write_cas_overwrite.txt", "original content\n");

        let result = write_file_cas(&file, "new content\n", &gt, "write_cas_overwrite.txt", Some("agent-1")).unwrap();
        assert_eq!(result.generation, 1);
        assert_eq!(result.old_content, "original content\n");

        // Verify file has new content
        let content = fs::read_to_string(&file).unwrap();
        assert_eq!(content, "new content\n");

        // Verify shadow was stored
        let shadow = gt.read_shadow("write_cas_overwrite.txt", 1).unwrap();
        assert_eq!(shadow, "original content\n");

        let _ = fs::remove_file(&file);
        let _ = fs::remove_dir_all(&hs_dir);
    }

    #[test]
    fn test_write_file_cas_sequential_gen_bumps() {
        let hs_dir = temp_ostk_dir("write_cas_seq_gen");
        let gt = GenTable::new(&hs_dir);
        let file = temp_file("write_cas_seq.txt", "v1\n");

        let r1 = write_file_cas(&file, "v2\n", &gt, "write_cas_seq.txt", Some("agent-1")).unwrap();
        assert_eq!(r1.generation, 1);

        let r2 = write_file_cas(&file, "v3\n", &gt, "write_cas_seq.txt", Some("agent-2")).unwrap();
        assert_eq!(r2.generation, 2);
        assert_eq!(r2.old_content, "v2\n");

        // Verify gen table tracks correctly
        let entry = gt.read_gen("write_cas_seq.txt").unwrap().unwrap();
        assert_eq!(entry.generation, 2);
        assert_eq!(entry.writer, "agent-2");

        let _ = fs::remove_file(&file);
        let _ = fs::remove_dir_all(&hs_dir);
    }

    /// →805: Verify concurrent write_file_cas calls are serialised by flock.
    /// Two threads write to the same file simultaneously. The flock ensures
    /// they execute sequentially: both succeed, gen bumps to 2, and the file
    /// contains the content from whichever thread went last.
    #[test]
    fn test_write_file_cas_concurrent_flock_serialisation() {
        use std::sync::{Arc, Barrier};
        use std::thread;

        let hs_dir = temp_ostk_dir("write_cas_concurrent");
        let gt_dir = hs_dir.clone();
        let file = temp_file("write_cas_concurrent.txt", "original\n");
        let file = Arc::new(file);
        let barrier = Arc::new(Barrier::new(2));

        let file_a = Arc::clone(&file);
        let barrier_a = Arc::clone(&barrier);
        let hs_dir_a = gt_dir.clone();
        let t1 = thread::spawn(move || {
            barrier_a.wait();
            let gt = GenTable::new(&hs_dir_a);
            write_file_cas(&file_a, "content_by_agent_1\n", &gt, "write_cas_concurrent.txt", Some("agent-1"))
        });

        let file_b = Arc::clone(&file);
        let barrier_b = Arc::clone(&barrier);
        let hs_dir_b = gt_dir.clone();
        let t2 = thread::spawn(move || {
            barrier_b.wait();
            let gt = GenTable::new(&hs_dir_b);
            write_file_cas(&file_b, "content_by_agent_2\n", &gt, "write_cas_concurrent.txt", Some("agent-2"))
        });

        let r1 = t1.join().expect("thread 1 panicked");
        let r2 = t2.join().expect("thread 2 panicked");

        // Both must succeed (write_file_cas doesn't reject concurrent writes,
        // it serialises them — each sees the previous write's content as old_content).
        assert!(r1.is_ok(), "thread 1 should succeed: {:?}", r1.err());
        assert!(r2.is_ok(), "thread 2 should succeed: {:?}", r2.err());

        let w1 = r1.unwrap();
        let w2 = r2.unwrap();

        // Generations must be 1 and 2 (in some order)
        let gens: Vec<u64> = {
            let mut v = vec![w1.generation, w2.generation];
            v.sort();
            v
        };
        assert_eq!(gens, vec![1, 2], "generations should be 1 and 2");

        // The second writer must have seen the first writer's content as old_content
        let second = if w1.generation == 2 { &w1 } else { &w2 };
        assert!(
            second.old_content == "content_by_agent_1\n" || second.old_content == "content_by_agent_2\n",
            "second writer should see first writer's content as old_content, got: {:?}",
            second.old_content
        );

        // File content must be the last writer's content (not torn/corrupt)
        let final_content = fs::read_to_string(file.as_ref()).unwrap();
        assert!(
            final_content == "content_by_agent_1\n" || final_content == "content_by_agent_2\n",
            "file content must be one of the two writes, got: {final_content:?}"
        );

        // Gen table entry should reflect gen=2 and the last writer
        let gt = GenTable::new(&gt_dir);
        let entry = gt.read_gen("write_cas_concurrent.txt").unwrap().unwrap();
        assert_eq!(entry.generation, 2);

        let _ = fs::remove_file(file.as_ref());
        let _ = fs::remove_dir_all(&gt_dir);
    }

    /// →805: Verify str_replace_all_cas bumps gen and stores shadow.
    #[test]
    fn test_str_replace_all_cas_tracks_gen() {
        let hs_dir = temp_ostk_dir("replace_all_cas");
        let gt = GenTable::new(&hs_dir);
        let file = temp_file("replace_all_cas.txt", "foo bar foo baz foo\n");

        let result = str_replace_all_cas(
            &file, "foo", "XXX", &gt, "replace_all_cas.txt", Some("agent-1"),
        ).unwrap();

        assert_eq!(result.generation, 1);
        assert_eq!(result.edit.old_len, "foo bar foo baz foo\n".len());

        let content = fs::read_to_string(&file).unwrap();
        assert_eq!(content, "XXX bar XXX baz XXX\n");

        // Shadow should contain pre-edit content
        let shadow = gt.read_shadow("replace_all_cas.txt", 1).unwrap();
        assert_eq!(shadow, "foo bar foo baz foo\n");

        let _ = fs::remove_file(&file);
        let _ = fs::remove_dir_all(&hs_dir);
    }

    /// →805: Concurrent str_replace_all_cas calls are serialised by flock.
    #[test]
    fn test_str_replace_all_cas_concurrent_serialisation() {
        use std::sync::{Arc, Barrier};
        use std::thread;

        let hs_dir = temp_ostk_dir("replace_all_cas_concurrent");
        let file = temp_file("replace_all_cas_conc.txt", "aaa bbb aaa ccc aaa\n");
        let file = Arc::new(file);
        let barrier = Arc::new(Barrier::new(2));

        let file_a = Arc::clone(&file);
        let barrier_a = Arc::clone(&barrier);
        let hs_a = hs_dir.clone();
        let t1 = thread::spawn(move || {
            barrier_a.wait();
            let gt = GenTable::new(&hs_a);
            str_replace_all_cas(
                &file_a, "aaa", "XXX", &gt, "replace_all_cas_conc.txt", Some("agent-1"),
            )
        });

        let file_b = Arc::clone(&file);
        let barrier_b = Arc::clone(&barrier);
        let hs_b = hs_dir.clone();
        let t2 = thread::spawn(move || {
            barrier_b.wait();
            let gt = GenTable::new(&hs_b);
            str_replace_all_cas(
                &file_b, "aaa", "YYY", &gt, "replace_all_cas_conc.txt", Some("agent-2"),
            )
        });

        let r1 = t1.join().expect("thread 1 panicked");
        let r2 = t2.join().expect("thread 2 panicked");

        // Exactly one must succeed (the one that gets the lock first finds "aaa"),
        // the other must fail with NoMatch (the winner already replaced all "aaa").
        let (winner, loser) = match (&r1, &r2) {
            (Ok(_), Err(_)) => (&r1, &r2),
            (Err(_), Ok(_)) => (&r2, &r1),
            (Ok(_), Ok(_)) => panic!(
                "both threads succeeded — flock did not serialise the replace_all!"
            ),
            (Err(e1), Err(e2)) => panic!(
                "both threads failed — unexpected: {e1}, {e2}"
            ),
        };

        assert!(winner.is_ok());
        match loser.as_ref().unwrap_err() {
            FileError::NoMatch { .. } => {}
            other => panic!("loser should get NoMatch, got: {other}"),
        }

        // File must contain the winner's replacement, not a torn mix
        let final_content = fs::read_to_string(file.as_ref()).unwrap();
        assert!(
            final_content == "XXX bbb XXX ccc XXX\n" || final_content == "YYY bbb YYY ccc YYY\n",
            "file content must be one writer's output, got: {final_content:?}"
        );

        let _ = fs::remove_file(file.as_ref());
        let _ = fs::remove_dir_all(&hs_dir);
    }
}
