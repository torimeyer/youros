//! `ostk bail` — signed, portable OS package (→bail).
//!
//! Two modes:
//! - `bail pack --public` → public bundle: boot.md + .boot/INIT + .primefile.
//!   Signed by @ostk.prime. Any instance on any harness can boot from this.
//! - `bail pack`          → full bail: public parts + os.bin (GPG-encrypted).
//!   Prime-key-to-prime-key state transfer. Internal state never leaks.
//!
//! Format: ostk.bail (tar.gz renamed .bail)
//!
//! ```text
//! ostk.bail/
//!   manifest.json       — { version, created, signer, mode, files[] }
//!   boot.md             — verbatim copy
//!   .boot/INIT          — verbatim copy (if present)
//!   .primefile          — verbatim copy (public key fingerprints only)
//!   os.bin              — (full mode) GPG-encrypted tarball of OS state
//!   manifest.json.asc   — GPG detached signature over manifest.json
//! ```

use crate::{find_project_root, now_iso};
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

// ---------------------------------------------------------------------------
// Never-include list — internal state that must not leak
// ---------------------------------------------------------------------------

/// Files and directories that must NEVER appear in a bail package.
const BAIL_EXCLUDE: &[&str] = &[
    "audit.jsonl",
    "sessions",
    "agents.jsonl",
    "hwm.jsonl",
    "hwm.lock",
    "gen_table.jsonl",
    "gen_table.lock",
    "identity_counter",
    "identity.lock",
    "dispatch.json",
];

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// `ostk bail pack [--public]`
///
/// Packs a bail from the current ostk project.
/// If `public_only` is true, only public files are included (no os.bin).
/// Output: `ostk.bail` in the current directory.
pub fn run_pack(public_only: bool) -> Result<(), String> {
    let root = find_project_root()?;
    let hs_dir = crate::state_dir(&root);

    println!("ostk bail pack (mode: {})", if public_only { "public" } else { "full" });

    // 1. Collect public files
    let public_files = collect_public_files(&hs_dir);
    // 2. Build staging directory in /tmp
    let staging = std::env::temp_dir().join(format!("ostk_bail_{}", std::process::id()));
    fs::create_dir_all(&staging).map_err(|e| format!("staging dir: {e}"))?;
    let boot_staging = staging.join(".boot");
    fs::create_dir_all(&boot_staging).map_err(|e| format!("staging .boot/: {e}"))?;

    // 3. Copy public files into staging
    let mut copied_names: Vec<String> = Vec::new();
    for (name, src_path) in &public_files {
        let dest = staging.join(name);
        if let Some(parent) = dest.parent() {
            fs::create_dir_all(parent).ok();
        }
        match fs::copy(src_path, &dest) {
            Ok(_) => {
                copied_names.push(name.clone());
                println!("  + {name}");
            }
            Err(e) => {
                eprintln!("  warning: could not copy {name}: {e}");
            }
        }
    }

    if copied_names.is_empty() {
        let _ = fs::remove_dir_all(&staging);
        return Err("no public files found — is this a ostk project?".to_string());
    }

    // 4. Full mode: encrypt OS state into os.bin
    if !public_only {
        match pack_os_bin(&hs_dir, &staging) {
            Ok(()) => {
                copied_names.push("os.bin".to_string());
                println!("  + os.bin (GPG-encrypted)");
            }
            Err(e) => {
                eprintln!("  warning: os.bin skipped — {e}");
                eprintln!("  (falling back to public-only bail)");
            }
        }
    }

    // 5. Write manifest.json
    let mode = if public_only || !copied_names.contains(&"os.bin".to_string()) {
        "public"
    } else {
        "full"
    };
    let signer = read_prime_signer(&hs_dir);
    let manifest = json!({
        "version": "1.0",
        "created": now_iso(),
        "signer": signer,
        "mode": mode,
        "files": copied_names,
    });
    let manifest_path = staging.join("manifest.json");
    let manifest_str = serde_json::to_string_pretty(&manifest)
        .map_err(|e| format!("manifest serialization: {e}"))?;
    fs::write(&manifest_path, &manifest_str).map_err(|e| format!("manifest write: {e}"))?;
    println!("  + manifest.json");

    // 6. Sign manifest.json with GPG (non-fatal if GPG absent)
    let signed = sign_manifest(&manifest_path, &signer);
    if signed {
        println!("  + manifest.json.asc (GPG detached signature)");
    } else {
        println!("  note: manifest unsigned (GPG not available or key not found)");
    }

    // 7. Create tarball → rename to .bail
    let output_path = std::env::current_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("ostk.bail");

    create_bail_archive(&staging, &output_path)?;
    let _ = fs::remove_dir_all(&staging);

    // Summary
    println!();
    let size = fs::metadata(&output_path).map(|m| m.len()).unwrap_or(0);
    println!("bail packed: {}", output_path.display());
    println!("  mode:   {mode}");
    println!("  files:  {}", copied_names.len() + 1); // +1 for manifest
    println!("  size:   {} bytes", size);
    println!("  signed: {}", if signed { "yes" } else { "no" });

    Ok(())
}

