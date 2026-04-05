//! Integration tests for the firecracker (QEMU VM launcher) module.
//!
//! Tests cover: KernelCmdline parsing, VsockAddr TCP port mapping,
//! IdentityAssignment JSON serialization, rootfs path logic, next_cid
//! file I/O (via identity_counter), wait_for_boot timeout/protocol,
//! and error paths.

use std::fs;
use std::io::Write;
use std::net::{TcpListener, TcpStream};
use std::path::Path;
use std::thread;
use std::time::Duration;
use tempfile::TempDir;

use ostk::commands::firecracker::{
    IdentityAssignment, KernelCmdline, VsockAddr, rootfs_cache_path,
};

// ── KernelCmdline parsing ────────────────────────────────────────────────────

#[test]
fn cmdline_parse_full() {
    let raw =
        "console=ttyAMA0 haystack.cid=5 haystack.host_vsock_port=52 haystack.hay_mount=/hay";
    let kc = KernelCmdline::parse(raw).unwrap();
    assert_eq!(kc.cid, 5);
    assert_eq!(kc.host_vsock_port, 52);
    assert_eq!(kc.hay_mount, "/hay");
}

#[test]
fn cmdline_parse_defaults_when_only_cid() {
    let kc = KernelCmdline::parse("haystack.cid=10").unwrap();
    assert_eq!(kc.cid, 10);
    assert_eq!(kc.host_vsock_port, 52, "default boot port should be 52");
    assert_eq!(kc.hay_mount, "/hay", "default mount should be /hay");
}

#[test]
fn cmdline_parse_missing_cid_is_err() {
    let result = KernelCmdline::parse("console=ttyAMA0 haystack.host_vsock_port=52");
    assert!(result.is_err());
    assert!(
        result.unwrap_err().contains("missing haystack.cid"),
        "error should mention missing cid"
    );
}

#[test]
fn cmdline_parse_empty_string_is_err() {
    let result = KernelCmdline::parse("");
    assert!(result.is_err());
}

#[test]
fn cmdline_parse_garbage_is_err() {
    let result = KernelCmdline::parse("root=/dev/sda1 ro quiet splash");
    assert!(result.is_err());
}

#[test]
fn cmdline_parse_cid_not_a_number() {
    // "haystack.cid=abc" — .parse::<u32>().ok() returns None → treated as missing
    let result = KernelCmdline::parse("haystack.cid=abc");
    assert!(result.is_err());
    assert!(result.unwrap_err().contains("missing haystack.cid"));
}

#[test]
fn cmdline_parse_cid_negative() {
    // -1 doesn't parse as u32 → treated as missing
    let result = KernelCmdline::parse("haystack.cid=-1");
    assert!(result.is_err());
}

#[test]
fn cmdline_parse_cid_zero() {
    // 0 is technically parseable as u32 even though CIDs 0-2 are reserved
    let kc = KernelCmdline::parse("haystack.cid=0").unwrap();
    assert_eq!(kc.cid, 0);
}

#[test]
fn cmdline_parse_extra_fields_ignored() {
    let raw = "console=ttyAMA0 haystack.cid=7 haystack.extra=hello root=/dev/sda1";
    let kc = KernelCmdline::parse(raw).unwrap();
    assert_eq!(kc.cid, 7);
    // Extra tokens don't interfere with parsing — only haystack.* prefixes are inspected
    assert_eq!(kc.host_vsock_port, 52);
    assert_eq!(kc.hay_mount, "/hay");
}

#[test]
fn cmdline_parse_duplicate_cid_last_wins() {
    // Multiple haystack.cid= tokens — last one wins because loop overwrites
    let raw = "haystack.cid=3 haystack.cid=7";
    let kc = KernelCmdline::parse(raw).unwrap();
    assert_eq!(kc.cid, 7);
}

#[test]
fn cmdline_parse_custom_mount() {
    let raw = "haystack.cid=3 haystack.hay_mount=/mnt/haystack";
    let kc = KernelCmdline::parse(raw).unwrap();
    assert_eq!(kc.hay_mount, "/mnt/haystack");
}

