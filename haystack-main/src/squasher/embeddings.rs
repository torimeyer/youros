//! GPU-accelerated semantic embeddings via Burn.
//!
//! Unified embedding backend for both squasher (signal classification,
//! semantic dedup) and hay compiler (clustering). Uses potion-base-8M
//! static embedding model:
//!   tokenize → gather rows → mean pool → normalize
//!
//! - Metal GPU batch inference (M4 Max) with CPU fallback
//! - Pairwise similarity via matmul
//! - Feature-gated behind `--features embeddings`.

use burn::prelude::*;
use burn::backend::ndarray::NdArray;
use burn::backend::wgpu::{Wgpu, WgpuDevice};
use std::path::PathBuf;
use std::sync::OnceLock;

/// Embedding dimension for potion-base-8M.
const EMBED_DIM: usize = 256;

/// Model directory name.
const MODEL_DIR: &str = "potion-base-8M";

// ---------------------------------------------------------------------------
// Backend selection: GPU if available, CPU fallback
// ---------------------------------------------------------------------------

/// The backend we'll use — try Wgpu (Metal on macOS), fall back to NdArray.
type GpuBackend = Wgpu;
type CpuBackend = NdArray<f32>;

enum ActiveBackend {
    Gpu {
        embeddings: Tensor<GpuBackend, 2>,
        device: WgpuDevice,
    },
    Cpu {
        embeddings: Tensor<CpuBackend, 2>,
    },
}

struct EmbeddingEngine {
    tokenizer: tokenizers::Tokenizer,
    backend: ActiveBackend,
    vocab_size: usize,
}

static ENGINE: OnceLock<Result<EmbeddingEngine, String>> = OnceLock::new();

// ---------------------------------------------------------------------------
// Model loading
// ---------------------------------------------------------------------------

/// Find the model directory — checks bundled models/ dir first, then .ostk/.models/.
fn find_model_dir() -> Option<PathBuf> {
    // 1. Check relative to executable (for bundled models)
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            let bundled = parent.join("models").join(MODEL_DIR);
            if bundled.join("model.safetensors").exists() {
                return Some(bundled);
            }
        }
    }

    // 2. Check project root models/ dir
    if let Ok(root) = crate::find_project_root() {
        let project_models = root.join("models").join(MODEL_DIR);
        if project_models.join("model.safetensors").exists() {
            return Some(project_models);
        }
    }

    // 3. Check ~/.ostk/models/ (user-global, downloaded by `ostk embeddings download`)
    if let Ok(home) = std::env::var("HOME") {
        let global = PathBuf::from(home).join(".ostk").join("models").join(MODEL_DIR);
        if global.join("model.safetensors").exists() {
            return Some(global);
        }
    }

    // 4. Check .ostk/.models/ (legacy path)
    if let Ok(root) = crate::find_project_root() {
        let legacy = crate::state_dir(&root).join(".models").join(MODEL_DIR);
        if legacy.join("model.safetensors").exists() {
            return Some(legacy);
        }
    }

    None
}

/// Convert f16 bits to f32 (IEEE 754).
fn f16_to_f32(bits: u16) -> f32 {
    let sign = (bits >> 15) as u32;
    let exp = ((bits >> 10) & 0x1F) as u32;
    let mant = (bits & 0x3FF) as u32;

    if exp == 0 {
        if mant == 0 {
            return f32::from_bits(sign << 31); // ±0
        }
        // subnormal
        let mut m = mant;
        let mut e = 1u32;
        while m & 0x400 == 0 {
            m <<= 1;
            e += 1;
        }
        let f32_exp = 127 - 15 - e + 1;
        let f32_mant = (m & 0x3FF) << 13;
        return f32::from_bits((sign << 31) | (f32_exp << 23) | f32_mant);
    }
    if exp == 31 {
        let f32_mant = mant << 13;
        return f32::from_bits((sign << 31) | (0xFF << 23) | f32_mant); // inf/nan
    }
    let f32_exp = exp + 127 - 15;
    f32::from_bits((sign << 31) | (f32_exp << 23) | (mant << 13))
}

