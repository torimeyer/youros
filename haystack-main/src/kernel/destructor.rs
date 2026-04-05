//! →649: Destructor detection for shell governance.
//!
//! Detects destructive shell commands before execution.
//! Applied when Agentfile has `LIMIT destructive_ops confirm|deny`.
//!
//! The real question is not "can we trust this agent?"
//! It's "what controls exist around destructive actions?"

/// Destructive operation policy from Agentfile LIMIT directive.
#[derive(Debug, Clone, PartialEq, Default)]
pub enum DestructiveOpsPolicy {
    /// Queue for operator confirmation with 60s countdown (default).
    #[default]
    Confirm,
    /// Block entirely — agent cannot run these commands.
    Deny,
    /// Pass through without check.
    Allow,
}

impl DestructiveOpsPolicy {
    #[allow(clippy::should_implement_trait)]
    pub fn from_str(s: &str) -> Option<Self> {
        match s.trim().to_lowercase().as_str() {
            "confirm" => Some(Self::Confirm),
            "deny" => Some(Self::Deny),
            "allow" => Some(Self::Allow),
            _ => None,
        }
    }
}

/// Destructive command patterns — each tuple is (category, pattern).
/// Pattern is checked as a substring of the normalized command.
const DESTRUCTIVE_PATTERNS: &[(&str, &str)] = &[
    // Infrastructure destroy
    ("infrastructure", "terraform destroy"),
    ("infrastructure", "pulumi destroy"),
    ("infrastructure", "cdk destroy"),
    ("infrastructure", "serverless remove"),
    // Database
    ("database", "drop table"),
    ("database", "drop database"),
    ("database", "drop schema"),
    ("database", "truncate table"),
    ("database", "truncate "),
    ("database", "db:drop"),
    ("database", "database:drop"),
    // Filesystem wipe
    ("filesystem", "rm -rf"),
    ("filesystem", "rm -fr"),
    ("filesystem", "rm -r /"),
    ("filesystem", "find / -delete"),
    ("filesystem", "find . -delete"),
    ("filesystem", "shred "),
    ("filesystem", "wipefs"),
    ("filesystem", "mkfs"),
    ("filesystem", "dd if="),
    // Container / cluster
    ("container", "docker system prune"),
    ("container", "docker volume prune"),
    ("container", "docker rm -f"),
    ("container", "docker rmi"),
    ("container", "kubectl delete namespace"),
    ("container", "kubectl delete ns"),
    ("container", "helm uninstall"),
    ("container", "helm delete"),
    // Cloud resources
    ("cloud", "aws s3 rm --recursive"),
    ("cloud", "aws s3 rb --force"),
    ("cloud", "gcloud compute instances delete"),
    ("cloud", "gcloud sql instances delete"),
    ("cloud", "az group delete"),
    ("cloud", "az vm delete"),
    // Git history rewrite
    ("git", "push --force origin main"),
    ("git", "push --force origin master"),
    ("git", "push -f origin main"),
    ("git", "push -f origin master"),
    ("git", "reset --hard"),
    ("git", "clean -fd"),
    ("git", "clean -fxd"),
    ("git", "branch -d"),
    // Package registry
    ("registry", "npm unpublish"),
    ("registry", "cargo yank"),
    ("registry", "pip yank"),
    ("registry", "yarn unpublish"),
    // Process termination (broad)
    ("process", "kill -9 1"),
    ("process", "kill -9 $("),
    ("process", "pkill -9"),
    ("process", "killall "),
];