#[test]
fn cmdline_parse_custom_port() {
    let raw = "haystack.cid=3 haystack.host_vsock_port=9999";
    let kc = KernelCmdline::parse(raw).unwrap();
    assert_eq!(kc.host_vsock_port, 9999);
}

#[test]
fn cmdline_parse_invalid_port_uses_default() {
    // Non-numeric port → parse::<u32>() fails → default is kept
    let raw = "haystack.cid=3 haystack.host_vsock_port=notanumber";
    let kc = KernelCmdline::parse(raw).unwrap();
    assert_eq!(kc.host_vsock_port, 52);
}

#[test]
fn cmdline_roundtrip() {
    let original = KernelCmdline {
        cid: 42,
        host_vsock_port: 100,
        hay_mount: "/data".to_string(),
    };
    let serialized = original.as_cmdline();
    let parsed = KernelCmdline::parse(&serialized).unwrap();
    assert_eq!(original, parsed);
}

#[test]
fn cmdline_roundtrip_default_values() {
    let original = KernelCmdline {
        cid: 3,
        host_vsock_port: 52,
        hay_mount: "/hay".to_string(),
    };
    let serialized = original.as_cmdline();
    assert!(serialized.contains("console=ttyAMA0"));
    let parsed = KernelCmdline::parse(&serialized).unwrap();
    assert_eq!(original, parsed);
}

#[test]
fn cmdline_to_string_format() {
    let kc = KernelCmdline {
        cid: 5,
        host_vsock_port: 52,
        hay_mount: "/hay".to_string(),
    };
    let s = kc.as_cmdline();
    assert!(s.starts_with("console=ttyAMA0 "));
    assert!(s.contains("haystack.cid=5"));
    assert!(s.contains("haystack.host_vsock_port=52"));
    assert!(s.contains("haystack.hay_mount=/hay"));
}

#[test]
fn cmdline_parse_lots_of_whitespace() {
    let raw = "  haystack.cid=3    haystack.host_vsock_port=80    ";
    let kc = KernelCmdline::parse(raw).unwrap();
    assert_eq!(kc.cid, 3);
    assert_eq!(kc.host_vsock_port, 80);
}

// ── VsockAddr ────────────────────────────────────────────────────────────────

#[test]
fn vsock_addr_construction() {
    let addr = VsockAddr::new(5, 52);
    assert_eq!(addr.cid, 5);
    assert_eq!(addr.port, 52);
}

#[test]
fn vsock_tcp_port_formula() {
    // TCP port = (cid * 1000 + vsock_port) % 65535
    let addr = VsockAddr::new(5, 52);
    assert_eq!(addr.to_tcp_port(), 5052);

    let addr = VsockAddr::new(5, 55);
    assert_eq!(addr.to_tcp_port(), 5055);

    let addr = VsockAddr::new(3, 52);
    assert_eq!(addr.to_tcp_port(), 3052);
}

#[test]
fn vsock_different_cids_no_collision() {
    let ports: Vec<u16> = (3..=20)
        .map(|cid| VsockAddr::new(cid, 52).to_tcp_port())
        .collect();
    // All unique
    let unique: std::collections::HashSet<u16> = ports.iter().copied().collect();
    assert_eq!(
        unique.len(),
        ports.len(),
        "each CID should map to a different TCP port"
    );
}

#[test]
fn vsock_identity_port_distinct_from_boot() {
    for cid in 3..=10 {
        let boot = VsockAddr::new(cid, 52).to_tcp_port();
        let identity = VsockAddr::new(cid, 55).to_tcp_port();
        assert_ne!(boot, identity, "boot and identity ports must differ for cid={cid}");
    }
}

#[test]
fn vsock_large_cid_wraps_into_valid_range() {
    // CID 100 → 100*1000+52 = 100052 → 100052 % 65535 = 34517
    let addr = VsockAddr::new(100, 52);
    let port = addr.to_tcp_port();
    assert!(port > 0, "port must be positive");
    assert!(port < 65535, "port must be < 65535");
}

#[test]
fn vsock_port_zero() {
    let addr = VsockAddr::new(3, 0);
    assert_eq!(addr.to_tcp_port(), 3000);
}

