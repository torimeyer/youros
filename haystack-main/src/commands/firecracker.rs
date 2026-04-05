//! Host-side QEMU VM launcher for sealed agent boot.
//!
//! Proof-of-concept: launches qemu-system-aarch64 with a minimal Alpine rootfs,
//! virtiofs sharing of .ostk/, and vsock for boot handshake + identity assignment.
//!
//! Protocol (matches src/commands/vm_init.rs):
//!   - Host listens on vsock port 52 for boot-ok/boot-fail from guest
//!   - Host connects to guest vsock port 55 to send identity assignment
//!   - Guest mounts virtiofs tag "hay" at /hay

use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::Duration;

use crate::agentfile;

// ── vsock port constants (mirror vm_init.rs) ─────────────────────────────────

const VSOCK_BOOT_PORT: u32 = 52;
const VSOCK_IDENTITY_PORT: u32 = 55;

// ── QEMU binary ──────────────────────────────────────────────────────────────

const QEMU_BINARY: &str = "qemu-system-aarch64";

// ── configuration ────────────────────────────────────────────────────────────

/// Parsed kernel command-line parameters for the guest.
#[derive(Debug, Clone, PartialEq)]
pub struct KernelCmdline {
    pub cid: u32,
    pub host_vsock_port: u32,
    pub hay_mount: String,
}

impl KernelCmdline {
    /// Build the kernel cmdline string for QEMU -append.
    pub fn as_cmdline(&self) -> String {
        format!(
            "console=ttyAMA0 ostk.cid={} ostk.host_vsock_port={} ostk.hay_mount={}",
            self.cid, self.host_vsock_port, self.hay_mount
        )
    }

    /// Parse ostk.* parameters from a kernel cmdline string.
    pub fn parse(cmdline: &str) -> Result<Self, String> {
        let mut cid = None;
        let mut host_vsock_port = VSOCK_BOOT_PORT;
        let mut hay_mount = String::from("/hay");

        for token in cmdline.split_whitespace() {
            if let Some(v) = token.strip_prefix("ostk.cid=") {
                cid = v.parse::<u32>().ok();
            } else if let Some(v) = token.strip_prefix("ostk.host_vsock_port=") {
                if let Ok(p) = v.parse::<u32>() {
                    host_vsock_port = p;
                }
            } else if let Some(v) = token.strip_prefix("ostk.hay_mount=") {
                hay_mount = v.to_string();
            }
        }

        Ok(KernelCmdline {
            cid: cid.ok_or("missing ostk.cid in cmdline")?,
            host_vsock_port,
            hay_mount,
        })
    }
}

/// vsock address for QEMU — maps to a TCP port on the host side via vhost-user.
/// QEMU exposes guest vsock CID N as a Unix/TCP socket the host can connect to.
#[derive(Debug, Clone, PartialEq)]
pub struct VsockAddr {
    pub cid: u32,
    pub port: u32,
}

impl VsockAddr {
    pub fn new(cid: u32, port: u32) -> Self {
        Self { cid, port }
    }

    /// For QEMU on macOS (no real vsock), we emulate via TCP localhost ports.
    /// Convention: TCP port = cid * 1000 + vsock_port.
    /// This avoids collisions when multiple VMs run concurrently.
    pub fn to_tcp_port(&self) -> u16 {
        ((self.cid * 1000 + self.port) % 65535) as u16
    }
}

/// Identity assignment payload sent from host to guest on vsock port 55.
#[derive(Debug, Clone, PartialEq)]
pub struct IdentityAssignment {
    pub alias: String,
    pub cid: u32,
}

impl IdentityAssignment {
    pub fn to_json(&self) -> String {
        serde_json::json!({
            "alias": self.alias,
            "cid": self.cid,
            "assigned_by": "ostk-host",
        })
        .to_string()
    }
}

// ── rootfs ───────────────────────────────────────────────────────────────────

/// Return the cache path for the rootfs image.
/// Located at ~/.ostk/vm/rootfs-aarch64.ext4
pub fn rootfs_cache_path() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    Path::new(&home)
        .join(".ostk")
        .join("vm")
        .join("rootfs-aarch64.ext4")
}