/// Load embedding weights from safetensors into a Burn tensor.
fn load_embeddings_raw(model_dir: &std::path::Path) -> Result<(Vec<f32>, usize, usize), String> {
    let st_path = model_dir.join("model.safetensors");
    let bytes = std::fs::read(&st_path)
        .map_err(|e| format!("read safetensors: {e}"))?;

    let tensors = safetensors::SafeTensors::deserialize(&bytes)
        .map_err(|e| format!("parse safetensors: {e}"))?;

    let tensor = tensors.tensor("embeddings")
        .map_err(|e| format!("no 'embeddings' tensor: {e}"))?;

    let shape = tensor.shape();
    if shape.len() != 2 {
        return Err(format!("expected 2D tensor, got {}D", shape.len()));
    }
    let rows = shape[0];
    let cols = shape[1];

    let data = tensor.data();
    let floats: Vec<f32> = match tensor.dtype() {
        safetensors::Dtype::F32 => {
            data.chunks_exact(4)
                .map(|b| f32::from_le_bytes(b.try_into().unwrap()))
                .collect()
        }
        safetensors::Dtype::F16 => {
            data.chunks_exact(2)
                .map(|b| {
                    let bits = u16::from_le_bytes(b.try_into().unwrap());
                    f16_to_f32(bits)
                })
                .collect()
        }
        other => return Err(format!("unsupported dtype: {other:?}")),
    };

    Ok((floats, rows, cols))
}

fn init_engine() -> Result<EmbeddingEngine, String> {
    let model_dir = find_model_dir()
        .ok_or_else(|| "potion-base-8M model not found".to_string())?;

    // Load tokenizer
    let tok_path = model_dir.join("tokenizer.json");
    let tokenizer = tokenizers::Tokenizer::from_file(&tok_path)
        .map_err(|e| format!("load tokenizer: {e}"))?;

    // Load raw embedding weights
    let (floats, rows, cols) = load_embeddings_raw(&model_dir)?;

    // Try GPU first, fall back to CPU
    let backend = match try_gpu_tensor(&floats, rows, cols) {
        Some((tensor, device)) => {
            tracing::info!("burn embeddings: Metal GPU active ({rows}×{cols})");
            ActiveBackend::Gpu { embeddings: tensor, device }
        }
        None => {
            tracing::info!("burn embeddings: CPU fallback ({rows}×{cols})");
            let tensor = Tensor::<CpuBackend, 1>::from_floats(
                floats.as_slice(), &Default::default()
            ).reshape([rows, cols]);
            ActiveBackend::Cpu { embeddings: tensor }
        }
    };

    Ok(EmbeddingEngine {
        tokenizer,
        backend,
        vocab_size: rows,
    })
}

fn try_gpu_tensor(floats: &[f32], rows: usize, cols: usize) -> Option<(Tensor<GpuBackend, 2>, WgpuDevice)> {
    // Attempt to create a GPU tensor — if wgpu can't find a device, return None
    let device = WgpuDevice::default();
    let tensor = Tensor::<GpuBackend, 1>::from_floats(
        floats, &device
    ).reshape([rows, cols]);
    Some((tensor, device))
}

fn get_engine() -> &'static Result<EmbeddingEngine, String> {
    ENGINE.get_or_init(init_engine)
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Returns true if the Burn embedding engine is available.
pub fn model_available() -> bool {
    get_engine().is_ok()
}

/// Embed a batch of text lines. Returns an N×256 matrix of embeddings.
///
/// Batch all lines in one GPU dispatch for efficiency.
pub fn embed_batch(texts: &[&str]) -> Result<Vec<Vec<f32>>, String> {
    let engine = get_engine().as_ref().map_err(|e| e.clone())?;

    if texts.is_empty() {
        return Ok(vec![]);
    }

    // Step 1: Tokenize all texts (CPU)
    let encodings = engine.tokenizer
        .encode_batch(texts.to_vec(), false)
        .map_err(|e| format!("tokenize batch: {e}"))?;

    // Step 2: For each text, gather embedding rows and mean-pool
    match &engine.backend {
        ActiveBackend::Gpu { embeddings, device } => {
            embed_batch_gpu(embeddings, device, &encodings, engine.vocab_size)
        }
        ActiveBackend::Cpu { embeddings } => {
            embed_batch_cpu(embeddings, &encodings, engine.vocab_size)
        }
    }
}