#[test]
fn vsock_cid_zero() {
    let addr = VsockAddr::new(0, 52);
    assert_eq!(addr.to_tcp_port(), 52);
}

#[test]
fn vsock_max_before_wrap() {
    // 65 * 1000 + 52 = 65052 — still under 65535
    let addr = VsockAddr::new(65, 52);
    assert_eq!(addr.to_tcp_port(), 65052);
}

#[test]
fn vsock_wraps_at_boundary() {
    // 66 * 1000 + 52 = 66052 → 66052 % 65535 = 517
    let addr = VsockAddr::new(66, 52);
    assert_eq!(addr.to_tcp_port(), ((66u32 * 1000 + 52) % 65535) as u16);
}

#[test]
fn vsock_equality() {
    let a = VsockAddr::new(5, 52);
    let b = VsockAddr::new(5, 52);
    let c = VsockAddr::new(6, 52);
    assert_eq!(a, b);
    assert_ne!(a, c);
}

#[test]
fn vsock_clone() {
    let a = VsockAddr::new(5, 52);
    let b = a.clone();
    assert_eq!(a, b);
}

#[test]
fn vsock_debug_format() {
    let addr = VsockAddr::new(5, 52);
    let debug = format!("{:?}", addr);
    assert!(debug.contains("VsockAddr"));
    assert!(debug.contains("5"));
    assert!(debug.contains("52"));
}

// ── IdentityAssignment ───────────────────────────────────────────────────────

#[test]
fn identity_assignment_json_fields() {
    let assignment = IdentityAssignment {
        alias: "agent-7".to_string(),
        cid: 7,
    };
    let json_str = assignment.to_json();
    let v: serde_json::Value = serde_json::from_str(&json_str).unwrap();
    assert_eq!(v["alias"], "agent-7");
    assert_eq!(v["cid"], 7);
    assert_eq!(v["assigned_by"], "haystack-host");
}

#[test]
fn identity_assignment_valid_json() {
    let assignment = IdentityAssignment {
        alias: "worker-sealed-42".to_string(),
        cid: 42,
    };
    let json_str = assignment.to_json();
    // Must parse as valid JSON
    let parsed: Result<serde_json::Value, _> = serde_json::from_str(&json_str);
    assert!(parsed.is_ok(), "identity JSON must be valid");
}

#[test]
fn identity_assignment_alias_roundtrip() {
    let assignment = IdentityAssignment {
        alias: "worker-sealed-42".to_string(),
        cid: 42,
    };
    let json_str = assignment.to_json();
    let v: serde_json::Value = serde_json::from_str(&json_str).unwrap();
    let alias = v.get("alias").and_then(|a| a.as_str()).unwrap();
    assert_eq!(alias, "worker-sealed-42");
}

#[test]
fn identity_assignment_special_chars_in_alias() {
    let assignment = IdentityAssignment {
        alias: "agent/with-special_chars.v2".to_string(),
        cid: 100,
    };
    let json_str = assignment.to_json();
    let v: serde_json::Value = serde_json::from_str(&json_str).unwrap();
    assert_eq!(v["alias"], "agent/with-special_chars.v2");
}

#[test]
fn identity_assignment_clone_and_eq() {
    let a = IdentityAssignment {
        alias: "a".to_string(),
        cid: 1,
    };
    let b = a.clone();
    assert_eq!(a, b);
}

// ── rootfs_cache_path ────────────────────────────────────────────────────────

#[test]
fn rootfs_cache_path_under_haystack_vm() {
    let path = rootfs_cache_path();
    let path_str = path.display().to_string();
    assert!(
        path_str.contains(".ostk/vm/"),
        "rootfs path should be under .ostk/vm/, got: {path_str}"
    );
    assert!(
        path_str.ends_with("rootfs-aarch64.ext4"),
        "rootfs should be named rootfs-aarch64.ext4, got: {path_str}"
    );
}

#[test]
fn rootfs_cache_path_is_absolute() {
    let path = rootfs_cache_path();
    assert!(
        path.is_absolute(),
        "rootfs cache path should be absolute, got: {}",
        path.display()
    );
}

