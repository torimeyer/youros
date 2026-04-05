use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Instant;

use crate::{append_audit, find_project_root, now_iso};
use serde_json::json;

/// Resolve the needle-bench project root.
/// Priority: NEEDLE_BENCH_PATH env → ~/projects/needle-bench → error.
fn resolve_needle_bench_root() -> Result<PathBuf, String> {
    if let Ok(p) = std::env::var("NEEDLE_BENCH_PATH") {
        let path = PathBuf::from(&p);
        if path.is_dir() {
            return Ok(path);
        }
        return Err(format!("NEEDLE_BENCH_PATH={p} does not exist"));
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let default = PathBuf::from(&home).join("projects/needle-bench");
    if default.is_dir() {
        return Ok(default);
    }
    Err("needle-bench not found. Set NEEDLE_BENCH_PATH or clone to ~/projects/needle-bench".to_string())
}

/// A discovered bench scenario under bench/needle-tests/.
struct Scenario {
    name: String,
    dir: PathBuf,
    description: String,
    has_setup: bool,
    has_verify: bool,
}

impl Scenario {
    /// A scenario is runnable if it has both setup.sh and verify.sh.
    fn runnable(&self) -> bool {
        self.has_setup && self.has_verify
    }

    fn status_label(&self) -> &'static str {
        if self.runnable() {
            "runnable"
        } else {
            "designed"
        }
    }
}

/// Discover all scenarios under bench/needle-tests/.
fn discover_scenarios(root: &Path) -> Vec<Scenario> {
    let bench_dir = root.join("bench/needle-tests");
    let mut scenarios = Vec::new();

    if !bench_dir.is_dir() {
        return scenarios;
    }

    let mut entries: Vec<_> = fs::read_dir(&bench_dir)
        .unwrap_or_else(|_| panic!("cannot read {}", bench_dir.display()))
        .filter_map(|e| e.ok())
        .filter(|e| e.path().is_dir())
        .collect();
    entries.sort_by_key(|e| e.file_name());

    for entry in entries {
        let dir = entry.path();
        let name = entry.file_name().to_string_lossy().into_owned();

        let scenario_md = dir.join("scenario.md");
        let description = if scenario_md.exists() {
            fs::read_to_string(&scenario_md)
                .ok()
                .and_then(|content| {
                    content
                        .lines()
                        .find(|l| !l.trim().is_empty() && !l.starts_with('#'))
                        .map(|l| l.trim().to_string())
                })
                .unwrap_or_default()
        } else {
            String::new()
        };

        let has_setup = dir.join("setup.sh").exists();
        let has_verify = dir.join("verify.sh").exists();

        scenarios.push(Scenario {
            name,
            dir,
            description,
            has_setup,
            has_verify,
        });
    }

    scenarios
}

/// Run a single scenario: setup.sh then verify.sh.
/// Returns (passed: bool, duration_ms: u64).
fn run_scenario(scenario: &Scenario) -> (bool, u64) {
    let start = Instant::now();

    // Run setup.sh
    let setup_path = scenario.dir.join("setup.sh");
    let setup_result = Command::new("bash")
        .arg(&setup_path)
        .current_dir(&scenario.dir)
        .output();

    match setup_result {
        Ok(output) if !output.status.success() => {
            let stderr = String::from_utf8_lossy(&output.stderr);
            println!("  setup.sh failed (exit {}):", output.status.code().unwrap_or(-1));
            for line in stderr.lines().take(10) {
                println!("    {line}");
            }
            return (false, start.elapsed().as_millis() as u64);
        }
        Err(e) => {
            println!("  setup.sh spawn error: {e}");
            return (false, start.elapsed().as_millis() as u64);
        }
        _ => {}
    }

    // Run verify.sh
    let verify_path = scenario.dir.join("verify.sh");
    let verify_result = Command::new("bash")
        .arg(&verify_path)
        .current_dir(&scenario.dir)
        .output();

    let duration_ms = start.elapsed().as_millis() as u64;

    match verify_result {
        Ok(output) => {
            let stdout = String::from_utf8_lossy(&output.stdout);
            for line in stdout.lines() {
                println!("  {line}");
            }
            if !output.status.success() {
                let stderr = String::from_utf8_lossy(&output.stderr);
                for line in stderr.lines().take(5) {
                    println!("  {line}");
                }
            }
            (output.status.success(), duration_ms)
        }
        Err(e) => {
            println!("  verify.sh spawn error: {e}");
            (false, duration_ms)
        }
    }
}

/// Run `cargo test --test needle_tests` and report results.
fn run_cargo_tests() -> Result<bool, String> {
    println!("running: cargo test --test needle_tests");
    println!();

    let start = Instant::now();
    let output = Command::new("cargo")
        .args(["test", "--test", "needle_tests"])
        .output()
        .map_err(|e| format!("failed to run cargo test: {e}"))?;

    let duration_ms = start.elapsed().as_millis() as u64;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);

    // Print test output (cargo test puts results on stdout, compilation on stderr)
    for line in stderr.lines() {
        // Skip cargo compilation noise, keep test result lines
        if line.contains("Compiling") || line.contains("Finished") || line.contains("Running") {
            println!("  {line}");
        }
    }
    for line in stdout.lines() {
        println!("  {line}");
    }

    let passed = output.status.success();
    let verdict = if passed { "PASS" } else { "FAIL" };
    println!();
    println!("cargo needle_tests: {verdict} ({duration_ms}ms)");

    Ok(passed)
}

/// A Docker-based bench scenario under bench/scenarios/.
struct DockerScenario {
    name: String,
    dir: PathBuf,
    description: String,
}

// ---------------------------------------------------------------------------
// →827: Docker container lifecycle helpers
// ---------------------------------------------------------------------------

/// Build a Docker image for a bench scenario.
fn docker_build(scenario_dir: &Path, tag: &str) -> Result<(), String> {
    let output = Command::new("docker")
        .args(["build", "-t", tag, "."])
        .current_dir(scenario_dir)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .output()
        .map_err(|e| format!("docker build spawn error: {e}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("docker build failed: {}", stderr.chars().take(500).collect::<String>()));
    }
    Ok(())
}

/// Run a Docker container in detached mode.
#[allow(dead_code)]
fn docker_run(tag: &str, name: &str) -> Result<(), String> {
    docker_run_with_timeout(tag, name, 3600)
}

/// Run a Docker container in detached mode with a specific timeout.
fn docker_run_with_timeout(tag: &str, name: &str, timeout_secs: u64) -> Result<(), String> {
    docker_run_with_opts(tag, name, timeout_secs, &[])
}

/// Run a Docker container in detached mode with extra docker run flags.
fn docker_run_with_opts(tag: &str, name: &str, timeout_secs: u64, extra_args: &[&str]) -> Result<(), String> {
    // Remove any existing container with the same name (ignore errors)
    let _ = Command::new("docker")
        .args(["rm", "-f", name])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status();

    let timeout_str = timeout_secs.to_string();
    let mut args = vec!["run", "-d", "--name", name];
    args.extend_from_slice(extra_args);
    args.extend_from_slice(&[tag, "sleep", &timeout_str]);

    let output = Command::new("docker")
        .args(&args)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .output()
        .map_err(|e| format!("docker run spawn error: {e}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("docker run failed: {}", stderr.chars().take(500).collect::<String>()));
    }
    Ok(())
}

/// Execute a command inside a running container.
fn docker_exec(name: &str, cmd: &str) -> Result<String, String> {
    let output = Command::new("docker")
        .args(["exec", name, "bash", "-c", cmd])
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .output()
        .map_err(|e| format!("docker exec spawn error: {e}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    if !output.status.success() {
        return Err(format!("{stdout}{stderr}"));
    }
    Ok(if stderr.is_empty() { stdout } else { format!("{stdout}{stderr}") })
}

/// Stop and remove a container.
fn docker_stop(name: &str) -> Result<(), String> {
    let _ = Command::new("docker")
        .args(["stop", name])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status();
    let _ = Command::new("docker")
        .args(["rm", "-f", name])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status();
    Ok(())
}

// ---------------------------------------------------------------------------
// →827: Bench result and scoring
// ---------------------------------------------------------------------------

/// Result of a bench scenario run.
struct BenchResult {
    resolved: bool,
    turns: u32,
    input_tokens: u64,
    output_tokens: u64,
    cost_usd: f64,
    wall_clock_ms: u64,
    tool_uses: u32,
    stop_reason: String,
    summary: String,
}

/// Experiment arm for two-arm bench.
#[derive(Debug, Clone, Copy, PartialEq)]
enum BenchArm {
    /// Model's native CLI harness (Claude Code, Gemini CLI, Codex CLI)
    /// or ostk's agent loop as fallback. No kernel features.
    Native,
    /// Full ostk kernel: init, boot, heartbeat, dying, governance, audit.
    Kernel,
}

// ---------------------------------------------------------------------------
// Native CLI harness detection
// ---------------------------------------------------------------------------

struct NativeHarness {
    command: &'static str,
    display: &'static str,
    prefixes: &'static [&'static str],
    env_key: &'static str,
    install_cmd: &'static str,
}

const NATIVE_HARNESSES: &[NativeHarness] = &[
    NativeHarness {
        command: "claude",
        display: "Claude Code",
        prefixes: &["claude-"],
        env_key: "ANTHROPIC_API_KEY",
        install_cmd: "npm install -g @anthropic-ai/claude-code",
    },
    NativeHarness {
        command: "gemini",
        display: "Gemini CLI",
        prefixes: &["gemini-"],
        env_key: "GEMINI_API_KEY",
        install_cmd: "npm install -g @google/gemini-cli",
    },
    NativeHarness {
        command: "codex",
        display: "Codex CLI",
        prefixes: &["gpt-", "o1", "o3", "o4"],
        env_key: "OPENAI_API_KEY",
        install_cmd: "npm install -g @openai/codex",
    },
    // Grok CLI (@vibe-kit/grok-cli) is currently broken — uses deprecated xAI
    // "Live search" API (410 error). Grok models route through OpenCode fallback
    // instead, using the xai provider.
    //
    // NativeHarness {
    //     command: "grok",
    //     display: "Grok CLI",
    //     prefixes: &["grok-"],
    //     env_key: "GROK_API_KEY",
    //     install_cmd: "npm install -g @vibe-kit/grok-cli",
    // },
    NativeHarness {
        command: "vibe",
        display: "Mistral Vibe",
        prefixes: &["mistral-", "devstral-", "codestral-"],
        env_key: "MISTRAL_API_KEY",
        install_cmd: "curl -LsSf https://mistral.ai/vibe/install.sh | bash",
    },
    NativeHarness {
        command: "kimi",
        display: "Kimi CLI",
        prefixes: &["kimi-", "moonshot-"],
        env_key: "KIMI_API_KEY",
        install_cmd: "curl -LsSf https://code.kimi.com/install.sh | bash",
    },
];

/// OpenCode install command — universal fallback for models without a vendor CLI.
/// Installs to ~/.opencode/bin/opencode; ensure PATH includes that.
const OPENCODE_INSTALL_CMD: &str = "curl -fsSL https://opencode.ai/install | bash && export PATH=/root/.opencode/bin:$PATH";

fn detect_native_harness(model: &str) -> Option<&'static NativeHarness> {
    NATIVE_HARNESSES.iter().find(|h| h.prefixes.iter().any(|p| model.starts_with(p)))
}

/// Name of the pre-rolled native image containing all vendor CLIs.
const NATIVE_IMAGE: &str = "needle-bench-native";

/// Alpine → Debian package name translation.
/// The needle-bench-native base image is Debian (node:22-slim) with runtimes
/// pre-installed. Scenario Dockerfiles are written for Alpine. This map
/// translates package names so the rewritten `apt-get install` commands work.
/// Packages mapped to "" are already in the base image and can be dropped.
fn alpine_to_debian_pkg(pkg: &str) -> Option<&'static str> {
    match pkg {
        // Already in needle-bench-native base — skip
        "go" | "golang" => Some(""),                 // golang-go pre-installed
        "gcc" => Some(""),                           // build-essential pre-installed
        "musl-dev" => Some(""),                      // libc headers via build-essential
        "bash" | "coreutils" | "curl" | "wget" | "git" | "jq" | "ca-certificates" => Some(""),
        "python3" | "python-is-python3" => Some(""), // pre-installed
        "py3-pip" => Some(""),                       // python3-pip pre-installed
        "build-base" => Some(""),                    // build-essential pre-installed
        // Translation needed
        "openjdk17-jdk" => Some("openjdk-17-jdk"),
        "openjdk17-jre" => Some("openjdk-17-jre"),
        "openjdk21-jdk" => Some("openjdk-21-jdk"),
        "openjdk11-jdk" => Some("openjdk-11-jdk"),
        "sqlite" => Some("sqlite3"),
        "openssl" => Some("openssl"),
        "procps" => Some(""),                        // pre-installed
        "libssl-dev" => Some(""),                    // pre-installed
        "pkg-config" => Some(""),                    // pre-installed
        // Pass through as-is (likely the same on Debian)
        _ => None,
    }
}

