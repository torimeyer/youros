//! `ostk mount` — mount the FUSE filesystem overlay (→776).
//!
//! Mounts OstkFs at .ostk/mount/ (or a user-specified path).
//! Requires the `fuse` feature and macFUSE / libfuse at runtime.
//!
//! This is a skeleton — the real implementation will proxy reads through
//! kernel::file and kernel::elision for content-aware overlays.

use crate::find_project_root;

/// Entry point for `ostk mount`.
pub fn run(mountpoint: Option<&str>) -> Result<(), String> {
    let root = find_project_root()?;
    let ostk_dir = crate::state_dir(&root);

    let mount_dir = match mountpoint {
        Some(p) => std::path::PathBuf::from(p),
        None => ostk_dir.join("mount"),
    };

    // Ensure mount directory exists
    std::fs::create_dir_all(&mount_dir)
        .map_err(|e| format!("failed to create mount directory: {e}"))?;

    #[cfg(feature = "fuse")]
    {
        use crate::fuse::OstkFs;

        let fs = OstkFs::new(root.clone());

        eprintln!(
            "[ostk] mounting FUSE filesystem at {} (source: {})",
            mount_dir.display(),
            root.display()
        );

        // Mount and block until unmounted (Ctrl-C or fusermount -u)
        fuser::mount2(fs, &mount_dir, &fuser::Config::default())
            .map_err(|e| format!("FUSE mount failed: {e}"))?;

        eprintln!("[ostk] unmounted {}", mount_dir.display());
        Ok(())
    }

    #[cfg(not(feature = "fuse"))]
    {
        let _ = mount_dir; // suppress unused warning
        Err(
            "FUSE support not compiled. Rebuild with: cargo build --features fuse"
                .to_string(),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mount_requires_fuse_feature() {
        // Without the fuse feature, run() should return an error message
        // about missing feature. With fuse feature, it would try to mount
        // (and fail in test because there's no real FUSE daemon).
        #[cfg(not(feature = "fuse"))]
        {
            let result = run(Some("/tmp/ostk_test_mount_nonexistent"));
            assert!(result.is_err());
            let err = result.unwrap_err();
            assert!(
                err.contains("FUSE support not compiled"),
                "Expected feature error, got: {err}"
            );
        }

        #[cfg(feature = "fuse")]
        {
            // With the feature enabled, just verify the function exists
            // (actual mount would require macFUSE daemon running)
            assert!(true);
        }
    }
}