/// Check if a command contains a destructive pattern.
/// Returns Some((category, matched_pattern)) if destructive, None otherwise.
pub fn detect_destructive(cmd: &str) -> Option<(&'static str, &'static str)> {
    let lower = cmd.to_lowercase();
    for (category, pattern) in DESTRUCTIVE_PATTERNS {
        if lower.contains(pattern) {
            return Some((category, pattern));
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_detect_terraform_destroy() {
        let cmd = "terraform destroy --auto-approve";
        let result = detect_destructive(cmd);
        assert!(result.is_some(), "terraform destroy should be detected");
        let (cat, _) = result.unwrap();
        assert_eq!(cat, "infrastructure");
    }

    #[test]
    fn test_detect_rm_rf() {
        let result = detect_destructive("rm -rf /var/app/data");
        assert!(result.is_some());
    }

    #[test]
    fn test_detect_drop_table() {
        let result = detect_destructive("psql -c 'DROP TABLE users'");
        assert!(result.is_some());
        assert_eq!(result.unwrap().0, "database");
    }

    #[test]
    fn test_detect_kubectl_delete_namespace() {
        let result = detect_destructive("kubectl delete namespace production");
        assert!(result.is_some());
        assert_eq!(result.unwrap().0, "container");
    }

    #[test]
    fn test_detect_git_force_push_main() {
        let result = detect_destructive("git push --force origin main");
        assert!(result.is_some());
        assert_eq!(result.unwrap().0, "git");
    }

    #[test]
    fn test_safe_command_not_detected() {
        assert!(detect_destructive("cargo test").is_none());
        assert!(detect_destructive("echo hello").is_none());
        assert!(detect_destructive("git status").is_none());
        assert!(detect_destructive("ls -la").is_none());
    }

    #[test]
    fn test_case_insensitive() {
        assert!(detect_destructive("TERRAFORM DESTROY --auto-approve").is_some());
        assert!(detect_destructive("DROP TABLE users").is_some());
    }

    #[test]
    fn test_detect_mkfs() {
        let result = detect_destructive("mkfs.ext4 /dev/sda1");
        assert!(result.is_some());
        assert_eq!(result.unwrap().0, "filesystem");
    }

    #[test]
    fn test_detect_dd() {
        let result = detect_destructive("dd if=/dev/zero of=/dev/sda bs=4M");
        assert!(result.is_some());
        assert_eq!(result.unwrap().0, "filesystem");
    }

    #[test]
    fn test_detect_git_branch_delete() {
        let result = detect_destructive("git branch -D feature-branch");
        assert!(result.is_some());
        assert_eq!(result.unwrap().0, "git");
    }

    #[test]
    fn test_detect_docker_rmi() {
        let result = detect_destructive("docker rmi my-image:latest");
        assert!(result.is_some());
        assert_eq!(result.unwrap().0, "container");
    }

    #[test]
    fn test_policy_from_str() {
        assert_eq!(DestructiveOpsPolicy::from_str("confirm"), Some(DestructiveOpsPolicy::Confirm));
        assert_eq!(DestructiveOpsPolicy::from_str("deny"), Some(DestructiveOpsPolicy::Deny));
        assert_eq!(DestructiveOpsPolicy::from_str("allow"), Some(DestructiveOpsPolicy::Allow));
        assert_eq!(DestructiveOpsPolicy::from_str("CONFIRM"), Some(DestructiveOpsPolicy::Confirm));
        assert_eq!(DestructiveOpsPolicy::from_str("invalid"), None);
    }

    // --- 779: rm -rf / detected as destructive ---
    #[test]
    fn test_detect_rm_rf_root() {
        let result = detect_destructive("rm -rf /");
        assert!(result.is_some(), "rm -rf / should be detected");
        let (cat, pattern) = result.unwrap();
        assert_eq!(cat, "filesystem");
        assert_eq!(pattern, "rm -rf");
    }

    // --- 779: git push --force detected as destructive ---
    #[test]
    fn test_detect_git_push_force() {
        // Force push to main
        let r1 = detect_destructive("git push --force origin main");
        assert!(r1.is_some(), "git push --force origin main should be detected");
        assert_eq!(r1.unwrap().0, "git");

        // Force push to master
        let r2 = detect_destructive("git push --force origin master");
        assert!(r2.is_some(), "git push --force origin master should be detected");
        assert_eq!(r2.unwrap().0, "git");

        // Short flag -f to main
        let r3 = detect_destructive("git push -f origin main");
        assert!(r3.is_some(), "git push -f origin main should be detected");
        assert_eq!(r3.unwrap().0, "git");
    }

    // --- 779: cargo test NOT detected as destructive ---
    #[test]
    fn test_cargo_test_not_destructive() {
        assert!(
            detect_destructive("cargo test").is_none(),
            "cargo test should NOT be detected as destructive"
        );
        assert!(
            detect_destructive("cargo test --lib").is_none(),
            "cargo test --lib should NOT be detected"
        );
        assert!(
            detect_destructive("cargo test -- --nocapture").is_none(),
            "cargo test with args should NOT be detected"
        );
    }

    // --- 779: git reset --hard detected as destructive ---
    #[test]
    fn test_detect_git_reset_hard() {
        let result = detect_destructive("git reset --hard HEAD~3");
        assert!(result.is_some(), "git reset --hard should be detected");
        let (cat, pattern) = result.unwrap();
        assert_eq!(cat, "git");
        assert_eq!(pattern, "reset --hard");

        // Without arguments
        let r2 = detect_destructive("git reset --hard");
        assert!(r2.is_some(), "git reset --hard (bare) should be detected");
        assert_eq!(r2.unwrap().0, "git");
    }

    /// Verify detect_destructive catches the three patterns from the issue:
    /// "rm -rf", "drop table", "docker rm -f" — each in a realistic
    /// compound command, and returns the correct category.
    #[test]
    fn test_detect_destructive_rm_rf_drop_table_docker_rm() {
        // "rm -rf" embedded in a longer pipeline
        let rm = detect_destructive("cd /app && rm -rf data/ && echo done");
        assert!(rm.is_some(), "rm -rf not detected");
        assert_eq!(rm.unwrap().0, "filesystem");

        // "drop table" inside a psql invocation (mixed case)
        let drop = detect_destructive("psql prod -c 'Drop Table sessions CASCADE;'");
        assert!(drop.is_some(), "drop table not detected");
        assert_eq!(drop.unwrap().0, "database");

        // "docker rm -f" removing a running container
        let docker = detect_destructive("docker rm -f my-container");
        assert!(docker.is_some(), "docker rm -f not detected");
        assert_eq!(docker.unwrap().0, "container");

        // Sanity: benign commands that superficially resemble destructive ones
        assert!(
            detect_destructive("echo 'rm -rf is dangerous'").is_some(),
            "pattern match is substring-based, even in echo"
        );
        assert!(
            detect_destructive("ls docker-rm-logs/").is_none(),
            "false positive on docker-rm substring"
        );
    }
}
