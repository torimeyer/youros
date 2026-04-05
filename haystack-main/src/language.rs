//! Shared `.language` file parser, formatter, and live recorder.
//!
//! The `.language` file is a pipe-delimited table with 9 columns:
//! `verb | tier | layer | last_gen | half_life | momentum | resolution | signature | doc`
//!
//! Used by shutdown (decay), show (introspect), fcp-tack (resolve),
//! and record_verb (live intent capture at tack resolution time).

use fs2::FileExt;
use std::fs;
use std::io::Write;
use std::path::Path;

/// A single entry in the `.language` file.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct LanguageEntry {
    pub verb: String,       // e.g. "boot" (without leading colon)
    pub tier: u8,
    pub layer: String,      // "kernel", "ceremony", "user", "device", "service"
    pub last_gen: u64,
    pub half_life: u64,
    pub momentum: f64,
    pub resolution: String,
    pub signature: String,  // e.g. "(hay) -> (needles)"
    pub doc: String,        // one-line docstring
    pub spec: String,       // authoritative spec path (e.g. "kernel-syscall-surface.md")
}

/// Signal verbs have bracket-delimited resolutions that don't map to CLI
/// commands. They're cognitive directives — mode shifts, control flow gates,
/// inference hints, or composition modifiers. The kernel routes these to the
/// signal handler instead of the shell.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SignalKind {
    /// `[intent signal: X]` — cognitive mode shift (e.g. :bg, :calibrate)
    Intent(String),
    /// `[LLM inference: X]` — change reasoning mode (e.g. :ultrathink)
    Inference(String),
    /// `[inferred: X]` — implicit command from usage patterns (e.g. :mode, :model)
    Inferred(String),
    /// `[control flow: X]` — ceremony gate (e.g. :confirm)
    ControlFlow(String),
    /// `[modifier: X]` — composition with other verbs (e.g. :compounds)
    Modifier(String),
}

impl SignalKind {
    /// Human-readable label for session summaries and headlines.
    pub fn label(&self) -> &str {
        match self {
            SignalKind::Intent(_) => "intent",
            SignalKind::Inference(_) => "inference",
            SignalKind::Inferred(_) => "inferred",
            SignalKind::ControlFlow(_) => "control-flow",
            SignalKind::Modifier(_) => "modifier",
        }
    }

    /// The detail string inside the brackets.
    pub fn detail(&self) -> &str {
        match self {
            SignalKind::Intent(d)
            | SignalKind::Inference(d)
            | SignalKind::Inferred(d)
            | SignalKind::ControlFlow(d)
            | SignalKind::Modifier(d) => d,
        }
    }
}

impl LanguageEntry {
    /// Is this a tier-0 infrastructure entry (not user-invocable)?
    pub fn is_infrastructure(&self) -> bool {
        self.tier == 0
    }

    /// Is this device/service alive?
    pub fn is_alive(&self) -> bool {
        self.momentum > 0.0
    }

    /// Is this a device driver?
    pub fn is_device(&self) -> bool {
        self.layer == "device"
    }

    /// Is this a kernel service?
    pub fn is_service(&self) -> bool {
        self.layer == "service"
    }

    /// Is this a signal verb (bracket-delimited resolution)?
    ///
    /// Signal verbs don't map to CLI commands — they're cognitive directives
    /// that affect session state, reasoning mode, or control flow.
    pub fn is_signal(&self) -> bool {
        self.signal_kind().is_some()
    }

    /// Parse the resolution into a SignalKind, if it's a bracket-delimited signal.
    ///
    /// Matches patterns like `[intent signal: background]`, `[LLM inference: deep analysis]`,
    /// `[inferred: mode]`, `[control flow: proceed]`, `[modifier: compound with target]`.
    pub fn signal_kind(&self) -> Option<SignalKind> {
        let res = self.resolution.trim();
        if !res.starts_with('[') || !res.ends_with(']') {
            return None;
        }
        // Strip brackets: "intent signal: background"
        let inner = res[1..res.len() - 1].trim();
        // Split on first colon: ("intent signal", "background")
        let (kind_str, detail) = match inner.split_once(':') {
            Some((k, d)) => (k.trim().to_lowercase(), d.trim().to_string()),
            None => (inner.to_lowercase(), String::new()),
        };
        match kind_str.as_str() {
            "intent signal" => Some(SignalKind::Intent(detail)),
            "llm inference" => Some(SignalKind::Inference(detail)),
            "inferred" => Some(SignalKind::Inferred(detail)),
            "control flow" => Some(SignalKind::ControlFlow(detail)),
            "modifier" => Some(SignalKind::Modifier(detail)),
            _ => None,
        }
    }
}