/// Check if a cached rootfs exists.
fn rootfs_exists() -> bool {
    rootfs_cache_path().exists()
}

/// Build a minimal rootfs (Alpine base + ostk binary).
/// Copies the current ostk binary into the rootfs at /usr/bin/ostk
/// so the guest init can exec it directly.
/// This shells out to standard tools — proof of concept only.
fn build_rootfs() -> Result<PathBuf, String> {
    let path = rootfs_cache_path();
    let parent = path.parent().ok_or("no parent for rootfs path")?;
    fs::create_dir_all(parent).map_err(|e| format!("mkdir rootfs dir: {e}"))?;

    // Create a 256MB sparse ext4 image
    let status = Command::new("dd")
        .args([
            "if=/dev/zero",
            &format!("of={}", path.display()),
            "bs=1m",
            "count=256",
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|e| format!("dd: {e}"))?;

    if !status.success() {
        return Err("dd failed to create rootfs image".to_string());
    }

    // mkfs.ext4
    let status = Command::new("mkfs.ext4")
        .args(["-F", &path.display().to_string()])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|e| format!("mkfs.ext4: {e}"))?;

    if !status.success() {
        return Err("mkfs.ext4 failed".to_string());
    }

    // Copy the current ostk binary into the rootfs at /usr/bin/ostk.
    // Uses debugfs to inject files without requiring a mount (works on macOS).
    let current_exe = std::env::current_exe()
        .map_err(|e| format!("current_exe: {e}"))?;

    eprintln!(
        "[firecracker] injecting {} into rootfs",
        current_exe.display()
    );

    // Create /usr and /usr/bin directories, then copy the binary
    let debugfs_cmds = format!(
        "mkdir /usr\nmkdir /usr/bin\nwrite {} /usr/bin/ostk\n",
        current_exe.display()
    );

    let mut debugfs = Command::new("debugfs")
        .args(["-w", &path.display().to_string()])
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("debugfs: {e}"))?;

    if let Some(ref mut stdin) = debugfs.stdin {
        use std::io::Write;
        let _ = stdin.write_all(debugfs_cmds.as_bytes());
    }
    drop(debugfs.stdin.take());

    let output = debugfs.wait_with_output()
        .map_err(|e| format!("debugfs wait: {e}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("debugfs failed to inject binary: {stderr}"));
    }

    eprintln!(
        "[firecracker] built rootfs at {} (256MB, ostk binary injected)",
        path.display()
    );
    Ok(path)
}

// ── identity counter ─────────────────────────────────────────────────────────

/// Read and increment the identity_counter, returning the new value as a CID.
/// CIDs start at 3 (0 = hypervisor, 1 = reserved, 2 = host).
///
/// Uses kernel::identity for flock-protected counter access to avoid races.
fn next_cid(ostk_dir: &Path) -> Result<u32, String> {
    let identity = crate::kernel::identity::Identity::new(ostk_dir);
    let next = identity.next_counter_locked()?;

    // CID offset: identity 1 → CID 3, identity 2 → CID 4, etc.
    Ok((next + 2) as u32)
}

// ── QEMU launch ──────────────────────────────────────────────────────────────

/// Check that qemu-system-aarch64 is on PATH.
fn check_qemu() -> Result<PathBuf, String> {
    let output = Command::new("which")
        .arg(QEMU_BINARY)
        .output()
        .map_err(|e| format!("which {QEMU_BINARY}: {e}"))?;

    if !output.status.success() {
        return Err(format!(
            "{QEMU_BINARY} not found — install QEMU: brew install qemu"
        ));
    }

    let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
    Ok(PathBuf::from(path))
}

/// Build the QEMU command line for an aarch64 VM.
fn build_qemu_command(
    qemu_path: &Path,
    rootfs: &Path,
    ostk_dir: &Path,
    cmdline: &KernelCmdline,
) -> Command {
    let mut cmd = Command::new(qemu_path);

    cmd.args([
        "-M",
        "virt",
        "-cpu",
        "cortex-a72",
        "-m",
        "512M",
        "-nographic",
        // Root filesystem
        "-drive",
        &format!("file={},format=raw,if=virtio", rootfs.display()),
        // virtiofs for .ostk/ sharing
        "-chardev",
        &format!(
            "socket,id=char0,path={}/virtiofs.sock",
            ostk_dir.display()
        ),
        "-device",
        "vhost-user-fs-pci,chardev=char0,tag=hay",
        // vsock device — CID assigned from identity counter
        "-device",
        &format!("vhost-vsock-pci,guest-cid={}", cmdline.cid),
        // Kernel command line
        "-append",
        &cmdline.as_cmdline(),
    ]);

    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());

    cmd
}