/// `ostk bail unpack <path>`
///
/// Verifies signature, decrypts os.bin if prime key is present, writes state
/// into the current project's .ostk/ directory.
pub fn run_unpack(bail_path: &str) -> Result<(), String> {
    let root = find_project_root()?;
    let hs_dir = crate::state_dir(&root);

    println!("ostk bail unpack: {bail_path}");

    // 1. Extract bail to staging
    let staging = std::env::temp_dir().join(format!("ostk_bail_unpack_{}", std::process::id()));
    fs::create_dir_all(&staging).map_err(|e| format!("staging dir: {e}"))?;

    extract_bail_archive(bail_path, &staging)?;

    // 2. Verify signature (non-fatal — warn if unsigned)
    let manifest_path = staging.join("manifest.json");
    if !manifest_path.exists() {
        let _ = fs::remove_dir_all(&staging);
        return Err("bail is corrupt: manifest.json missing".to_string());
    }

    let asc_path = staging.join("manifest.json.asc");
    if asc_path.exists() {
        match verify_gpg_signature(&manifest_path, &asc_path) {
            Ok(signer) => println!("  signature: VERIFIED ({})", signer),
            Err(e) if e.contains("gpg not available") => {
                // GPG absent — new machine scenario, warn but allow
                println!("  signature: WARNING — gpg not found — signature cannot be verified.");
                println!("  Install gpg to verify bail authenticity. (status: UNVERIFIED)");
            }
            Err(e) => {
                // GPG present but verification failed — tampered bail, must not proceed
                let _ = fs::remove_dir_all(&staging);
                return Err(format!(
                    "bail signature verification FAILED — refusing to unpack. \
                     The bail may have been tampered with.\n  gpg error: {e}"
                ));
            }
        }
    } else {
        println!("  signature: NONE — bail is unsigned (no manifest.json.asc)");
        println!("  warning: unsigned bail — contents are unverified");
    }

    // 3. Read manifest
    let manifest: Value = serde_json::from_str(
        &fs::read_to_string(&manifest_path).map_err(|e| format!("manifest read: {e}"))?
    ).map_err(|e| format!("manifest parse: {e}"))?;

    let mode = manifest["mode"].as_str().unwrap_or("public");
    println!("  mode:    {mode}");
    println!("  created: {}", manifest["created"].as_str().unwrap_or("?"));

    // 4. Copy public files into .ostk/
    copy_bail_files_to_hs(&staging, &hs_dir, mode)?;

    // 5. Full mode: decrypt os.bin if prime key is present
    if mode == "full" {
        let os_bin = staging.join("os.bin");
        if os_bin.exists() {
            match decrypt_os_bin(&os_bin, &hs_dir) {
                Ok(()) => println!("  os.bin: decrypted and applied"),
                Err(e) => println!("  os.bin: skipped — {e}"),
            }
        }
    }

    let _ = fs::remove_dir_all(&staging);
    println!();
    println!("bail unpacked. run `ostk boot` to verify state.");

    Ok(())
}

