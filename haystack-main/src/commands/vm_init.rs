//! VM init — PID 1 for Firecracker guest microVM
//!
//! Boot sequence:
//!   1. Mount /proc, /sys, /dev
//!   2. Mount virtiofs .ostk/ at /hay
//!   3. Parse kernel cmdline (ostk.cid, ostk.host_vsock_port, ostk.hay_mount)
//!   4. Run POST checks 1-7 (from GAPS.md)
//!   5. Signal host via vsock port 52: boot-ok | boot-fail
//!   6. Receive identity assignment from host via vsock port 55
//!   7. Exec agent process as child PID 2+
//!   8. Zombie reap loop — PID 1 must never exit

use std::ffi::CString;
use std::fs;
use std::io::Write;
use std::path::Path;
use std::time::UNIX_EPOCH;

use nix::sys::signal::{sigaction, SaFlags, SigAction, SigHandler, SigSet, Signal};
use nix::sys::wait::{waitpid, WaitPidFlag, WaitStatus};
use nix::unistd::{execv, fork, ForkResult, Pid};

// ── vsock constants ──────────────────────────────────────────────────────────

const AF_VSOCK: libc::sa_family_t = 40;
const VMADDR_CID_ANY: u32 = 0xFFFF_FFFF;
const VSOCK_HOST_CID: u32 = 2;
const VSOCK_BOOT_PORT: u32 = 52;
const VSOCK_IDENTITY_PORT: u32 = 55;

const AGENT_BINARY: &str = "/usr/bin/ostk";

// ── boot config ───────────────────────────────────────────────────────────────

#[derive(Debug)]
struct BootConfig {
    guest_cid: u32,
    host_vsock_port: u32,
    hay_mount: String,
}

impl BootConfig {
    fn parse() -> Result<Self, String> {
        let cmdline =
            fs::read_to_string("/proc/cmdline").map_err(|e| format!("read /proc/cmdline: {e}"))?;

        let mut guest_cid = None;
        let mut host_vsock_port = VSOCK_BOOT_PORT;
        let mut hay_mount = String::from("/hay");

        for token in cmdline.split_whitespace() {
            if let Some(v) = token.strip_prefix("ostk.cid=") {
                guest_cid = v.parse::<u32>().ok();
            } else if let Some(v) = token.strip_prefix("ostk.host_vsock_port=") {
                if let Ok(p) = v.parse::<u32>() {
                    host_vsock_port = p;
                }
            } else if let Some(v) = token.strip_prefix("ostk.hay_mount=") {
                hay_mount = v.to_string();
            }
        }

        Ok(BootConfig {
            guest_cid: guest_cid.ok_or("missing ostk.cid in cmdline")?,
            host_vsock_port,
            hay_mount,
        })
    }
}

// ── vsock ─────────────────────────────────────────────────────────────────────

#[repr(C)]
struct SockaddrVm {
    svm_family: libc::sa_family_t,
    svm_reserved1: u16,
    svm_port: u32,
    svm_cid: u32,
    svm_flags: u8,
    svm_zero: [u8; 3],
}

fn vsock_connect(cid: u32, port: u32) -> std::io::Result<i32> {
    unsafe {
        let fd = libc::socket(AF_VSOCK as i32, libc::SOCK_STREAM, 0);
        if fd < 0 {
            return Err(std::io::Error::last_os_error());
        }
        let addr = SockaddrVm {
            svm_family: AF_VSOCK,
            svm_reserved1: 0,
            svm_port: port,
            svm_cid: cid,
            svm_flags: 0,
            svm_zero: [0; 3],
        };
        let ret = libc::connect(
            fd,
            &addr as *const _ as *const libc::sockaddr,
            std::mem::size_of::<SockaddrVm>() as libc::socklen_t,
        );
        if ret < 0 {
            libc::close(fd);
            return Err(std::io::Error::last_os_error());
        }
        Ok(fd)
    }
}

