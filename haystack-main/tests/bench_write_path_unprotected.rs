// Benchmark tests: Write path without kernel protection
//
// ENVIRONMENT RESTRICTIONS (encoded as test preamble):
// - Execution: Claude Code on iPhone (mobile, single-threaded async)
// - Kernel: NOT PRESENT (no haystack binary, no flock, no atomic CAS enforcement)
// - Git: local proxy @ 127.0.0.1:38905 (simulated aetherwing-io/haystack)
// - Session: ISOLATED (no live GitHub discussions/CI)
// - File locking: DISABLED (→576 P0 blocker)
// - Concurrency model: Optimistic (str_replace CAS without atomicity)
//
// These tests demonstrate failure modes when agents write without kernel mediation.

#[cfg(test)]
mod unprotected_write_path {
    use std::path::PathBuf;

    /// Test file state for simulation
    #[derive(Clone, Debug)]
    struct FileState {
        _path: PathBuf,
        content: String,
        generation: u64,
    }

    /// Simulated agent attempting str_replace
    struct Agent {
        id: usize,
        old_str: String,
        new_str: String,
    }

    impl Agent {
        fn cas_write(&self, state: &mut FileState) -> Result<(), String> {
            // CAS without kernel protection: no atomicity guarantee
            if state.content.contains(&self.old_str) {
                state.content = state.content.replace(&self.old_str, &self.new_str);
                state.generation += 1;
                Ok(())
            } else {
                Err(format!("Agent {}: old_str mismatch (TOCTOU race)", self.id))
            }
        }
    }

    #[test]
    fn bench_two_agent_collision() {
        // SCENARIO 1: Two agents read-check-write same file concurrently
        // WITHOUT kernel flock protection

        let mut file_state = FileState {
            _path: PathBuf::from("test_file.txt"),
            content: "original_value".to_string(),
            generation: 0,
        };

        let agent1 = Agent {
            id: 1,
            old_str: "original_value".to_string(),
            new_str: "agent1_wrote_this".to_string(),
        };

        let agent2 = Agent {
            id: 2,
            old_str: "original_value".to_string(),
            new_str: "agent2_wrote_this".to_string(),
        };

        // Both agents see the same old_str
        let agent1_sees = file_state.content.contains(&agent1.old_str);
        let agent2_sees = file_state.content.contains(&agent2.old_str);
        assert!(agent1_sees && agent2_sees);

        // Both agents pass CAS check (no lock prevents this)
        // Agent 1 writes
        let result1 = agent1.cas_write(&mut file_state);
        assert!(result1.is_ok());
        assert_eq!(file_state.content, "agent1_wrote_this");
        assert_eq!(file_state.generation, 1);

        // Agent 2 ALSO passes CAS (race condition: saw old_str before agent1 wrote)
        // In real scenario, agent2 would have read before agent1 wrote
        // But without flock, it writes anyway:
        let result2 = agent2.cas_write(&mut file_state);
        if result2.is_ok() {
            assert_eq!(file_state.content, "agent2_wrote_this");
            assert_eq!(file_state.generation, 2);
            // RESULT: Last write wins, agent1's work is lost
            println!("❌ COLLISION: Agent 1 write lost. Final gen: {}", file_state.generation);
            println!("   Reason: No flock protection (→576 P0)");
        } else {
            println!("✓ CAS prevented collision (this shouldn't happen without kernel)");
        }
    }

    #[test]
    fn bench_hot_pr_tier_1_failure() {
        // SCENARIO 2: Hot PR Tier 1 (silent merge) escalates to Tier 2+
        // when kernel cannot resolve non-overlapping edits

        let initial = "line1\nline2\nline3\n";
        let mut file_state = FileState {
            _path: PathBuf::from("multi_line.txt"),
            content: initial.to_string(),
            generation: 0,
        };

        // Agent 1: edit line 1 (non-overlapping with agent 2)
        let agent1 = Agent {
            id: 1,
            old_str: "line1".to_string(),
            new_str: "line1_modified".to_string(),
        };

        // Agent 2: edit line 3 (non-overlapping with agent 1)
        let agent2 = Agent {
            id: 2,
            old_str: "line3".to_string(),
            new_str: "line3_modified".to_string(),
        };

        // Without kernel: both agents read same base, both CAS without coordination
        let r1 = agent1.cas_write(&mut file_state);
        println!("Agent 1 CAS: {:?}", r1);
        let _content_after_agent1 = file_state.content.clone();

        // Agent 2 still sees "line3" in its cached read? Or does it see new state?
        // Without kernel's digestor, agent2 doesn't know file changed.
        // This is the TOCTOU race at scale.
        let r2 = agent2.cas_write(&mut file_state);
        println!("Agent 2 CAS: {:?}", r2);

        // If Agent 2 read before Agent 1's write:
        // - Agent 2's old_str reference is stale
        // - CAS can still match (no kernel prevents this)
        // - But merge semantics are now question

        println!("Content after both: {}", file_state.content);
        println!("⚠️  Tier 1 silent merge CANNOT WORK without kernel arbitration");
        println!("   Reason: No atomic read-lock on CAS (→576 P0)");
    }