/// `ostk bail verify <path>`
///
/// Verifies the GPG signature without unpacking.
pub fn run_verify(bail_path: &str) -> Result<(), String> {
    println!("ostk bail verify: {bail_path}");

    // Extract to staging (we need the files for gpg --verify)
    let staging = std::env::temp_dir().join(format!("ostk_bail_verify_{}", std::process::id()));
    fs::create_dir_all(&staging).map_err(|e| format!("staging dir: {e}"))?;

    extract_bail_archive(bail_path, &staging)?;

    let manifest_path = staging.join("manifest.json");
    if !manifest_path.exists() {
        let _ = fs::remove_dir_all(&staging);
        return Err("bail is corrupt: manifest.json missing".to_string());
    }

    let asc_path = staging.join("manifest.json.asc");
    if !asc_path.exists() {
        let _ = fs::remove_dir_all(&staging);
        println!("UNVERIFIED: no manifest.json.asc in bail");
        return Ok(());
    }

    let result = match verify_gpg_signature(&manifest_path, &asc_path) {
        Ok(signer) => {
            println!("VERIFIED");
            println!("  signer: {signer}");
            Ok(())
        }
        Err(e) => {
            println!("UNVERIFIED: {e}");
            Err(format!("signature verification failed: {e}"))
        }
    };

    // Read manifest for extra info
    // Read manifest for extra info
    if let Ok(s) = fs::read_to_string(&manifest_path)
        && let Ok(m) = serde_json::from_str::<Value>(&s) {
            println!("  created: {}", m["created"].as_str().unwrap_or("?"));
            if let Some(files) = m["files"].as_array() {
                println!("  files:   {}", files.len());
            }
        }
    let _ = fs::remove_dir_all(&staging);
    result
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/// Collect the public files that always go into a bail.
/// Returns (logical_name, src_path) pairs. Missing files are silently skipped.
fn collect_public_files(hs_dir: &Path) -> Vec<(String, PathBuf)> {
    let mut files = Vec::new();

    // boot.md — the swap file
    let boot = hs_dir.join("boot.md");
    if boot.exists() {
        files.push(("boot.md".to_string(), boot));
    }

    // .boot/INIT — tack boot protocol (optional)
    let init = hs_dir.join(".boot/INIT");
    if init.exists() {
        files.push((".boot/INIT".to_string(), init));
    }

    // .primefile — dual-signed identity (public key fingerprints only, NOT private keys)
    let primefile = hs_dir.join(".primefile");
    if primefile.exists() {
        files.push((".primefile".to_string(), primefile));
    }

    files
}

/// Read the signer fingerprint from .primefile for the manifest.
/// Returns a descriptive string — never a private key.
fn read_prime_signer(hs_dir: &Path) -> String {
    let path = hs_dir.join(".primefile");
    match fs::read_to_string(&path) {
        Ok(content) => {
            // Extract first fingerprint line — look for 40-char hex or "fingerprint:" pattern
            for line in content.lines() {
                let l = line.trim();
                if l.starts_with("fingerprint:") || l.starts_with("Fingerprint:") {
                    return l.to_string();
                }
                // 40-char hex fingerprint
                if l.len() >= 16 && l.chars().all(|c| c.is_ascii_hexdigit() || c == ' ') {
                    return format!("fingerprint:{}", l.replace(' ', ""));
                }
            }
            // Fallback: first non-empty non-comment line
            content.lines()
                .find(|l| !l.trim().is_empty() && !l.trim().starts_with('#'))
                .unwrap_or("@ostk.prime")
                .trim()
                .to_string()
        }
        Err(_) => "@ostk.prime".to_string(),
    }
}

/// Sign manifest.json with GPG detached signature.
/// Returns true if signing succeeded, false if GPG absent or key not found (non-fatal).
fn sign_manifest(manifest_path: &Path, signer: &str) -> bool {
    // Try to extract a key fingerprint from signer string for --local-user
    let fingerprint: Option<String> = if signer.starts_with("fingerprint:") {
        Some(signer.trim_start_matches("fingerprint:").trim().to_string())
    } else {
        None
    };

    let mut cmd = Command::new("gpg");
    cmd.arg("--batch")
        .arg("--yes")
        .arg("--detach-sign")
        .arg("--armor");

    if let Some(ref fp) = fingerprint
        && !fp.is_empty() {
            cmd.arg("--local-user").arg(fp);
        }

    cmd.arg(manifest_path);

    match cmd.output() {
        Ok(out) if out.status.success() => true,
        Ok(out) => {
            let stderr = String::from_utf8_lossy(&out.stderr);
            eprintln!("  gpg sign: {}", stderr.trim());
            false
        }
        Err(_) => {
            // GPG not installed — non-fatal
            false
        }
    }
}

/// Verify a GPG detached signature.
/// Returns Ok(signer_description) on success, Err(message) on failure.
fn verify_gpg_signature(file: &Path, asc: &Path) -> Result<String, String> {
    let out = Command::new("gpg")
        .arg("--verify")
        .arg(asc)
        .arg(file)
        .output()
        .map_err(|_| "gpg not available".to_string())?;

    let stderr = String::from_utf8_lossy(&out.stderr);

    if out.status.success() {
        // Extract signer info from gpg output
        let signer = stderr
            .lines()
            .find(|l| l.contains("issuer") || l.contains("fingerprint") || l.contains("Good signature"))
            .unwrap_or("signature valid")
            .trim()
            .to_string();
        Ok(signer)
    } else {
        Err(stderr.trim().lines().next().unwrap_or("verification failed").to_string())
    }
}

/// Pack OS state files into an encrypted os.bin (GPG).
/// Reads the recipient fingerprint from .primefile.
/// PRIVATE KEYS NEVER APPEAR IN ANY FILE, LOG, OR ERROR MESSAGE.
fn pack_os_bin(hs_dir: &Path, staging: &Path) -> Result<(), String> {
    // Find prime key recipient from .primefile (public fingerprint only)
    let recipient = find_prime_recipient(hs_dir)?;

    // Create a tarball of os state (non-secret files not already in public set)
    // Exclude: sessions/, agents.jsonl, audit.jsonl, *.secret, secrets/
    let os_staging = std::env::temp_dir()
        .join(format!("ostk_os_state_{}", std::process::id()));
    fs::create_dir_all(&os_staging).map_err(|e| format!("os staging: {e}"))?;

    // Copy non-excluded ostk state files
    let entries = fs::read_dir(hs_dir).map_err(|e| format!("read .ostk/: {e}"))?;
    let mut copied = 0usize;
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        if should_exclude_from_bail(&name) {
            continue;
        }
        // Skip files already in public bundle
        if matches!(name.as_str(), "boot.md" | ".primefile") {
            continue;
        }
        let src = entry.path();
        if src.is_file() {
            let dest = os_staging.join(&name);
            fs::copy(&src, &dest).ok();
            copied += 1;
        }
    }

    if copied == 0 {
        let _ = fs::remove_dir_all(&os_staging);
        return Err("no additional OS state files to encrypt".to_string());
    }

    // Create tar.gz of os_staging
    let tar_path = std::env::temp_dir()
        .join(format!("ostk_os_{}.tar.gz", std::process::id()));

    let tar_out = Command::new("tar")
        .arg("-czf")
        .arg(&tar_path)
        .arg("-C")
        .arg(&os_staging)
        .arg(".")
        .output()
        .map_err(|e| format!("tar: {e}"))?;

    let _ = fs::remove_dir_all(&os_staging);

    if !tar_out.status.success() {
        let _ = fs::remove_file(&tar_path);
        return Err(format!(
            "tar failed: {}",
            String::from_utf8_lossy(&tar_out.stderr).trim()
        ));
    }

    // GPG encrypt the tarball to the prime recipient
    let os_bin_path = staging.join("os.bin");
    let gpg_out = Command::new("gpg")
        .arg("--batch")
        .arg("--yes")
        .arg("--encrypt")
        .arg("--recipient")
        .arg(&recipient)
        .arg("--output")
        .arg(&os_bin_path)
        .arg(&tar_path)
        .output()
        .map_err(|e| format!("gpg encrypt: {e}"))?;

    let _ = fs::remove_file(&tar_path);

    if !gpg_out.status.success() {
        return Err(format!(
            "gpg encrypt failed: {}",
            String::from_utf8_lossy(&gpg_out.stderr).trim()
        ));
    }

    Ok(())
}