/// Parse `.language` file content into entries.
///
/// Skips blank lines and `#`-prefixed comment lines.
/// Requires at least 7 pipe-delimited columns; columns 7 and 8 are optional.
pub fn parse_language(content: &str) -> Vec<LanguageEntry> {
    content
        .lines()
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .filter_map(|line| {
            let cols: Vec<&str> = line.split('|').map(|c| c.trim()).collect();
            if cols.len() < 7 {
                return None;
            }
            let verb = cols[0].trim_start_matches(':').to_string();
            if verb.is_empty() {
                return None;
            }
            Some(LanguageEntry {
                verb,
                tier: cols[1].parse().unwrap_or(3),
                layer: cols[2].to_string(),
                last_gen: cols[3].parse().unwrap_or(0),
                half_life: cols[4].parse().unwrap_or(0),
                momentum: cols[5].parse().unwrap_or(0.0),
                resolution: cols[6].to_string(),
                signature: cols.get(7).unwrap_or(&"").to_string(),
                doc: cols.get(8).unwrap_or(&"").to_string(),
                spec: cols.get(9).unwrap_or(&"").to_string(),
            })
        })
        .collect()
}

/// Read and parse the `.language` file from a project root.
///
/// Respects `OSTK_STATE_DIR` env var (defaults to `.ostk`).
/// Returns an error if the file does not exist.
pub fn parse_language_file(root: &Path) -> Result<Vec<LanguageEntry>, String> {
    let state_dir =
        std::env::var("OSTK_STATE_DIR").unwrap_or_else(|_| ".ostk".to_string());
    let lang_path = root.join(&state_dir).join(".language");
    if !lang_path.exists() {
        return Err(format!(
            "{} not found \u{2014} run `ostk shutdown` to compile the language table",
            lang_path.display()
        ));
    }
    let content =
        fs::read_to_string(&lang_path).map_err(|e| format!("failed to read .language: {e}"))?;
    Ok(parse_language(&content))
}

/// Format entries back into `.language` file content.
pub fn format_language(entries: &[LanguageEntry]) -> String {
    let mut out = String::from("# .language \u{2014} compiled tack dialect\n");
    out.push_str(
        "# verb | tier | layer | last_gen | half_life | momentum | resolution | signature | doc | spec\n",
    );

    // Sort by layer order: service → device → kernel → ceremony → user
    let layer_order = |l: &str| -> u8 {
        match l {
            "service" => 0,
            "device" => 1,
            "kernel" => 2,
            "ceremony" => 3,
            _ => 4, // user
        }
    };

    let mut sorted: Vec<&LanguageEntry> = entries.iter().collect();
    sorted.sort_by(|a, b| {
        layer_order(&a.layer)
            .cmp(&layer_order(&b.layer))
            .then_with(|| a.verb.cmp(&b.verb))
    });

    let mut last_layer: Option<&str> = None;
    for e in sorted {
        // Insert section header when layer group changes
        let cur_layer = e.layer.as_str();
        if last_layer != Some(cur_layer) {
            let header = match cur_layer {
                "service" => "# --- services (tier 0) ---",
                "device" => "# --- devices (tier 0) ---",
                "kernel" => "# --- kernel ---",
                "ceremony" => "# --- ceremony ---",
                _ => "# --- user ---",
            };
            out.push_str(header);
            out.push('\n');
            last_layer = Some(cur_layer);
        }
        out.push_str(&format!(
            ":{:<12}| {} | {:<9}| {:<5}| {:<6}| {:<5}| {} | {} | {} | {}\n",
            e.verb,
            e.tier,
            e.layer,
            e.last_gen,
            e.half_life,
            format!("{:.2}", e.momentum),
            e.resolution,
            e.signature,
            e.doc,
            e.spec,
        ));
    }
    out
}

/// Record a verb usage at tack resolution time.
///
/// This is the primary mechanism for populating `.language` — called from
/// `resolve_tack()` whenever a `:verb` resolves successfully. Increments
/// momentum and updates `last_used` timestamp. Creates a new entry if the
/// verb doesn't exist yet.
///
/// Uses file-level locking (flock) for safe concurrent writes from multiple
/// agents.
pub fn record_verb(root: &Path, verb: &str, resolution: &str) -> Result<(), String> {
    let lang_path = crate::state_dir(root).join(".language");

    // Acquire exclusive lock on a sidecar .lock file
    let lock_path = lang_path.with_extension("language.lock");
    let lock_file = fs::OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(false)
        .open(&lock_path)
        .map_err(|e| format!("failed to open .language lock: {e}"))?;
    lock_file
        .lock_exclusive()
        .map_err(|e| format!("failed to lock .language: {e}"))?;

    let result = record_verb_inner(&lang_path, verb, resolution);

    // Release lock (also released on drop, but be explicit)
    let _ = lock_file.unlock();
    result
}