/// Translate an `apk add` line to `apt-get install` with package name mapping.
fn translate_apk_line(line: &str) -> String {
    // Extract the package list portion after "apk add [--no-cache]"
    // and translate each package name.
    let mut result = String::new();
    let mut remaining = line.to_string();

    // Replace apk command with apt-get
    remaining = remaining
        .replace("apk add --no-cache", "APK_PLACEHOLDER")
        .replace("apk add", "APK_PLACEHOLDER");

    // Split around APK_PLACEHOLDER to find the package list
    if let Some(idx) = remaining.find("APK_PLACEHOLDER") {
        let before = &remaining[..idx];
        let after = &remaining[idx + "APK_PLACEHOLDER".len()..];

        result.push_str(before);
        result.push_str("apt-get update -qq && apt-get install -y --no-install-recommends");

        // Parse package names from after, handling continuation lines (\)
        // and && chains. We only translate bare words that look like package names.
        let mut translated_pkgs = Vec::new();
        let mut rest_after_pkgs = String::new();

        for token in after.split_whitespace() {
            if token == "\\" || token.starts_with("&&") || token.starts_with("||") {
                // End of package list — append rest verbatim
                rest_after_pkgs.push(' ');
                rest_after_pkgs.push_str(token);
                rest_after_pkgs.push_str(&after[after.find(token).unwrap() + token.len()..]);
                break;
            }
            if token.starts_with('-') {
                // Flags (e.g. --virtual) — skip
                continue;
            }
            match alpine_to_debian_pkg(token) {
                Some("") => {} // Drop — already in base
                Some(deb) => translated_pkgs.push(deb.to_string()),
                None => translated_pkgs.push(token.to_string()), // Pass through
            }
        }

        if translated_pkgs.is_empty() {
            // All packages already in base — replace with a no-op
            result.clear();
            result.push_str(before);
            result.push_str("true # all packages pre-installed in base");
            if !rest_after_pkgs.is_empty() {
                result.push_str(&rest_after_pkgs);
            }
        } else {
            result.push(' ');
            result.push_str(&translated_pkgs.join(" "));
            if !rest_after_pkgs.is_empty() {
                result.push_str(&rest_after_pkgs);
            }
        }
    } else {
        return line.to_string();
    }

    result
}

/// Build a scenario image rebased onto needle-bench-native.
///
/// Only the LAST `FROM` (the runtime stage) is replaced with `FROM needle-bench-native`.
/// Builder stages keep their original toolchain images (golang, rust, etc.) so they
/// can compile the benchmark's own code. Alpine `apk` commands are only translated
/// in the runtime stage since builder stages stay Alpine.
fn docker_build_with_native_base(scenario_dir: &Path, tag: &str) -> Result<(), String> {
    let dockerfile = scenario_dir.join("Dockerfile");
    let content = fs::read_to_string(&dockerfile)
        .map_err(|e| format!("cannot read Dockerfile: {e}"))?;

    let lines: Vec<&str> = content.lines().collect();

    // Find the last FROM line — that's the runtime stage.
    // Builder stages (FROM ... AS builder) keep their original toolchain images.
    let last_from_idx = lines.iter().rposition(|l| l.trim().starts_with("FROM "))
        .unwrap_or(0);

    let mut modified = String::new();
    for (i, line) in lines.iter().enumerate() {
        let trimmed = line.trim();
        let in_runtime = i >= last_from_idx;

        if trimmed.starts_with("FROM ") && in_runtime {
            // Replace only the runtime stage FROM, preserve any AS alias
            let as_part = trimmed.find(" AS ").or_else(|| trimmed.find(" as "))
                .map(|pos| &trimmed[pos..])
                .unwrap_or("");
            modified.push_str(&format!("FROM {NATIVE_IMAGE}{as_part}\n"));
            // Debian defaults to US-ASCII; Alpine defaults to UTF-8.
            // Java sources with em-dashes etc. need UTF-8 encoding.
            modified.push_str("ENV JAVA_TOOL_OPTIONS=\"-Dfile.encoding=UTF-8\"\n");
        } else if in_runtime && (trimmed.starts_with("RUN apk add") || trimmed.contains("&& apk add")) {
            // Translate apk→apt only in runtime stage
            modified.push_str(&translate_apk_line(line));
            modified.push('\n');
        } else if in_runtime && (trimmed.starts_with("RUN apk del") || trimmed.contains("&& apk del")) {
            let replaced = line.replace("apk del", "apt-get remove -y");
            modified.push_str(&replaced);
            modified.push('\n');
        } else if in_runtime && trimmed.contains("/var/cache/apk") {
            // Alpine cache dir doesn't exist on Debian — replace with no-op
            let replaced = line.replace("rm -rf /var/cache/apk/*", "true");
            modified.push_str(&replaced);
            modified.push('\n');
        } else {
            modified.push_str(line);
            modified.push('\n');
        }
    }

    // Write temp Dockerfile outside the build context so COPY . . doesn't pick it up
    let tmp_dockerfile = std::env::temp_dir().join(format!("Dockerfile.bench-native-{}", std::process::id()));
    fs::write(&tmp_dockerfile, &modified)
        .map_err(|e| format!("cannot write temp Dockerfile: {e}"))?;

    let output = Command::new("docker")
        .args(["build", "-t", tag, "-f", &tmp_dockerfile.to_string_lossy(), "."])
        .current_dir(scenario_dir)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .output()
        .map_err(|e| format!("docker build spawn error: {e}"))?;

    let _ = fs::remove_file(&tmp_dockerfile);

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("docker build (native base) failed: {}", stderr.chars().take(2000).collect::<String>()));
    }
    Ok(())
}

/// Run a model's native CLI harness inside the container. Returns BenchResult.
///
/// The container is expected to be built from needle-bench-native base (via
/// `docker_build_with_native_base`), so CLIs should already be present.
/// Falls back to direct install if a CLI is somehow missing.
fn run_native_harness(container_name: &str, harness: &NativeHarness, model: &str, max_turns: u32, workdir: &str) -> Result<BenchResult, String> {
    let start = Instant::now();

    // CLI should be present from native base image; install as fallback
    let has_cli = docker_exec(container_name, &format!("which {} 2>/dev/null", harness.command)).is_ok();
    if !has_cli {
        println!("  [native] {} not in image, installing...", harness.display);
        let _ = docker_exec(container_name, &format!("{} 2>&1 | tail -5", harness.install_cmd));
    }

    // Get API key from env or ostk secrets
    let api_key = std::env::var(harness.env_key)
        .or_else(|_| crate::commands::secret::resolve_secret(harness.env_key))
        .unwrap_or_default();
    if api_key.is_empty() {
        return Err(format!("{} not set", harness.env_key));
    }

    // Build the CLI command — each harness has its own flags
    let prompt = "find the needle. run test.sh to verify your fix.";
    let cli_cmd = match harness.command {
        "claude" => {
            // Claude Code refuses --dangerously-skip-permissions as root.
            // Write a run script and execute as non-root bench user.
            format!(
                "cat > /tmp/bench-run.sh << 'BENCHEOF'\ncd {workdir} && {key}={val} claude \
                 -p '{prompt}' \
                 --model {model} \
                 --dangerously-skip-permissions \
                 --max-turns {max_turns} \
                 --output-format json 2>&1\nBENCHEOF\n\
                 chmod +x /tmp/bench-run.sh && \
                 chown -R bench:bench {workdir} && \
                 su bench -c 'bash /tmp/bench-run.sh'",
                key = harness.env_key, val = api_key
            )
        }
        "gemini" => format!(
            "export {key}={val} && cd {workdir} && gemini \
             -p '{prompt}' \
             --model {model} \
             --yolo \
             --output-format json 2>&1",
            key = harness.env_key, val = api_key
        ),
        "codex" => format!(
            "export {key}={val} && \
             printf '%s' \"{val}\" | codex login --with-api-key 2>/dev/null; \
             cd {workdir} && codex exec \
             --model {model} \
             --dangerously-bypass-approvals-and-sandbox \
             --json \
             '{prompt}' 2>&1",
            key = harness.env_key, val = api_key
        ),
        // "grok" — disabled: CLI uses deprecated xAI API. Grok routes through OpenCode.
        "vibe" => {
            let vibe_model = crate::cpu::mistral::normalize_mistral_model(model);
            format!(
                "mkdir -p ~/.vibe && cat > ~/.vibe/config.toml << 'VIBECONF'\n\
active_model = \"{vibe_model}\"\n\
\n\
[[models]]\n\
name = \"{vibe_model}\"\n\
provider = \"mistral\"\n\
VIBECONF\n\
                 export {key}={val} && cd {workdir} && vibe \
                 --prompt '{prompt}' \
                 --max-turns {max_turns} \
                 --output json 2>&1",
                key = harness.env_key, val = api_key
            )
        }
        "kimi" => {
            // Kimi needs a full config.toml with providers + models sections.
            let kimi_toml = format!(
                "default_model = \"{m}\"\n\n\
                 [providers.kimi]\n\
                 type = \"kimi\"\n\
                 base_url = \"https://api.moonshot.ai/v1\"\n\
                 api_key = \"{k}\"\n\n\
                 [models.\"{m}\"]\n\
                 provider = \"kimi\"\n\
                 model = \"{m}\"\n\
                 max_context_size = 131072",
                m = model, k = api_key,
            );
            format!(
                "mkdir -p ~/.kimi && cat > ~/.kimi/config.toml << 'KIMIEOF'\n\
                 {kimi_toml}\n\
                 KIMIEOF\n\
                 cd {workdir} && kimi \
                 -p '{prompt}' \
                 --model {model} \
                 --yolo \
                 --print 2>&1",
            )
        }
        _ => format!(
            "cd {workdir} && {key}={val} {cmd} -p '{prompt}' 2>&1",
            key = harness.env_key, val = api_key, cmd = harness.command
        ),
    };

    println!("  [native] running {} ...", harness.command);
    let output = docker_exec(container_name, &cli_cmd)
        .unwrap_or_else(|e| format!("harness error: {e}"));

    // Log last few lines of harness output for debugging
    let tail: Vec<&str> = output.lines().rev().take(5).collect();
    for line in tail.iter().rev() {
        println!("  [native]   {line}");
    }

    let wall_clock_ms = start.elapsed().as_millis() as u64;
    let resolved = docker_exec(container_name, &format!("cd {workdir} && bash test.sh")).is_ok();

    // Parse metrics from CLI output
    let mut metrics = parse_cli_metrics(harness.command, &output);
    // Enrich from container artifacts for CLIs with incomplete stdout metrics
    if harness.command == "vibe" {
        parse_vibe_session_stats(container_name, &mut metrics);
    }

    Ok(BenchResult {
        resolved,
        turns: metrics.turns,
        input_tokens: metrics.input_tokens,
        output_tokens: metrics.output_tokens,
        cost_usd: metrics.cost_usd,
        wall_clock_ms,
        tool_uses: metrics.tool_uses,
        stop_reason: if resolved { "pass".to_string() } else { "fail".to_string() },
        summary: metrics.summary,
    })
}

