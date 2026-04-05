//! `ostk embeddings download` — fetch the embedding model for semantic search.
//!
//! Downloads potion-base-8M from HuggingFace Hub to ~/.ostk/models/.
//! Two files: model.safetensors (~32MB) and tokenizer.json (~700KB).
//!
//! The model enables:
//! - Semantic search (pitchfork, near, hay clustering)
//! - Intelligent hay compilation (diversity-aware needle generation)

use std::fs;
use std::path::PathBuf;
use std::process::Command;

const MODEL_NAME: &str = "potion-base-8M";
const HF_REPO: &str = "minishlab/potion-base-8M";
const FILES: &[(&str, &str)] = &[
    ("model.safetensors", "https://huggingface.co/minishlab/potion-base-8M/resolve/main/model.safetensors"),
    ("tokenizer.json", "https://huggingface.co/minishlab/potion-base-8M/resolve/main/tokenizer.json"),
];

/// Download the embedding model to ~/.ostk/models/potion-base-8M/.
pub fn run_download() -> Result<(), String> {
    let model_dir = default_model_dir()?;

    println!("embeddings: downloading {MODEL_NAME} from {HF_REPO}");
    println!("  target: {}", model_dir.display());
    println!();

    fs::create_dir_all(&model_dir)
        .map_err(|e| format!("failed to create model directory: {e}"))?;

    for (filename, url) in FILES {
        let target = model_dir.join(filename);
        if target.exists() {
            let size = target.metadata().map(|m| m.len()).unwrap_or(0);
            println!("  {filename}: already present ({} bytes), skipping", format_size(size));
            continue;
        }

        println!("  {filename}: downloading...");
        download_file(url, &target)?;
        let size = target.metadata().map(|m| m.len()).unwrap_or(0);
        println!("  {filename}: done ({}).", format_size(size));
    }

    println!();
    println!("embeddings: model ready. Restart ostk to activate.");
    println!("  verify: ostk boot (should show embeddings: active)");

    Ok(())
}

/// Show embedding model status.
pub fn run_status() -> Result<(), String> {
    let model_dir = default_model_dir()?;
    let safetensors = model_dir.join("model.safetensors");
    let tokenizer = model_dir.join("tokenizer.json");

    println!("embeddings: {MODEL_NAME}");
    println!("  path: {}", model_dir.display());

    if safetensors.exists() && tokenizer.exists() {
        let size = safetensors.metadata().map(|m| m.len()).unwrap_or(0);
        println!("  model.safetensors: {} ✓", format_size(size));
        println!("  tokenizer.json: ✓");

        #[cfg(feature = "embeddings")]
        {
            if crate::squasher::embeddings::model_available() {
                println!("  status: active (GPU/CPU backend loaded)");
            } else {
                println!("  status: present but failed to load");
            }
        }
        #[cfg(not(feature = "embeddings"))]
        println!("  status: present (compile with --features embeddings to activate)");
    } else {
        println!("  status: not downloaded");
        println!("  run: ostk embeddings download");
    }

    Ok(())
}

/// Default model directory: ~/.ostk/models/potion-base-8M/
fn default_model_dir() -> Result<PathBuf, String> {
    let home = std::env::var("HOME")
        .map_err(|_| "HOME not set".to_string())?;
    Ok(PathBuf::from(home).join(".ostk").join("models").join(MODEL_NAME))
}

/// Download a file using curl (available on macOS and Linux).
fn download_file(url: &str, target: &std::path::Path) -> Result<(), String> {
    let tmp = target.with_extension("tmp");

    let status = Command::new("curl")
        .args([
            "-fSL",           // fail on HTTP errors, show errors, follow redirects
            "--progress-bar", // progress indicator
            "-o",
        ])
        .arg(&tmp)
        .arg(url)
        .status()
        .map_err(|e| format!("curl failed: {e}"))?;

    if !status.success() {
        let _ = fs::remove_file(&tmp);
        return Err(format!("download failed: {url}"));
    }

    fs::rename(&tmp, target)
        .map_err(|e| format!("failed to move downloaded file: {e}"))?;

    Ok(())
}

fn format_size(bytes: u64) -> String {
    if bytes >= 1_000_000 {
        format!("{:.1} MB", bytes as f64 / 1_000_000.0)
    } else if bytes >= 1_000 {
        format!("{:.1} KB", bytes as f64 / 1_000.0)
    } else {
        format!("{bytes} bytes")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_model_dir() {
        let dir = default_model_dir().unwrap();
        assert!(dir.to_string_lossy().contains("potion-base-8M"));
        assert!(dir.to_string_lossy().contains(".ostk/models"));
    }

    #[test]
    fn test_format_size() {
        assert_eq!(format_size(500), "500 bytes");
        assert_eq!(format_size(1500), "1.5 KB");
        assert_eq!(format_size(32_000_000), "32.0 MB");
    }
}
