/// PTY allocation and management for ostk kernel.
///
/// PTY allocation (originated in mish, ostk's experimental predecessor). Minimal subset needed to:
/// - Spawn a child process in a pseudoterminal (forkpty)
/// - Read/write the PTY master
/// - Resize the PTY
/// - Wait for child exit
/// - Clean up on drop
use std::ffi::CString;
use std::os::unix::io::{AsFd, AsRawFd, OwnedFd};
use std::sync::atomic::{AtomicI32, Ordering};
use std::time::Instant;

use nix::fcntl::{FcntlArg, OFlag, fcntl};
use nix::libc;
use nix::pty::{ForkptyResult, Winsize, forkpty};
use nix::sys::signal::{Signal, kill};
use nix::sys::wait::{WaitPidFlag, WaitStatus, waitpid};
use nix::unistd::{Pid, read, write};

/// Sentinel value for "exit status not yet captured".
const STATUS_NOT_CAPTURED: i32 = -999;

/// Error type for PTY operations.
#[derive(Debug)]
pub enum PtyError {
    /// System call failed
    Nix(nix::Error),
    /// Invalid command
    InvalidCommand(String),
    /// Child process error
    ChildExecFailed,
    /// I/O error
    Io(std::io::Error),
    /// PTY slave closed (child exited) — EIO on master read
    ChildGone,
}

impl std::fmt::Display for PtyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PtyError::Nix(e) => write!(f, "PTY syscall error: {e}"),
            PtyError::InvalidCommand(s) => write!(f, "invalid command: {s}"),
            PtyError::ChildExecFailed => write!(f, "child exec failed"),
            PtyError::Io(e) => write!(f, "I/O error: {e}"),
            PtyError::ChildGone => write!(f, "child process exited (PTY slave closed)"),
        }
    }
}

impl std::error::Error for PtyError {}

impl From<nix::Error> for PtyError {
    fn from(e: nix::Error) -> Self {
        PtyError::Nix(e)
    }
}

impl From<std::io::Error> for PtyError {
    fn from(e: std::io::Error) -> Self {
        PtyError::Io(e)
    }
}

/// Exit information from a child process.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ExitStatus {
    /// Exit code (0 = success), or None if killed by signal
    pub code: Option<i32>,
    /// Signal that killed the process, if any
    pub signal: Option<i32>,
}

impl ExitStatus {
    pub fn success(&self) -> bool {
        self.code == Some(0)
    }
}

/// PTY capture — owns a child process running in a pseudoterminal.
///
/// `cached_status` uses AtomicI32 to avoid lock overhead and Cell unsafety:
///   -999 = not yet captured (sentinel)
///   0..=255 = normal exit code
///   -1..-128 = killed by signal (stored as -signal_number)
///   -500 = unknown exit (child reaped before we could capture)
pub struct PtyCapture {
    master_fd: OwnedFd,
    child_pid: Pid,
    start_time: Instant,
    /// Cached exit status — captured atomically on first waitpid success
    /// to prevent ECHILD from losing the real exit code.
    cached_status: AtomicI32,
}

impl std::fmt::Debug for PtyCapture {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("PtyCapture")
            .field("child_pid", &self.child_pid)
            .field("cached_status", &self.cached_status.load(Ordering::Relaxed))
            .finish()
    }
}

impl PtyCapture {
    /// Encode an ExitStatus into a single i32 for atomic storage.
    fn encode_status(status: &ExitStatus) -> i32 {
        if let Some(sig) = status.signal {
            -sig // signal kill: stored as negative signal number
        } else {
            status.code.unwrap_or(-500) // exit code, or -500 for unknown
        }
    }

    /// Decode an i32 from the atomic back into an ExitStatus.
    fn decode_status(raw: i32) -> Option<ExitStatus> {
        if raw == STATUS_NOT_CAPTURED {
            None
        } else if raw < 0 && raw > -500 {
            // Negative = killed by signal
            Some(ExitStatus { code: None, signal: Some(-raw) })
        } else if raw == -500 {
            Some(ExitStatus { code: Some(-1), signal: None })
        } else {
            Some(ExitStatus { code: Some(raw), signal: None })
        }
    }