#[test]
fn rootfs_cache_path_stable() {
    // Calling twice produces same path
    let a = rootfs_cache_path();
    let b = rootfs_cache_path();
    assert_eq!(a, b);
}

// ── next_cid (tested via identity_counter file I/O) ──────────────────────────
//
// next_cid is private, so we test it indirectly by replicating its logic
// against a temp directory, verifying the file protocol is correct.

/// Replicate the next_cid file protocol for testing.
/// This ensures the integration contract matches: read counter file, increment, write back.
fn simulate_next_cid(haystack_dir: &Path) -> Result<u32, String> {
    let counter_path = haystack_dir.join("identity_counter");
    let current = fs::read_to_string(&counter_path)
        .unwrap_or_else(|_| "0".to_string())
        .trim()
        .parse::<u64>()
        .unwrap_or(0);
    let next = current + 1;
    fs::write(&counter_path, next.to_string())
        .map_err(|e| format!("write identity_counter: {e}"))?;
    Ok((next + 2) as u32)
}

#[test]
fn next_cid_from_scratch() {
    let dir = TempDir::new().unwrap();
    let cid = simulate_next_cid(dir.path()).unwrap();
    // First call: counter goes 0 → 1, CID = 1 + 2 = 3
    assert_eq!(cid, 3);

    // Verify the file was written
    let content = fs::read_to_string(dir.path().join("identity_counter")).unwrap();
    assert_eq!(content, "1");
}

#[test]
fn next_cid_increments_sequentially() {
    let dir = TempDir::new().unwrap();

    let cid1 = simulate_next_cid(dir.path()).unwrap();
    let cid2 = simulate_next_cid(dir.path()).unwrap();
    let cid3 = simulate_next_cid(dir.path()).unwrap();

    assert_eq!(cid1, 3);
    assert_eq!(cid2, 4);
    assert_eq!(cid3, 5);
}

#[test]
fn next_cid_resumes_from_existing_counter() {
    let dir = TempDir::new().unwrap();
    // Pre-seed counter at 10
    fs::write(dir.path().join("identity_counter"), "10").unwrap();

    let cid = simulate_next_cid(dir.path()).unwrap();
    // 10 → 11, CID = 11 + 2 = 13
    assert_eq!(cid, 13);
}

#[test]
fn next_cid_handles_malformed_counter_file() {
    let dir = TempDir::new().unwrap();
    // Non-numeric content → falls back to 0
    fs::write(dir.path().join("identity_counter"), "garbage").unwrap();

    let cid = simulate_next_cid(dir.path()).unwrap();
    // Falls back to 0 → next=1, CID=3
    assert_eq!(cid, 3);
}

#[test]
fn next_cid_handles_empty_counter_file() {
    let dir = TempDir::new().unwrap();
    fs::write(dir.path().join("identity_counter"), "").unwrap();

    let cid = simulate_next_cid(dir.path()).unwrap();
    assert_eq!(cid, 3);
}

#[test]
fn next_cid_handles_whitespace_in_counter() {
    let dir = TempDir::new().unwrap();
    fs::write(dir.path().join("identity_counter"), "  5\n").unwrap();

    let cid = simulate_next_cid(dir.path()).unwrap();
    // trim() strips whitespace, parse gives 5, next=6, CID=8
    assert_eq!(cid, 8);
}

#[test]
fn next_cid_counter_file_persists() {
    let dir = TempDir::new().unwrap();

    for i in 1..=5 {
        let _ = simulate_next_cid(dir.path()).unwrap();
        let content = fs::read_to_string(dir.path().join("identity_counter")).unwrap();
        assert_eq!(content, i.to_string());
    }
}

// ── rootfs path logic / build_rootfs error handling ──────────────────────────
//
// build_rootfs is private and shells out to dd/mkfs — we don't call it.
// Instead we test the path construction and directory creation logic.

#[test]
fn rootfs_parent_dir_creation() {
    let dir = TempDir::new().unwrap();
    let rootfs_dir = dir.path().join(".ostk").join("vm");

    // Simulate build_rootfs directory creation
    assert!(!rootfs_dir.exists());
    fs::create_dir_all(&rootfs_dir).unwrap();
    assert!(rootfs_dir.exists());
    assert!(rootfs_dir.is_dir());
}