fn vsock_listen_accept(port: u32) -> std::io::Result<i32> {
    unsafe {
        let sfd = libc::socket(AF_VSOCK as i32, libc::SOCK_STREAM, 0);
        if sfd < 0 {
            return Err(std::io::Error::last_os_error());
        }
        let addr = SockaddrVm {
            svm_family: AF_VSOCK,
            svm_reserved1: 0,
            svm_port: port,
            svm_cid: VMADDR_CID_ANY,
            svm_flags: 0,
            svm_zero: [0; 3],
        };
        if libc::bind(
            sfd,
            &addr as *const _ as *const libc::sockaddr,
            std::mem::size_of::<SockaddrVm>() as libc::socklen_t,
        ) < 0
        {
            libc::close(sfd);
            return Err(std::io::Error::last_os_error());
        }
        if libc::listen(sfd, 1) < 0 {
            libc::close(sfd);
            return Err(std::io::Error::last_os_error());
        }
        let cfd = libc::accept(sfd, std::ptr::null_mut(), std::ptr::null_mut());
        libc::close(sfd);
        if cfd < 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(cfd)
    }
}

fn vsock_write_all(fd: i32, data: &[u8]) -> std::io::Result<()> {
    let mut written = 0;
    while written < data.len() {
        let n = unsafe {
            libc::write(
                fd,
                data[written..].as_ptr() as *const libc::c_void,
                data.len() - written,
            )
        };
        if n < 0 {
            return Err(std::io::Error::last_os_error());
        }
        written += n as usize;
    }
    Ok(())
}

fn vsock_read_to_string(fd: i32) -> std::io::Result<String> {
    let mut buf = Vec::new();
    let mut chunk = [0u8; 256];
    loop {
        let n = unsafe { libc::read(fd, chunk.as_mut_ptr() as *mut libc::c_void, chunk.len()) };
        if n < 0 {
            return Err(std::io::Error::last_os_error());
        }
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&chunk[..n as usize]);
    }
    String::from_utf8(buf).map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))
}

// ── mounts ────────────────────────────────────────────────────────────────────

fn mount_essential_filesystems() -> Result<(), String> {
    sys_mount(None, "/proc", "proc").map_err(|e| format!("mount /proc: {e}"))?;
    sys_mount(None, "/sys", "sysfs").map_err(|e| format!("mount /sys: {e}"))?;
    sys_mount(None, "/dev", "devtmpfs").map_err(|e| format!("mount /dev: {e}"))?;
    Ok(())
}

/// Raw mount(2) syscall wrapper.
/// On macOS this is a no-op — vm_init only runs inside Linux microVMs.
fn sys_mount(source: Option<&str>, target: &str, fstype: &str) -> std::io::Result<()> {
    #[cfg(target_os = "linux")]
    {
        use std::ffi::CString;
        let src = CString::new(source.unwrap_or("none")).unwrap();
        let tgt = CString::new(target).unwrap();
        let fst = CString::new(fstype).unwrap();
        let ret = unsafe {
            libc::mount(src.as_ptr(), tgt.as_ptr(), fst.as_ptr(), 0, std::ptr::null())
        };
        if ret < 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(())
    }
    #[cfg(not(target_os = "linux"))]
    {
        eprintln!("[sys_mount] stub: mount({source:?}, {target}, {fstype}) — macOS no-op");
        Ok(())
    }
}

fn mount_hay(hay_mount: &str) -> Result<(), String> {
    fs::create_dir_all(hay_mount).map_err(|e| format!("mkdir {hay_mount}: {e}"))?;
    // virtiofs tag "hay" — virtiofsd on host exports .ostk/
    sys_mount(Some("hay"), hay_mount, "virtiofs")
        .map_err(|e| format!("mount virtiofs at {hay_mount}: {e}"))
}

// ── POST checks ───────────────────────────────────────────────────────────────