    /// Store an exit status atomically. Only the first capture wins.
    fn cache_status(&self, status: &ExitStatus) {
        let encoded = Self::encode_status(status);
        // compare_exchange: only store if currently the sentinel
        let _ = self.cached_status.compare_exchange(
            STATUS_NOT_CAPTURED,
            encoded,
            Ordering::Release,
            Ordering::Relaxed,
        );
    }

    /// Read the cached exit status, if one has been captured.
    fn read_cached_status(&self) -> Option<ExitStatus> {
        Self::decode_status(self.cached_status.load(Ordering::Acquire))
    }

    /// Spawn a child process in a PTY.
    ///
    /// `command` is a slice where the first element is the program and the rest are arguments.
    pub fn spawn(command: &[String]) -> Result<Self, PtyError> {
        Self::spawn_in(command, None)
    }

    /// Spawn a child process in a PTY, optionally setting its working directory.
    ///
    /// →879: When `cwd` is Some, the child chdir's before exec.
    /// The kernel resolves this to project root so agents never need `cd`.
    pub fn spawn_in(command: &[String], cwd: Option<&std::path::Path>) -> Result<Self, PtyError> {
        if command.is_empty() {
            return Err(PtyError::InvalidCommand("empty command".to_string()));
        }

        // Pre-convert cwd to CString before fork (allocation not safe after fork).
        let cwd_c = cwd.map(|p| {
            CString::new(p.to_string_lossy().as_bytes().to_vec())
                .unwrap_or_else(|_| CString::new("/").unwrap())
        });

        let winsize = get_terminal_size();

        // Safety: we exec immediately in the child and only use async-signal-safe
        // operations before the exec.
        let fork_result = unsafe { forkpty(&winsize, None)? };

        match fork_result {
            ForkptyResult::Child => {
                // In child process: set up environment, then exec.
                // MUST use libc::setenv, NOT std::env::set_var (lock safety after fork).
                unsafe {
                    // →879: chdir to project root before exec
                    if let Some(ref dir) = cwd_c {
                        libc::chdir(dir.as_ptr());
                    }
                    let col = CString::new(winsize.ws_col.to_string()).unwrap();
                    let row = CString::new(winsize.ws_row.to_string()).unwrap();
                    let term = CString::new("xterm-256color").unwrap();
                    libc::setenv(c"COLUMNS".as_ptr(), col.as_ptr(), 1);
                    libc::setenv(c"LINES".as_ptr(), row.as_ptr(), 1);
                    libc::setenv(c"TERM".as_ptr(), term.as_ptr(), 1);

                    // Suppress pagers
                    let cat = CString::new("cat").unwrap();
                    libc::setenv(c"PAGER".as_ptr(), cat.as_ptr(), 1);
                    libc::setenv(c"GIT_PAGER".as_ptr(), cat.as_ptr(), 1);
                    libc::setenv(c"MANPAGER".as_ptr(), cat.as_ptr(), 1);

                    // Neutralize bat chrome
                    let never = CString::new("never").unwrap();
                    let plain = CString::new("plain").unwrap();
                    libc::setenv(c"BAT_PAGING".as_ptr(), never.as_ptr(), 1);
                    libc::setenv(c"BAT_STYLE".as_ptr(), plain.as_ptr(), 1);

                    // Suppress zsh PROMPT_SP
                    let empty = CString::new("").unwrap();
                    libc::setenv(c"PROMPT_EOL_MARK".as_ptr(), empty.as_ptr(), 1);
                }

                let program = CString::new(command[0].as_str())
                    .map_err(|_| PtyError::InvalidCommand(command[0].clone()))?;

                let args: Vec<CString> = command
                    .iter()
                    .map(|a| CString::new(a.as_str()).unwrap())
                    .collect();

                nix::unistd::execvp(&program, &args).map_err(PtyError::Nix)?;
                unreachable!()
            }
            ForkptyResult::Parent { child, master } => {
                // Set master FD to non-blocking
                let raw_fd = master.as_raw_fd();
                let flags = fcntl(raw_fd, FcntlArg::F_GETFL)?;
                let mut oflags = OFlag::from_bits_truncate(flags);
                oflags.insert(OFlag::O_NONBLOCK);
                fcntl(raw_fd, FcntlArg::F_SETFL(oflags))?;

                Ok(PtyCapture {
                    master_fd: master,
                    child_pid: child,
                    start_time: Instant::now(),
                    cached_status: AtomicI32::new(STATUS_NOT_CAPTURED),
                })
            }
        }
    }

