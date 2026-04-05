//! Provider routing, model aliases, and model capability lookups.
//!
//! These are CPU-layer primitives consumed by `create_driver`, `into_loop_config`,
//! and `config_from_agentfile`. Moved here from `commands::run` to fix the inverted
//! dependency (cpu importing from commands).

/// API provider tag — determines which client to instantiate.
#[derive(Debug, Clone, PartialEq)]
pub enum ApiProvider {
    /// Anthropic API — claude-* models
    Anthropic,
    /// OpenAI API — gpt-* models
    OpenAi,
    /// Google Gemini API
    Google,
    /// Mistral API — mistral-* models
    Mistral,
    /// OpenRouter — any model when OPENROUTER_API_KEY is set (fallback)
    OpenRouter,
    /// Ollama — local models via OpenAI-compatible API at localhost:11434
    Ollama,
}

impl std::fmt::Display for ApiProvider {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ApiProvider::Anthropic => write!(f, "anthropic"),
            ApiProvider::OpenAi => write!(f, "openai"),
            ApiProvider::Google => write!(f, "google"),
            ApiProvider::Mistral => write!(f, "mistral"),
            ApiProvider::OpenRouter => write!(f, "openrouter"),
            ApiProvider::Ollama => write!(f, "ollama"),
        }
    }
}

/// Resolve provider from model name and available env keys.
/// Intent-preserving: model name tells you the provider.
pub fn resolve_provider(model: &str) -> ApiProvider {
    // Local models — explicit local/ or ollama/ prefix routes to Ollama
    if model.starts_with("local/") || model.starts_with("ollama/") {
        return ApiProvider::Ollama;
    }
    // Auto-detect local: model names with ':' (e.g. codestral:22b, qwen2.5-coder:32b)
    if model.contains(':') {
        return ApiProvider::Ollama;
    }

    // Explicit Anthropic models
    if model.starts_with("claude") {
        return ApiProvider::Anthropic;
    }
    // OpenAI models — route via OpenRouter if key available, else OpenAI
    if model.starts_with("gpt") || model.starts_with("o1") || model.starts_with("o3") {
        if crate::commands::secret::resolve_secret("OPENROUTER_API_KEY").is_ok() {
            return ApiProvider::OpenRouter;
        }
        return ApiProvider::OpenAi;
    }
    // Google/Gemini
    if model.starts_with("gemini") || model.starts_with("google/") {
        if crate::commands::secret::resolve_secret("GEMINI_API_KEY").is_ok()
            || crate::commands::secret::resolve_secret("GOOGLE_API_KEY").is_ok()
        {
            return ApiProvider::Google;
        }
        return ApiProvider::OpenRouter;
    }
    // Mistral API
    if model.starts_with("mistral")
        || model.starts_with("codestral")
        || model.starts_with("ministral")
        || model.starts_with("devstral")
        || model.starts_with("magistral")
    {
        if crate::commands::secret::resolve_secret("MISTRAL_API_KEY").is_ok() {
            return ApiProvider::Mistral;
        }
        return ApiProvider::OpenRouter;
    }
    // Explicit openrouter/ prefix or known non-Anthropic providers
    if model.starts_with("openrouter/")
        || model.starts_with("meta-llama")
        || model.starts_with("deepseek/")
        || model.contains('/')  // org/model format → OpenRouter
    {
        return ApiProvider::OpenRouter;
    }
    // Default: try Anthropic
    ApiProvider::Anthropic
}

/// Map short model aliases to full model IDs.
/// Unknown names pass through unchanged (allows raw model IDs).
pub fn resolve_model_alias(name: &str) -> &str {
    match name {
        // Claude
        "opus"   => "claude-opus-4-6",
        "sonnet" => "claude-sonnet-4-5-20250929",
        "haiku"  => "claude-haiku-4-5-20251001",
        // Gemini — API names differ from marketing names
        "gemini-3.1-pro" => "gemini-3.1-pro-preview",
        "gemini-3-flash" => "gemini-3-flash-preview",
        "gemini"         => "gemini-2.5-pro",
        "flash"          => "gemini-3-flash-preview",
        // Local models — :model qwen / :model llama / :model deepseek
        "qwen"   => "local/qwen2.5-coder:32b",
        "llama"  => "local/llama3.3:70b",
        "deepseek" => "local/deepseek-r1:70b",
        "codestral" => "local/codestral:22b",
        "gemma"  => "local/gemma3:27b",
        "local"  => "local/codestral:22b",  // fast local coder
        other    => other,
    }
}

/// Return the maximum output tokens supported by a given model.
///
/// Prevents silent API 400 errors that cause agents to spin dead for minutes.
/// Used by `into_loop_config` and `config_from_agentfile` to clamp before API calls.
pub fn model_max_output_tokens(model: &str) -> u32 {
    if model.contains("opus") { 32_000 }
    else if model.contains("sonnet") { 64_000 }
    else if model.contains("haiku") { 8_192 }
    else if model.starts_with("gemini") || model.starts_with("google/") { 65_536 }
    else if model.contains("mistral")
        || model.starts_with("codestral")
        || model.starts_with("ministral")
        || model.starts_with("devstral")
        || model.starts_with("magistral") { 32_000 }
    else { 8_192 } // conservative default
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_resolve_model_alias_known() {
        assert_eq!(resolve_model_alias("opus"), "claude-opus-4-6");
        assert_eq!(resolve_model_alias("sonnet"), "claude-sonnet-4-5-20250929");
        assert_eq!(resolve_model_alias("haiku"), "claude-haiku-4-5-20251001");
    }

    #[test]
    fn test_resolve_model_alias_passthrough() {
        assert_eq!(resolve_model_alias("claude-opus-4-6"), "claude-opus-4-6");
        assert_eq!(resolve_model_alias("custom-model-v1"), "custom-model-v1");
    }

    #[test]
    fn test_model_max_output_tokens() {
        assert_eq!(model_max_output_tokens("claude-opus-4-6"), 32_000);
        assert_eq!(model_max_output_tokens("claude-sonnet-4-6"), 64_000);
        assert_eq!(model_max_output_tokens("claude-haiku-4-5-20251001"), 8_192);
        assert_eq!(model_max_output_tokens("gemini-2.5-pro"), 65_536);
        assert_eq!(model_max_output_tokens("gemini-2.0-flash"), 65_536);
        assert_eq!(model_max_output_tokens("mistral-large-latest"), 32_000);
        assert_eq!(model_max_output_tokens("codestral-latest"), 32_000);
        assert_eq!(model_max_output_tokens("devstral-small"), 32_000);
        assert_eq!(model_max_output_tokens("some-unknown-model"), 8_192);
    }
}