// ---------------------------------------------------------------------------
// CLI output metrics parsing
// ---------------------------------------------------------------------------

/// Parsed metrics from a CLI's stdout/log output.
struct CliMetrics {
    turns: u32,
    input_tokens: u64,
    output_tokens: u64,
    cost_usd: f64,
    tool_uses: u32,
    summary: String,
}

impl Default for CliMetrics {
    fn default() -> Self {
        Self { turns: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0.0, tool_uses: 0, summary: String::new() }
    }
}

/// Parse metrics from a CLI's stdout output, dispatching by CLI name.
fn parse_cli_metrics(command: &str, output: &str) -> CliMetrics {
    match command {
        "claude" => parse_claude_metrics(output),
        "gemini" => parse_gemini_metrics(output),
        "codex" => parse_codex_metrics(output),
        "vibe" => parse_vibe_metrics(output),
        "kimi" => parse_kimi_metrics(output),
        "opencode" => parse_opencode_metrics(output),
        "aider" => parse_aider_metrics(output),
        _ => CliMetrics::default(),
    }
}

/// Claude Code: single JSON line with type=result.
fn parse_claude_metrics(output: &str) -> CliMetrics {
    let mut m = CliMetrics::default();
    // Find the JSON result line (last line containing "type":"result")
    for line in output.lines().rev() {
        if line.contains("\"type\":\"result\"") {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
                m.turns = v.get("num_turns").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
                m.cost_usd = v.get("total_cost_usd").and_then(|v| v.as_f64()).unwrap_or(0.0);
                if let Some(usage) = v.get("usage") {
                    m.input_tokens = usage.get("input_tokens").and_then(|v| v.as_u64()).unwrap_or(0)
                        + usage.get("cache_creation_input_tokens").and_then(|v| v.as_u64()).unwrap_or(0)
                        + usage.get("cache_read_input_tokens").and_then(|v| v.as_u64()).unwrap_or(0);
                    m.output_tokens = usage.get("output_tokens").and_then(|v| v.as_u64()).unwrap_or(0);
                }
                m.summary = v.get("result").and_then(|v| v.as_str()).unwrap_or("").to_string();
                // Count tool uses from permission_denials length (they ran) + turns - 1 (final text turn)
                // Better: num_turns counts API turns, each with potential tool use
                m.tool_uses = m.turns.saturating_sub(1); // approximate: last turn is text
            }
            break;
        }
    }
    m
}

/// Gemini CLI: single JSON object with stats and response.
/// Output may have non-JSON preamble lines (e.g. "YOLO mode...").
fn parse_gemini_metrics(output: &str) -> CliMetrics {
    let mut m = CliMetrics::default();
    // Find the JSON object — look for the first '{' that starts a valid JSON
    let json_str = output.find('{').map(|i| &output[i..]).unwrap_or("");
    if let Ok(v) = serde_json::from_str::<serde_json::Value>(json_str) {
        m.summary = v.get("response").and_then(|v| v.as_str()).unwrap_or("").to_string();
        if let Some(stats) = v.get("stats") {
            // Tokens from first model entry
            if let Some(models) = stats.get("models").and_then(|v| v.as_object()) {
                for (_name, model_stats) in models {
                    m.turns = model_stats.pointer("/api/totalRequests").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
                    m.input_tokens = model_stats.pointer("/tokens/input").and_then(|v| v.as_u64()).unwrap_or(0);
                    m.output_tokens = model_stats.pointer("/tokens/candidates").and_then(|v| v.as_u64()).unwrap_or(0);
                    break; // first model only
                }
            }
            m.tool_uses = stats.pointer("/tools/totalCalls").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
        }
    }
    m
}

/// Codex CLI: JSONL events.
fn parse_codex_metrics(output: &str) -> CliMetrics {
    let mut m = CliMetrics::default();
    for line in output.lines() {
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
            match v.get("type").and_then(|v| v.as_str()) {
                Some("turn.completed") => {
                    if let Some(usage) = v.get("usage") {
                        m.input_tokens += usage.get("input_tokens").and_then(|v| v.as_u64()).unwrap_or(0);
                        m.output_tokens += usage.get("output_tokens").and_then(|v| v.as_u64()).unwrap_or(0);
                    }
                    m.turns += 1;
                }
                Some("item.completed") => {
                    if let Some(item) = v.get("item") {
                        let item_type = item.get("type").and_then(|v| v.as_str()).unwrap_or("");
                        if item_type == "command_execution" || item_type == "file_edit" {
                            m.tool_uses += 1;
                        }
                        if item_type == "agent_message" {
                            m.summary = item.get("text").and_then(|v| v.as_str()).unwrap_or("").to_string();
                        }
                    }
                }
                _ => {}
            }
        }
    }
    m
}

/// Mistral Vibe: JSON output (array of messages or object).
fn parse_vibe_metrics(output: &str) -> CliMetrics {
    let mut m = CliMetrics::default();
    // Vibe --output json may have non-JSON preamble
    let json_str = output.find('[').map(|i| &output[i..])
        .or_else(|| output.find('{').map(|i| &output[i..]))
        .unwrap_or("");
    if let Ok(v) = serde_json::from_str::<serde_json::Value>(json_str) {
        let messages = if v.is_array() { v.as_array().cloned().unwrap_or_default() }
            else if let Some(arr) = v.get("messages").and_then(|v| v.as_array()) { arr.clone() }
            else { vec![] };
        for msg in &messages {
            let role = msg.get("role").and_then(|v| v.as_str()).unwrap_or("");
            if role == "assistant" {
                m.turns += 1;
                if let Some(text) = msg.get("content").and_then(|v| v.as_str()) {
                    m.summary = text.to_string();
                }
                if let Some(tool_calls) = msg.get("tool_calls").and_then(|v| v.as_array()) {
                    m.tool_uses += tool_calls.len() as u32;
                }
            }
        }
    }
    // Vibe also prints "Total tokens used this session: input=X output=Y (total=Z)"
    // in some modes — parse it if present
    for line in output.lines() {
        if line.contains("Total tokens used this session") {
            if let Some(input_start) = line.find("input=") {
                let rest = &line[input_start + 6..];
                m.input_tokens = rest.split_whitespace().next()
                    .and_then(|s| s.parse::<u64>().ok()).unwrap_or(0);
            }
            if let Some(output_start) = line.find("output=") {
                let rest = &line[output_start + 7..];
                m.output_tokens = rest.split(|c: char| !c.is_ascii_digit()).next()
                    .and_then(|s| s.parse::<u64>().ok()).unwrap_or(0);
            }
        }
    }
    m
}

/// Try to read Vibe session stats from container after run.
fn parse_vibe_session_stats(container_name: &str, metrics: &mut CliMetrics) {
    // Vibe stores session stats in ~/.vibe/logs/session/session_*/meta.json
    // meta.json has stats.session_prompt_tokens and stats.session_completion_tokens
    if let Ok(session) = docker_exec(container_name,
        "ls -t ~/.vibe/logs/session/session_*/meta.json 2>/dev/null | head -1 | xargs cat 2>/dev/null"
    )
        && let Ok(v) = serde_json::from_str::<serde_json::Value>(&session)
            && let Some(stats) = v.get("stats") {
                if let Some(inp) = stats.get("session_prompt_tokens").and_then(|v| v.as_u64()) {
                    metrics.input_tokens = metrics.input_tokens.max(inp);
                }
                if let Some(out) = stats.get("session_completion_tokens").and_then(|v| v.as_u64()) {
                    metrics.output_tokens = metrics.output_tokens.max(out);
                }
            }
}

/// Kimi CLI: best-effort from --print stdout.
fn parse_kimi_metrics(output: &str) -> CliMetrics {
    let mut m = CliMetrics::default();
    for line in output.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with("StepBegin") {
            m.turns += 1;
        }
        if trimmed.starts_with("ToolCallBegin") || trimmed.starts_with("ToolCall(") {
            m.tool_uses += 1;
        }
        // Last non-empty, non-event line is likely the summary
        if !trimmed.is_empty()
            && !trimmed.starts_with("StepBegin") && !trimmed.starts_with("StepEnd")
            && !trimmed.starts_with("TurnBegin") && !trimmed.starts_with("TurnEnd")
            && !trimmed.starts_with("StepInterrupted") && !trimmed.starts_with("ToolCall")
            && !trimmed.starts_with("Error") && !trimmed.starts_with("See logs:")
        {
            m.summary = trimmed.to_string();
        }
    }
    m
}

/// OpenCode: JSONL events with step_finish, tool_use, text.
fn parse_opencode_metrics(output: &str) -> CliMetrics {
    let mut m = CliMetrics::default();
    for line in output.lines() {
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
            match v.get("type").and_then(|v| v.as_str()) {
                Some("step_finish") => {
                    if let Some(part) = v.get("part") {
                        if let Some(tokens) = part.get("tokens") {
                            m.input_tokens += tokens.get("input").and_then(|v| v.as_u64()).unwrap_or(0);
                            m.output_tokens += tokens.get("output").and_then(|v| v.as_u64()).unwrap_or(0);
                            // Include cache read as input
                            m.input_tokens += tokens.pointer("/cache/read").and_then(|v| v.as_u64()).unwrap_or(0);
                        }
                        m.cost_usd += part.get("cost").and_then(|v| v.as_f64()).unwrap_or(0.0);
                    }
                    m.turns += 1;
                }
                Some("tool_use") => {
                    m.tool_uses += 1;
                }
                Some("text") => {
                    if let Some(part) = v.get("part")
                        && let Some(text) = part.get("text").and_then(|v| v.as_str()) {
                            m.summary = text.to_string();
                        }
                }
                _ => {}
            }
        }
    }
    m
}

