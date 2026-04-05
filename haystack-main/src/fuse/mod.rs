//! FUSE passthrough filesystem for ostk (→807).
//!
//! Full read/write passthrough that proxies to the real filesystem.
//! Inode table maps FUSE inodes ↔ real paths. Future: intercept
//! through kernel::file (CAS, gen table) and kernel::elision (304).
//! Gated behind `fuse` feature flag (requires macFUSE / libfuse).

#[cfg(feature = "fuse")]
mod passthrough;

#[cfg(feature = "fuse")]
pub use passthrough::OstkFs;

#[cfg(test)]
mod tests {
    #[test]
    fn ostkfs_compiles() {
        #[cfg(feature = "fuse")]
        {
            let fs = super::OstkFs::new(std::path::PathBuf::from("/tmp"));
            assert_eq!(fs.source, std::path::PathBuf::from("/tmp"));
        }
    }
}