/// Inner implementation without locking (for testability).
fn record_verb_inner(lang_path: &Path, verb: &str, resolution: &str) -> Result<(), String> {
    let verb = verb.trim_start_matches(':').to_lowercase();
    if verb.is_empty() {
        return Ok(());
    }

    // Read existing entries
    let content = fs::read_to_string(lang_path).unwrap_or_default();
    let mut entries = parse_language(&content);

    // Find or create the entry
    if let Some(entry) = entries.iter_mut().find(|e| e.verb == verb) {
        // Increment momentum (cap at 1.0)
        entry.momentum = (entry.momentum + 0.1).min(1.0);
        // Update last_gen as a unix epoch day counter (lightweight timestamp)
        entry.last_gen = unix_epoch_days();
    } else {
        // New verb — mint a user-layer entry
        entries.push(LanguageEntry {
            verb: verb.to_string(),
            tier: 3,
            layer: "user".to_string(),
            last_gen: unix_epoch_days(),
            half_life: 50,
            momentum: 0.3,
            resolution: resolution.to_string(),
            signature: String::new(),
            doc: String::new(),
            spec: String::new(),
        });
    }

    // Write atomically via temp file
    let tmp_path = lang_path.with_extension("language.tmp");
    let output = format_language(&entries);
    let mut tmp = fs::OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(true)
        .open(&tmp_path)
        .map_err(|e| format!("failed to create .language tmp: {e}"))?;
    tmp.write_all(output.as_bytes())
        .map_err(|e| format!("failed to write .language tmp: {e}"))?;
    tmp.flush()
        .map_err(|e| format!("failed to flush .language tmp: {e}"))?;
    fs::rename(&tmp_path, lang_path)
        .map_err(|e| format!("failed to rename .language tmp: {e}"))?;

    Ok(())
}

/// Register a device or service in `.language` (creates or updates entry).
/// Uses flock for safe concurrent writes.
pub fn register_capability(
    root: &Path,
    verb: &str,
    layer: &str,
    resolution: &str,
    signature: &str,
    doc: &str,
    alive: bool,
) -> Result<(), String> {
    let lang_path = crate::state_dir(root).join(".language");

    // Acquire exclusive lock on a sidecar .lock file
    let lock_path = lang_path.with_extension("language.lock");
    let lock_file = fs::OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(false)
        .open(&lock_path)
        .map_err(|e| format!("failed to open .language lock: {e}"))?;
    lock_file
        .lock_exclusive()
        .map_err(|e| format!("failed to lock .language: {e}"))?;

    let result =
        register_capability_inner(&lang_path, verb, layer, resolution, signature, doc, alive);

    let _ = lock_file.unlock();
    result
}

/// Inner implementation without locking (for testability).
fn register_capability_inner(
    lang_path: &Path,
    verb: &str,
    layer: &str,
    resolution: &str,
    signature: &str,
    doc: &str,
    alive: bool,
) -> Result<(), String> {
    let verb = verb.trim_start_matches(':').to_lowercase();
    if verb.is_empty() {
        return Ok(());
    }

    let momentum = if alive { 1.0 } else { 0.0 };

    // Read existing entries
    let content = fs::read_to_string(lang_path).unwrap_or_default();
    let mut entries = parse_language(&content);

    // Find or create the entry
    if let Some(entry) = entries.iter_mut().find(|e| e.verb == verb) {
        // Update existing entry
        entry.layer = layer.to_string();
        entry.resolution = resolution.to_string();
        entry.signature = signature.to_string();
        entry.doc = doc.to_string();
        entry.momentum = momentum;
        entry.last_gen = unix_epoch_days();
    } else {
        // New capability — tier 0 infrastructure entry
        entries.push(LanguageEntry {
            verb: verb.to_string(),
            tier: 0,
            layer: layer.to_string(),
            last_gen: unix_epoch_days(),
            half_life: 0,
            momentum,
            resolution: resolution.to_string(),
            signature: signature.to_string(),
            doc: doc.to_string(),
            spec: String::new(),
        });
    }

    // Write atomically via temp file
    let tmp_path = lang_path.with_extension("language.tmp");
    let output = format_language(&entries);
    let mut tmp = fs::OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(true)
        .open(&tmp_path)
        .map_err(|e| format!("failed to create .language tmp: {e}"))?;
    tmp.write_all(output.as_bytes())
        .map_err(|e| format!("failed to write .language tmp: {e}"))?;
    tmp.flush()
        .map_err(|e| format!("failed to flush .language tmp: {e}"))?;
    fs::rename(&tmp_path, lang_path)
        .map_err(|e| format!("failed to rename .language tmp: {e}"))?;

    Ok(())
}