/// aider: parse "Tokens: Xk sent, Y received. Cost: $0.04 message, $0.12 session."
fn parse_aider_metrics(output: &str) -> CliMetrics {
    let mut m = CliMetrics::default();
    for line in output.lines() {
        let trimmed = line.trim();
        // "Tokens: 2.6k sent, 117 received. Cost: $0.04 message, $0.12 session."
        if trimmed.starts_with("Tokens:")
            && let Some(sent_part) = trimmed.strip_prefix("Tokens:") {
                let parts: Vec<&str> = sent_part.split(',').collect();
                if let Some(sent) = parts.first() {
                    let sent = sent.trim();
                    if let Some(num) = sent.strip_suffix("k sent") {
                        m.input_tokens = (num.trim().parse::<f64>().unwrap_or(0.0) * 1000.0) as u64;
                    } else if let Some(num) = sent.strip_suffix(" sent") {
                        m.input_tokens = num.trim().parse::<u64>().unwrap_or(0);
                    }
                }
                if let Some(recv) = parts.get(1) {
                    let recv = recv.trim();
                    // May end with "received." or "received" before Cost:
                    let recv_clean = recv.split('.').next().unwrap_or(recv).trim();
                    if let Some(num) = recv_clean.strip_suffix(" received") {
                        m.output_tokens = num.trim().parse::<u64>().unwrap_or(0);
                    }
                }
                // Parse "Cost: $X.XX message" or "Cost: $X.XX session"
                if let Some(cost_idx) = trimmed.find("Cost:") {
                    let cost_part = &trimmed[cost_idx + 5..];
                    // Find session cost: "Cost: $0.04 message, $0.12 session."
                    // or just "Cost: $0.04 message."
                    for segment in cost_part.split(',') {
                        let seg = segment.trim();
                        if seg.contains("session") || (!seg.contains("message") && seg.starts_with('$')) {
                            if let Some(num) = seg.strip_prefix('$') {
                                let num = num.split_whitespace().next().unwrap_or("");
                                m.cost_usd = num.parse::<f64>().unwrap_or(0.0);
                            }
                        } else if seg.contains("message") && m.cost_usd == 0.0 {
                            // Fallback to message cost if no session cost
                            if let Some(num) = seg.strip_prefix('$') {
                                let num = num.split_whitespace().next().unwrap_or("");
                                m.cost_usd = num.parse::<f64>().unwrap_or(0.0);
                            }
                        }
                    }
                }
            }
        // Count "Applied edit to X" as tool uses
        if trimmed.starts_with("Applied edit to ") {
            m.tool_uses += 1;
        }
    }
    // Summary: grab from between the prompt and first "Tokens:" or "Applied edit"
    let mut in_response = false;
    let mut summary_lines = Vec::new();
    for line in output.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with("####") {
            in_response = true;
            continue;
        }
        if in_response {
            if trimmed.starts_with("Tokens:") || trimmed.starts_with("Applied edit") || trimmed.starts_with(">") {
                break;
            }
            if !trimmed.is_empty() {
                summary_lines.push(trimmed);
            }
        }
    }
    if !summary_lines.is_empty() {
        m.summary = summary_lines.join(" ");
    }
    m.turns = 1; // aider is single-turn
    m
}

/// Parse aider analytics log for structured token/cost data.
/// Falls back to stdout parsing if log not available.
fn parse_aider_analytics(container_name: &str, _workdir: &str, stdout_metrics: &mut CliMetrics) {
    if let Ok(log) = docker_exec(container_name, "cat /tmp/aider-analytics.jsonl 2>/dev/null") {
        for line in log.lines() {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
                if let Some(pt) = v.get("prompt_tokens").and_then(|v| v.as_u64()) {
                    stdout_metrics.input_tokens = stdout_metrics.input_tokens.max(pt);
                }
                if let Some(ct) = v.get("completion_tokens").and_then(|v| v.as_u64()) {
                    stdout_metrics.output_tokens = stdout_metrics.output_tokens.max(ct);
                }
                if let Some(c) = v.get("cost").and_then(|v| v.as_f64()) {
                    stdout_metrics.cost_usd += c;
                }
            }
        }
    }
}

/// Map a model name to OpenCode's provider/model format.
/// If already in provider/model format (contains '/'), pass through as-is.
fn opencode_model(model: &str) -> String {
    if model.contains('/') { model.to_string() }
    else if model.starts_with("gpt-") || model.starts_with("o1") || model.starts_with("o3") || model.starts_with("o4") { format!("openai/{model}") }
    // DeepSeek model name mapping — API uses different names
    else if model == "deepseek-r1" { "deepseek/deepseek-reasoner".to_string() }
    else if model == "deepseek-r1-0528" { "deepseek/deepseek-r1-0528".to_string() }
    else if model.starts_with("deepseek-") { format!("deepseek/{model}") }
    else if model.starts_with("llama-") || model.starts_with("meta-") { format!("groq/{model}") }
    else if model.starts_with("qwen") { format!("qwen/{model}") }
    else if model.starts_with("grok-") { format!("xai/{model}") }
    else { model.to_string() }
}

/// Returns true if the model targets a local Ollama instance.
fn is_local_model(model: &str) -> bool {
    model.starts_with("ollama/") || model.starts_with("local/")
}

/// Map a model name to OpenRouter's provider/model format.
/// OpenRouter requires the provider prefix (e.g. anthropic/claude-sonnet-4-6).
fn openrouter_model(model: &str) -> String {
    if model.contains('/') { return model.to_string(); }
    if model.starts_with("claude-") { format!("anthropic/{model}") }
    else if model.starts_with("gemini-") { format!("google/{model}") }
    else if model.starts_with("gpt-") || model.starts_with("o1") || model.starts_with("o3") || model.starts_with("o4") { format!("openai/{model}") }
    else if model.starts_with("grok-") { format!("x-ai/{model}") }
    else if model.starts_with("codestral-") { format!("mistralai/{model}") }
    else if model.starts_with("devstral-") {
        // OpenRouter uses base names without -latest suffix
        let base = model.strip_suffix("-latest").unwrap_or(model);
        format!("mistralai/{base}")
    }
    else if model.starts_with("mistral-") { format!("mistralai/{model}") }
    else if model.starts_with("kimi-") || model.starts_with("moonshot-") { format!("moonshotai/{model}") }
    else if model.starts_with("deepseek-") { format!("deepseek/{model}") }
    else if model.starts_with("qwen") { format!("qwen/{model}") }
    else if model.starts_with("llama-") { format!("meta-llama/{model}") }
    else { model.to_string() }
}

/// Run a local (Ollama) model via aider inside the container.
fn run_local_harness(container_name: &str, model: &str, _max_turns: u32, workdir: &str) -> Result<BenchResult, String> {
    let start = Instant::now();

    // Install aider if not present
    let has_aider = docker_exec(container_name, "which aider 2>/dev/null").is_ok();
    if !has_aider {
        println!("  [local] installing aider...");
        let _ = docker_exec(container_name, "pip install --break-system-packages -q aider-chat 2>&1 | tail -3");
    }

    // Map model name: "ollama/qwen2.5-coder:32b" → "ollama_chat/qwen2.5-coder:32b"
    let aider_model = if model.starts_with("ollama/") {
        format!("ollama_chat/{}", &model["ollama/".len()..])
    } else if model.starts_with("local/") {
        format!("ollama_chat/{}", &model["local/".len()..])
    } else {
        model.to_string()
    };

    let prompt = "find the needle. run test.sh to verify your fix.";
    let cli_cmd = format!(
        "export OLLAMA_API_BASE=http://host.docker.internal:11434 && \
         cd {workdir} && aider \
         --model {aider_model} \
         --yes-always \
         --no-auto-commits \
         --analytics-log /tmp/aider-analytics.jsonl \
         --message '{prompt}' 2>&1"
    );

    println!("  [local] running aider with {aider_model}...");
    let output = docker_exec(container_name, &cli_cmd)
        .unwrap_or_else(|e| format!("aider error: {e}"));

    let tail: Vec<&str> = output.lines().rev().take(5).collect();
    for line in tail.iter().rev() {
        println!("  [local]   {line}");
    }

    let wall_clock_ms = start.elapsed().as_millis() as u64;
    let resolved = docker_exec(container_name, &format!("cd {workdir} && bash test.sh")).is_ok();

    let mut metrics = parse_aider_metrics(&output);
    // Enrich with structured analytics log if available
    parse_aider_analytics(container_name, workdir, &mut metrics);
    // Try to get richer summary from chat history if stdout summary is empty
    if metrics.summary.is_empty()
        && let Ok(history) = docker_exec(container_name, &format!("cat {workdir}/.aider.chat.history.md 2>/dev/null")) {
            // Extract last assistant response
            let mut last_response = String::new();
            for line in history.lines() {
                if line.starts_with("####") { last_response.clear(); }
                else if !line.starts_with(">") && !line.trim().is_empty() {
                    last_response = line.trim().to_string();
                }
            }
            if !last_response.is_empty() {
                metrics.summary = last_response;
            }
        }

    Ok(BenchResult {
        resolved,
        turns: metrics.turns,
        input_tokens: metrics.input_tokens,
        output_tokens: metrics.output_tokens,
        cost_usd: metrics.cost_usd,
        wall_clock_ms,
        tool_uses: metrics.tool_uses,
        stop_reason: if resolved { "pass".to_string() } else { "fail".to_string() },
        summary: metrics.summary,
    })
}

/// Run OpenCode as a universal fallback for models without a vendor CLI.
fn run_opencode_fallback(container_name: &str, model: &str, _max_turns: u32, workdir: &str) -> Result<BenchResult, String> {
    let start = Instant::now();

    // Install opencode if not present
    let has_opencode = docker_exec(container_name, "which opencode 2>/dev/null").is_ok();
    if !has_opencode {
        println!("  [native] installing opencode...");
        let _ = docker_exec(container_name, &format!("{OPENCODE_INSTALL_CMD} 2>&1 | tail -5"));
    }

    // Collect all available API keys to pass through
    let mut env_parts = Vec::new();
    for key in &["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY",
                  "MISTRAL_API_KEY", "GROK_API_KEY", "KIMI_API_KEY", "GROQ_API_KEY",
                  "OPENROUTER_API_KEY", "XAI_API_KEY"] {
        if let Ok(val) = std::env::var(key)
            && !val.is_empty() {
                env_parts.push(format!("export {key}={val}"));
            }
    }
    // Also forward GROK_API_KEY as XAI_API_KEY for OpenCode's xai provider
    if let Ok(val) = std::env::var("GROK_API_KEY").or_else(|_| crate::commands::secret::resolve_secret("GROK_API_KEY"))
        && !val.is_empty() && !env_parts.iter().any(|p| p.contains("XAI_API_KEY")) {
            env_parts.push(format!("export XAI_API_KEY={val}"));
        }
    // For local/ollama models, point at host Ollama instance and write provider config
    if is_local_model(model) {
        env_parts.push("export OLLAMA_HOST=http://host.docker.internal:11434".to_string());
        // Write opencode.json with Ollama provider config
        let _ = docker_exec(container_name, &format!(
            "cat > {workdir}/opencode.json << 'OCEOF'\n\
             {{\n\
               \"provider\": {{\n\
                 \"ollama\": {{\n\
                   \"options\": {{ \"baseUrl\": \"http://host.docker.internal:11434\" }}\n\
                 }}\n\
               }},\n\
               \"permission\": \"allow\"\n\
             }}\n\
             OCEOF"
        ));
    }
    let env_str = env_parts.join(" && ");

    let prompt = "find the needle. run test.sh to verify your fix.";
    let oc_model = opencode_model(model);
    let cli_cmd = format!(
        "{env_str} && cd {workdir} && OPENCODE_PERMISSION='{{\"*\":\"allow\"}}' \
         opencode run -m '{oc_model}' '{prompt}' --format json 2>&1"
    );

    println!("  [native] running opencode with model {model}...");
    let output = docker_exec(container_name, &cli_cmd)
        .unwrap_or_else(|e| format!("opencode error: {e}"));

    let tail: Vec<&str> = output.lines().rev().take(5).collect();
    for line in tail.iter().rev() {
        println!("  [native]   {line}");
    }

    let wall_clock_ms = start.elapsed().as_millis() as u64;
    let resolved = docker_exec(container_name, &format!("cd {workdir} && bash test.sh")).is_ok();

    let metrics = parse_opencode_metrics(&output);

    Ok(BenchResult {
        resolved,
        turns: metrics.turns,
        input_tokens: metrics.input_tokens,
        output_tokens: metrics.output_tokens,
        cost_usd: metrics.cost_usd,
        wall_clock_ms,
        tool_uses: metrics.tool_uses,
        stop_reason: if resolved { "pass".to_string() } else { "fail".to_string() },
        summary: metrics.summary,
    })
}