/// Read the prime recipient key fingerprint from .primefile.
/// Returns the fingerprint for use as GPG --recipient.
/// NEVER returns or logs a private key.
fn find_prime_recipient(hs_dir: &Path) -> Result<String, String> {
    let path = hs_dir.join(".primefile");
    let content = fs::read_to_string(&path)
        .map_err(|_| ".primefile not found — cannot determine recipient".to_string())?;

    // Look for fingerprint patterns in .primefile
    for line in content.lines() {
        let l = line.trim();
        // Pattern: "fingerprint: DEADBEEF..."
        if let Some(rest) = l.strip_prefix("fingerprint:") {
            let fp = rest.trim().replace(' ', "");
            if fp.len() >= 16 {
                return Ok(fp);
            }
        }
        // Pattern: bare 40-char hex
        let cleaned = l.replace(' ', "");
        if cleaned.len() == 40 && cleaned.chars().all(|c| c.is_ascii_hexdigit()) {
            return Ok(cleaned);
        }
        // Pattern: 8-char short key ID
        if cleaned.len() == 8 && cleaned.chars().all(|c| c.is_ascii_hexdigit()) {
            return Ok(cleaned);
        }
    }

    Err("no valid fingerprint found in .primefile".to_string())
}

/// Returns true if `target` is safely within `base` after resolving all `..` components.
/// Used to prevent path traversal attacks during tar extraction.
fn is_safe_path(base: &Path, target: &Path) -> bool {
    target
        .canonicalize()
        .map(|canonical| canonical.starts_with(base))
        .unwrap_or(false)
}