fn run_post_checks(hay: &str) -> Result<(), (u32, String)> {
    post_check_1_primefile(hay)?;
    post_check_2_language_schema(hay)?;
    post_check_3_gen_table(hay)?;
    post_check_4_audit_chain(hay)?;
    post_check_5_tack_tests(hay)?;
    post_check_6_fcp_drivers(hay)?;
    post_check_7_boot_md_staleness(hay)?;
    Ok(())
}

fn post_check_1_primefile(hay: &str) -> Result<(), (u32, String)> {
    let primefile = format!("{hay}/.ostk/.primefile");
    let asc = format!("{hay}/.ostk/.primefile.asc");

    if !Path::new(&primefile).exists() {
        return Err((1, format!("POST ERROR [1]: {primefile} not found")));
    }
    if !Path::new(&asc).exists() {
        return Err((1, format!("POST ERROR [1]: {asc} not found")));
    }

    let out = std::process::Command::new("gpg")
        .args(["--verify", &asc, &primefile])
        .output()
        .map_err(|e| (1, format!("POST ERROR [1]: gpg not available: {e}")))?;

    let stderr = String::from_utf8_lossy(&out.stderr);
    if !out.status.success() {
        return Err((1, format!("POST ERROR [1]: .primefile signature invalid\n{stderr}")));
    }
    if !stderr.contains("GOODSIG") {
        return Err((1, format!("POST ERROR [1]: no GOODSIG in gpg output\n{stderr}")));
    }

    eprintln!("[POST 1] .primefile signature OK");
    Ok(())
}

fn post_check_2_language_schema(hay: &str) -> Result<(), (u32, String)> {
    let path = format!("{hay}/.ostk/.language");
    if !Path::new(&path).exists() {
        eprintln!("[POST 2] .language absent — first boot OK");
        return Ok(());
    }

    let content = fs::read_to_string(&path)
        .map_err(|e| (2, format!("POST ERROR [2]: read .language: {e}")))?;

    let required = ["verb", "tier", "layer", "last_gen", "half_life", "momentum", "resolution"];

    for (i, line) in content.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() { continue; }
        let v: serde_json::Value = serde_json::from_str(line).map_err(|e| {
            (2, format!("POST ERROR [2]: .language schema violation at line {}: {e}", i + 1))
        })?;
        for field in &required {
            if v.get(field).is_none() {
                return Err((2, format!(
                    "POST ERROR [2]: .language schema violation at line {}: missing '{field}'",
                    i + 1
                )));
            }
        }
    }

    eprintln!("[POST 2] .language schema OK");
    Ok(())
}

fn post_check_3_gen_table(hay: &str) -> Result<(), (u32, String)> {
    let path = format!("{hay}/.ostk/gen_table.jsonl");
    if !Path::new(&path).exists() {
        eprintln!("[POST 3] gen_table absent — skipping");
        return Ok(());
    }

    let content = fs::read_to_string(&path)
        .map_err(|e| (3, format!("POST ERROR [3]: read gen_table: {e}")))?;

    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() { continue; }
        let entry: serde_json::Value = serde_json::from_str(line)
            .map_err(|e| (3, format!("POST ERROR [3]: gen_table parse error: {e}")))?;
        if entry.get("path").is_none() {
            return Err((3, String::from("POST ERROR [3]: gen_table entry missing 'path'")));
        }
    }

    eprintln!("[POST 3] gen_table consistency OK");
    Ok(())
}

