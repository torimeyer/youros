//! Sane defaults for ostk — zero-config operation.
//!
//! When no scheduler.af or HUMANFILE preference exists, the kernel uses
//! these defaults. The goal: `ostk` works if you have an API key in your
//! environment. Everything else is customization.

/// Known API key environment variables, checked in priority order.
const API_KEY_VARS: &[(&str, &str)] = &[
    ("ANTHROPIC_API_KEY", "anthropic"),
    ("OPENROUTER_API_KEY", "openrouter"),
    ("GEMINI_API_KEY", "google"),
    ("OLLAMA_HOST", "ollama"),
];

/// Detect the best available API key from environment.
/// Returns `(env_var_name, provider_name)` or `None`.
pub fn detect_api_key() -> Option<(&'static str, &'static str)> {
    for &(var, provider) in API_KEY_VARS {
        if let Ok(val) = std::env::var(var)
            && !val.is_empty()
        {
            return Some((var, provider));
        }
    }
    None
}

/// Default model when no scheduler.af or HUMANFILE MODEL directive exists.
/// Resolution chain: OSTK_MODEL env → cloud default → Ollama probe → last resort.
///
/// This extracts the logic from `do_cmd.rs:34-66` into a reusable function.
pub fn default_model() -> String {
    // Explicit override
    if let Ok(m) = std::env::var("OSTK_MODEL")
        && !m.is_empty()
    {
        return m;
    }

    // Cloud API key available → use Claude
    if std::env::var("ANTHROPIC_API_KEY").is_ok_and(|v| !v.is_empty()) {
        return "claude-sonnet-4-6".to_string();
    }
    if std::env::var("OPENROUTER_API_KEY").is_ok_and(|v| !v.is_empty()) {
        return "anthropic/claude-sonnet-4-6".to_string();
    }
    if std::env::var("GEMINI_API_KEY").is_ok_and(|v| !v.is_empty()) {
        return "gemini-3.1-pro-preview".to_string();
    }

    // Probe Ollama
    let host = std::env::var("OLLAMA_HOST")
        .unwrap_or_else(|_| "http://localhost:11434".to_string());
    if let Ok(resp) = std::process::Command::new("curl")
        .args(["-sf", &format!("{host}/api/tags")])
        .output()
        && resp.status.success()
        && let Ok(tags) = serde_json::from_slice::<serde_json::Value>(&resp.stdout)
        && let Some(name) = tags["models"][0]["name"].as_str()
    {
        return format!("local/{name}");
    }

    // Last resort
    "local/codestral:22b".to_string()
}

/// Default tools available to agents when no Agentfile specifies them.
pub fn default_tools() -> Vec<String> {
    vec![
        "shell".to_string(),
        "file:read".to_string(),
        "file:edit".to_string(),
    ]
}

/// Models available based on which API keys are present in the environment.
/// Used by `:models` picker when HUMANFILE has no AVAILABLE block.
pub fn models_for_available_keys() -> Vec<String> {
    let mut models = Vec::new();
    if std::env::var("ANTHROPIC_API_KEY").is_ok_and(|v| !v.is_empty()) {
        models.extend([
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-haiku-4-5",
        ].map(String::from));
    }
    if std::env::var("OPENROUTER_API_KEY").is_ok_and(|v| !v.is_empty()) {
        models.extend([
            "anthropic/claude-sonnet-4-6",
            "google/gemini-2.5-pro",
            "meta-llama/llama-4-maverick",
        ].map(String::from));
    }
    if std::env::var("GEMINI_API_KEY").is_ok_and(|v| !v.is_empty()) {
        models.extend([
            "gemini-3.1-pro-preview",
            "gemini-3-flash-preview",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-3.1-flash-lite-preview",
        ].map(String::from));
    }
    if models.is_empty() {
        // No cloud keys — check Ollama
        models.push("local/auto".to_string());
    }
    models
}

/// Default token limit when no Agentfile specifies one.
pub const DEFAULT_TOKEN_LIMIT: u64 = 200_000;

/// Default permission mode when no Agentfile or HUMANFILE specifies one.
pub const DEFAULT_PERMISSIONS: &str = "autonomous";

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    /// Mutex to serialize tests that mutate process-global API key env vars.
    /// Without this, `detect_api_key_returns_none_when_empty` and
    /// `models_for_available_keys_with_anthropic` can race on ANTHROPIC_API_KEY.
    static ENV_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn detect_api_key_returns_none_when_empty() {
        let _lock = ENV_LOCK.lock().unwrap();
        // Save and clear
        let saved: Vec<_> = API_KEY_VARS
            .iter()
            .map(|(k, _)| (*k, std::env::var(k).ok()))
            .collect();
        for (k, _) in API_KEY_VARS {
            // SAFETY: Serialized by ENV_LOCK; no concurrent env readers in this module.
            unsafe { std::env::remove_var(k); }
        }

        let result = detect_api_key();
        assert!(result.is_none());

        // Restore
        for (k, v) in &saved {
            if let Some(val) = v {
                // SAFETY: Serialized by ENV_LOCK.
                unsafe { std::env::set_var(k, val); }
            }
        }
    }

    #[test]
    fn default_tools_includes_shell() {
        let tools = default_tools();
        assert!(tools.contains(&"shell".to_string()));
        assert!(tools.contains(&"file:read".to_string()));
        assert!(tools.contains(&"file:edit".to_string()));
    }

    #[test]
    fn models_for_available_keys_always_nonempty() {
        // Regardless of which API keys are set, the function must never
        // return an empty vec — it falls back to "local/auto".
        let models = models_for_available_keys();
        assert!(!models.is_empty(), "models_for_available_keys() must never return empty");
    }

    #[test]
    fn models_for_available_keys_with_anthropic() {
        let _lock = ENV_LOCK.lock().unwrap();
        // Save, set, call, restore
        let saved = std::env::var("ANTHROPIC_API_KEY").ok();
        // SAFETY: Serialized by ENV_LOCK; no concurrent env readers in this module.
        unsafe { std::env::set_var("ANTHROPIC_API_KEY", "sk-test-fake"); }

        let models = models_for_available_keys();
        assert!(models.iter().any(|m| m.contains("claude")),
            "should include Claude models when ANTHROPIC_API_KEY is set, got: {:?}", models);

        // Restore
        match saved {
            // SAFETY: Serialized by ENV_LOCK.
            Some(val) => unsafe { std::env::set_var("ANTHROPIC_API_KEY", val); },
            None => unsafe { std::env::remove_var("ANTHROPIC_API_KEY"); },
        }
    }
}