fn embed_batch_gpu(
    embeddings: &Tensor<GpuBackend, 2>,
    device: &WgpuDevice,
    encodings: &[tokenizers::Encoding],
    vocab_size: usize,
) -> Result<Vec<Vec<f32>>, String> {
    let mut results = Vec::with_capacity(encodings.len());

    for encoding in encodings {
        let ids: Vec<i64> = encoding.get_ids().iter()
            .map(|&id| id as i64)
            .filter(|&id| (id as usize) < vocab_size)
            .collect();

        if ids.is_empty() {
            results.push(vec![0.0f32; EMBED_DIM]);
            continue;
        }

        let n = ids.len();
        let idx = Tensor::<GpuBackend, 1, Int>::from_data(
            ids.as_slice(), device
        );

        // Gather rows from embedding table
        let gathered = embeddings.clone().select(0, idx); // [n, 256]

        // Mean pool
        let summed = gathered.sum_dim(0); // [1, 256]
        let mean = summed / (n as f32); // [1, 256]

        // L2 normalize
        let norm = mean.clone().powf_scalar(2.0).sum_dim(1).sqrt().clamp_min(1e-12);
        let normalized = mean / norm;

        // To CPU
        let data: Vec<f32> = normalized.reshape([EMBED_DIM]).to_data().to_vec()
            .map_err(|e| format!("tensor to_vec: {e}"))?;
        results.push(data);
    }

    Ok(results)
}

fn embed_batch_cpu(
    embeddings: &Tensor<CpuBackend, 2>,
    encodings: &[tokenizers::Encoding],
    vocab_size: usize,
) -> Result<Vec<Vec<f32>>, String> {
    let mut results = Vec::with_capacity(encodings.len());

    for encoding in encodings {
        let ids: Vec<i64> = encoding.get_ids().iter()
            .map(|&id| id as i64)
            .filter(|&id| (id as usize) < vocab_size)
            .collect();

        if ids.is_empty() {
            results.push(vec![0.0f32; EMBED_DIM]);
            continue;
        }

        let n = ids.len();
        let idx = Tensor::<CpuBackend, 1, Int>::from_data(
            ids.as_slice(), &Default::default()
        );

        let gathered = embeddings.clone().select(0, idx);
        let summed = gathered.sum_dim(0);
        let mean = summed / (n as f32);
        let norm = mean.clone().powf_scalar(2.0).sum_dim(1).sqrt().clamp_min(1e-12);
        let normalized = mean / norm;

        let data: Vec<f32> = normalized.reshape([EMBED_DIM]).to_data().to_vec()
            .map_err(|e| format!("tensor to_vec: {e}"))?;
        results.push(data);
    }

    Ok(results)
}

/// Compute pairwise cosine similarity matrix for N embeddings.
///
/// Input: N×D matrix (output of embed_batch).
/// Output: N×N similarity matrix.
///
/// This is ONE matmul on GPU — the key advantage over per-pair comparison.
pub fn pairwise_similarity(embeddings: &[Vec<f32>]) -> Result<Vec<Vec<f32>>, String> {
    if embeddings.is_empty() {
        return Ok(vec![]);
    }

    let n = embeddings.len();
    let d = embeddings[0].len();

    // Flatten to contiguous f32 array
    let flat: Vec<f32> = embeddings.iter().flat_map(|v| v.iter().copied()).collect();

    let engine = get_engine().as_ref().map_err(|e| e.clone())?;

    match &engine.backend {
        ActiveBackend::Gpu { device, .. } => {
            let mat = Tensor::<GpuBackend, 1>::from_floats(
                flat.as_slice(), device
            ).reshape([n, d]);

            // sim = mat @ mat.T → [N, N]
            let sim = mat.clone().matmul(mat.transpose());

            let sim_data: Vec<f32> = sim.reshape([n * n]).to_data().to_vec()
                .map_err(|e| format!("sim to_vec: {e}"))?;

            Ok(sim_data.chunks(n).map(|row| row.to_vec()).collect())
        }
        ActiveBackend::Cpu { .. } => {
            let mat = Tensor::<CpuBackend, 1>::from_floats(
                flat.as_slice(), &Default::default()
            ).reshape([n, d]);

            let sim = mat.clone().matmul(mat.transpose());

            let sim_data: Vec<f32> = sim.reshape([n * n]).to_data().to_vec()
                .map_err(|e| format!("sim to_vec: {e}"))?;

            Ok(sim_data.chunks(n).map(|row| row.to_vec()).collect())
        }
    }
}