/// Check if a capability is registered and alive.
pub fn is_capability_alive(root: &Path, verb: &str) -> bool {
    let lang_path = crate::state_dir(root).join(".language");
    let content = fs::read_to_string(lang_path).unwrap_or_default();
    let entries = parse_language(&content);
    let verb = verb.trim_start_matches(':').to_lowercase();
    entries
        .iter()
        .any(|e| e.verb == verb && e.momentum > 0.0)
}

/// Update momentum for a registered capability.
pub fn update_capability_health(root: &Path, verb: &str, alive: bool) -> Result<(), String> {
    let lang_path = crate::state_dir(root).join(".language");

    // Acquire exclusive lock
    let lock_path = lang_path.with_extension("language.lock");
    let lock_file = fs::OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(false)
        .open(&lock_path)
        .map_err(|e| format!("failed to open .language lock: {e}"))?;
    lock_file
        .lock_exclusive()
        .map_err(|e| format!("failed to lock .language: {e}"))?;

    let verb = verb.trim_start_matches(':').to_lowercase();
    let content = fs::read_to_string(&lang_path).unwrap_or_default();
    let mut entries = parse_language(&content);

    if let Some(entry) = entries.iter_mut().find(|e| e.verb == verb) {
        entry.momentum = if alive { 1.0 } else { 0.0 };
        entry.last_gen = unix_epoch_days();

        // Write back atomically
        let tmp_path = lang_path.with_extension("language.tmp");
        let output = format_language(&entries);
        let mut tmp = fs::OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(&tmp_path)
            .map_err(|e| format!("failed to create .language tmp: {e}"))?;
        tmp.write_all(output.as_bytes())
            .map_err(|e| format!("failed to write .language tmp: {e}"))?;
        tmp.flush()
            .map_err(|e| format!("failed to flush .language tmp: {e}"))?;
        fs::rename(&tmp_path, &lang_path)
            .map_err(|e| format!("failed to rename .language tmp: {e}"))?;
    }

    let _ = lock_file.unlock();
    Ok(())
}

/// Collapse alias verbs that share the same resolution string.
///
/// For each unique resolution, keep the verb with the highest momentum.
/// Ties are broken alphabetically (lowest verb name wins).
/// Only operates on user-layer entries with tier <= 2; all other entries
/// pass through unmodified.
pub fn deduplicate_by_resolution(entries: &[LanguageEntry]) -> Vec<LanguageEntry> {
    use std::collections::HashMap;

    let mut pass_through: Vec<LanguageEntry> = Vec::new();
    // resolution -> best entry so far
    let mut best: HashMap<String, LanguageEntry> = HashMap::new();

    for entry in entries {
        // Only dedup user-layer, tier <= 2
        if entry.layer != "user" || entry.tier > 2 {
            pass_through.push(entry.clone());
            continue;
        }

        let key = entry.resolution.clone();
        match best.get(&key) {
            Some(existing) => {
                // Higher momentum wins; ties broken alphabetically
                let dominated = if (entry.momentum - existing.momentum).abs() < f64::EPSILON {
                    entry.verb > existing.verb // alphabetically later loses
                } else {
                    entry.momentum < existing.momentum
                };
                if !dominated {
                    best.insert(key, entry.clone());
                }
            }
            None => {
                best.insert(key, entry.clone());
            }
        }
    }

    // Collect winners and append to pass_through
    let mut winners: Vec<LanguageEntry> = best.into_values().collect();
    winners.sort_by(|a, b| a.verb.cmp(&b.verb));
    pass_through.extend(winners);
    pass_through
}

