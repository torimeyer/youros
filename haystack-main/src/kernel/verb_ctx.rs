//! Execution context for kernel verbs (→1157).
//!
//! `VerbCtx` is the calling convention for internalized verbs. It carries
//! the project root, input arguments, and an output buffer. Commands write
//! to it via `std::fmt::Write` (standard `write!`/`writeln!` macros).
//!
//! This replaces the CLI pattern where each command finds its own root,
//! prints to stdout, and returns `Result<(), String>`.
//!
//! # Usage
//!
//! ```ignore
//! use std::fmt::Write;
//! use crate::kernel::verb_ctx::VerbCtx;
//!
//! pub fn run(ctx: &mut VerbCtx) -> Result<(), String> {
//!     writeln!(ctx, "hello from verb").unwrap();
//!     Ok(())
//! }
//! ```

use std::fmt;
use std::path::{Path, PathBuf};

use serde_json::Value;

/// Execution context for a kernel verb invocation.
///
/// Implements `fmt::Write` so commands use `write!`/`writeln!` macros —
/// output goes to an internal buffer regardless of whether the caller
/// is the CLI (prints to stdout) or the agent loop (returns as tool result).
pub struct VerbCtx<'a> {
    /// Project root (parent of .ostk/).
    pub root: &'a Path,
    /// Tool call arguments (JSON object from MCP).
    pub input: &'a Value,
    buf: String,
}

impl<'a> VerbCtx<'a> {
    pub fn new(root: &'a Path, input: &'a Value) -> Self {
        Self {
            root,
            input,
            buf: String::new(),
        }
    }

    /// Path to the .ostk/ state directory.
    pub fn ostk_dir(&self) -> PathBuf {
        crate::state_dir(self.root)
    }

    /// Consume the context and return the accumulated output.
    pub fn into_output(self) -> String {
        self.buf
    }
}

impl fmt::Write for VerbCtx<'_> {
    fn write_str(&mut self, s: &str) -> fmt::Result {
        self.buf.push_str(s);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fmt::Write;

    #[test]
    fn test_verb_ctx_write() {
        let root = Path::new("/tmp/test");
        let input = serde_json::json!({});
        let mut ctx = VerbCtx::new(root, &input);

        writeln!(ctx, "hello {}", "world").unwrap();
        writeln!(ctx, "line 2").unwrap();

        let output = ctx.into_output();
        assert_eq!(output, "hello world\nline 2\n");
    }

    #[test]
    fn test_verb_ctx_empty() {
        let root = Path::new("/tmp/test");
        let input = serde_json::json!({});
        let ctx = VerbCtx::new(root, &input);
        assert!(ctx.into_output().is_empty());
    }

    #[test]
    fn test_verb_ctx_ostk_dir() {
        let root = Path::new("/tmp/test");
        let input = serde_json::json!({});
        let ctx = VerbCtx::new(root, &input);
        assert_eq!(ctx.ostk_dir(), PathBuf::from("/tmp/test/.ostk"));
    }

    #[test]
    fn test_verb_ctx_input_access() {
        let root = Path::new("/tmp/test");
        let input = serde_json::json!({"query": "hello"});
        let ctx = VerbCtx::new(root, &input);
        assert_eq!(ctx.input["query"].as_str(), Some("hello"));
    }
}