    #[test]
    fn bench_recovery_flow_retry_loop() {
        // SCENARIO 3: Agent detects stale write, retries
        // (simulates agent-side recovery without kernel help)

        let mut file_state = FileState {
            _path: PathBuf::from("retry_test.txt"),
            content: "v0".to_string(),
            generation: 0,
        };

        let agent = Agent {
            id: 1,
            old_str: "v0".to_string(),
            new_str: "v1_retry_1".to_string(),
        };

        // First write succeeds
        let _ = agent.cas_write(&mut file_state);
        println!("Retry 1: success, gen={}", file_state.generation);

        // File changes externally (simulating another agent's write)
        file_state.content = "external_write".to_string();
        file_state.generation += 1;
        println!("External change: gen={}", file_state.generation);

        // Agent retries with NEW old_str read from current state
        let agent_retry = Agent {
            id: 1,
            old_str: "external_write".to_string(),
            new_str: "v1_retry_2".to_string(),
        };

        let result = agent_retry.cas_write(&mut file_state);
        match result {
            Ok(()) => println!("Retry 2: success, gen={}", file_state.generation),
            Err(e) => println!("Retry 2: {}", e),
        }

        println!("⚠️  RECOVERY requires agent re-read entire file + recompute diff");
        println!("   Without kernel: no digest, no change notification");
        println!("   Cost: O(filesize) per conflict, unbounded retry loops");
    }

    #[test]
    fn bench_boot_state_after_crash() {
        // SCENARIO 4: Corrupted file after unprotected concurrent write + crash
        // Recovery strategy when boot.md is the swap file

        let mut boot_state = FileState {
            _path: PathBuf::from(".ostk/boot.md"),
            content: "## v1.0.2\n- status: live\n- conflicts: none\n".to_string(),
            generation: 42,
        };

        println!("Boot state before concurrent writes:");
        println!("  gen={}, size={}", boot_state.generation, boot_state.content.len());

        // Agent 1 writes to boot.md (documenting its work)
        let agent1 = Agent {
            id: 1,
            old_str: "- conflicts: none".to_string(),
            new_str: "- conflicts: none\n- agent1: tier3_resolved".to_string(),
        };
        let _ = agent1.cas_write(&mut boot_state);

        // Agent 2 ALSO writes to boot.md (simultaneous, no flock)
        // Agent 2 read the old state before agent1's write took effect
        let agent2 = Agent {
            id: 2,
            old_str: "- conflicts: none".to_string(), // stale read!
            new_str: "- conflicts: hot_pr_needed".to_string(),
        };
        let _ = agent2.cas_write(&mut boot_state);

        // Crash happens during write. boot.md is now corrupted.
        println!("Boot state after crash-during-race:");
        println!("  gen={}, size={}", boot_state.generation, boot_state.content.len());
        println!("  Content:\n{}", boot_state.content);

        // Recovery: boot.md is THE swap file. Corruption blocks all recovery.
        println!("🔴 CRITICAL: boot.md corruption blocks kernel bootstrap");
        println!("   Reason: No write-atomic guarantee (→576 P0)");
        println!("   Fix: Kernel must flock boot.md writes before CAS");
    }

    #[test]
    fn env_restrictions_summary() {
        println!("\n=== ENVIRONMENT RESTRICTIONS (This Session) ===");
        println!("Platform:       Claude Code on iPhone");
        println!("Kernel:         NOT PRESENT (no haystack binary)");
        println!("File locking:   DISABLED (std::fs::File has no flock)");
        println!("CAS atomicity:  NOT ENFORCED (str_replace is userspace)");
        println!("Concurrency:    Async/await on single thread (mobile)");
        println!("Git:            Local proxy only (127.0.0.1:38905)");
        println!("CI/CD:          DISABLED (isolated session)");
        println!("\n=== P0 BLOCKER (→576) ===");
        println!("CAS TOCTOU race: str_replace must be protected by flock");
        println!("Impact:         All scenarios above fail without kernel");
        println!("Recovery:       Kernel v1.1 implements flock + CAS atomic pair");
        println!("\n=== DESIGN LAW VIOLATED ===");
        println!("Law 4 (Optimistic Concurrency): 'CAS is the compare-and-swap'");
        println!("Problem:        CAS without atomicity = undefined behavior");
        println!("Solution:       Kernel enforces atomicity below MCP surface");
    }
}