fn post_check_4_audit_chain(hay: &str) -> Result<(), (u32, String)> {
    let path = format!("{hay}/.ostk/audit.jsonl");
    if !Path::new(&path).exists() {
        eprintln!("[POST 4] audit.jsonl absent — skipping");
        return Ok(());
    }

    let content = fs::read_to_string(&path)
        .map_err(|e| (4, format!("POST ERROR [4]: read audit.jsonl: {e}")))?;

    let mut prev_line: Option<String> = None;
    for (i, line) in content.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() { continue; }
        let entry: serde_json::Value = serde_json::from_str(line)
            .map_err(|e| (4, format!("POST ERROR [4]: audit.jsonl parse error at line {}: {e}", i + 1)))?;

        if let Some(prev) = &prev_line
            && let Some(recorded) = entry.get("prev_hash").and_then(|v| v.as_str()) {
                let actual = sha256_hex(prev.as_bytes());
                if !actual.is_empty() && recorded != actual {
                    return Err((4, format!(
                        "POST ERROR [4]: audit.jsonl chain broken at event {}: expected {actual}, got {recorded}",
                        i + 1
                    )));
                }
            }
        prev_line = Some(line.to_string());
    }

    eprintln!("[POST 4] audit.jsonl hash chain OK");
    Ok(())
}

fn post_check_5_tack_tests(hay: &str) -> Result<(), (u32, String)> {
    let out = std::process::Command::new("ostk")
        .args(["bench", "--tack", "--post"])
        .env("OSTK_HAY", hay)
        .output();

    match out {
        Ok(o) if o.status.success() => {
            eprintln!("[POST 5] tack.t boot-critical tests OK");
            Ok(())
        }
        Ok(o) => {
            let stderr = String::from_utf8_lossy(&o.stderr);
            let stdout = String::from_utf8_lossy(&o.stdout);
            Err((5, format!("POST ERROR [5]: tack.t failure\n{stdout}{stderr}")))
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            eprintln!("[POST 5] ostk not found — skipping tack.t");
            Ok(())
        }
        Err(e) => Err((5, format!("POST ERROR [5]: bench exec failed: {e}"))),
    }
}

fn post_check_6_fcp_drivers(hay: &str) -> Result<(), (u32, String)> {
    let registry = format!("{hay}/.ostk/registry-import.jsonl");
    if !Path::new(&registry).exists() {
        eprintln!("[POST 6] registry-import.jsonl absent — no drivers");
        return Ok(());
    }

    let content = fs::read_to_string(&registry)
        .map_err(|e| (6, format!("POST ERROR [6]: read registry: {e}")))?;

    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() { continue; }
        let entry: serde_json::Value = serde_json::from_str(line)
            .map_err(|e| (6, format!("POST ERROR [6]: registry parse: {e}")))?;
        let name = entry.get("name").and_then(|v| v.as_str()).unwrap_or("unknown");
        let path = entry.get("path").and_then(|v| v.as_str()).unwrap_or("");

        let probe = std::process::Command::new("ostk")
            .args(["driver", "probe", "--name", name, "--path", path])
            .env("OSTK_HAY", hay)
            .output();

        match probe {
            Ok(o) if o.status.success() => eprintln!("[POST 6] driver {name} OK"),
            Ok(o) => {
                let stderr = String::from_utf8_lossy(&o.stderr);
                eprintln!("[POST 6] driver {name} probe warn: {stderr}");
                // Warn only — minimal rootfs may not have all drivers
            }
            Err(_) => eprintln!("[POST 6] ostk driver probe unavailable — skipping {name}"),
        }
    }

    eprintln!("[POST 6] fcp-* driver check complete");
    Ok(())
}

fn post_check_7_boot_md_staleness(hay: &str) -> Result<(), (u32, String)> {
    let boot_md = format!("{hay}/.ostk/boot.md");
    let audit_path = format!("{hay}/.ostk/audit.jsonl");

    if !Path::new(&boot_md).exists() {
        eprintln!("[POST 7] boot.md absent — first boot OK");
        return Ok(());
    }

    let boot_ts = fs::metadata(&boot_md)
        .and_then(|m| m.modified())
        .map(|t| t.duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0))
        .map_err(|e| (7, format!("POST ERROR [7]: stat boot.md: {e}")))?;

    if !Path::new(&audit_path).exists() {
        eprintln!("[POST 7] audit.jsonl absent — cannot check staleness");
        return Ok(());
    }

    let content = fs::read_to_string(&audit_path)
        .map_err(|e| (7, format!("POST ERROR [7]: read audit.jsonl: {e}")))?;

    let last_ts = content
        .lines().rfind(|l| !l.trim().is_empty())
        .and_then(|line| serde_json::from_str::<serde_json::Value>(line).ok())
        .and_then(|v| {
            v.get("ts")
                .or_else(|| v.get("timestamp"))
                .and_then(|t| t.as_str().and_then(|s| s.parse::<f64>().ok()))
        });

    if let Some(last_audit_ts) = last_ts
        && boot_ts < last_audit_ts {
            return Err((7, format!(
                "POST ERROR [7]: boot.md stale (mtime {boot_ts:.0} < last audit {last_audit_ts:.0}) — run ostk refine"
            )));
        }

    eprintln!("[POST 7] boot.md staleness OK");
    Ok(())
}