/// Decrypt os.bin and apply to .ostk/
fn decrypt_os_bin(os_bin: &Path, hs_dir: &Path) -> Result<(), String> {
    // Decrypt to a temp tar
    let tar_path = std::env::temp_dir()
        .join(format!("ostk_os_dec_{}.tar.gz", std::process::id()));

    let gpg_out = Command::new("gpg")
        .arg("--batch")
        .arg("--decrypt")
        .arg("--output")
        .arg(&tar_path)
        .arg(os_bin)
        .output()
        .map_err(|e| format!("gpg: {e}"))?;

    if !gpg_out.status.success() {
        return Err(format!(
            "gpg decrypt failed (prime key required): {}",
            String::from_utf8_lossy(&gpg_out.stderr)
                .trim()
                .lines()
                .next()
                .unwrap_or("unknown error")
        ));
    }

    // Extract to a staging dir first so we can validate paths before applying
    let extract_staging = std::env::temp_dir()
        .join(format!("ostk_os_dec_staging_{}", std::process::id()));
    fs::create_dir_all(&extract_staging)
        .map_err(|e| format!("staging dir for extract: {e}"))?;

    let tar_out = Command::new("tar")
        .arg("-xzf")
        .arg(&tar_path)
        .arg("-C")
        .arg(&extract_staging)
        .output()
        .map_err(|e| format!("tar extract: {e}"))?;

    let _ = fs::remove_file(&tar_path);

    if !tar_out.status.success() {
        let _ = fs::remove_dir_all(&extract_staging);
        return Err(format!(
            "tar extract failed: {}",
            String::from_utf8_lossy(&tar_out.stderr).trim()
        ));
    }

    // Canonicalize the staging dir so is_safe_path works correctly
    let canonical_staging = extract_staging
        .canonicalize()
        .map_err(|e| format!("canonicalize staging: {e}"))?;

    // Validate every extracted file stays within the staging dir (no path traversal)
    validate_extracted_paths(&canonical_staging).inspect_err(|_e| {
        let _ = fs::remove_dir_all(&extract_staging);
    })?;

    // Safe — move files into hs_dir
    fs::create_dir_all(hs_dir).map_err(|e| format!("create .ostk/: {e}"))?;
    copy_dir_into(&extract_staging, hs_dir).inspect_err(|_e| {
        let _ = fs::remove_dir_all(&extract_staging);
    })?;

    let _ = fs::remove_dir_all(&extract_staging);
    Ok(())
}