#[test]
fn rootfs_exists_when_present() {
    let dir = TempDir::new().unwrap();
    let vm_dir = dir.path().join(".ostk").join("vm");
    fs::create_dir_all(&vm_dir).unwrap();
    let rootfs = vm_dir.join("rootfs-aarch64.ext4");

    // Not yet created
    assert!(!rootfs.exists());

    // Create a fake rootfs file
    fs::write(&rootfs, "fake rootfs content").unwrap();
    assert!(rootfs.exists());
}

#[test]
fn rootfs_exists_when_absent() {
    let dir = TempDir::new().unwrap();
    let rootfs = dir.path().join(".ostk").join("vm").join("rootfs-aarch64.ext4");
    assert!(!rootfs.exists());
}

// ── FirecrackerConfig / KernelCmdline construction ───────────────────────────

#[test]
fn cmdline_construction_for_vm() {
    // Simulate what run_vm does: build a KernelCmdline from assigned CID
    let cid = 5u32;
    let cmdline = KernelCmdline {
        cid,
        host_vsock_port: 52,
        hay_mount: String::from("/hay"),
    };

    let s = cmdline.as_cmdline();
    assert!(s.contains("haystack.cid=5"));
    assert!(s.contains("haystack.host_vsock_port=52"));
    assert!(s.contains("haystack.hay_mount=/hay"));

    // Verify the boot and identity VsockAddrs derived from this CID
    let boot_addr = VsockAddr::new(cid, 52);
    let identity_addr = VsockAddr::new(cid, 55);
    assert_eq!(boot_addr.to_tcp_port(), 5052);
    assert_eq!(identity_addr.to_tcp_port(), 5055);
}

#[test]
fn cmdline_and_identity_consistent_cid() {
    // Verify CID flows consistently from cmdline → identity assignment
    let cid = 42u32;
    let cmdline = KernelCmdline {
        cid,
        host_vsock_port: 52,
        hay_mount: "/hay".to_string(),
    };
    let assignment = IdentityAssignment {
        alias: format!("agent-{}", cid),
        cid,
    };

    let parsed = KernelCmdline::parse(&cmdline.as_cmdline()).unwrap();
    assert_eq!(parsed.cid, cid);

    let id_json: serde_json::Value = serde_json::from_str(&assignment.to_json()).unwrap();
    assert_eq!(id_json["cid"], cid);
    assert_eq!(id_json["alias"], format!("agent-{cid}"));
}

// ── wait_for_boot timeout behavior ──────────────────────────────────────────
//
// wait_for_boot is private, but we can test the TCP protocol it implements:
// it binds a TCP listener, waits for a connection, reads JSON, and validates
// the "status" field.

/// Simulate the wait_for_boot protocol using a TCP listener.
/// Spawns a listener, has a client connect and send data, then validates the response.
fn run_boot_protocol(
    payload: &str,
    timeout: Duration,
) -> Result<String, String> {
    // Bind to a random port
    let listener = TcpListener::bind("127.0.0.1:0").map_err(|e| format!("bind: {e}"))?;
    let port = listener.local_addr().unwrap().port();

    let payload_clone = payload.to_string();
    let handle = thread::spawn(move || {
        // Small delay to let accept() be ready
        thread::sleep(Duration::from_millis(50));
        let mut stream =
            TcpStream::connect(format!("127.0.0.1:{port}")).expect("connect failed");
        stream
            .write_all(payload_clone.as_bytes())
            .expect("write failed");
        // Shutdown to signal EOF
        stream
            .shutdown(std::net::Shutdown::Write)
            .expect("shutdown failed");
    });

    // Set socket timeout
    listener
        .set_nonblocking(false)
        .map_err(|e| format!("set_nonblocking: {e}"))?;

    #[cfg(unix)]
    {
        use std::os::unix::io::AsRawFd;
        let raw_fd = listener.as_raw_fd();
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
    }

    let (mut stream, _addr) = listener
        .accept()
        .map_err(|e| format!("accept: {e}"))?;

    let mut buf = String::new();
    std::io::Read::read_to_string(&mut stream, &mut buf)
        .map_err(|e| format!("read: {e}"))?;

    handle.join().unwrap();

    // Parse and verify — same logic as wait_for_boot
    let v: serde_json::Value =
        serde_json::from_str(&buf).map_err(|e| format!("parse boot status: {e}"))?;

    match v.get("status").and_then(|s| s.as_str()) {
        Some("boot-ok") => Ok(buf),
        Some("boot-fail") => {
            let check = v.get("check").and_then(|c| c.as_u64()).unwrap_or(0);
            let error = v
                .get("error")
                .and_then(|e| e.as_str())
                .unwrap_or("unknown");
            Err(format!("guest POST failed at check {check}: {error}"))
        }
        other => Err(format!(
            "unexpected boot status: {:?}",
            other.unwrap_or("(missing)")
        )),
    }
}