/// Wait for boot-ok on the boot vsock port (emulated via TCP on macOS).
/// Returns the boot status JSON from the guest.
fn wait_for_boot(boot_addr: &VsockAddr, timeout: Duration) -> Result<String, String> {
    let tcp_port = boot_addr.to_tcp_port();
    let bind_addr = format!("127.0.0.1:{tcp_port}");

    let listener = TcpListener::bind(&bind_addr)
        .map_err(|e| format!("bind boot listener on {bind_addr}: {e}"))?;

    listener
        .set_nonblocking(false)
        .map_err(|e| format!("set_nonblocking: {e}"))?;

    // Set a timeout on accept via SO_RCVTIMEO
    let raw_fd = {
        #[cfg(unix)]
        {
            use std::os::unix::io::AsRawFd;
            listener.as_raw_fd()
        }
        #[cfg(not(unix))]
        {
            return Err("vsock emulation requires unix".to_string());
        }
    };

    let timeval = libc::timeval {
        tv_sec: timeout.as_secs() as libc::time_t,
        tv_usec: 0,
    };

    unsafe {
        libc::setsockopt(
            raw_fd,
            libc::SOL_SOCKET,
            libc::SO_RCVTIMEO,
            &timeval as *const _ as *const libc::c_void,
            std::mem::size_of::<libc::timeval>() as libc::socklen_t,
        );
    }

    let (mut stream, _addr) = listener
        .accept()
        .map_err(|e| format!("accept on boot port (timeout={timeout:?}): {e}"))?;

    let mut buf = String::new();
    stream
        .read_to_string(&mut buf)
        .map_err(|e| format!("read boot status: {e}"))?;

    // Parse and verify
    let v: serde_json::Value =
        serde_json::from_str(&buf).map_err(|e| format!("parse boot status: {e}"))?;

    match v.get("status").and_then(|s| s.as_str()) {
        Some("boot-ok") => {
            eprintln!("[firecracker] guest boot-ok (cid={})", v["cid"]);
            Ok(buf)
        }
        Some("boot-fail") => {
            let check = v.get("check").and_then(|c| c.as_u64()).unwrap_or(0);
            let error = v
                .get("error")
                .and_then(|e| e.as_str())
                .unwrap_or("unknown");
            Err(format!(
                "guest POST failed at check {check}: {error}"
            ))
        }
        other => Err(format!(
            "unexpected boot status: {:?}",
            other.unwrap_or("(missing)")
        )),
    }
}

/// Send identity assignment to the guest on vsock port 55.
fn send_identity(identity_addr: &VsockAddr, assignment: &IdentityAssignment) -> Result<(), String> {
    let tcp_port = identity_addr.to_tcp_port();
    let addr = format!("127.0.0.1:{tcp_port}");

    let mut stream = TcpStream::connect_timeout(
        &addr.parse().map_err(|e| format!("parse addr: {e}"))?,
        Duration::from_secs(10),
    )
    .map_err(|e| format!("connect identity port {addr}: {e}"))?;

    let payload = assignment.to_json();
    stream
        .write_all(payload.as_bytes())
        .map_err(|e| format!("write identity: {e}"))?;
    stream
        .shutdown(std::net::Shutdown::Write)
        .map_err(|e| format!("shutdown identity stream: {e}"))?;

    eprintln!(
        "[firecracker] sent identity: {} (cid={})",
        assignment.alias, assignment.cid
    );
    Ok(())
}

/// Stream QEMU stdout to the caller's stdout until the child exits.
fn stream_output(child: &mut Child) -> Result<(), String> {
    if let Some(stdout) = child.stdout.take() {
        let reader = BufReader::new(stdout);
        let mut out = std::io::stdout();
        for line in reader.lines() {
            match line {
                Ok(l) => {
                    let _ = writeln!(out, "{l}");
                    let _ = out.flush();
                }
                Err(_) => break,
            }
        }
    }
    Ok(())
}