/// Read `.ostk/verbs.lock` — one verb per line, `#` comments and blank lines skipped.
///
/// Returns an empty set if the file does not exist.
pub fn read_verbs_lock(root: &Path) -> std::collections::HashSet<String> {
    let lock_path = crate::state_dir(root).join("verbs.lock");
    let content = match fs::read_to_string(&lock_path) {
        Ok(c) => c,
        Err(_) => return std::collections::HashSet::new(),
    };
    content
        .lines()
        .map(|l| l.trim())
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .map(|l| l.trim_start_matches(':').to_string())
        .collect()
}

/// Check whether a verb appears in `verbs.lock`.
pub fn is_verbs_lock_entry(root: &Path, verb: &str) -> bool {
    let set = read_verbs_lock(root);
    let verb = verb.trim_start_matches(':');
    set.contains(verb)
}

/// Days since Unix epoch — lightweight timestamp for last_gen field.
fn unix_epoch_days() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
        / 86400
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn record_verb_creates_new_entry() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let hs = crate::state_dir(&root);
        std::fs::create_dir_all(&hs).unwrap();

        // Start with empty .language
        std::fs::write(hs.join(".language"), "# .language\n").unwrap();

        record_verb(root, "compounds", "ostk compounds").unwrap();

        let content = std::fs::read_to_string(hs.join(".language")).unwrap();
        let entries = parse_language(&content);
        let entry = entries.iter().find(|e| e.verb == "compounds");
        assert!(entry.is_some(), "compounds should appear in .language");
        let entry = entry.unwrap();
        assert_eq!(entry.layer, "user");
        assert_eq!(entry.tier, 3);
        assert!((entry.momentum - 0.3).abs() < f64::EPSILON);
        assert_eq!(entry.resolution, "ostk compounds");
    }

    #[test]
    fn record_verb_increments_existing() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let hs = crate::state_dir(&root);
        std::fs::create_dir_all(&hs).unwrap();

        let lang = "# .language\n\
                     :compile    | 1 | user    | 0    | 500   | 0.5  | ostk compile |  | \n";
        std::fs::write(hs.join(".language"), lang).unwrap();

        record_verb(root, "compile", "ostk compile").unwrap();

        let content = std::fs::read_to_string(hs.join(".language")).unwrap();
        let entries = parse_language(&content);
        let entry = entries.iter().find(|e| e.verb == "compile").unwrap();
        assert!(
            (entry.momentum - 0.6).abs() < f64::EPSILON,
            "momentum should increase from 0.5 to 0.6, got {}",
            entry.momentum
        );
    }

    #[test]
    fn record_verb_updates_timestamp() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let hs = crate::state_dir(&root);
        std::fs::create_dir_all(&hs).unwrap();

        let lang = "# .language\n\
                     :compile    | 1 | user    | 0    | 500   | 0.5  | ostk compile |  | \n";
        std::fs::write(hs.join(".language"), lang).unwrap();

        record_verb(root, "compile", "ostk compile").unwrap();

        let content = std::fs::read_to_string(hs.join(".language")).unwrap();
        let entries = parse_language(&content);
        let entry = entries.iter().find(|e| e.verb == "compile").unwrap();
        assert!(
            entry.last_gen > 0,
            "last_gen should be updated to current epoch day, got {}",
            entry.last_gen
        );
    }

    #[test]
    fn record_verb_handles_colon_prefix() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let hs = crate::state_dir(&root);
        std::fs::create_dir_all(&hs).unwrap();
        std::fs::write(hs.join(".language"), "# .language\n").unwrap();

        // Pass with leading colon — should be stripped
        record_verb(root, ":trace", "ostk trace").unwrap();

        let content = std::fs::read_to_string(hs.join(".language")).unwrap();
        let entries = parse_language(&content);
        let entry = entries.iter().find(|e| e.verb == "trace");
        assert!(entry.is_some(), "trace should appear (colon stripped)");
    }

    #[test]
    fn record_verb_caps_momentum_at_one() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let hs = crate::state_dir(&root);
        std::fs::create_dir_all(&hs).unwrap();

        let lang = "# .language\n\
                     :boot       | 1 | kernel  | 0    | 0     | 0.95 | ostk boot |  | \n";
        std::fs::write(hs.join(".language"), lang).unwrap();

        record_verb(root, "boot", "ostk boot").unwrap();

        let content = std::fs::read_to_string(hs.join(".language")).unwrap();
        let entries = parse_language(&content);
        let entry = entries.iter().find(|e| e.verb == "boot").unwrap();
        assert!(
            (entry.momentum - 1.0).abs() < f64::EPSILON,
            "momentum should cap at 1.0, got {}",
            entry.momentum
        );
    }

    #[test]
    fn record_verb_creates_file_if_missing() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let hs = crate::state_dir(&root);
        std::fs::create_dir_all(&hs).unwrap();
        // No .language file at all

        record_verb(root, "link", "ostk link").unwrap();

        let content = std::fs::read_to_string(hs.join(".language")).unwrap();
        let entries = parse_language(&content);
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].verb, "link");
    }

    // ── Capability register tests ────────────────────────────────────────

    #[test]
    fn test_register_capability_creates_entry() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let hs = crate::state_dir(root);
        std::fs::create_dir_all(&hs).unwrap();
        std::fs::write(hs.join(".language"), "# .language\n").unwrap();

        register_capability(
            root,
            "fcp-rust",
            "device",
            ".ostk/drivers/fcp-rust.sock",
            "(query) -> (result)",
            "rust intelligence",
            true,
        )
        .unwrap();

        let content = std::fs::read_to_string(hs.join(".language")).unwrap();
        let entries = parse_language(&content);
        let entry = entries.iter().find(|e| e.verb == "fcp-rust");
        assert!(entry.is_some(), "fcp-rust should appear in .language");
        let entry = entry.unwrap();
        assert_eq!(entry.layer, "device");
        assert_eq!(entry.tier, 0);
        assert!((entry.momentum - 1.0).abs() < f64::EPSILON);
        assert_eq!(entry.resolution, ".ostk/drivers/fcp-rust.sock");
        assert_eq!(entry.signature, "(query) -> (result)");
        assert_eq!(entry.doc, "rust intelligence");
    }

    #[test]
    fn test_register_capability_updates_existing() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let hs = crate::state_dir(root);
        std::fs::create_dir_all(&hs).unwrap();
        std::fs::write(hs.join(".language"), "# .language\n").unwrap();

        // Register twice with different docs
        register_capability(root, "gen-table", "service", "internal",
            "() -> ()", "generation tracking v1", true).unwrap();
        register_capability(root, "gen-table", "service", "internal",
            "() -> ()", "generation tracking v2", false).unwrap();

        let content = std::fs::read_to_string(hs.join(".language")).unwrap();
        let entries = parse_language(&content);
        let matches: Vec<_> = entries.iter().filter(|e| e.verb == "gen-table").collect();
        assert_eq!(matches.len(), 1, "should not duplicate");
        assert_eq!(matches[0].doc, "generation tracking v2");
        assert!((matches[0].momentum - 0.0).abs() < f64::EPSILON, "should be dead after second register");
    }

    #[test]
    fn test_is_capability_alive() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let hs = crate::state_dir(root);
        std::fs::create_dir_all(&hs).unwrap();
        std::fs::write(hs.join(".language"), "# .language\n").unwrap();

        // Register alive device
        register_capability(root, "fcp-screen", "device", "internal",
            "(event) -> (render)", "display driver", true).unwrap();
        // Register dead service
        register_capability(root, "dying", "service", ".ostk/fleet/",
            "() -> (state)", "context pressure notification", false).unwrap();

        assert!(is_capability_alive(root, "fcp-screen"));
        assert!(!is_capability_alive(root, "dying"));
        assert!(!is_capability_alive(root, "nonexistent"));
    }

    #[test]
    fn test_update_capability_health() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let hs = crate::state_dir(root);
        std::fs::create_dir_all(&hs).unwrap();
        std::fs::write(hs.join(".language"), "# .language\n").unwrap();

        // Register as alive
        register_capability(root, "fcp-rust", "device", ".ostk/drivers/fcp-rust.sock",
            "(query) -> (result)", "rust driver", true).unwrap();
        assert!(is_capability_alive(root, "fcp-rust"));

        // Update to dead
        update_capability_health(root, "fcp-rust", false).unwrap();
        assert!(!is_capability_alive(root, "fcp-rust"));

        // Update back to alive
        update_capability_health(root, "fcp-rust", true).unwrap();
        assert!(is_capability_alive(root, "fcp-rust"));
    }

    #[test]
    fn test_infrastructure_entries_filtered_from_user_verbs() {
        let content = "# .language\n\
            :fcp-screen | 0 | device  | 0 | 0 | 1.00 | internal | (event) -> (render) | display driver\n\
            :gen-table  | 0 | service | 0 | 0 | 1.00 | internal | (path) -> (gen) | generation tracking\n\
            :boot       | 1 | kernel  | 0 | 0 | 1.00 | ostk boot |  | \n\
            :compile    | 3 | user    | 0 | 500 | 0.80 | ostk compile |  | \n";
        let entries = parse_language(content);
        let infra: Vec<_> = entries.iter().filter(|e| e.is_infrastructure()).collect();
        let user: Vec<_> = entries.iter().filter(|e| !e.is_infrastructure()).collect();
        assert_eq!(infra.len(), 2, "should have 2 tier-0 entries");
        assert_eq!(user.len(), 2, "should have 2 non-infrastructure entries");
        assert!(infra.iter().all(|e| e.tier == 0));
    }

    #[test]
    fn test_format_language_groups_by_layer() {
        let entries = vec![
            LanguageEntry {
                verb: "compile".to_string(), tier: 3, layer: "user".to_string(),
                last_gen: 0, half_life: 500, momentum: 0.8,
                resolution: "ostk compile".to_string(),
                signature: String::new(), doc: String::new(), spec: String::new(),
            },
            LanguageEntry {
                verb: "boot".to_string(), tier: 1, layer: "kernel".to_string(),
                last_gen: 0, half_life: 0, momentum: 1.0,
                resolution: "ostk boot".to_string(),
                signature: String::new(), doc: String::new(), spec: String::new(),
            },
            LanguageEntry {
                verb: "fcp-screen".to_string(), tier: 0, layer: "device".to_string(),
                last_gen: 0, half_life: 0, momentum: 1.0,
                resolution: "internal".to_string(),
                signature: "(event) -> (render)".to_string(),
                doc: "display driver".to_string(), spec: String::new(),
            },
            LanguageEntry {
                verb: "gen-table".to_string(), tier: 0, layer: "service".to_string(),
                last_gen: 0, half_life: 0, momentum: 1.0,
                resolution: "internal".to_string(),
                signature: "(path) -> (gen)".to_string(),
                doc: "generation tracking".to_string(), spec: String::new(),
            },
        ];
        let output = format_language(&entries);
        let lines: Vec<&str> = output.lines().collect();
        // Services should come first, then devices, then kernel, then user
        // Use colon-prefixed verb to avoid matching comments/headers
        let svc_pos = lines.iter().position(|l| l.starts_with(":gen-table")).unwrap();
        let dev_pos = lines.iter().position(|l| l.starts_with(":fcp-screen")).unwrap();
        let kern_pos = lines.iter().position(|l| l.starts_with(":boot")).unwrap();
        let user_pos = lines.iter().position(|l| l.starts_with(":compile")).unwrap();
        assert!(svc_pos < dev_pos, "services before devices: svc={svc_pos} dev={dev_pos}");
        assert!(dev_pos < kern_pos, "devices before kernel: dev={dev_pos} kern={kern_pos}");
        assert!(kern_pos < user_pos, "kernel before user: kern={kern_pos} user={user_pos}");
        // Section headers should exist
        assert!(output.contains("# --- services"), "should have services header");
        assert!(output.contains("# --- devices"), "should have devices header");
        assert!(output.contains("# --- kernel"), "should have kernel header");
        assert!(output.contains("# --- user"), "should have user header");
    }

    // ── deduplicate_by_resolution tests ─────────────────────────────────

    #[test]
    fn dedup_picks_highest_momentum() {
        let entries = vec![
            LanguageEntry {
                verb: "alias-a".to_string(), tier: 1, layer: "user".to_string(),
                last_gen: 0, half_life: 50, momentum: 0.3,
                resolution: "ostk compile".to_string(),
                signature: String::new(), doc: String::new(), spec: String::new(),
            },
            LanguageEntry {
                verb: "alias-b".to_string(), tier: 2, layer: "user".to_string(),
                last_gen: 0, half_life: 50, momentum: 0.8,
                resolution: "ostk compile".to_string(),
                signature: String::new(), doc: String::new(), spec: String::new(),
            },
        ];
        let result = deduplicate_by_resolution(&entries);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].verb, "alias-b");
    }

    #[test]
    fn dedup_unique_resolutions_keeps_all() {
        let entries = vec![
            LanguageEntry {
                verb: "compile".to_string(), tier: 1, layer: "user".to_string(),
                last_gen: 0, half_life: 50, momentum: 0.5,
                resolution: "ostk compile".to_string(),
                signature: String::new(), doc: String::new(), spec: String::new(),
            },
            LanguageEntry {
                verb: "boot".to_string(), tier: 1, layer: "user".to_string(),
                last_gen: 0, half_life: 50, momentum: 0.9,
                resolution: "ostk boot".to_string(),
                signature: String::new(), doc: String::new(), spec: String::new(),
            },
        ];
        let result = deduplicate_by_resolution(&entries);
        assert_eq!(result.len(), 2);
    }

    #[test]
    fn dedup_filters_to_user_layer_tier_lte_2() {
        let entries = vec![
            // user layer, tier 1 — eligible for dedup
            LanguageEntry {
                verb: "alias-x".to_string(), tier: 1, layer: "user".to_string(),
                last_gen: 0, half_life: 50, momentum: 0.3,
                resolution: "shared-res".to_string(),
                signature: String::new(), doc: String::new(), spec: String::new(),
            },
            // user layer, tier 1 — eligible, same resolution → should be deduped
            LanguageEntry {
                verb: "alias-y".to_string(), tier: 1, layer: "user".to_string(),
                last_gen: 0, half_life: 50, momentum: 0.5,
                resolution: "shared-res".to_string(),
                signature: String::new(), doc: String::new(), spec: String::new(),
            },
            // user layer, tier 3 — NOT eligible (tier > 2), passes through
            LanguageEntry {
                verb: "alias-z".to_string(), tier: 3, layer: "user".to_string(),
                last_gen: 0, half_life: 50, momentum: 0.1,
                resolution: "shared-res".to_string(),
                signature: String::new(), doc: String::new(), spec: String::new(),
            },
            // kernel layer — NOT eligible, passes through
            LanguageEntry {
                verb: "boot".to_string(), tier: 1, layer: "kernel".to_string(),
                last_gen: 0, half_life: 0, momentum: 1.0,
                resolution: "shared-res".to_string(),
                signature: String::new(), doc: String::new(), spec: String::new(),
            },
        ];
        let result = deduplicate_by_resolution(&entries);
        // pass-through: alias-z (tier 3) + boot (kernel) = 2
        // dedup winners: alias-y (higher momentum) = 1
        // total = 3
        assert_eq!(result.len(), 3);
        let verbs: Vec<&str> = result.iter().map(|e| e.verb.as_str()).collect();
        assert!(verbs.contains(&"alias-y"), "alias-y should win dedup");
        assert!(verbs.contains(&"alias-z"), "alias-z should pass through (tier 3)");
        assert!(verbs.contains(&"boot"), "boot should pass through (kernel layer)");
        assert!(!verbs.contains(&"alias-x"), "alias-x should be deduped away");
    }

    #[test]
    fn dedup_tie_broken_alphabetically() {
        let entries = vec![
            LanguageEntry {
                verb: "beta".to_string(), tier: 1, layer: "user".to_string(),
                last_gen: 0, half_life: 50, momentum: 0.5,
                resolution: "same".to_string(),
                signature: String::new(), doc: String::new(), spec: String::new(),
            },
            LanguageEntry {
                verb: "alpha".to_string(), tier: 1, layer: "user".to_string(),
                last_gen: 0, half_life: 50, momentum: 0.5,
                resolution: "same".to_string(),
                signature: String::new(), doc: String::new(), spec: String::new(),
            },
        ];
        let result = deduplicate_by_resolution(&entries);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].verb, "alpha", "alphabetically first should win on tie");
    }

    // ── verbs.lock tests ────────────────────────────────────────────────

    #[test]
    fn read_verbs_lock_with_comments_and_blanks() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let hs = crate::state_dir(root);
        std::fs::create_dir_all(&hs).unwrap();

        let content = "# locked verbs\ncompile\n\n# another comment\nboot\n  trace  \n";
        std::fs::write(hs.join("verbs.lock"), content).unwrap();

        let set = read_verbs_lock(root);
        assert_eq!(set.len(), 3);
        assert!(set.contains("compile"));
        assert!(set.contains("boot"));
        assert!(set.contains("trace"));
    }

    #[test]
    fn read_verbs_lock_missing_file_returns_empty() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let hs = crate::state_dir(root);
        std::fs::create_dir_all(&hs).unwrap();
        // no verbs.lock file

        let set = read_verbs_lock(root);
        assert!(set.is_empty());
    }

    #[test]
    fn is_verbs_lock_entry_true_false() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let hs = crate::state_dir(root);
        std::fs::create_dir_all(&hs).unwrap();

        std::fs::write(hs.join("verbs.lock"), "compile\nboot\n").unwrap();

        assert!(is_verbs_lock_entry(root, "compile"));
        assert!(is_verbs_lock_entry(root, ":boot")); // colon stripped
        assert!(!is_verbs_lock_entry(root, "nonexistent"));
    }
}