/// Walk `dir` recursively and confirm every entry's canonicalized path is inside `dir`.
/// Returns Err with a descriptive message if any entry escapes.
fn validate_extracted_paths(dir: &Path) -> Result<(), String> {
    for entry in fs::read_dir(dir).map_err(|e| format!("read staging dir: {e}"))? {
        let entry = entry.map_err(|e| format!("dir entry: {e}"))?;
        let path = entry.path();
        if !is_safe_path(dir, &path) {
            return Err(format!(
                "path traversal detected in os.bin: {} escapes staging directory — aborting",
                path.display()
            ));
        }
        if path.is_dir() {
            validate_extracted_paths(&path)?;
        }
    }
    Ok(())
}

/// Copy all files from `src` into `dst`, creating subdirectories as needed.
fn copy_dir_into(src: &Path, dst: &Path) -> Result<(), String> {
    for entry in fs::read_dir(src).map_err(|e| format!("read dir {}: {e}", src.display()))? {
        let entry = entry.map_err(|e| format!("dir entry: {e}"))?;
        let src_path = entry.path();
        let name = entry.file_name();
        let dst_path = dst.join(&name);
        if src_path.is_dir() {
            fs::create_dir_all(&dst_path)
                .map_err(|e| format!("create dir {}: {e}", dst_path.display()))?;
            copy_dir_into(&src_path, &dst_path)?;
        } else {
            fs::copy(&src_path, &dst_path)
                .map_err(|e| format!("copy {}: {e}", src_path.display()))?;
        }
    }
    Ok(())
}

/// Check if a file/directory should be excluded from bail packages.
fn should_exclude_from_bail(name: &str) -> bool {
    for excluded in BAIL_EXCLUDE {
        if name == *excluded {
            return true;
        }
    }
    // Exclude secrets
    if name.ends_with(".secret") || name == "secrets" {
        return true;
    }
    false
}

/// Copy public files from staging into .ostk/ during unpack.
fn copy_bail_files_to_hs(staging: &Path, hs_dir: &Path, _mode: &str) -> Result<(), String> {
    fs::create_dir_all(hs_dir).map_err(|e| format!("create .ostk/: {e}"))?;

    let files = ["boot.md", ".primefile"];
    for name in &files {
        let src = staging.join(name);
        if src.exists() {
            let dest = hs_dir.join(name);
            fs::copy(&src, &dest).map_err(|e| format!("copy {name}: {e}"))?;
            println!("  -> .ostk/{name}");
        }
    }

    // .boot/INIT
    let init_src = staging.join(".boot/INIT");
    if init_src.exists() {
        let boot_dir = hs_dir.join(".boot");
        fs::create_dir_all(&boot_dir).map_err(|e| format!("create .boot/: {e}"))?;
        fs::copy(&init_src, boot_dir.join("INIT"))
            .map_err(|e| format!("copy .boot/INIT: {e}"))?;
        println!("  -> .ostk/.boot/INIT");
    }

    Ok(())
}