/// Path to the cross-compiled Linux binary built by `make bench-binary`.
/// Checks the haystack project root first (where cargo builds), then CWD.
fn local_linux_binary() -> PathBuf {
    let suffix = "target/x86_64-unknown-linux-musl/release/ostk";
    // Prefer the haystack source tree (where `make bench-binary` builds)
    if let Ok(home) = std::env::var("HOME") {
        let haystack = PathBuf::from(&home).join("projects/haystack").join(suffix);
        if haystack.exists() { return haystack; }
    }
    // Fall back to project root (works if running from haystack dir)
    let root = find_project_root().unwrap_or_else(|_| PathBuf::from("."));
    root.join(suffix)
}

/// Install ostk inside a Docker container and boot the kernel.
///
/// Install ostk and boot the kernel inside a container.
///
/// Default: assumes the container has ostk pre-installed (from
/// `docker/Dockerfile.bench-kernel`). Falls back to download if missing.
///
/// `--local`: overwrites with the cross-compiled dev binary from
/// `target/x86_64-unknown-linux-musl/release/ostk` via `docker cp`.
fn install_and_boot_kernel(container_name: &str, workdir: &str, use_local: bool) {
    // Check if ostk is already in the image (pre-rolled)
    let has_ostk = docker_exec(container_name, "which ostk 2>/dev/null").is_ok();

    if use_local {
        // --local: always overwrite with dev binary
        let bin = local_linux_binary();
        if bin.exists() {
            println!("  [kernel] copying local binary: {}", bin.display());
            let status = Command::new("docker")
                .args(["cp", &bin.to_string_lossy(), &format!("{container_name}:/usr/local/bin/ostk")])
                .status();
            if status.map(|s| s.success()).unwrap_or(false) {
                let _ = docker_exec(container_name, "chmod +x /usr/local/bin/ostk");
            } else {
                println!("  [kernel] WARN: docker cp failed");
            }
        } else {
            println!("  [kernel] ERROR: local binary not found at {}", bin.display());
            println!("  [kernel] run `make install` to cross-compile, or omit --local");
            if !has_ostk {
                // No local binary AND no pre-installed — try remote
                println!("  [kernel] falling back to download...");
                let _ = docker_exec(container_name,
                    "curl -fsSL https://ostk.ai/install | OSTK_INSTALL_DIR=/usr/local/bin sh 2>/dev/null"
                );
            }
        }
    } else if !has_ostk {
        // No pre-installed binary — download
        println!("  [kernel] ostk not in image, downloading...");
        let _ = docker_exec(container_name,
            "curl -fsSL https://ostk.ai/install | OSTK_INSTALL_DIR=/usr/local/bin sh 2>/dev/null"
        );
    } else {
        println!("  [kernel] using pre-installed ostk");
    }

    println!("  [kernel] ostk init + boot...");
    let _ = docker_exec(container_name, &format!("cd {workdir} && ostk init --non-interactive 2>&1 | tail -3"));
    let _ = docker_exec(container_name, &format!("cd {workdir} && ostk boot 2>&1 | tail -5"));
}

/// Read difficulty limits from needle-bench/difficulty.json for a scenario.
/// Returns (max_turns, max_tokens, wall_clock_secs).
fn read_difficulty_limits(nb_root: &Path, scenario_name: &str) -> (u32, u64, u64) {
    let difficulty_path = nb_root.join("difficulty.json");
    if let Ok(content) = fs::read_to_string(&difficulty_path)
        && let Ok(json) = serde_json::from_str::<serde_json::Value>(&content)
    {
        // Find the tier for this scenario
        let tier = json.get("benchmarks")
            .and_then(|b| b.get(scenario_name))
            .and_then(|t| t.as_str())
            .unwrap_or("medium");
        // Look up tier limits
        if let Some(tier_obj) = json.get("tiers").and_then(|t| t.get(tier)) {
            let turns = tier_obj.get("turns").and_then(|v| v.as_u64()).unwrap_or(40) as u32;
            let tokens = tier_obj.get("tokens").and_then(|v| v.as_u64()).unwrap_or(200_000);
            let wall = tier_obj.get("wall_clock").and_then(|v| v.as_u64()).unwrap_or(600);
            return (turns, tokens, wall);
        }
    }
    // Also check scenario-local Agentfile for LIMIT overrides
    // Default: medium tier
    (40, 200_000, 600)
}

/// Write a score file in needle-bench format.
fn write_score(nb_root: &Path, model: &str, scenario_name: &str, result: &BenchResult, difficulty_tier: &str, arm: Option<BenchArm>, driver: &str) {
    let agent_name = match arm {
        Some(BenchArm::Native) => format!("{model}-native"),
        Some(BenchArm::Kernel) if driver == "cpu" => format!("{model}-kernel-cpu"),
        Some(BenchArm::Kernel) => format!("{model}-kernel"),
        None => model.to_string(),
    };
    let model_dir = nb_root.join("runs").join(&agent_name);
    let _ = fs::create_dir_all(&model_dir);
    let score_path = model_dir.join(format!("{scenario_name}.score.json"));

    let total_tokens = result.input_tokens + result.output_tokens;
    let wall_clock_s = result.wall_clock_ms as f64 / 1000.0;

    let arm_str = match arm {
        Some(BenchArm::Native) => "native",
        Some(BenchArm::Kernel) => "kernel",
        None => "default",
    };

    let score = json!({
        "benchmark": scenario_name,
        "agent": agent_name,
        "arm": arm_str,
        "control_type": arm_str,
        "timestamp": crate::now_iso(),
        "difficulty_tier": difficulty_tier,
        "resolved": result.resolved,
        "turns_to_fix": result.turns,
        "token_cost": total_tokens,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "estimated_cost_usd": result.cost_usd,
        "wall_clock": wall_clock_s,
        "tool_uses": result.tool_uses,
        "stop_reason": result.stop_reason,
        "summary": result.summary,
    });

    if let Ok(json_str) = serde_json::to_string_pretty(&score) {
        let _ = fs::write(&score_path, json_str);
    }
}

/// Discover Docker-based scenarios under needle-bench/benchmarks/.
fn discover_docker_scenarios(root: &Path) -> Vec<DockerScenario> {
    let bench_dir = resolve_needle_bench_root()
        .map(|nb| nb.join("benchmarks"))
        .unwrap_or_else(|_| root.join("bench/scenarios"));
    let mut scenarios = Vec::new();

    if !bench_dir.is_dir() {
        return scenarios;
    }

    let mut entries: Vec<_> = fs::read_dir(&bench_dir)
        .ok()
        .into_iter()
        .flatten()
        .filter_map(|e| e.ok())
        .filter(|e| e.path().is_dir() && e.path().join("Dockerfile").exists())
        .collect();
    entries.sort_by_key(|e| e.file_name());

    for entry in entries {
        let dir = entry.path();
        let name = entry.file_name().to_string_lossy().into_owned();

        let scenario_md = dir.join("scenario.md");
        let description = if scenario_md.exists() {
            fs::read_to_string(&scenario_md)
                .ok()
                .and_then(|content| {
                    content
                        .lines()
                        .find(|l| !l.trim().is_empty() && !l.starts_with('#'))
                        .map(|l| l.trim().to_string())
                })
                .unwrap_or_default()
        } else {
            String::new()
        };

        scenarios.push(DockerScenario {
            name,
            dir,
            description,
        });
    }

    scenarios
}