    /// Read output bytes from the PTY (non-blocking).
    ///
    /// Returns `Ok(n)` with bytes read, `Ok(0)` if no data available yet,
    /// or `Err(PtyError::ChildGone)` if the PTY slave was closed (child exited).
    pub fn read_output(&self, buf: &mut [u8]) -> Result<usize, PtyError> {
        use nix::poll::{PollFd, PollFlags, PollTimeout, poll};

        let mut pfd = [PollFd::new(self.master_fd.as_fd(), PollFlags::POLLIN)];
        match poll(&mut pfd, PollTimeout::ZERO) {
            Ok(0) => return Ok(0),
            Err(nix::Error::EINTR) => return Ok(0),
            Err(e) => return Err(PtyError::Nix(e)),
            Ok(_) => {
                // Check for POLLHUP — slave side closed
                if let Some(revents) = pfd[0].revents()
                    && revents.contains(PollFlags::POLLHUP) && !revents.contains(PollFlags::POLLIN)
                    {
                        return Err(PtyError::ChildGone);
                    }
            }
        }

        match read(self.master_fd.as_raw_fd(), buf) {
            Ok(n) => Ok(n),
            Err(nix::Error::EAGAIN) => Ok(0),
            Err(nix::Error::EIO) => Err(PtyError::ChildGone),
            Err(e) => Err(PtyError::Nix(e)),
        }
    }

    /// Write to child's stdin via the PTY master.
    pub fn write_stdin(&self, buf: &[u8]) -> Result<usize, PtyError> {
        use nix::poll::{PollFd, PollFlags, PollTimeout, poll};

        if buf.is_empty() {
            return Ok(0);
        }

        const CHUNK_SIZE: usize = 4096;
        const MAX_RETRIES: usize = 3000;

        let mut total_written = 0;

        while total_written < buf.len() {
            let end = std::cmp::min(total_written + CHUNK_SIZE, buf.len());
            let chunk = &buf[total_written..end];
            let mut retries = 0;

            loop {
                match write(&self.master_fd, chunk) {
                    Ok(n) => {
                        total_written += n;
                        break;
                    }
                    Err(nix::Error::EAGAIN) => {
                        retries += 1;
                        if retries >= MAX_RETRIES {
                            return Err(PtyError::Io(std::io::Error::new(
                                std::io::ErrorKind::TimedOut,
                                format!(
                                    "PTY write backpressure timeout after {total_written}/{} bytes",
                                    buf.len()
                                ),
                            )));
                        }
                        let mut pfd = [PollFd::new(self.master_fd.as_fd(), PollFlags::POLLOUT)];
                        let _ = poll(&mut pfd, PollTimeout::from(10u16));
                    }
                    Err(e) => return Err(PtyError::Nix(e)),
                }
            }
        }

        Ok(total_written)
    }

    /// Wait for child to exit. Returns exit status.
    ///
    /// If the child was already reaped (ChildGone detected during read_output),
    /// returns the cached exit status instead of defaulting to 0 on ECHILD.
    pub fn wait(&self) -> Result<ExitStatus, PtyError> {
        loop {
            match waitpid(self.child_pid, Some(WaitPidFlag::WNOHANG)) {
                Ok(WaitStatus::Exited(_, code)) => {
                    let status = ExitStatus { code: Some(code), signal: None };
                    self.cache_status(&status);
                    return Ok(status);
                }
                Ok(WaitStatus::Signaled(_, sig, _)) => {
                    let status = ExitStatus { code: None, signal: Some(sig as i32) };
                    self.cache_status(&status);
                    return Ok(status);
                }
                Ok(WaitStatus::StillAlive) | Ok(_) => {
                    std::thread::sleep(std::time::Duration::from_millis(10));
                }
                Err(nix::Error::ECHILD) => {
                    // Child already reaped — return cached status if available,
                    // otherwise unknown exit (not 0, which hides failures).
                    return Ok(self.read_cached_status().unwrap_or(ExitStatus {
                        code: Some(-1),
                        signal: None,
                    }));
                }
                Err(e) => return Err(PtyError::Nix(e)),
            }
        }
    }