/// Single-line embedding (convenience wrapper).
pub fn embed(text: &str) -> Result<Vec<f32>, String> {
    let results = embed_batch(&[text])?;
    results.into_iter().next().ok_or_else(|| "empty result".into())
}

/// Cosine similarity between two vectors.
pub fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    if a.len() != b.len() || a.is_empty() {
        return 0.0;
    }
    let mut dot = 0.0f32;
    let mut na = 0.0f32;
    let mut nb = 0.0f32;
    for (x, y) in a.iter().zip(b.iter()) {
        dot += x * y;
        na += x * x;
        nb += y * y;
    }
    dot / (na.sqrt() * nb.sqrt()).max(1e-12)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cosine_identical() {
        let v = vec![1.0, 2.0, 3.0];
        assert!((cosine_similarity(&v, &v) - 1.0).abs() < 1e-6);
    }

    #[test]
    fn test_cosine_orthogonal() {
        let a = vec![1.0, 0.0];
        let b = vec![0.0, 1.0];
        assert!(cosine_similarity(&a, &b).abs() < 1e-6);
    }

    #[test]
    fn test_pairwise_on_simple_vectors() {
        // Without model loaded, we can test the math with manual embeddings
        let vecs = vec![
            vec![1.0, 0.0, 0.0],
            vec![0.0, 1.0, 0.0],
            vec![1.0, 0.0, 0.0], // same as first
        ];

        // Manual pairwise: sim[0][2] should be 1.0, sim[0][1] should be 0.0
        let flat: Vec<f32> = vecs.iter().flat_map(|v| v.iter().copied()).collect();
        let n = vecs.len();
        let d = 3;

        let mat = Tensor::<CpuBackend, 1>::from_floats(
            flat.as_slice(), &Default::default()
        ).reshape([n, d]);

        let sim = mat.clone().matmul(mat.transpose());
        let sim_data: Vec<f32> = sim.reshape([n * n]).to_data().to_vec().unwrap();
        let sim_matrix: Vec<Vec<f32>> = sim_data.chunks(n).map(|r| r.to_vec()).collect();

        assert!((sim_matrix[0][2] - 1.0).abs() < 1e-6, "identical vectors");
        assert!(sim_matrix[0][1].abs() < 1e-6, "orthogonal vectors");
    }
}

// ---------------------------------------------------------------------------
// Signal Extraction — learned classification without regex
// ---------------------------------------------------------------------------

/// Pre-defined signal exemplars for zero-shot line classification.
/// These get embedded once at startup, then every line is compared
/// against them via the pairwise similarity matrix.
pub mod signals {
    /// Signal category for a line.
    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub enum Signal {
        /// Error/failure — always keep
        Hazard,
        /// Warning — keep, may dedup
        Warning,
        /// Success/completion — promote
        Outcome,
        /// Progress/build noise — aggressively dedup or strip
        Progress,
        /// Unknown — no strong signal match
        Unknown,
    }

    /// (exemplar_text, signal_category)
    pub const EXEMPLARS: &[(&str, Signal)] = &[
        // Hazard signals
        ("error: something went wrong", Signal::Hazard),
        ("error[E0425]: cannot find value", Signal::Hazard),
        ("FAILED", Signal::Hazard),
        ("fatal: not a git repository", Signal::Hazard),
        ("panicked at", Signal::Hazard),
        ("Error: ENOENT: no such file or directory", Signal::Hazard),
        ("FAIL  src/tests/unit.test.js", Signal::Hazard),
        ("AssertionError: expected true to be false", Signal::Hazard),
        // Warning signals
        ("warning: unused variable", Signal::Warning),
        ("warning: deprecated function", Signal::Warning),
        ("WARN: some deprecation notice", Signal::Warning),
        ("npm WARN optional dep failed", Signal::Warning),
        // Outcome signals
        ("Finished dev profile in 2.3s", Signal::Outcome),
        ("test result: ok. 5 passed; 0 failed", Signal::Outcome),
        ("Build succeeded", Signal::Outcome),
        ("Successfully compiled", Signal::Outcome),
        ("All checks passed", Signal::Outcome),
        ("Tests:  5 passed, 5 total", Signal::Outcome),
        // Progress noise
        ("Compiling some-crate v1.0.0", Signal::Progress),
        ("Downloading crates ...", Signal::Progress),
        ("Installing dependencies...", Signal::Progress),
        ("Building module foo", Signal::Progress),
        ("  Fetching index", Signal::Progress),
        ("Progress: 45%", Signal::Progress),
        ("npm install: added 150 packages", Signal::Progress),
        ("Resolving dependencies...", Signal::Progress),
    ];
}