// ── public entry point ───────────────────────────────────────────────────────

/// Launch a QEMU VM for sealed agent boot.
///
/// 1. Parse Agentfile
/// 2. Check for QEMU binary
/// 3. Build rootfs if not cached
/// 4. Assign CID from identity_counter
/// 5. Launch QEMU with virtiofs + vsock
/// 6. Wait for boot-ok on vsock:52
/// 7. Send identity on vsock:55
/// 8. Stream output
/// 9. Cleanup
pub fn run_vm(agentfile_path: &str) -> Result<(), String> {
    // 1. Parse Agentfile
    let content = fs::read_to_string(agentfile_path)
        .map_err(|e| format!("read Agentfile '{}': {e}", agentfile_path))?;
    let af = agentfile::parse(&content).map_err(|e| format!("{e}"))?;

    // →773: Verify Agentfile GPG signature, respecting HUMANFILE TRUST.
    let af_path = Path::new(agentfile_path);
    let trust_unsigned = crate::find_project_root().ok()
        .and_then(|r| crate::humanfile::load(&crate::state_dir(&r)).humanfile.trust)
        .as_deref() == Some("unsigned");
    crate::commands::sign::verify_and_log_agentfile(af_path, trust_unsigned);

    eprintln!(
        "[firecracker] model={} prompts={} tools={}",
        af.from,
        af.prompts.len(),
        af.tools.len()
    );

    // 2. Check QEMU
    let qemu_path = check_qemu()?;
    eprintln!("[firecracker] qemu: {}", qemu_path.display());

    // 3. Rootfs
    let rootfs = if rootfs_exists() {
        let p = rootfs_cache_path();
        eprintln!("[firecracker] rootfs cached: {}", p.display());
        p
    } else {
        eprintln!("[firecracker] building rootfs...");
        build_rootfs()?
    };

    // 4. Resolve .ostk dir and assign CID
    let ostk_dir = crate::find_project_root()
        .map(|r| crate::state_dir(&r))
        .unwrap_or_else(|_| {
            let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
            Path::new(&home).join(".ostk")
        });

    let cid = next_cid(&ostk_dir)?;
    eprintln!("[firecracker] assigned CID {cid}");

    // 5. Build kernel cmdline + QEMU command
    let cmdline = KernelCmdline {
        cid,
        host_vsock_port: VSOCK_BOOT_PORT,
        hay_mount: String::from("/hay"),
    };

    let mut cmd = build_qemu_command(&qemu_path, &rootfs, &ostk_dir, &cmdline);
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("spawn qemu: {e}"))?;

    eprintln!("[firecracker] QEMU PID {}", child.id());

    // 6. Wait for boot-ok
    let boot_addr = VsockAddr::new(cid, VSOCK_BOOT_PORT);
    match wait_for_boot(&boot_addr, Duration::from_secs(30)) {
        Ok(status) => eprintln!("[firecracker] boot confirmed: {status}"),
        Err(e) => {
            eprintln!("[firecracker] boot failed: {e}");
            let _ = child.kill();
            let _ = child.wait();
            return Err(e);
        }
    }

    // 7. Send identity
    let alias = format!("agent-{}", cid);
    let assignment = IdentityAssignment {
        alias: alias.clone(),
        cid,
    };
    let identity_addr = VsockAddr::new(cid, VSOCK_IDENTITY_PORT);
    send_identity(&identity_addr, &assignment)?;

    // 8. Stream output
    stream_output(&mut child)?;

    // 9. Wait for QEMU to exit
    let status = child.wait().map_err(|e| format!("wait qemu: {e}"))?;
    eprintln!(
        "[firecracker] QEMU exited: {}",
        status.code().unwrap_or(-1)
    );

    Ok(())
}

// ── tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_kernel_cmdline() {
        let cmdline =
            "console=ttyAMA0 ostk.cid=5 ostk.host_vsock_port=52 ostk.hay_mount=/hay";
        let parsed = KernelCmdline::parse(cmdline).unwrap();
        assert_eq!(parsed.cid, 5);
        assert_eq!(parsed.host_vsock_port, 52);
        assert_eq!(parsed.hay_mount, "/hay");

        // Roundtrip: build → parse
        let rebuilt = KernelCmdline::parse(&parsed.as_cmdline()).unwrap();
        assert_eq!(rebuilt, parsed);
    }

    #[test]
    fn test_parse_kernel_cmdline_defaults() {
        // Only cid specified — port and mount use defaults
        let cmdline = "ostk.cid=10";
        let parsed = KernelCmdline::parse(cmdline).unwrap();
        assert_eq!(parsed.cid, 10);
        assert_eq!(parsed.host_vsock_port, 52);
        assert_eq!(parsed.hay_mount, "/hay");
    }

    #[test]
    fn test_parse_kernel_cmdline_missing_cid() {
        let cmdline = "console=ttyAMA0 ostk.host_vsock_port=52";
        let result = KernelCmdline::parse(cmdline);
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("missing ostk.cid"));
    }

    #[test]
    fn test_vsock_addr_construction() {
        let addr = VsockAddr::new(5, 52);
        assert_eq!(addr.cid, 5);
        assert_eq!(addr.port, 52);

        // TCP port mapping: cid * 1000 + port
        assert_eq!(addr.to_tcp_port(), 5052);

        // Different CIDs produce different TCP ports (no collision)
        let addr2 = VsockAddr::new(6, 52);
        assert_ne!(addr.to_tcp_port(), addr2.to_tcp_port());

        // Identity port
        let id_addr = VsockAddr::new(5, 55);
        assert_eq!(id_addr.to_tcp_port(), 5055);
    }

    #[test]
    fn test_vsock_addr_large_cid() {
        // Ensure modulo wrapping works for large CIDs
        let addr = VsockAddr::new(100, 52);
        let port = addr.to_tcp_port();
        assert!(port > 0);
        assert!(port < 65535);
    }

    #[test]
    fn test_rootfs_cache_path() {
        let path = rootfs_cache_path();
        let path_str = path.display().to_string();

        // Must be under ~/.ostk/vm/
        assert!(path_str.contains(".ostk/vm/"), "path: {path_str}");
        // Must end with expected filename
        assert!(
            path_str.ends_with("rootfs-aarch64.ext4"),
            "path: {path_str}"
        );
    }

    #[test]
    fn test_identity_assignment_json() {
        let assignment = IdentityAssignment {
            alias: "agent-7".to_string(),
            cid: 7,
        };
        let json_str = assignment.to_json();

        // Parse it back
        let v: serde_json::Value = serde_json::from_str(&json_str).unwrap();
        assert_eq!(v["alias"], "agent-7");
        assert_eq!(v["cid"], 7);
        assert_eq!(v["assigned_by"], "ostk-host");
    }

    #[test]
    fn test_identity_assignment_roundtrip() {
        let assignment = IdentityAssignment {
            alias: "worker-sealed-42".to_string(),
            cid: 42,
        };
        let json_str = assignment.to_json();
        let v: serde_json::Value = serde_json::from_str(&json_str).unwrap();

        // Verify the guest can extract alias (matches vsock_receive_identity in vm_init.rs)
        let alias = v.get("alias").and_then(|a| a.as_str()).unwrap();
        assert_eq!(alias, "worker-sealed-42");
    }

    #[test]
    fn test_kernel_cmdline_to_string() {
        let cmdline = KernelCmdline {
            cid: 3,
            host_vsock_port: 52,
            hay_mount: "/hay".to_string(),
        };
        let s = cmdline.as_cmdline();
        assert!(s.contains("ostk.cid=3"));
        assert!(s.contains("ostk.host_vsock_port=52"));
        assert!(s.contains("ostk.hay_mount=/hay"));
        assert!(s.contains("console=ttyAMA0"));
    }

    #[test]
    fn test_kernel_cmdline_custom_mount() {
        let cmdline = "ostk.cid=3 ostk.hay_mount=/mnt/ostk";
        let parsed = KernelCmdline::parse(cmdline).unwrap();
        assert_eq!(parsed.hay_mount, "/mnt/ostk");
    }
}
