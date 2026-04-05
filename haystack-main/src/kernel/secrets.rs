//! Secret masking — prevent secret values from entering LLM context.
//!
//! At boot, the kernel resolves all HUMANFILE SECRET key names to values.
//! Every tool result passes through `mask()` before the LLM sees it.
//! Known secret values are replaced with `●●●●●●●●`.
//!
//! Values are mutable — `ostk secret set` calls `register_value()` to add
//! or update the mask list at runtime. Old values are kept (a changed secret
//! still leaks if the old value appears in output).
//!
//! This catches ALL leak vectors: Bash output, file reads, error messages,
//! environment variable dumps — anything that passes through the tool result path.

use std::sync::Mutex;

const MIN_LENGTH: usize = 8; // don't mask short values (high false positive risk)

/// Build a structure-preserving mask: prefix + masked middle + length hint.
/// e.g., "sk-proj-abc123..." → "sk-proj-●●●● (48 chars)"
/// Preserves enough for the model to verify format without leaking the value.
fn build_mask(secret: &str) -> String {
    let len = secret.len();
    // Find prefix boundary: first segment before alphanumeric run
    // e.g., "sk-proj-" in "sk-proj-abc123def...", "AKIA" in "AKIAIOSFODNN7EXAMPLE"
    let prefix_len = secret
        .char_indices()
        .take(12) // max 12 chars of prefix
        .take_while(|(i, c)| *i < 4 || !c.is_alphanumeric() || {
            // Keep going through prefix separators (-, _)
            let next = secret[i + c.len_utf8()..].chars().next();
            matches!(next, Some('-') | Some('_'))
        })
        .last()
        .map(|(i, c)| i + c.len_utf8())
        .unwrap_or(4.min(len));

    let prefix = &secret[..prefix_len.min(len)];
    format!("{prefix}●●●● ({len} chars)")
}

/// Resolved secret values. Initialized at boot, updated on `secret set`.
static SECRET_VALUES: Mutex<Vec<String>> = Mutex::new(Vec::new());

/// Initialize the secret mask list from HUMANFILE secret key names.
/// Called at boot. Resolves each key and stores values for masking.
pub fn init(secret_keys: &[String]) {
    let values: Vec<String> = secret_keys
        .iter()
        .filter_map(|key| crate::commands::secret::resolve_secret(key).ok())
        .filter(|v| v.len() >= MIN_LENGTH)
        .collect();

    if let Ok(mut guard) = SECRET_VALUES.lock() {
        *guard = values;
    }
}

/// Register a new secret value for masking. Called by `ostk secret set`.
/// If the value was changed, the old value remains masked too (defense in depth).
pub fn register_value(value: &str) {
    if value.len() < MIN_LENGTH {
        return;
    }
    if let Ok(mut guard) = SECRET_VALUES.lock() {
        let val = value.to_string();
        if !guard.contains(&val) {
            guard.push(val);
        }
    }
}

/// Mask any known secret values in the output string.
/// Returns the original string if no secrets are loaded or found.
pub fn mask(output: &str) -> String {
    let values = match SECRET_VALUES.lock() {
        Ok(guard) if !guard.is_empty() => guard,
        _ => return output.to_string(),
    };

    let mut masked = output.to_string();
    for secret in values.iter() {
        if masked.contains(secret.as_str()) {
            masked = masked.replace(secret.as_str(), &build_mask(secret));
        }
    }
    masked
}

/// Check if a secret key is available (resolved successfully).
/// Returns true/false — never the value.
pub fn is_available(key: &str) -> bool {
    crate::commands::secret::resolve_secret(key).is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_build_mask_preserves_prefix() {
        // sk- prefix preserved, value hidden, length shown
        let masked = build_mask("sk-abc123def456ghi789jklmnop");
        assert!(masked.starts_with("sk-"), "should preserve sk- prefix: {masked}");
        assert!(masked.contains("●●●●"), "should contain mask: {masked}");
        assert!(masked.contains("chars"), "should show length: {masked}");
        assert!(!masked.contains("abc123"), "should not contain secret value: {masked}");
    }

    #[test]
    fn test_build_mask_aws_style() {
        let masked = build_mask("AKIAIOSFODNN7EXAMPLE");
        assert!(masked.starts_with("AKIA"), "should preserve AKIA prefix: {masked}");
        assert!(masked.contains("20 chars"), "should show length: {masked}");
    }

    #[test]
    fn test_build_mask_short_key() {
        let masked = build_mask("abcdefghij");
        assert!(masked.contains("10 chars"), "should show length: {masked}");
        assert!(masked.contains("●●●●"), "should contain mask: {masked}");
    }

    #[test]
    fn test_mask_no_secrets_returns_original() {
        let output = "normal output with no secrets";
        let result = mask(output);
        assert_eq!(result, output);
    }

    #[test]
    fn test_register_value_skips_short() {
        register_value("short"); // < 8 chars, should be ignored
        let result = mask("output containing short value");
        assert!(result.contains("short"), "short values should not be masked");
    }

    #[test]
    fn test_register_and_mask() {
        let secret = "test-secret-value-long-enough";
        register_value(secret);
        let result = mask(&format!("output containing {secret} in it"));
        assert!(!result.contains("value-long-enough"), "should mask the secret value");
        assert!(result.contains("●●●●"), "should contain mask");
        assert!(result.contains("chars"), "should show length hint");
    }

    #[test]
    fn test_register_deduplicates() {
        let secret = "dedup-test-secret-12345";
        register_value(secret);
        register_value(secret);
        if let Ok(guard) = SECRET_VALUES.lock() {
            let count = guard.iter().filter(|v| *v == secret).count();
            assert!(count <= 1, "should not duplicate: found {count}");
        }
    }
}