    /// Drain remaining bytes from the PTY master after child exits.
    pub fn drain(&self) -> Result<Vec<u8>, PtyError> {
        use nix::poll::{PollFd, PollFlags, PollTimeout, poll};

        let mut all = Vec::new();
        let mut buf = [0u8; 4096];
        let mut first = true;
        loop {
            let timeout = if first {
                PollTimeout::from(50u16)
            } else {
                PollTimeout::ZERO
            };
            first = false;
            let mut pfd = [PollFd::new(self.master_fd.as_fd(), PollFlags::POLLIN)];
            match poll(&mut pfd, timeout) {
                Ok(0) => break,
                Err(nix::Error::EINTR) => continue,
                Err(e) => return Err(PtyError::Nix(e)),
                Ok(_) => {}
            }

            match read(self.master_fd.as_raw_fd(), &mut buf) {
                Ok(0) => break,
                Ok(n) => all.extend_from_slice(&buf[..n]),
                Err(nix::Error::EAGAIN) => break,
                Err(nix::Error::EIO) => break,
                Err(e) => return Err(PtyError::Nix(e)),
            }
        }
        Ok(all)
    }

    /// Resize the PTY to new dimensions.
    pub fn resize(&self, cols: u16, rows: u16) -> Result<(), PtyError> {
        let ws = Winsize {
            ws_row: rows,
            ws_col: cols,
            ws_xpixel: 0,
            ws_ypixel: 0,
        };

        let ret = unsafe {
            libc::ioctl(
                self.master_fd.as_raw_fd(),
                libc::TIOCSWINSZ,
                &ws as *const Winsize,
            )
        };
        if ret == -1 {
            return Err(PtyError::Nix(nix::Error::last()));
        }

        kill(self.child_pid, Signal::SIGWINCH)?;
        Ok(())
    }

    /// Send a signal to the child process.
    pub fn signal(&self, sig: Signal) -> Result<(), PtyError> {
        kill(self.child_pid, sig)?;
        Ok(())
    }

    /// Get the child's PID.
    pub fn pid(&self) -> Pid {
        self.child_pid
    }

    /// Time elapsed since spawn.
    pub fn elapsed(&self) -> std::time::Duration {
        self.start_time.elapsed()
    }

    /// Check if the child process has exited (non-blocking).
    /// Returns `Some(ExitStatus)` if the child has exited, `None` if still running.
    pub fn try_reap(&self) -> Option<ExitStatus> {
        match waitpid(self.child_pid, Some(WaitPidFlag::WNOHANG)) {
            Ok(WaitStatus::Exited(_, code)) => {
                let status = ExitStatus { code: Some(code), signal: None };
                self.cache_status(&status);
                Some(status)
            }
            Ok(WaitStatus::Signaled(_, sig, _)) => {
                let status = ExitStatus { code: None, signal: Some(sig as i32) };
                self.cache_status(&status);
                Some(status)
            }
            Err(nix::Error::ECHILD) => {
                // Already reaped — return cached status or unknown
                Some(self.read_cached_status().unwrap_or(ExitStatus {
                    code: Some(-1),
                    signal: None,
                }))
            }
            _ => None,
        }
    }
}

impl Drop for PtyCapture {
    fn drop(&mut self) {
        // Try to reap the child — send SIGTERM first, then SIGKILL if needed.
        // NEVER use blocking waitpid(None) — this runs on async threads and must not block.
        if let Ok(WaitStatus::StillAlive) = waitpid(self.child_pid, Some(WaitPidFlag::WNOHANG)) {
            let _ = kill(self.child_pid, Signal::SIGTERM);
            std::thread::sleep(std::time::Duration::from_millis(50));
            if let Ok(WaitStatus::StillAlive) = waitpid(self.child_pid, Some(WaitPidFlag::WNOHANG)) {
                let _ = kill(self.child_pid, Signal::SIGKILL);
                // Non-blocking reap with a bounded retry instead of blocking forever.
                for _ in 0..10 {
                    std::thread::sleep(std::time::Duration::from_millis(10));
                    match waitpid(self.child_pid, Some(WaitPidFlag::WNOHANG)) {
                        Ok(WaitStatus::StillAlive) => continue,
                        _ => break,
                    }
                }
            }
        }
    }
}