#[test]
fn boot_protocol_boot_ok() {
    let payload = serde_json::json!({
        "status": "boot-ok",
        "cid": 5,
    })
    .to_string();

    let result = run_boot_protocol(&payload, Duration::from_secs(5));
    assert!(result.is_ok(), "boot-ok should succeed: {:?}", result);
    let body = result.unwrap();
    assert!(body.contains("boot-ok"));
}

#[test]
fn boot_protocol_boot_fail() {
    let payload = serde_json::json!({
        "status": "boot-fail",
        "check": 2,
        "error": "virtiofs mount failed",
    })
    .to_string();

    let result = run_boot_protocol(&payload, Duration::from_secs(5));
    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(err.contains("guest POST failed"));
    assert!(err.contains("check 2"));
    assert!(err.contains("virtiofs mount failed"));
}

#[test]
fn boot_protocol_unexpected_status() {
    let payload = serde_json::json!({
        "status": "something-weird",
    })
    .to_string();

    let result = run_boot_protocol(&payload, Duration::from_secs(5));
    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(err.contains("unexpected boot status"));
}

#[test]
fn boot_protocol_missing_status_field() {
    let payload = serde_json::json!({
        "cid": 5,
    })
    .to_string();

    let result = run_boot_protocol(&payload, Duration::from_secs(5));
    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(err.contains("unexpected boot status"));
    assert!(err.contains("(missing)"));
}

#[test]
fn boot_protocol_invalid_json() {
    let result = run_boot_protocol("not json at all", Duration::from_secs(5));
    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(err.contains("parse boot status"));
}

#[test]
fn boot_protocol_empty_payload() {
    let result = run_boot_protocol("", Duration::from_secs(5));
    assert!(result.is_err());
}

#[test]
fn boot_protocol_boot_fail_missing_fields() {
    let payload = serde_json::json!({
        "status": "boot-fail",
    })
    .to_string();

    let result = run_boot_protocol(&payload, Duration::from_secs(5));
    assert!(result.is_err());
    let err = result.unwrap_err();
    // check defaults to 0, error defaults to "unknown"
    assert!(err.contains("check 0"));
    assert!(err.contains("unknown"));
}

#[test]
fn boot_timeout_nonblocking_accept_fails_immediately() {
    // When no client connects and we use non-blocking mode, accept returns
    // WouldBlock immediately — this tests the error path of wait_for_boot
    // when no guest is running.
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();

    let result = listener.accept();
    assert!(result.is_err(), "accept with no client should fail");
    let err = result.unwrap_err();
    assert_eq!(
        err.kind(),
        std::io::ErrorKind::WouldBlock,
        "error should be WouldBlock, got: {err}"
    );
}

#[test]
fn boot_timeout_threaded() {
    // Simulate a timeout scenario: bind listener, spawn a thread to accept
    // with a short deadline, and verify it times out when no client connects.
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();

    let start = std::time::Instant::now();
    let deadline = Duration::from_millis(200);

    // Poll for accept until deadline
    let mut accepted = false;
    while start.elapsed() < deadline {
        match listener.accept() {
            Ok(_) => {
                accepted = true;
                break;
            }
            Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(10));
            }
            Err(_) => break,
        }
    }

    assert!(!accepted, "should not have accepted a connection");
    assert!(
        start.elapsed() >= Duration::from_millis(190),
        "should have polled for ~200ms, elapsed: {:?}",
        start.elapsed()
    );
}