/// →827: Run a Docker scenario natively through the kernel agent loop.
/// Replaces the old runner.py delegation with kernel-native execution.
/// Returns (passed: bool, duration_ms: u64, stop_reason: Option<String>).
fn run_docker_scenario(root: &Path, scenario: &DockerScenario, model: &str, arm: Option<BenchArm>, local: bool, driver: &str, keep: bool) -> (bool, u64, Option<String>) {
    let start = Instant::now();
    let nb_root = resolve_needle_bench_root().unwrap_or_else(|_| root.to_path_buf());
    let arm_suffix = match arm {
        Some(BenchArm::Native) => "-native",
        Some(BenchArm::Kernel) => "-kernel",
        None => "",
    };
    let model_short: String = model.replace('/', "-").chars().take(20).collect();
    let container_name = format!("nb-{}{}-{}-{}", scenario.name, arm_suffix, model_short,
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_millis());
    let image_tag = format!("needle-bench-{}", scenario.name);

    // 1. Docker build — for native and kernel arms, rebase onto needle-bench-native
    //    (native has vendor CLIs + runtimes, kernel only needs runtimes but the
    //    superset image is fine and avoids maintaining two base images)
    let use_native_base = arm == Some(BenchArm::Native) || arm == Some(BenchArm::Kernel);
    if use_native_base {
        println!("  building image {image_tag} (FROM {NATIVE_IMAGE})...");
    } else {
        println!("  building image {image_tag}...");
    }
    let build_result = if use_native_base {
        docker_build_with_native_base(&scenario.dir, &image_tag)
    } else {
        docker_build(&scenario.dir, &image_tag)
    };
    if let Err(e) = build_result {
        println!("  build error: {e}");
        return (false, start.elapsed().as_millis() as u64, Some(format!("build_error: {e}")));
    }

    // 2. Read difficulty limits (need wall_clock for container timeout)
    let (max_turns, _max_tokens, wall_clock_s) = read_difficulty_limits(&nb_root, &scenario.name);
    let container_timeout = wall_clock_s + 60; // buffer for cleanup

    // 3. Docker run — container auto-dies after wall_clock + buffer
    //    For local/ollama models, add host gateway so the container can reach
    //    Ollama on the host machine.
    let needs_host = model.starts_with("ollama/") || model.starts_with("local/");
    let extra_run_args: Vec<&str> = if needs_host {
        println!("  starting container {container_name} (timeout {container_timeout}s, host network)...");
        vec!["--add-host", "host.docker.internal:host-gateway"]
    } else {
        println!("  starting container {container_name} (timeout {container_timeout}s)...");
        vec![]
    };
    if let Err(e) = docker_run_with_opts(&image_tag, &container_name, container_timeout, &extra_run_args) {
        println!("  run error: {e}");
        return (false, start.elapsed().as_millis() as u64, Some(format!("run_error: {e}")));
    }

    // 4. Detect WORKDIR (not all benchmarks use /app)
    let workdir = docker_exec(&container_name, "pwd")
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|_| "/app".to_string());

    // 5. Snapshot workspace (git init + commit inside container)
    println!("  snapshotting workspace...");
    let _ = docker_exec(&container_name, &format!("cd {workdir} && git init && git add -A && git commit -m init 2>/dev/null"));

    // Also check scenario-local Agentfile for LIMIT overrides
    let scenario_turns = fs::read_to_string(scenario.dir.join("Agentfile"))
        .ok()
        .and_then(|content| {
            content.lines()
                .find(|l| l.starts_with("LIMIT turns") || l.starts_with("LIMIT max_turns"))
                .and_then(|l| l.split_whitespace().nth(2))
                .and_then(|v| v.parse::<u32>().ok())
        });
    let effective_turns = scenario_turns.unwrap_or(max_turns);

    // 5. Determine difficulty tier for scoring
    let difficulty_tier = {
        let difficulty_path = nb_root.join("difficulty.json");
        fs::read_to_string(&difficulty_path)
            .ok()
            .and_then(|c| serde_json::from_str::<serde_json::Value>(&c).ok())
            .and_then(|j| j.get("benchmarks")?.get(&scenario.name)?.as_str().map(|s| s.to_string()))
            .unwrap_or_else(|| "medium".to_string())
    };

    // 6. Arm-specific setup and dispatch
    let arm_label = match arm {
        Some(BenchArm::Native) => "native",
        Some(BenchArm::Kernel) => "kernel",
        None => "default",
    };

    // Kernel arm: install ostk and boot before running
    if arm == Some(BenchArm::Kernel) {
        install_and_boot_kernel(&container_name, &workdir, local);
    }

    // 7. Dispatch to the appropriate runner
    let result = match arm {
        Some(BenchArm::Native) => {
            if let Some(harness) = detect_native_harness(model) {
                println!("  [native] using {} ({})", harness.command, harness.display);
                run_native_harness(&container_name, harness, model, effective_turns, &workdir)
            } else if is_local_model(model) {
                println!("  [local] using aider for local model...");
                run_local_harness(&container_name, model, effective_turns, &workdir)
            } else {
                println!("  [native] no vendor CLI, using opencode fallback...");
                run_opencode_fallback(&container_name, model, effective_turns, &workdir)
            }
        }
        Some(BenchArm::Kernel) => {
            println!("  [kernel] running with live kernel...");
            let boot_ctx = docker_exec(&container_name, &format!(
                "cd {workdir} && ostk context 2>/dev/null"
            )).unwrap_or_default();
            run_bench_agent_loop(model, &container_name, effective_turns, &workdir, &boot_ctx, driver)
        }
        None => {
            println!("  [default] running agent loop...");
            run_bench_agent_loop(model, &container_name, effective_turns, &workdir, "", driver)
        }
    };

    let duration_ms = start.elapsed().as_millis() as u64;

    let (resolved, bench_result_opt) = match result {
        Ok(br) => {
            let resolved = br.resolved;
            println!("  [{arm_label}] complete: {} turns, {}+{} tokens, ${:.4}, {} tools, stop={}",
                br.turns, br.input_tokens, br.output_tokens, br.cost_usd, br.tool_uses, br.stop_reason);
            if !br.summary.is_empty() {
                let trunc: String = br.summary.chars().take(120).collect();
                println!("  [{arm_label}] summary: {trunc}");
            }
            (resolved, Some(br))
        }
        Err(e) => {
            println!("  [{arm_label}] agent loop error: {e}");
            (false, None)
        }
    };

    // 8. Final verification: run test.sh in container
    let final_resolved = if !resolved {
        match docker_exec(&container_name, &format!("cd {workdir} && bash test.sh")) {
            Ok(_) => {
                println!("  [{arm_label}] final test.sh: PASS");
                true
            }
            Err(_) => false,
        }
    } else {
        resolved
    };

    // 9. Write score
    let stop_reason = bench_result_opt.as_ref()
        .map(|br| br.stop_reason.clone())
        .unwrap_or_else(|| "error".to_string());
    let final_result = BenchResult {
        resolved: final_resolved,
        turns: bench_result_opt.as_ref().map(|br| br.turns).unwrap_or(0),
        input_tokens: bench_result_opt.as_ref().map(|br| br.input_tokens).unwrap_or(0),
        output_tokens: bench_result_opt.as_ref().map(|br| br.output_tokens).unwrap_or(0),
        cost_usd: bench_result_opt.as_ref().map(|br| br.cost_usd).unwrap_or(0.0),
        wall_clock_ms: duration_ms,
        tool_uses: bench_result_opt.as_ref().map(|br| br.tool_uses).unwrap_or(0),
        stop_reason: stop_reason.clone(),
        summary: bench_result_opt.as_ref().map(|br| br.summary.clone()).unwrap_or_default(),
    };
    write_score(&nb_root, model, &scenario.name, &final_result, &difficulty_tier, arm, driver);

    // 10. Cleanup (skip if --keep for investigation)
    if keep {
        println!("  [{arm_label}] container kept: {container_name}");
    } else {
        let _ = docker_stop(&container_name);
    }

    (final_resolved, duration_ms, Some(stop_reason))
}

/// →827: Run the kernel agent loop inside a bench container.
///
/// Sets OSTK_BENCH_CONTAINER so tool_exec routes through docker exec,
/// creates a CpuDriver for the model, builds a LoopConfig, and runs the loop.
/// Returns a BenchResult with metrics.
fn run_bench_agent_loop(model: &str, container_name: &str, max_turns: u32, _workdir: &str, silent_context: &str, driver_mode: &str) -> Result<BenchResult, String> {
    // Build a tokio runtime for the agent loop
    let rt = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .map_err(|e| format!("failed to build tokio runtime: {e}"))?;

    rt.block_on(async {
        // Set the env var so tool_exec routes through docker.
        // SAFETY: We are inside rt.block_on but before any tokio::spawn calls.
        // The multi-thread runtime exists but only this task is running at this
        // point — no concurrent readers of OSTK_BENCH_CONTAINER yet.
        unsafe { std::env::set_var("OSTK_BENCH_CONTAINER", container_name); }

        // Create the driver based on --driver flag:
        //   openrouter: consistent API for all models (scientific comparison)
        //   cpu: optimized native drivers (Anthropic w/ caching, Gemini, Mistral)
        let driver: std::sync::Arc<dyn crate::cpu::CpuDriver> = if driver_mode == "cpu" {
            println!("  [kernel] using native CPU driver for {model}");
            crate::cpu::create_driver(model)?
        } else {
            let or_model = openrouter_model(model);
            println!("  [kernel] using OpenRouter driver ({or_model})");
            std::sync::Arc::new(
                crate::cpu::openrouter::OpenRouterClient::new()
                    .map_err(|e| format!("OpenRouter driver failed (need OPENROUTER_API_KEY): {e}"))?
            )
        };

        // Build tool schemas for bench: Bash, Read, Edit, Write, Glob, Grep
        let bench_tools = crate::cpu::tool_schemas(&[
            "shell".to_string(),
            "file:read".to_string(),
            "file:edit".to_string(),
            "Write".to_string(),
            "Glob".to_string(),
            "Grep".to_string(),
        ]);

        // Both arms get the same base system prompt — bare and generic
        let mut system_prompt = "This is a benchmark. There is a hidden problem in the container you are running in. Your goal is to find the needle in the haystack.".to_string();

        // Silent arm: silently inject context. Model can't tell it came from an OS.
        if !silent_context.is_empty() {
            system_prompt.push_str("\n\n");
            system_prompt.push_str(silent_context);
        }

        let config = crate::cpu::agent_loop::LoopConfig {
            model: if driver_mode == "cpu" { model.to_string() } else { openrouter_model(model) },
            system_prompt: Some(system_prompt),
            tools: bench_tools,
            max_tokens: 16384,
            max_turns: Some(max_turns),
            context_budget: None,
            permission_mode: crate::cpu::PermissionMode::Autonomous,
            betas: vec![],
            preload_context: vec![],
            fast_mode: false,
            root: None,
            thinking: None,
            citations: false,
            tool_patterns: vec![],
        };

        let (event_tx, mut event_rx) = tokio::sync::mpsc::channel::<crate::cpu::agent_loop::CpuEvent>(256);

        let messages = vec![crate::cpu::anthropic::Message {
            role: "user".into(),
            content: vec![crate::cpu::anthropic::ContentBlock::Text {
                text: "find the needle. run test.sh to verify your fix.".into(),
            }],
            model: None,
        }];

        // Spawn event consumer to track metrics (including cost)
        let metrics_handle = tokio::spawn(async move {
            let mut total_input = 0u64;
            let mut total_output = 0u64;
            let mut total_cost = 0.0f64;
            let mut turns = 0u32;
            let mut tool_uses = 0u32;
            let mut stop_reason = "end_turn".to_string();
            let mut last_tool_output = String::new();
            let mut last_text = String::new();

            while let Some(event) = event_rx.recv().await {
                match event {
                    crate::cpu::agent_loop::CpuEvent::Usage { usage } => {
                        total_input += usage.input_tokens;
                        total_output += usage.output_tokens;
                        total_cost += usage.cost_usd;
                    }
                    crate::cpu::agent_loop::CpuEvent::TurnComplete { usage } => {
                        total_input += usage.input_tokens;
                        total_output += usage.output_tokens;
                        total_cost += usage.cost_usd;
                        turns += 1;
                    }
                    crate::cpu::agent_loop::CpuEvent::ToolStart { .. } => {
                        tool_uses += 1;
                    }
                    crate::cpu::agent_loop::CpuEvent::ToolResult { output, .. } => {
                        last_tool_output = output;
                    }
                    crate::cpu::agent_loop::CpuEvent::TextComplete(text) => {
                        last_text = text;
                    }
                    crate::cpu::agent_loop::CpuEvent::Error(e) => {
                        stop_reason = format!("error: {e}");
                    }
                    _ => {}
                }
            }

            // Check if the last test.sh output indicates pass
            let resolved = last_tool_output.to_lowercase().contains("pass")
                || last_tool_output.contains("ok");

            let summary = if !last_text.is_empty() { last_text } else { String::new() };

            (total_input, total_output, total_cost, turns, tool_uses, stop_reason, resolved, summary)
        });

        // Run the agent loop
        let shared_messages = crate::cpu::agent_loop::SharedMessages::new(messages);
        let loop_result = crate::cpu::agent_loop::run_loop(
            driver.as_ref(),
            &config,
            &shared_messages,
            event_tx,
            Default::default(),
        ).await;

        // Clear the env var.
        // SAFETY: The agent loop has completed and all spawned tasks have joined;
        // no concurrent readers of OSTK_BENCH_CONTAINER remain.
        unsafe { std::env::remove_var("OSTK_BENCH_CONTAINER"); }

        // Collect metrics
        let (input_tokens, output_tokens, cost_usd, turns, tool_uses, stop_reason, resolved, summary) = metrics_handle.await
            .map_err(|e| format!("metrics task failed: {e}"))?;

        let stop = match &loop_result {
            Ok(()) => stop_reason,
            Err(e) => format!("error: {e}"),
        };

        Ok(BenchResult {
            resolved,
            turns,
            input_tokens,
            output_tokens,
            cost_usd,
            wall_clock_ms: 0, // filled in by caller
            tool_uses,
            stop_reason: stop,
            summary,
        })
    })
}