/// Query real terminal dimensions, falling back to 80x24.
fn get_terminal_size() -> Winsize {
    let mut ws = Winsize {
        ws_row: 24,
        ws_col: 80,
        ws_xpixel: 0,
        ws_ypixel: 0,
    };

    unsafe {
        if libc::ioctl(libc::STDOUT_FILENO, libc::TIOCGWINSZ, &mut ws) == -1
            && libc::ioctl(libc::STDERR_FILENO, libc::TIOCGWINSZ, &mut ws) == -1
        {
            ws.ws_row = 24;
            ws.ws_col = 80;
        }
    }

    if ws.ws_row == 0 {
        ws.ws_row = 24;
    }
    if ws.ws_col == 0 {
        ws.ws_col = 80;
    }

    ws
}

/// Run a command in a PTY and capture all output until exit.
///
/// Returns (exit_status, output_bytes). This is the main entry point
/// for the MCP kernel shell tool.
pub fn run_command(command: &[String]) -> Result<(ExitStatus, Vec<u8>), PtyError> {
    run_command_in(command, None)
}

/// →879: Run a command in a PTY with an explicit working directory.
///
/// When `cwd` is Some, the child process chdir's to that path before exec.
/// The kernel sets this to the project root so agents never need `cd`.
pub fn run_command_in(command: &[String], cwd: Option<&std::path::Path>) -> Result<(ExitStatus, Vec<u8>), PtyError> {
    let pty = PtyCapture::spawn_in(command, cwd)?;

    let mut all_output = Vec::new();
    let mut buf = [0u8; 4096];
    let deadline = Instant::now() + std::time::Duration::from_secs(300);

    // →619: FCP Interceptor state
    let mut line_buffer = String::new();
    let root = crate::find_project_root().unwrap_or_else(|_| std::path::PathBuf::from("."));
    let hs_dir = crate::state_dir(&root);

    loop {
        if Instant::now() > deadline {
            let _ = pty.signal(Signal::SIGKILL);
            break;
        }

        match pty.read_output(&mut buf) {
            Ok(0) => {
                match waitpid(pty.child_pid, Some(WaitPidFlag::WNOHANG)) {
                    Ok(WaitStatus::Exited(_, code)) => {
                        let status = ExitStatus { code: Some(code), signal: None };
                        pty.cache_status(&status);
                        if let Ok(remaining) = pty.drain() {
                            all_output.extend_from_slice(&remaining);
                        }
                        break;
                    }
                    Ok(WaitStatus::Signaled(_, sig, _)) => {
                        let status = ExitStatus { code: None, signal: Some(sig as i32) };
                        pty.cache_status(&status);
                        if let Ok(remaining) = pty.drain() {
                            all_output.extend_from_slice(&remaining);
                        }
                        break;
                    }
                    _ => {}
                }
                std::thread::sleep(std::time::Duration::from_millis(10));
            }
            Ok(n) => {
                let chunk = &buf[..n];
                all_output.extend_from_slice(chunk);
                
                // →619: Intercept FCP signals in real-time
                if let Ok(s) = std::str::from_utf8(chunk) {
                    for c in s.chars() {
                        if c == '\n' || c == '\r' {
                            if !line_buffer.is_empty() {
                                let trimmed = line_buffer.trim();
                                if trimmed.starts_with(':') || trimmed.starts_with('→') || trimmed.starts_with(".?") {
                                    let input = trimmed.to_string();
                                    let hs_dir_clone = hs_dir.clone();
                                    // Spawn FCP execution in background to avoid blocking PTY pipe.
                                    // →752: Guard against missing tokio runtime — PTY runs in sync
                                    // context (e.g., tests, standalone CLI) where no reactor exists.
                                    if let Ok(handle) = tokio::runtime::Handle::try_current() {
                                        handle.spawn(async move {
                                            let _ = crate::fcp::driver::execute_fcp(&input, &hs_dir_clone).await;
                                        });
                                    } else {
                                        // No runtime available — run synchronously as a best-effort.
                                        let rt = tokio::runtime::Builder::new_current_thread()
                                            .enable_all()
                                            .build();
                                        if let Ok(rt) = rt {
                                            let _ = rt.block_on(crate::fcp::driver::execute_fcp(&input, &hs_dir_clone));
                                        }
                                    }
                                }
                                line_buffer.clear();
                            }
                        } else {
                            line_buffer.push(c);
                        }
                    }
                }
            },
            Err(PtyError::ChildGone) => {
                // Child exited — capture exit status NOW via waitpid before it's
                // lost to ECHILD. The PTY slave closing means the child is dead or
                // dying; waitpid must happen immediately.
                match waitpid(pty.child_pid, Some(WaitPidFlag::WNOHANG)) {
                    Ok(WaitStatus::Exited(_, code)) => {
                        let status = ExitStatus { code: Some(code), signal: None };
                        pty.cache_status(&status);
                    }
                    Ok(WaitStatus::Signaled(_, sig, _)) => {
                        let status = ExitStatus { code: None, signal: Some(sig as i32) };
                        pty.cache_status(&status);
                    }
                    Ok(WaitStatus::StillAlive) => {
                        // Child hasn't exited yet despite ChildGone — wait briefly
                        std::thread::sleep(std::time::Duration::from_millis(5));
                        let _ = pty.try_reap();
                    }
                    Err(nix::Error::ECHILD) => {
                        // Already reaped — cached_status is our only hope
                    }
                    _ => {}
                }
                // Drain any remaining buffered output
                if let Ok(remaining) = pty.drain() {
                    all_output.extend_from_slice(&remaining);
                }
                break;
            }
            Err(_) => break,
        }
    }

    let status = pty.wait()?;
    Ok((status, all_output))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::thread;
    use std::time::Duration;

    /// Helper: read all output from PTY until child exits or deadline.
    /// Caches exit status at every waitpid site so wait() never loses it.
    fn read_all(pty: &PtyCapture, timeout: Duration) -> Vec<u8> {
        let mut all_bytes = Vec::new();
        let mut buf = [0u8; 4096];
        let deadline = Instant::now() + timeout;

        loop {
            if Instant::now() > deadline {
                break;
            }
            match pty.read_output(&mut buf) {
                Ok(0) => {
                    match waitpid(pty.child_pid, Some(WaitPidFlag::WNOHANG)) {
                        Ok(WaitStatus::Exited(_, code)) => {
                            let status = ExitStatus { code: Some(code), signal: None };
                            pty.cache_status(&status);
                            if let Ok(remaining) = pty.drain() {
                                all_bytes.extend_from_slice(&remaining);
                            }
                            break;
                        }
                        Ok(WaitStatus::Signaled(_, sig, _)) => {
                            let status = ExitStatus { code: None, signal: Some(sig as i32) };
                            pty.cache_status(&status);
                            if let Ok(remaining) = pty.drain() {
                                all_bytes.extend_from_slice(&remaining);
                            }
                            break;
                        }
                        _ => {}
                    }
                    thread::sleep(Duration::from_millis(10));
                }
                Ok(n) => all_bytes.extend_from_slice(&buf[..n]),
                Err(PtyError::ChildGone) => {
                    // Capture exit status immediately before it's lost
                    let _ = pty.try_reap();
                    if let Ok(remaining) = pty.drain() {
                        all_bytes.extend_from_slice(&remaining);
                    }
                    break;
                }
                Err(_) => break,
            }
        }
        all_bytes
    }

    #[test]
    fn test_spawn_echo_capture_output() {
        let pty = PtyCapture::spawn(&[
            "/bin/sh".to_string(),
            "-c".to_string(),
            "echo hello_ostk".to_string(),
        ])
        .expect("spawn failed");

        let all_bytes = read_all(&pty, Duration::from_secs(5));
        let output = String::from_utf8_lossy(&all_bytes);

        assert!(
            output.contains("hello_ostk"),
            "expected 'hello_ostk' in output, got: {:?}",
            output
        );

        let status = pty.wait().expect("wait failed");
        assert!(status.success(), "expected exit code 0, got: {:?}", status);
    }

    #[test]
    fn test_empty_command() {
        let result = PtyCapture::spawn(&[]);
        assert!(result.is_err());
    }

    #[test]
    fn test_run_command() {
        let (status, output) = run_command(&[
            "/bin/sh".to_string(),
            "-c".to_string(),
            "echo kernel_test && exit 0".to_string(),
        ])
        .expect("run_command failed");

        let output_str = String::from_utf8_lossy(&output);
        assert!(
            output_str.contains("kernel_test"),
            "expected 'kernel_test' in output, got: {:?}",
            output_str
        );
        assert!(status.success());
    }

    #[test]
    fn test_nonzero_exit() {
        let pty = PtyCapture::spawn(&[
            "/bin/sh".to_string(),
            "-c".to_string(),
            "exit 42".to_string(),
        ])
        .expect("spawn failed");

        let status = pty.wait().expect("wait failed");
        assert_eq!(status.code, Some(42));
        assert!(!status.success());
    }

    /// `false` (the Unix command) always exits with code 1.
    /// This is the critical regression test: prior to the AtomicI32 fix,
    /// fast-exiting children would trigger ECHILD and default to exit 0.
    #[test]
    fn test_false_returns_exit_code_1() {
        let pty = PtyCapture::spawn(&[
            "/usr/bin/false".to_string(),
        ])
        .expect("spawn /usr/bin/false failed");

        let _output = read_all(&pty, Duration::from_secs(5));
        let status = pty.wait().expect("wait failed");

        assert_eq!(
            status.code, Some(1),
            "false must return exit code 1, got: {:?}",
            status
        );
        assert!(!status.success());
    }

    /// `false` via run_command (the kernel entry point) must also capture exit 1.
    #[test]
    fn test_run_command_false_exit_code() {
        let (status, _output) = run_command(&[
            "/usr/bin/false".to_string(),
        ])
        .expect("run_command failed");

        assert_eq!(
            status.code, Some(1),
            "run_command(false) must return exit code 1, got: {:?}",
            status
        );
        assert!(!status.success());
    }

    /// A signal-killed process must report the signal number, not exit code 0.
    #[test]
    fn test_signal_killed_reports_signal() {
        let pty = PtyCapture::spawn(&[
            "/bin/sh".to_string(),
            "-c".to_string(),
            // Sleep long enough for us to send SIGKILL, but not so long the test hangs
            "exec sleep 60".to_string(),
        ])
        .expect("spawn failed");

        // Give the child a moment to start
        thread::sleep(Duration::from_millis(50));

        // Send SIGKILL (signal 9)
        pty.signal(Signal::SIGKILL).expect("signal failed");

        let _output = read_all(&pty, Duration::from_secs(5));
        let status = pty.wait().expect("wait failed");

        assert_eq!(
            status.signal, Some(9),
            "SIGKILL-ed process must report signal 9, got: {:?}",
            status
        );
        assert!(status.code.is_none(), "signal-killed should have no exit code");
        assert!(!status.success());
    }

    /// run_command with a signal-killed child.
    #[test]
    fn test_run_command_signal_killed() {
        // Use a command that kills itself with SIGTERM (signal 15)
        let (status, _output) = run_command(&[
            "/bin/sh".to_string(),
            "-c".to_string(),
            "kill -TERM $$".to_string(),
        ])
        .expect("run_command failed");

        assert_eq!(
            status.signal, Some(Signal::SIGTERM as i32),
            "self-SIGTERM must report signal 15, got: {:?}",
            status
        );
        assert!(!status.success());
    }

    /// Encode/decode round-trip for exit codes and signals.
    #[test]
    fn test_status_encode_decode_roundtrip() {
        // Normal exit code
        let s0 = ExitStatus { code: Some(0), signal: None };
        assert_eq!(PtyCapture::decode_status(PtyCapture::encode_status(&s0)), Some(s0));

        let s1 = ExitStatus { code: Some(1), signal: None };
        assert_eq!(PtyCapture::decode_status(PtyCapture::encode_status(&s1)), Some(s1));

        let s42 = ExitStatus { code: Some(42), signal: None };
        assert_eq!(PtyCapture::decode_status(PtyCapture::encode_status(&s42)), Some(s42));

        let s127 = ExitStatus { code: Some(127), signal: None };
        assert_eq!(PtyCapture::decode_status(PtyCapture::encode_status(&s127)), Some(s127));

        // Signal kills
        let sig9 = ExitStatus { code: None, signal: Some(9) };
        assert_eq!(PtyCapture::decode_status(PtyCapture::encode_status(&sig9)), Some(sig9));

        let sig15 = ExitStatus { code: None, signal: Some(15) };
        assert_eq!(PtyCapture::decode_status(PtyCapture::encode_status(&sig15)), Some(sig15));

        // Sentinel
        assert_eq!(PtyCapture::decode_status(STATUS_NOT_CAPTURED), None);
    }
}