// ── send_identity protocol ───────────────────────────────────────────────────

#[test]
fn identity_send_protocol() {
    // Simulate the guest side: listen for identity on a TCP port
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();

    let assignment = IdentityAssignment {
        alias: "agent-5".to_string(),
        cid: 5,
    };
    let expected_json = assignment.to_json();

    let handle = thread::spawn(move || {
        thread::sleep(Duration::from_millis(50));
        let mut stream =
            TcpStream::connect(format!("127.0.0.1:{port}")).unwrap();
        // Send identity payload (what send_identity does)
        stream
            .write_all(expected_json.as_bytes())
            .unwrap();
        stream.shutdown(std::net::Shutdown::Write).unwrap();
    });

    let (mut stream, _) = listener.accept().unwrap();
    let mut buf = String::new();
    std::io::Read::read_to_string(&mut stream, &mut buf).unwrap();

    handle.join().unwrap();

    let v: serde_json::Value = serde_json::from_str(&buf).unwrap();
    assert_eq!(v["alias"], "agent-5");
    assert_eq!(v["cid"], 5);
    assert_eq!(v["assigned_by"], "haystack-host");
}

// ── end-to-end CID → VsockAddr → TCP port pipeline ──────────────────────────

#[test]
fn full_cid_to_tcp_pipeline() {
    let dir = TempDir::new().unwrap();

    // Simulate multiple VM launches, each getting unique ports
    let mut boot_ports = Vec::new();
    let mut identity_ports = Vec::new();

    for _ in 0..5 {
        let cid = simulate_next_cid(dir.path()).unwrap();
        let boot_addr = VsockAddr::new(cid, 52);
        let identity_addr = VsockAddr::new(cid, 55);

        boot_ports.push(boot_addr.to_tcp_port());
        identity_ports.push(identity_addr.to_tcp_port());
    }

    // All boot ports unique
    let unique_boot: std::collections::HashSet<u16> = boot_ports.iter().copied().collect();
    assert_eq!(unique_boot.len(), 5, "all boot ports should be unique");

    // All identity ports unique
    let unique_id: std::collections::HashSet<u16> = identity_ports.iter().copied().collect();
    assert_eq!(unique_id.len(), 5, "all identity ports should be unique");

    // No overlap between boot and identity ports
    for bp in &boot_ports {
        assert!(
            !identity_ports.contains(bp),
            "boot port {bp} should not collide with any identity port"
        );
    }
}

// ── error paths ──────────────────────────────────────────────────────────────

#[test]
fn next_cid_readonly_dir_fails() {
    // On macOS/Linux, writing to a read-only directory should fail
    let dir = TempDir::new().unwrap();
    let ro_dir = dir.path().join("readonly");
    fs::create_dir(&ro_dir).unwrap();

    // Make directory read-only
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&ro_dir, fs::Permissions::from_mode(0o444)).unwrap();
    }

    let result = simulate_next_cid(&ro_dir);

    // Restore permissions before assertion so TempDir can clean up
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&ro_dir, fs::Permissions::from_mode(0o755)).unwrap();
    }

    assert!(result.is_err(), "writing to read-only dir should fail");
}

#[test]
fn cmdline_parse_large_cid_value() {
    let raw = "haystack.cid=4294967295"; // u32::MAX
    let kc = KernelCmdline::parse(raw).unwrap();
    assert_eq!(kc.cid, u32::MAX);
}

#[test]
fn cmdline_parse_cid_overflow() {
    // u32::MAX + 1 doesn't fit in u32 → parse fails → treated as missing
    let raw = "haystack.cid=4294967296";
    let result = KernelCmdline::parse(raw);
    assert!(result.is_err());
}

#[test]
fn identity_empty_alias() {
    let assignment = IdentityAssignment {
        alias: String::new(),
        cid: 1,
    };
    let json_str = assignment.to_json();
    let v: serde_json::Value = serde_json::from_str(&json_str).unwrap();
    assert_eq!(v["alias"], "");
    assert_eq!(v["cid"], 1);
}