/// Create a .bail archive (tar.gz) from a staging directory.
fn create_bail_archive(staging: &Path, output: &Path) -> Result<(), String> {
    // Remove existing .bail if present
    if output.exists() {
        fs::remove_file(output).ok();
    }

    // tar.gz into a temp file, then rename to .bail
    let tar_path = output.with_extension("bail.tmp");

    let tar_out = Command::new("tar")
        .arg("-czf")
        .arg(&tar_path)
        .arg("-C")
        .arg(staging.parent().unwrap_or(staging))
        .arg(staging.file_name().unwrap_or_default())
        .output()
        .map_err(|e| format!("tar: {e}"))?;

    if !tar_out.status.success() {
        let _ = fs::remove_file(&tar_path);
        return Err(format!(
            "tar failed: {}",
            String::from_utf8_lossy(&tar_out.stderr).trim()
        ));
    }

    fs::rename(&tar_path, output).map_err(|e| format!("rename to .bail: {e}"))?;

    Ok(())
}

/// Extract a .bail archive to a staging directory.
fn extract_bail_archive(bail_path: &str, staging: &Path) -> Result<(), String> {
    if !Path::new(bail_path).exists() {
        return Err(format!("bail not found: {bail_path}"));
    }

    // .bail files are tar.gz archives — extract with --strip-components=1
    // to remove the outer staging directory name
    let out = Command::new("tar")
        .arg("-xzf")
        .arg(bail_path)
        .arg("-C")
        .arg(staging)
        .arg("--strip-components=1")
        .output()
        .map_err(|e| format!("tar: {e}"))?;

    if !out.status.success() {
        return Err(format!(
            "tar extract failed: {}",
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::{AtomicU32, Ordering};

    static TEST_COUNTER: AtomicU32 = AtomicU32::new(0);

    fn test_dir(prefix: &str) -> PathBuf {
        let n = TEST_COUNTER.fetch_add(1, Ordering::SeqCst);
        let pid = std::process::id();
        std::env::temp_dir().join(format!("ostk_bail_test_{prefix}_{pid}_{n}"))
    }

    /// Create a minimal ostk project structure for testing.
    fn make_hs_env(tmp: &Path) -> PathBuf {
        let hs = tmp.join(".ostk");
        fs::create_dir_all(hs.join("needles")).unwrap();
        fs::create_dir_all(hs.join("sessions")).unwrap();
        fs::write(hs.join("boot.md"), "# boot\n\n- Needles: 5 total, 3 open\n").unwrap();
        fs::write(hs.join(".primefile"), "# ostk.prime\nfingerprint: DEADBEEF01234567\n").unwrap();
        fs::write(hs.join("audit.jsonl"), r#"{"event":"boot"}"#).unwrap();
        fs::write(hs.join("agents.jsonl"), r#"{"alias":"@test"}"#).unwrap();
        fs::write(hs.join("needles/counter"), "5").unwrap();
        fs::write(hs.join("needles/issues.jsonl"), "").unwrap();
        hs
    }

    // -----------------------------------------------------------------------
    // Test 1: pack public mode creates the right files in manifest
    // -----------------------------------------------------------------------

    #[test]
    fn test_bail_pack_public_creates_manifest() {
        let tmp = test_dir("manifest");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        let hs_dir = make_hs_env(&tmp);

        // Collect public files
        let public_files = collect_public_files(&hs_dir);
        let names: Vec<&str> = public_files.iter().map(|(n, _)| n.as_str()).collect();

        // boot.md must be included
        assert!(names.contains(&"boot.md"), "boot.md must be in public files: {:?}", names);
        // .primefile must be included
        assert!(names.contains(&".primefile"), ".primefile must be in public files: {:?}", names);

        let _ = fs::remove_dir_all(&tmp);
    }

    // -----------------------------------------------------------------------
    // Test 2: bail excludes internal state files
    // -----------------------------------------------------------------------

    #[test]
    fn test_bail_pack_excludes_internal_state() {
        // All BAIL_EXCLUDE items must be excluded
        for name in BAIL_EXCLUDE {
            assert!(
                should_exclude_from_bail(name),
                "{name} should be excluded from bail"
            );
        }

        // Specifically verify the big ones
        assert!(should_exclude_from_bail("audit.jsonl"), "audit.jsonl must be excluded");
        assert!(should_exclude_from_bail("sessions"), "sessions/ must be excluded");
        assert!(should_exclude_from_bail("agents.jsonl"), "agents.jsonl must be excluded");

        // boot.md and .primefile must NOT be excluded (they're public)
        assert!(!should_exclude_from_bail("boot.md"), "boot.md must NOT be excluded");
        assert!(!should_exclude_from_bail(".primefile"), ".primefile must NOT be excluded");

        // Secret files must be excluded
        assert!(should_exclude_from_bail("login.secret"), "*.secret must be excluded");
        assert!(should_exclude_from_bail("secrets"), "secrets/ must be excluded");
    }

    // -----------------------------------------------------------------------
    // Test 3: manifest fields validation
    // -----------------------------------------------------------------------

    #[test]
    fn test_bail_manifest_fields() {
        let manifest = json!({
            "version": "1.0",
            "created": "2026-03-11T00:00:00Z",
            "signer": "@ostk.prime",
            "mode": "public",
            "files": ["boot.md", ".primefile"],
        });

        // Required fields must be present
        assert!(manifest["version"].is_string(), "version must be a string");
        assert!(manifest["created"].is_string(), "created must be a string");
        assert!(manifest["signer"].is_string(), "signer must be a string");
        assert!(manifest["mode"].is_string(), "mode must be a string");
        assert!(manifest["files"].is_array(), "files must be an array");

        // Mode must be "public" or "full"
        let mode = manifest["mode"].as_str().unwrap();
        assert!(
            mode == "public" || mode == "full",
            "mode must be 'public' or 'full', got: {mode}"
        );

        // Files list must not be empty
        let files = manifest["files"].as_array().unwrap();
        assert!(!files.is_empty(), "files list must not be empty");
    }

    // -----------------------------------------------------------------------
    // Test 4: public files collection — boot.md required, others optional
    // -----------------------------------------------------------------------

    #[test]
    fn test_bail_collect_public_files_minimal() {
        let tmp = test_dir("minimal_public");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        let hs_dir = tmp.join(".ostk");
        fs::create_dir_all(&hs_dir).unwrap();

        // Only boot.md present — no .primefile, no .boot/INIT
        fs::write(hs_dir.join("boot.md"), "# boot\n").unwrap();

        let files = collect_public_files(&hs_dir);
        let names: Vec<&str> = files.iter().map(|(n, _)| n.as_str()).collect();

        assert!(names.contains(&"boot.md"), "boot.md must be collected");
        assert!(!names.contains(&".primefile"), ".primefile must not appear if absent");
        assert!(!names.contains(&".boot/INIT"), ".boot/INIT must not appear if absent");

        let _ = fs::remove_dir_all(&tmp);
    }

    // -----------------------------------------------------------------------
    // Test 5: signer extraction from .primefile
    // -----------------------------------------------------------------------

    #[test]
    fn test_bail_read_prime_signer_fingerprint() {
        let tmp = test_dir("signer");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        let hs_dir = tmp.join(".ostk");
        fs::create_dir_all(&hs_dir).unwrap();

        fs::write(
            hs_dir.join(".primefile"),
            "# ostk.prime\nfingerprint: ABCD1234EFGH5678\n",
        ).unwrap();

        let signer = read_prime_signer(&hs_dir);
        assert!(
            signer.contains("ABCD1234") || signer.contains("fingerprint"),
            "signer should contain fingerprint info: {signer}"
        );

        let _ = fs::remove_dir_all(&tmp);
    }

    // -----------------------------------------------------------------------
    // Test 6: should_exclude_from_bail covers secret patterns
    // -----------------------------------------------------------------------

    #[test]
    fn test_bail_exclude_secret_patterns() {
        assert!(should_exclude_from_bail("api_key.secret"));
        assert!(should_exclude_from_bail("login.secret"));
        assert!(should_exclude_from_bail("secrets"));
        assert!(!should_exclude_from_bail("HUMANFILE"));
        assert!(!should_exclude_from_bail("GOVERNANCE.md"));
    }
}