/// A parsed bench result from a JSON file.
/// →502: public for TUI bench pane
pub struct ScoreEntry {
    pub model: String,
    pub scenario: String,
    pub resolved: bool,
    pub turns: Option<u64>,
    pub tokens: Option<u64>,
    pub wall_clock_s: Option<f64>,
    pub cost_usd: Option<f64>,
    pub tool_uses: Option<u64>,
    pub summary: String,
}

/// Extract scenario name from filename by stripping the model prefix.
/// Handles both `model_scenario.json` and `model__scenario.json`.
#[allow(dead_code)] // used by bench result parsing, not yet wired
fn scenario_from_filename(filename: &str, model: &str) -> String {
    let stem = filename.strip_suffix(".json").unwrap_or(filename);
    // Try double-underscore first (claude-opus-4-6__csv-quoting)
    if let Some(rest) = stem.strip_prefix(&format!("{model}__")) {
        return rest.to_string();
    }
    // Then single underscore (gemini-2.0-flash_csv-quoting)
    if let Some(rest) = stem.strip_prefix(&format!("{model}_")) {
        return rest.to_string();
    }
    // Fallback: everything after first _ or __
    stem.to_string()
}

/// →502: public for TUI bench pane
pub fn load_results(root: &Path) -> Vec<ScoreEntry> {
    let results_dir = resolve_needle_bench_root()
        .map(|nb| nb.join("runs"))
        .unwrap_or_else(|_| root.join("bench/results"));
    if !results_dir.is_dir() {
        return Vec::new();
    }

    let mut entries = Vec::new();

    // needle-bench structure: runs/<model>/<scenario>.score.json
    // Walk model subdirectories
    let mut model_dirs: Vec<_> = fs::read_dir(&results_dir)
        .ok()
        .into_iter()
        .flatten()
        .filter_map(|e| e.ok())
        .filter(|e| e.path().is_dir())
        .collect();
    model_dirs.sort_by_key(|e| e.file_name());

    for model_dir in model_dirs {
        let model_name = model_dir.file_name().to_string_lossy().into_owned();
        let model_path = model_dir.path();

        let mut score_files: Vec<_> = fs::read_dir(&model_path)
            .ok()
            .into_iter()
            .flatten()
            .filter_map(|e| e.ok())
            .filter(|e| {
                e.file_name().to_string_lossy().ends_with(".score.json")
            })
            .collect();
        score_files.sort_by_key(|e| e.file_name());

        for file in score_files {
            let path = file.path();
            let content = match fs::read_to_string(&path) {
                Ok(c) => c,
                Err(_) => continue,
            };
            let json: serde_json::Value = match serde_json::from_str(&content) {
                Ok(v) => v,
                Err(_) => continue,
            };

            let model = json.get("agent")
                .or_else(|| json.get("model"))
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
                .unwrap_or_else(|| model_name.clone());

            let scenario = json.get("benchmark")
                .or_else(|| json.get("scenario"))
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
                .unwrap_or_default();

            let resolved = json.get("resolved").and_then(|v| v.as_bool()).unwrap_or(false);

            let turns = json.get("turns_to_fix")
                .or_else(|| json.get("turns"))
                .and_then(|v| v.as_u64());

            let tokens = json.get("token_cost")
                .or_else(|| json.get("tokens"))
                .and_then(|v| v.as_u64());

            let wall_clock_s = json.get("wall_clock")
                .or_else(|| json.get("wall_clock_s"))
                .and_then(|v| v.as_f64());

            let cost_usd = json.get("estimated_cost_usd")
                .or_else(|| json.get("cost_usd"))
                .and_then(|v| v.as_f64());

            let tool_uses = json.get("tool_uses")
                .and_then(|v| v.as_u64());

            let summary = json.get("summary")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            entries.push(ScoreEntry {
                model,
                scenario,
                resolved,
                turns,
                tokens,
                wall_clock_s,
                cost_usd,
                tool_uses,
                summary,
            });
        }
    }

    entries
}

/// Format a token count with comma separators.
fn fmt_tokens(n: u64) -> String {
    let s = n.to_string();
    let mut result = String::new();
    for (i, ch) in s.chars().rev().enumerate() {
        if i > 0 && i % 3 == 0 {
            result.push(',');
        }
        result.push(ch);
    }
    result.chars().rev().collect()
}

