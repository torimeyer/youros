pub mod approval;
pub mod bootloader;
pub mod context;
pub mod dispatch;
pub mod markdown;
pub mod overlays;
pub mod render;
pub mod selection;
pub mod session;
pub mod sphere_nav;
pub mod spinner;
pub mod status;
pub mod tabs;
pub mod tool_display;

/// Derive a short display name from a full model ID.
/// Single source of truth — replaces 5 inline copies (→767).
pub fn model_short_name(model: &str) -> &str {
    if model.contains("opus") { "opus" }
    else if model.contains("sonnet") { "sonnet" }
    else if model.contains("haiku") { "haiku" }
    else if model.contains("gemini") { "gemini" }
    else if model.contains("llama") { "llama" }
    else if model.contains("gpt") { "gpt" }
    else if model.contains('/') { model.rsplit('/').next().unwrap_or(model) }
    else { model.split('-').next().unwrap_or(model) }
}

/// →903: Provider-aware badge color for model attribution labels.
/// - Claude family (opus/sonnet/haiku/claude): Cyan
/// - Gemini family: Yellow
/// - Mistral/Codestral family: Blue
/// - Default: Green (legacy behavior)
pub fn model_badge_color(model: &str) -> crate::fcp_screen::protocol::Color {
    use crate::fcp_screen::protocol::Color;
    if model.contains("claude") || model.contains("opus") || model.contains("sonnet") || model.contains("haiku") {
        Color::Cyan
    } else if model.contains("gemini") {
        Color::Yellow
    } else if model.contains("mistral") || model.contains("codestral") {
        Color::Blue
    } else {
        Color::Green
    }
}