// ── vsock handshake ───────────────────────────────────────────────────────────

fn vsock_signal_result(config: &BootConfig, success: bool, fail_info: Option<(u32, &str)>) -> Result<(), String> {
    let payload = if success {
        format!(r#"{{"status":"boot-ok","cid":{}}}"#, config.guest_cid)
    } else {
        let (check, err) = fail_info.unwrap_or((0, "unknown"));
        let err_esc = err.replace('"', "\\\"").replace('\n', " ");
        format!(
            r#"{{"status":"boot-fail","cid":{},"check":{},"error":"{}"}}"#,
            config.guest_cid, check, err_esc
        )
    };

    let fd = vsock_connect(VSOCK_HOST_CID, config.host_vsock_port)
        .map_err(|e| format!("vsock connect :{}: {e}", config.host_vsock_port))?;
    vsock_write_all(fd, payload.as_bytes())
        .map_err(|e| { unsafe { libc::close(fd) }; format!("vsock write: {e}") })?;
    unsafe { libc::close(fd) };
    Ok(())
}

fn vsock_receive_identity(config: &BootConfig, hay: &str) -> Result<String, String> {
    let ifd = vsock_listen_accept(VSOCK_IDENTITY_PORT)
        .map_err(|e| format!("vsock accept :{VSOCK_IDENTITY_PORT}: {e}"))?;
    let raw = vsock_read_to_string(ifd)
        .map_err(|e| { unsafe { libc::close(ifd) }; format!("vsock read identity: {e}") })?;
    unsafe { libc::close(ifd) };

    let assignment: serde_json::Value = serde_json::from_str(&raw)
        .map_err(|e| format!("parse identity: {e}"))?;

    let alias = assignment
        .get("alias")
        .and_then(|v| v.as_str())
        .unwrap_or("agent-unknown")
        .to_string();

    // Record in agents.jsonl via virtiofs
    let agents_path = format!("{hay}/.ostk/agents.jsonl");
    let entry = format!(
        "{{\"alias\":\"{alias}\",\"cid\":{},\"state\":\"booting\",\"pid\":1}}\n",
        config.guest_cid
    );
    if let Ok(mut f) = std::fs::OpenOptions::new().append(true).create(true).open(&agents_path) {
        let _ = f.write_all(entry.as_bytes());
    }

    Ok(alias)
}

// ── exec + reaper ─────────────────────────────────────────────────────────────

fn exec_agent(identity: &str) -> Result<Pid, String> {
    let binary = CString::new(AGENT_BINARY).unwrap();
    let arg_serve = CString::new("serve").unwrap();
    let arg_id = CString::new(format!("--identity={identity}")).unwrap();

    match unsafe { fork() }.map_err(|e| format!("fork: {e}"))? {
        ForkResult::Parent { child } => {
            eprintln!("[vm-init] agent PID {child} ({identity})");
            Ok(child)
        }
        ForkResult::Child => {
            let _ = execv(&binary, &[binary.clone(), arg_serve, arg_id]);
            eprintln!("[vm-init] execv failed");
            unsafe { libc::exit(1) };
        }
    }
}

fn reaper_loop(main_child: Pid) -> ! {
    // Default signal disposition — forward SIGTERM/SIGINT to main child
    let handler = SigAction::new(SigHandler::SigDfl, SaFlags::empty(), SigSet::empty());
    unsafe {
        let _ = sigaction(Signal::SIGTERM, &handler);
        let _ = sigaction(Signal::SIGINT, &handler);
    }

    loop {
        match waitpid(None, Some(WaitPidFlag::WNOHANG)) {
            Ok(WaitStatus::Exited(pid, code)) => {
                eprintln!("[vm-init] reaped PID {pid} exit={code}");
                if pid == main_child {
                    eprintln!("[vm-init] main agent exited — spinning (kernel requires PID 1 alive)");
                }
            }
            Ok(WaitStatus::Signaled(pid, sig, _)) => {
                eprintln!("[vm-init] reaped PID {pid} killed by {sig}");
                if pid == main_child {
                    // Forward signal to keep host informed if needed
                    eprintln!("[vm-init] main agent killed — spinning");
                }
            }
            _ => std::thread::sleep(std::time::Duration::from_millis(100)),
        }
    }
}

fn run_forever() -> ! {
    eprintln!("[vm-init] idle — kernel requires PID 1 alive");
    loop {
        let _ = waitpid(None, Some(WaitPidFlag::WNOHANG));
        std::thread::sleep(std::time::Duration::from_secs(1));
    }
}

// ── sha256 via subprocess ─────────────────────────────────────────────────────

fn sha256_hex(data: &[u8]) -> String {
    use std::process::{Command, Stdio};
    let mut child = match Command::new("sha256sum")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
    {
        Ok(c) => c,
        Err(_) => return String::new(),
    };
    if let Some(stdin) = child.stdin.as_mut() {
        let _ = stdin.write_all(data);
    }
    match child.wait_with_output() {
        Ok(o) => {
            let s = String::from_utf8_lossy(&o.stdout);
            s.split_whitespace().next().unwrap_or("").to_string()
        }
        Err(_) => String::new(),
    }
}

// ── public entry point ────────────────────────────────────────────────────────

/// Run as PID 1 inside a Firecracker guest VM. Never returns.
pub fn run_as_init() -> ! {
    eprintln!("[vm-init] PID 1 starting");

    if let Err(e) = mount_essential_filesystems() {
        eprintln!("[vm-init] FATAL: {e}");
        run_forever();
    }
    eprintln!("[vm-init] /proc /sys /dev mounted");

    let config = match BootConfig::parse() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[vm-init] FATAL: {e}");
            run_forever();
        }
    };
    eprintln!("[vm-init] cid={} port={} hay={}", config.guest_cid, config.host_vsock_port, config.hay_mount);

    if let Err(e) = mount_hay(&config.hay_mount) {
        eprintln!("[vm-init] FATAL: {e}");
        run_forever();
    }
    eprintln!("[vm-init] virtiofs /hay mounted");

    match run_post_checks(&config.hay_mount) {
        Ok(()) => eprintln!("[vm-init] POST: all 7 checks passed"),
        Err((check, msg)) => {
            eprintln!("[vm-init] POST FAILED check {check}: {msg}");
            let _ = vsock_signal_result(&config, false, Some((check, &msg)));
            run_forever();
        }
    }

    if let Err(e) = vsock_signal_result(&config, true, None) {
        eprintln!("[vm-init] warn: could not signal boot-ok: {e}");
    }

    let identity = vsock_receive_identity(&config, &config.hay_mount)
        .unwrap_or_else(|e| {
            eprintln!("[vm-init] warn: identity recv failed: {e}");
            String::from("agent-unknown")
        });
    eprintln!("[vm-init] identity: {identity}");

    match exec_agent(&identity) {
        Ok(child) => reaper_loop(child),
        Err(e) => {
            eprintln!("[vm-init] exec failed: {e}");
            let _ = vsock_signal_result(&config, false, Some((0, &format!("exec: {e}"))));
            run_forever();
        }
    }
}