/// Render the leaderboard from bench/results/.
fn run_score(root: &Path) -> Result<(), String> {
    let entries = load_results(root);
    if entries.is_empty() {
        println!("no results found in bench/results/");
        return Ok(());
    }

    // Group by model
    let mut by_model: HashMap<String, Vec<&ScoreEntry>> = HashMap::new();
    for entry in &entries {
        by_model.entry(entry.model.clone()).or_default().push(entry);
    }

    // Build model summaries for sorting
    struct ModelSummary<'a> {
        model: String,
        pass_count: usize,
        total: usize,
        pct: f64,
        avg_turns: f64,
        avg_tokens: f64,
        scenarios: Vec<&'a ScoreEntry>,
    }

    let mut summaries: Vec<ModelSummary> = by_model
        .into_iter()
        .map(|(model, scenarios)| {
            let total = scenarios.len();
            let pass_count = scenarios.iter().filter(|s| s.resolved).count();
            let pct = if total > 0 {
                (pass_count as f64 / total as f64) * 100.0
            } else {
                0.0
            };

            let turns_entries: Vec<u64> =
                scenarios.iter().filter_map(|s| s.turns).collect();
            let avg_turns = if turns_entries.is_empty() {
                0.0
            } else {
                turns_entries.iter().sum::<u64>() as f64 / turns_entries.len() as f64
            };

            let token_entries: Vec<u64> =
                scenarios.iter().filter_map(|s| s.tokens).collect();
            let avg_tokens = if token_entries.is_empty() {
                0.0
            } else {
                token_entries.iter().sum::<u64>() as f64 / token_entries.len() as f64
            };

            ModelSummary {
                model,
                pass_count,
                total,
                pct,
                avg_turns,
                avg_tokens,
                scenarios,
            }
        })
        .collect();

    // Sort models: by score % descending
    summaries.sort_by(|a, b| {
        b.pct
            .partial_cmp(&a.pct)
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    println!("ostk bench score");
    println!();

    for summary in &summaries {
        // Header line: model  pass/total  pct%  avg turns  avg tokens
        let turns_str = if summary.avg_turns > 0.0 {
            format!("avg {:.1}t", summary.avg_turns)
        } else {
            String::new()
        };
        let tokens_str = if summary.avg_tokens > 0.0 {
            format!("{}tok", fmt_tokens(summary.avg_tokens as u64))
        } else {
            String::new()
        };

        println!(
            "{:<25} {}/{}  {:.0}%  {}  {}",
            summary.model,
            summary.pass_count,
            summary.total,
            summary.pct,
            turns_str,
            tokens_str
        );

        // Sort scenarios: resolved desc, then turns asc
        let mut sorted_scenarios: Vec<&&ScoreEntry> = summary.scenarios.iter().collect();
        sorted_scenarios.sort_by(|a, b| {
            b.resolved
                .cmp(&a.resolved)
                .then_with(|| {
                    a.turns
                        .unwrap_or(u64::MAX)
                        .cmp(&b.turns.unwrap_or(u64::MAX))
                })
        });

        for entry in &sorted_scenarios {
            let verdict = if entry.resolved { "PASS" } else { "FAIL" };
            let turns_part = entry
                .turns
                .map(|t| format!("{:>3}t", t))
                .unwrap_or_else(|| "   -".to_string());
            let tokens_part = entry
                .tokens
                .map(|t| format!("{:>9}tok", fmt_tokens(t)))
                .unwrap_or_else(|| "         -".to_string());
            let cost_part = entry
                .cost_usd
                .filter(|c| *c > 0.0)
                .map(|c| format!("${:.4}", c))
                .unwrap_or_else(|| "     -".to_string());
            let tools_part = entry
                .tool_uses
                .map(|t| format!("{:>2}tools", t))
                .unwrap_or_else(|| "     -".to_string());
            let wall_part = entry
                .wall_clock_s
                .map(|s| format!("{:>4}s", s as u64))
                .unwrap_or_else(|| "   -".to_string());

            println!(
                "  {}  {:<25} {} {} {} {} {}",
                verdict, entry.scenario, turns_part, tokens_part, cost_part, tools_part, wall_part
            );
            if !entry.summary.is_empty() {
                let trunc: String = entry.summary.chars().take(80).collect();
                println!("       {trunc}");
            }
        }
        println!();
    }

    Ok(())
}

/// Parse --arm flag into list of BenchArm values.
fn parse_arms(arm_str: &str) -> Result<Vec<BenchArm>, String> {
    match arm_str {
        "native" => Ok(vec![BenchArm::Native]),
        "kernel" => Ok(vec![BenchArm::Kernel]),
        "both" => Ok(vec![BenchArm::Native, BenchArm::Kernel]),
        other => Err(format!("unknown arm '{other}'. Valid: native, kernel, both")),
    }
}

/// Entry point for `ostk bench`.
pub fn run(category: Option<&str>, list: bool, cargo_only: bool, model: &str, docker_only: bool, score: bool, arm_str: &str, all: bool, local: bool, driver: &str, keep: bool) -> Result<(), String> {
    let root = find_project_root()?;

    // --score: render leaderboard from needle-bench/runs/
    if score {
        return run_score(&root);
    }

    let scenarios = discover_scenarios(&root);
    let docker_scenarios = discover_docker_scenarios(&root);

    // --list: print all scenarios with status
    if list {
        println!("needle-test scenarios:");
        println!();
        for s in &scenarios {
            let status = s.status_label();
            let desc = if s.description.is_empty() {
                String::new()
            } else {
                format!(" -- {}", s.description)
            };
            println!("  {:<25} [{}]{}", s.name, status, desc);
        }
        println!();

        let runnable = scenarios.iter().filter(|s| s.runnable()).count();
        let designed = scenarios.len() - runnable;
        println!("{} runnable, {} designed, {} total", runnable, designed, scenarios.len());

        if !docker_scenarios.is_empty() {
            println!();
            println!("docker scenarios (--docker --model MODEL):");
            println!();
            for s in &docker_scenarios {
                let desc = if s.description.is_empty() {
                    String::new()
                } else {
                    format!(" -- {}", s.description)
                };
                println!("  {:<25} [docker]{}", s.name, desc);
            }
            println!();
            println!("{} docker scenarios", docker_scenarios.len());
        }

        // Also note cargo tests
        println!();
        println!("cargo tests: cargo test --test needle_tests (use --cargo-only)");

        return Ok(());
    }

    // --cargo-only: just run cargo tests
    if cargo_only {
        let passed = run_cargo_tests()?;

        // Audit
        let _ = append_audit(
            &root,
            &json!({
                "event": "bench.run",
                "scenario": "cargo-needle-tests",
                "result": if passed { "pass" } else { "fail" },
                "timestamp": now_iso()
            }),
        );

        if passed {
            return Ok(());
        } else {
            return Err("cargo needle_tests failed".into());
        }
    }

    let mut passed = 0usize;
    let mut failed = 0usize;
    let mut total_ms = 0u64;

    // Parse arms
    let arms = parse_arms(arm_str)?;

    // --docker or --all: run Docker scenarios
    if docker_only || all {
        let docker_to_run: Vec<&DockerScenario> = if let Some(cat) = category {
            match docker_scenarios.iter().find(|s| s.name == cat) {
                Some(s) => vec![s],
                None => return Err(format!("unknown docker scenario '{cat}'")),
            }
        } else {
            docker_scenarios.iter().collect()
        };

        if docker_to_run.is_empty() {
            println!("no docker scenarios found");
            return Ok(());
        }

        let arm_labels: Vec<&str> = arms.iter().map(|a| match a {
            BenchArm::Native => "native",
            BenchArm::Kernel => "kernel",
        }).collect();
        println!("ostk bench: {} scenario(s) x {} arm(s) [model={}, arms={}]",
            docker_to_run.len(), arms.len(), model, arm_labels.join(","));
        println!();

        for scenario in &docker_to_run {
            for &bench_arm in &arms {
                let arm_label = match bench_arm {
                    BenchArm::Native => "native",
                    BenchArm::Kernel => "kernel",
                };
                println!("--- {} [{arm_label}] ---", scenario.name);
                let (ok, duration_ms, stop_reason) = run_docker_scenario(&root, scenario, model, Some(bench_arm), local, driver, keep);
                total_ms += duration_ms;
                if ok { passed += 1; } else { failed += 1; }

                let mut entry = json!({
                    "event": "bench.docker",
                    "scenario": scenario.name,
                    "model": model,
                    "arm": arm_label,
                    "result": if ok { "pass" } else { "fail" },
                    "duration_ms": duration_ms,
                    "timestamp": now_iso()
                });
                if let Some(reason) = &stop_reason {
                    entry["stop_reason"] = json!(reason);
                }
                let _ = append_audit(&root, &entry);
                println!();
            }
        }

        println!("=== bench summary ===");
        println!("{}/{} passed, {}ms total [model={}, arms={}]",
            passed, passed + failed, total_ms, model, arm_labels.join(","));

        return if failed > 0 {
            Err(format!("{} scenario(s) failed", failed))
        } else {
            Ok(())
        };
    }

    // Default / category: run script-based scenario tests
    let to_run: Vec<&Scenario> = if let Some(cat) = category {
        let found = scenarios.iter().find(|s| s.name == cat);
        match found {
            Some(s) if s.runnable() => vec![s],
            Some(s) => {
                return Err(format!(
                    "scenario '{}' is designed but not runnable (missing setup.sh or verify.sh)",
                    s.name
                ));
            }
            None => {
                // Check docker scenarios as fallback
                if let Some(ds) = docker_scenarios.iter().find(|s| s.name == cat) {
                    println!("--- {} [docker] ---", ds.name);
                    let (ok, duration_ms, stop_reason) = run_docker_scenario(&root, ds, model, None, local, driver, keep);
                    let mut entry = json!({
                        "event": "bench.docker",
                        "scenario": ds.name,
                        "model": model,
                        "result": if ok { "pass" } else { "fail" },
                        "duration_ms": duration_ms,
                        "timestamp": now_iso()
                    });
                    if let Some(reason) = &stop_reason {
                        entry["stop_reason"] = json!(reason);
                    }
                    let _ = append_audit(&root, &entry);
                    return if ok { Ok(()) } else { Err(format!("docker scenario '{}' failed", cat)) };
                }
                return Err(format!(
                    "unknown scenario '{}'. Run `ostk bench --list` to see available scenarios.",
                    cat
                ));
            }
        }
    } else {
        scenarios.iter().filter(|s| s.runnable()).collect()
    };

    if to_run.is_empty() {
        println!("no runnable scenarios found");
        return Ok(());
    }

    println!("ostk bench: {} scenario(s)", to_run.len());
    println!();

    for scenario in &to_run {
        println!("--- {} ---", scenario.name);
        let (ok, duration_ms) = run_scenario(scenario);
        total_ms += duration_ms;

        if ok {
            passed += 1;
        } else {
            failed += 1;
        }

        let _ = append_audit(
            &root,
            &json!({
                "event": "bench.run",
                "scenario": scenario.name,
                "result": if ok { "pass" } else { "fail" },
                "duration_ms": duration_ms,
                "timestamp": now_iso()
            }),
        );

        println!();
    }

    // Summary
    println!("=== bench summary ===");
    println!("{}/{} passed, {}ms total", passed, passed + failed, total_ms);

    if let Err(e) = crate::verify_os_integrity(&root) {
        eprintln!("warning: OS integrity check failed: {e}");
    }

    if failed > 0 {
        Err(format!("{} scenario(s) failed", failed))
    } else {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── BenchArm ──────────────────────────────────────────────────

    #[test]
    fn parse_arms_native() {
        let arms = parse_arms("native").unwrap();
        assert_eq!(arms, vec![BenchArm::Native]);
    }

    #[test]
    fn parse_arms_kernel() {
        let arms = parse_arms("kernel").unwrap();
        assert_eq!(arms, vec![BenchArm::Kernel]);
    }

    #[test]
    fn parse_arms_both() {
        let arms = parse_arms("both").unwrap();
        assert_eq!(arms, vec![BenchArm::Native, BenchArm::Kernel]);
    }

    #[test]
    fn parse_arms_invalid() {
        assert!(parse_arms("silent").is_err());
        assert!(parse_arms("bare").is_err());
        assert!(parse_arms("xyz").is_err());
    }

    // ── Native harness detection ──────────────────────────────────

    #[test]
    fn detect_claude_harness() {
        let h = detect_native_harness("claude-opus-4-6").unwrap();
        assert_eq!(h.command, "claude");
        assert_eq!(h.env_key, "ANTHROPIC_API_KEY");
    }

    #[test]
    fn detect_gemini_harness() {
        let h = detect_native_harness("gemini-2.5-pro").unwrap();
        assert_eq!(h.command, "gemini");
        assert_eq!(h.env_key, "GEMINI_API_KEY");
    }

    #[test]
    fn detect_codex_harness() {
        let h = detect_native_harness("gpt-4.1").unwrap();
        assert_eq!(h.command, "codex");
        assert_eq!(h.env_key, "OPENAI_API_KEY");
    }

    #[test]
    fn detect_codex_o3() {
        let h = detect_native_harness("o3-pro").unwrap();
        assert_eq!(h.command, "codex");
    }

    #[test]
    fn detect_codex_o4() {
        let h = detect_native_harness("o4-mini").unwrap();
        assert_eq!(h.command, "codex");
    }

    #[test]
    fn detect_no_harness_grok() {
        // Grok CLI is broken; grok models route through OpenCode fallback
        assert!(detect_native_harness("grok-4-fast").is_none());
    }

    #[test]
    fn detect_vibe_mistral() {
        let h = detect_native_harness("mistral-large").unwrap();
        assert_eq!(h.command, "vibe");
        assert_eq!(h.env_key, "MISTRAL_API_KEY");
    }

    #[test]
    fn detect_vibe_devstral() {
        let h = detect_native_harness("devstral-small-2").unwrap();
        assert_eq!(h.command, "vibe");
    }

    #[test]
    fn detect_kimi_harness() {
        let h = detect_native_harness("kimi-k2.5").unwrap();
        assert_eq!(h.command, "kimi");
        assert_eq!(h.env_key, "KIMI_API_KEY");
    }

    #[test]
    fn detect_kimi_moonshot() {
        let h = detect_native_harness("moonshot-v1-128k").unwrap();
        assert_eq!(h.command, "kimi");
    }

    #[test]
    fn detect_no_harness_deepseek() {
        assert!(detect_native_harness("deepseek-chat").is_none());
    }

    #[test]
    fn detect_no_harness_qwen() {
        assert!(detect_native_harness("qwen3-coder-plus").is_none());
    }

    #[test]
    fn detect_no_harness_llama() {
        assert!(detect_native_harness("llama-4-maverick").is_none());
    }

    // ── OpenCode model mapping ───────────────────────────────────

    #[test]
    fn opencode_model_deepseek() {
        assert_eq!(opencode_model("deepseek-chat"), "deepseek/deepseek-chat");
        assert_eq!(opencode_model("deepseek-r1"), "deepseek/deepseek-reasoner");
        assert_eq!(opencode_model("deepseek-r1-0528"), "deepseek/deepseek-r1-0528");
        assert_eq!(opencode_model("deepseek-v3.2"), "deepseek/deepseek-v3.2");
    }

    #[test]
    fn opencode_model_llama() {
        assert_eq!(opencode_model("llama-4-maverick"), "groq/llama-4-maverick");
    }

    #[test]
    fn opencode_model_gpt() {
        assert_eq!(opencode_model("gpt-4.1"), "openai/gpt-4.1");
    }

    #[test]
    fn opencode_model_o3() {
        assert_eq!(opencode_model("o3-pro"), "openai/o3-pro");
    }

    #[test]
    fn opencode_model_grok() {
        assert_eq!(opencode_model("grok-4-fast"), "xai/grok-4-fast");
    }

    #[test]
    fn opencode_model_ollama_passthrough() {
        assert_eq!(opencode_model("ollama/qwen3"), "ollama/qwen3");
    }

    #[test]
    fn opencode_model_passthrough() {
        assert_eq!(opencode_model("some-unknown-model"), "some-unknown-model");
    }

    // ── Local model detection ────────────────────────────────────

    #[test]
    fn is_local_ollama() {
        assert!(is_local_model("ollama/qwen3"));
        assert!(is_local_model("ollama/llama4"));
        assert!(is_local_model("local/my-model"));
    }

    #[test]
    fn is_not_local() {
        assert!(!is_local_model("claude-sonnet-4-6"));
        assert!(!is_local_model("grok-4-fast"));
        assert!(!is_local_model("deepseek-chat"));
    }

    // ── Score file naming ─────────────────────────────────────────

    #[test]
    fn score_arm_suffix_native() {
        // Verify the arm label strings used in score paths
        let label = match Some(BenchArm::Native) {
            Some(BenchArm::Native) => "native",
            Some(BenchArm::Kernel) => "kernel",
            None => "default",
        };
        assert_eq!(label, "native");
    }

    #[test]
    fn score_arm_suffix_kernel() {
        let label = match Some(BenchArm::Kernel) {
            Some(BenchArm::Native) => "native",
            Some(BenchArm::Kernel) => "kernel",
            None => "default",
        };
        assert_eq!(label, "kernel");
    }

    // ── Token formatting ──────────────────────────────────────────

    #[test]
    fn fmt_tokens_small() {
        assert_eq!(fmt_tokens(42), "42");
        assert_eq!(fmt_tokens(999), "999");
    }

    #[test]
    fn fmt_tokens_thousands() {
        assert_eq!(fmt_tokens(1_000), "1,000");
        assert_eq!(fmt_tokens(45_200), "45,200");
    }

    #[test]
    fn fmt_tokens_millions() {
        assert_eq!(fmt_tokens(1_000_000), "1,000,000");
    }
}
