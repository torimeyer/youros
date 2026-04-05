//! Host OS identity discovery.
//!
//! Pulls username, full name, git config, and GPG keys from the local system
//! to initialize the HUMANFILE.

use std::process::Command;

#[derive(Debug, Default, Clone)]
pub struct HostIdentity {
    pub username: String,
    pub full_name: String,
    pub git_name: String,
    pub git_email: String,
    pub gpg_key: Option<String>,
}

pub fn discover() -> HostIdentity {
    let mut id = HostIdentity {
        username: std::env::var("USER")
            .or_else(|_| std::env::var("USERNAME"))
            .unwrap_or_else(|_| "unknown".to_string()),
        ..Default::default()
    };

    // 2. Full Name
    #[cfg(target_os = "macos")]
    {
        if let Ok(out) = Command::new("id").arg("-F").output() {
            id.full_name = String::from_utf8_lossy(&out.stdout).trim().to_string();
        }
    }
    #[cfg(target_os = "linux")]
    {
        if let Ok(out) = Command::new("getent").args(["passwd", &id.username]).output() {
            let s = String::from_utf8_lossy(&out.stdout);
            if let Some(gecos) = s.split(':').nth(4) {
                id.full_name = gecos.split(',').next().unwrap_or("").trim().to_string();
            }
        }
    }

    // 3. Git config
    if let Ok(out) = Command::new("git").args(["config", "user.name"]).output() {
        id.git_name = String::from_utf8_lossy(&out.stdout).trim().to_string();
    }
    if let Ok(out) = Command::new("git").args(["config", "user.email"]).output() {
        id.git_email = String::from_utf8_lossy(&out.stdout).trim().to_string();
    }

    // 4. GPG Key
    // Try to find the most recent secret key
    if let Ok(out) = Command::new("gpg")
        .args(["--list-secret-keys", "--keyid-format", "LONG", "--with-colons"])
        .output()
    {
        let s = String::from_utf8_lossy(&out.stdout);
        for line in s.lines() {
            if line.starts_with("sec:")
                && let Some(fp) = line.split(':').nth(4) {
                    id.gpg_key = Some(fp.to_string());
                    break;
                }
        }
    }

    id
}
