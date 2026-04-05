//! ostk.toml configuration reader.

use serde::Deserialize;
use std::path::Path;

#[derive(Debug, Deserialize, Default)]
pub struct Config {
    pub kernel: Option<KernelConfig>,
    pub squasher: Option<SquasherConfig>,
    pub fcp: Option<FcpConfig>,
    pub governance: Option<GovernanceConfig>,
    pub should: Option<ShouldConfig>,
}

#[derive(Debug, Deserialize, Default)]
pub struct KernelConfig {
    pub identity: Option<String>,
    pub version: Option<String>,
    pub visibility: Option<String>, // "invisible" or "native"
}

#[derive(Debug, Deserialize, Default)]
pub struct SquasherConfig {
    pub enabled: Option<bool>,
    pub quota: Option<String>,
    pub free_tier_tokens: Option<u64>,
    pub dedup: Option<DedupConfig>,
    pub embeddings: Option<EmbeddingsConfig>,
}

#[derive(Debug, Deserialize, Default)]
pub struct DedupConfig {
    pub similarity_threshold: Option<f64>,
    pub tokenizers: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
pub struct EmbeddingsConfig {
    pub enabled: Option<bool>,
    pub model: Option<String>,
    pub cosine_threshold: Option<f64>,
}

#[derive(Debug, Deserialize, Default)]
pub struct FcpConfig {
    pub drivers: Option<Vec<String>>,
}

#[derive(Debug, Deserialize, Default)]
pub struct GovernanceConfig {
    pub attestation: Option<String>,
    pub required_signatures: Option<u32>,
}

#[derive(Debug, Deserialize, Default)]
pub struct ShouldConfig {
    pub repo: Option<String>,
}

/// Load config from ostk.toml, searching up from the given path.
pub fn load_config(root: &Path) -> Config {
    let config_path = root.join("ostk.toml");
    if !config_path.exists() {
        return Config::default();
    }
    let content = match std::fs::read_to_string(&config_path) {
        Ok(c) => c,
        Err(_) => return Config::default(),
    };
    toml::from_str(&content).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn test_empty_file_returns_default() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("ostk.toml");
        std::fs::File::create(&path).unwrap();

        let config = load_config(dir.path());
        assert!(config.kernel.is_none());
        assert!(config.squasher.is_none());
        assert!(config.fcp.is_none());
        assert!(config.governance.is_none());
        assert!(config.should.is_none());
    }

    #[test]
    fn test_missing_file_returns_default() {
        let dir = tempfile::tempdir().unwrap();
        let config = load_config(dir.path());
        assert!(config.kernel.is_none());
    }

    #[test]
    fn test_partial_config() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("ostk.toml");
        let mut f = std::fs::File::create(&path).unwrap();
        writeln!(
            f,
            r#"
[kernel]
identity = "@test"

[should]
repo = "my-org/my-repo"
"#
        )
        .unwrap();

        let config = load_config(dir.path());
        let kernel = config.kernel.unwrap();
        assert_eq!(kernel.identity.as_deref(), Some("@test"));
        assert!(kernel.version.is_none());

        let should = config.should.unwrap();
        assert_eq!(should.repo.as_deref(), Some("my-org/my-repo"));

        assert!(config.squasher.is_none());
    }

    #[test]
    fn test_full_config() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("ostk.toml");
        let mut f = std::fs::File::create(&path).unwrap();
        writeln!(
            f,
            r#"
[kernel]
identity = "@ostk.ai.prime"
version = "1.5.0"
visibility = "invisible"

[squasher]
enabled = true
quota = "audit-backed"
free_tier_tokens = 100_000_000

[squasher.dedup]
similarity_threshold = 0.3
tokenizers = "default"

[squasher.embeddings]
cosine_threshold = 0.85
enabled = true

[fcp]
drivers = []

[governance]
attestation = "optional"
required_signatures = 0

[should]
repo = "os-tack/ostk.ai"
"#
        )
        .unwrap();

        let config = load_config(dir.path());

        let kernel = config.kernel.unwrap();
        assert_eq!(kernel.identity.as_deref(), Some("@ostk.ai.prime"));
        assert_eq!(kernel.version.as_deref(), Some("1.5.0"));
        assert_eq!(kernel.visibility.as_deref(), Some("invisible"));

        let squasher = config.squasher.unwrap();
        assert_eq!(squasher.enabled, Some(true));
        assert_eq!(squasher.quota.as_deref(), Some("audit-backed"));
        assert_eq!(squasher.free_tier_tokens, Some(100_000_000));

        let dedup = squasher.dedup.unwrap();
        assert!((dedup.similarity_threshold.unwrap() - 0.3).abs() < f64::EPSILON);
        assert_eq!(dedup.tokenizers.as_deref(), Some("default"));

        let embeddings = squasher.embeddings.unwrap();
        assert_eq!(embeddings.enabled, Some(true));
        assert!((embeddings.cosine_threshold.unwrap() - 0.85).abs() < f64::EPSILON);

        let fcp = config.fcp.unwrap();
        assert_eq!(fcp.drivers, Some(vec![]));

        let gov = config.governance.unwrap();
        assert_eq!(gov.attestation.as_deref(), Some("optional"));
        assert_eq!(gov.required_signatures, Some(0));

        let should = config.should.unwrap();
        assert_eq!(should.repo.as_deref(), Some("os-tack/ostk.ai"));
    }

    #[test]
    fn test_squasher_similarity_threshold_parsed() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("ostk.toml");
        let mut f = std::fs::File::create(&path).unwrap();
        writeln!(
            f,
            r#"
[squasher.dedup]
similarity_threshold = 0.5
"#
        )
        .unwrap();

        let config = load_config(dir.path());
        let squasher = config.squasher.unwrap();
        let dedup = squasher.dedup.unwrap();
        assert!((dedup.similarity_threshold.unwrap() - 0.5).abs() < f64::EPSILON);
    }

    #[test]
    fn test_invalid_toml_returns_default() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("ostk.toml");
        let mut f = std::fs::File::create(&path).unwrap();
        writeln!(f, "this is not valid toml {{{{").unwrap();

        let config = load_config(dir.path());
        assert!(config.kernel.is_none());
    }
}