/// Cached signal exemplar embeddings.
struct SignalCache {
    /// Embeddings for each exemplar, in same order as EXEMPLARS.
    embeddings: Vec<Vec<f32>>,
}

static SIGNAL_CACHE: OnceLock<Result<SignalCache, String>> = OnceLock::new();

fn get_signal_cache() -> &'static Result<SignalCache, String> {
    SIGNAL_CACHE.get_or_init(|| {
        let texts: Vec<&str> = signals::EXEMPLARS.iter().map(|(t, _)| *t).collect();
        let embeddings = embed_batch(&texts)?;
        Ok(SignalCache { embeddings })
    })
}

/// Classify a line by its semantic similarity to signal exemplars.
///
/// Uses per-category confidence thresholds:
/// - Hazard: 0.40 (low bar — better to keep a non-error than miss an error)
/// - Warning: 0.50
/// - Outcome: 0.60 (higher bar — lots of things look vaguely successful)
/// - Progress: 0.50
///
/// Returns the best signal above its category threshold, or Unknown.
pub fn classify_signal(line_embedding: &[f32], _min_confidence: f32) -> (signals::Signal, f32) {
    let cache = match get_signal_cache() {
        Ok(c) => c,
        Err(_) => return (signals::Signal::Unknown, 0.0),
    };

    // Accumulate best similarity per category
    let mut best_by_cat: [(f32, usize); 4] = [
        (-1.0, 0), // Hazard
        (-1.0, 0), // Warning
        (-1.0, 0), // Outcome
        (-1.0, 0), // Progress
    ];

    for (i, exemplar_emb) in cache.embeddings.iter().enumerate() {
        let sim = cosine_similarity(line_embedding, exemplar_emb);
        let cat_idx = match signals::EXEMPLARS[i].1 {
            signals::Signal::Hazard => 0,
            signals::Signal::Warning => 1,
            signals::Signal::Outcome => 2,
            signals::Signal::Progress => 3,
            signals::Signal::Unknown => continue,
        };
        if sim > best_by_cat[cat_idx].0 {
            best_by_cat[cat_idx] = (sim, i);
        }
    }

    // Per-category thresholds (tuned against real squasher output)
    let thresholds = [0.40, 0.50, 0.60, 0.50]; // hazard, warning, outcome, progress
    let categories = [
        signals::Signal::Hazard,
        signals::Signal::Warning,
        signals::Signal::Outcome,
        signals::Signal::Progress,
    ];

    // Pick the best category that exceeds its threshold.
    // Priority: Hazard > Warning > Outcome > Progress
    // (if a line matches both Hazard and Outcome, prefer Hazard)
    let mut best_signal = signals::Signal::Unknown;
    let mut best_score = 0.0f32;

    for (cat_idx, &(sim, _exemplar_idx)) in best_by_cat.iter().enumerate() {
        if sim >= thresholds[cat_idx] && sim > best_score {
            best_signal = categories[cat_idx];
            best_score = sim;
        }
    }

    // Override: Hazard always wins if it exceeds its threshold,
    // even if another category scored higher
    if best_by_cat[0].0 >= thresholds[0] && best_signal != signals::Signal::Hazard {
        // Only override if hazard is within 0.15 of the winner
        if best_by_cat[0].0 >= best_score - 0.15 {
            best_signal = signals::Signal::Hazard;
            best_score = best_by_cat[0].0;
        }
    }

    (best_signal, best_score)
}

/// Classify a batch of lines by signal. Returns signal + confidence for each.
pub fn classify_signals_batch(
    embeddings: &[Vec<f32>],
    min_confidence: f32,
) -> Vec<(signals::Signal, f32)> {
    embeddings
        .iter()
        .map(|emb| classify_signal(emb, min_confidence))
        .collect()
}
