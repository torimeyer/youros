"""Cryptographic git, ostk trace, and kernel audit exit-zero receipt verifier (task ->3092).

Verifies task completion against ground-truth repository and kernel artifacts:
1. Git commits ahead of base commit with diff stats and changed file list.
2. Task attribution via commit message/body, ostk trace, or .ostk stores.
3. Real test runner execution with latest exit code 0 in .ostk/audit.jsonl.

Chat prose heuristics (receipts_gate.py, receipts_check.py) remain separate;
prose claims in chat never satisfy this verifier.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import subprocess
from pathlib import Path
from typing import Optional


_TEST_RUNNER_RE = re.compile(
    r"(?:"
    r"\bpytest\b"
    r"|\bvitest\b"
    r"|run-vitest\.sh"
    r"|\bnpm\s+(?:run\s+)?test\b"
    r"|\bnpx\s+tsc\b"
    r"|\btsc\s+-b\b"
    r"|\bcargo\s+test\b"
    r"|\bgo\s+test\b"
    r"|e2e_smoke\.sh"
    r")"
)

_HEX_SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")


@dataclass
class GitReceiptResult:
    verified: bool
    commits: list[dict]
    diff_stat: str
    changed_files: list[str]
    trace_linked: bool
    test_runs: list[dict]
    missing_reasons: list[str]
    repo_root: str
    worktree_path: Optional[str] = None
    branch_name: Optional[str] = None
    base_sha: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "verified": self.verified,
            "commits": list(self.commits),
            "diff_stat": self.diff_stat,
            "changed_files": list(self.changed_files),
            "trace_linked": self.trace_linked,
            "test_runs": list(self.test_runs),
            "missing_reasons": list(self.missing_reasons),
            "repo_root": self.repo_root,
            "worktree_path": self.worktree_path,
            "branch_name": self.branch_name,
            "base_sha": self.base_sha,
        }


def _run_git(cwd: Path, *args: str, timeout: float = 5.0) -> Optional[subprocess.CompletedProcess[str]]:
    if not cwd.exists():
        return None
    try:
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _rev_parse(cwd: Path, rev: str) -> Optional[str]:
    proc = _run_git(cwd, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    if proc is None or proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    return out.splitlines()[0] if out else None


def _normalize_task_num(task_id: str) -> str:
    return re.sub(r"^(?:\u2192|#|t-)+", "", str(task_id).strip())


def _text_references_task(text: str, task_id: str, task_num: str) -> bool:
    if not text:
        return False
    if task_num:
        if re.search(rf"\u2192{re.escape(task_num)}(?!\d)", text):
            return True
        if re.search(rf"#{re.escape(task_num)}(?!\d)", text):
            return True
        if re.search(rf"\bt-{re.escape(task_num)}(?!\d)", text):
            return True
        if re.search(rf"(?<![0-9a-zA-Z]){re.escape(task_num)}(?![0-9a-zA-Z])", text):
            return True
    raw = str(task_id).strip()
    if raw and re.search(rf"(?<![0-9a-zA-Z]){re.escape(raw)}(?![0-9a-zA-Z])", text):
        return True
    return False


def _commit_mentions_task(cwd: Path, ref: str, task_id: str) -> bool:
    task_num = _normalize_task_num(task_id)
    proc = _run_git(cwd, "log", "-n", "1", "--format=%s%n%b", ref)
    if proc is None or proc.returncode != 0:
        return False
    return _text_references_task(proc.stdout, task_id, task_num)


def _collect_git_commits(
    target_git_dir: Path,
    branch_name: Optional[str],
    base_sha: Optional[str],
    task_id: Optional[str],
) -> tuple[list[dict], list[str], str, list[str]]:
    """Return (commits, commit_full_texts, diff_stat, changed_files)."""
    ref = "HEAD"
    if branch_name and _rev_parse(target_git_dir, branch_name) is not None:
        ref = branch_name

    ref_sha = _rev_parse(target_git_dir, ref)
    if ref_sha is None:
        return [], [], "", []

    effective_base: Optional[str] = None
    single_root_commit = False

    if base_sha:
        if _rev_parse(target_git_dir, base_sha) is None:
            return [], [], "", []
        effective_base = base_sha
    elif branch_name:
        base_branch = None
        for candidate in ("main", "master"):
            if _rev_parse(target_git_dir, candidate) is not None:
                base_branch = candidate
                break
        if base_branch and _rev_parse(target_git_dir, base_branch) != ref_sha:
            effective_base = base_branch
        elif task_id and _commit_mentions_task(target_git_dir, ref, task_id):
            if _rev_parse(target_git_dir, f"{ref}~1") is not None:
                effective_base = f"{ref}~1"
            else:
                single_root_commit = True
        else:
            return [], [], "", []
    else:
        base_branch = None
        for candidate in ("main", "master"):
            if _rev_parse(target_git_dir, candidate) is not None:
                base_branch = candidate
                break
        if base_branch and _rev_parse(target_git_dir, base_branch) != ref_sha:
            effective_base = base_branch
        elif task_id and _commit_mentions_task(target_git_dir, ref, task_id):
            if _rev_parse(target_git_dir, f"{ref}~1") is not None:
                effective_base = f"{ref}~1"
            else:
                single_root_commit = True
        else:
            return [], [], "", []

    log_fmt = "%H\x1f%s\x1f%cI\x1f%b\x1e"
    if single_root_commit:
        log_proc = _run_git(target_git_dir, "log", "-n", "1", f"--format={log_fmt}", ref)
    else:
        assert effective_base is not None
        log_proc = _run_git(target_git_dir, "log", f"--format={log_fmt}", f"{effective_base}..{ref}")

    if log_proc is None or log_proc.returncode != 0 or not log_proc.stdout.strip():
        return [], [], "", []

    commits: list[dict] = []
    commit_texts: list[str] = []
    for raw_record in log_proc.stdout.split("\x1e"):
        record = raw_record.strip()
        if not record:
            continue
        parts = record.split("\x1f")
        if len(parts) < 3:
            continue
        full_sha = parts[0].strip()
        subject = parts[1].strip()
        timestamp = parts[2].strip()
        body = parts[3].strip() if len(parts) > 3 else ""
        if not full_sha:
            continue
        commits.append({
            "sha": full_sha,
            "short_sha": full_sha[:8],
            "subject": subject,
            "timestamp": timestamp,
        })
        commit_texts.append(f"{subject}\n{body}".strip())

    if not commits:
        return [], [], "", []

    if single_root_commit:
        stat_proc = _run_git(target_git_dir, "show", "--stat", "--format=", ref)
        names_proc = _run_git(target_git_dir, "diff-tree", "--no-commit-id", "--name-only", "-r", ref)
    else:
        assert effective_base is not None
        stat_proc = _run_git(target_git_dir, "diff", "--stat", f"{effective_base}..{ref}")
        names_proc = _run_git(target_git_dir, "diff", "--name-only", f"{effective_base}..{ref}")

    diff_stat = stat_proc.stdout.strip() if (stat_proc and stat_proc.returncode == 0) else ""
    changed_files = (
        [line.strip() for line in names_proc.stdout.splitlines() if line.strip()]
        if (names_proc and names_proc.returncode == 0)
        else []
    )
    return commits, commit_texts, diff_stat, changed_files


def _check_ostk_trace_or_store_link(
    target_git_dir: Path,
    repo_root: Path,
    worktree_path: Optional[Path],
    task_id: str,
    task_num: str,
    commits: list[dict],
) -> bool:
    commit_shas = [c["sha"] for c in commits]
    short_shas = [c["short_sha"] for c in commits]
    short7_shas = [c["sha"][:7] for c in commits]

    # 1. Try `ostk trace ->task_num` with 3s timeout
    if task_num:
        try:
            proc = subprocess.run(
                ["ostk", "trace", f"\u2192{task_num}"],
                cwd=str(target_git_dir),
                capture_output=True,
                text=True,
                timeout=3.0,
            )
            if proc.returncode == 0 and proc.stdout:
                out = proc.stdout
                for s in short_shas + short7_shas:
                    if s and s in out:
                        return True
                for line in out.splitlines():
                    low = line.lower()
                    if "commit" in low and _HEX_SHA_RE.search(line):
                        return True
        except (OSError, subprocess.SubprocessError):
            pass

    # 2. Check .ostk/issues.jsonl, .ostk/needles/issues.jsonl, and .ostk/audit.jsonl
    roots = [repo_root]
    if worktree_path and worktree_path.exists():
        roots.append(worktree_path)

    seen_files: set[Path] = set()
    for r in roots:
        for rel in (
            Path(".ostk") / "issues.jsonl",
            Path(".ostk") / "needles" / "issues.jsonl",
        ):
            p = r / rel
            if not p.exists():
                continue
            try:
                resolved = p.resolve()
            except OSError:
                resolved = p
            if resolved in seen_files:
                continue
            seen_files.add(resolved)
            try:
                for raw_line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    entry_id = _normalize_task_num(str(entry.get("id", "")))
                    if entry_id != task_num and str(entry.get("id", "")) != str(task_id):
                        continue
                    commit_ref = str(entry.get("commit_ref") or "").strip()
                    if commit_ref:
                        if not commit_shas:
                            return True
                        if any(sha.startswith(commit_ref) or commit_ref.startswith(sha[:7]) for sha in commit_shas):
                            return True
            except OSError:
                continue

        audit_p = r / ".ostk" / "audit.jsonl"
        if audit_p.exists():
            try:
                resolved_audit = audit_p.resolve()
            except OSError:
                resolved_audit = audit_p
            if resolved_audit not in seen_files:
                seen_files.add(resolved_audit)
                try:
                    for raw_line in audit_p.read_text(encoding="utf-8", errors="replace").splitlines():
                        line = raw_line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        ev = str(entry.get("event") or entry.get("event_type") or "").lower()
                        if "commit" not in ev and "trace" not in ev and "needle" not in ev:
                            continue
                        dumped = json.dumps(entry, ensure_ascii=False)
                        if _text_references_task(dumped, task_id, task_num):
                            if not short7_shas or any(s in dumped for s in short7_shas):
                                return True
                except OSError:
                    pass

    return False


def _extract_command_string(entry: dict) -> Optional[str]:
    for key in ("cmd", "command", "shell_command", "argv"):
        val = entry.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, list) and val:
            return " ".join(str(x) for x in val if x is not None).strip()

    for nested_key in ("input", "args", "detail", "data"):
        nested = entry.get(nested_key)
        if isinstance(nested, dict):
            for key in ("cmd", "command", "shell_command", "argv"):
                val = nested.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
                if isinstance(val, list) and val:
                    return " ".join(str(x) for x in val if x is not None).strip()
    return None


def _extract_exit_code(entry: dict) -> Optional[int]:
    containers: list[dict] = [entry]
    for nested_key in ("result", "output", "detail", "data"):
        nested = entry.get(nested_key)
        if isinstance(nested, dict):
            containers.append(nested)

    for container in containers:
        for key in ("exit_code", "returncode", "exitCode", "return_code", "code"):
            if key in container and container[key] is not None and not isinstance(container[key], bool):
                try:
                    return int(container[key])
                except (ValueError, TypeError):
                    continue

    for container in containers:
        status = container.get("status")
        if isinstance(status, str):
            low = status.strip().lower()
            if low in ("ok", "success", "passed", "completed"):
                return 0
            if low in ("error", "failed", "failure", "timeout"):
                return 1
    return None


def _collect_audit_test_runs(repo_root: Path, worktree_path: Optional[Path]) -> list[dict]:
    candidate_paths: list[Path] = [repo_root / ".ostk" / "audit.jsonl"]
    if worktree_path is not None:
        candidate_paths.append(worktree_path / ".ostk" / "audit.jsonl")

    seen_resolved: set[Path] = set()
    per_file_runs: list[list[dict]] = []

    for audit_path in candidate_paths:
        if not audit_path.exists():
            continue
        try:
            resolved = audit_path.resolve()
        except OSError:
            resolved = audit_path
        if resolved in seen_resolved:
            continue
        seen_resolved.add(resolved)

        file_runs: list[dict] = []
        try:
            lines = audit_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            cmd_str = _extract_command_string(entry)
            if not cmd_str or not _TEST_RUNNER_RE.search(cmd_str):
                continue
            exit_code = _extract_exit_code(entry)
            if exit_code is None:
                continue
            ts = str(entry.get("timestamp") or entry.get("ts") or "")
            file_runs.append({
                "command": cmd_str,
                "exit_code": exit_code,
                "timestamp": ts,
            })
        if file_runs:
            per_file_runs.append(file_runs)

    if not per_file_runs:
        return []
    if len(per_file_runs) == 1:
        return per_file_runs[0]

    combined = [run for group in per_file_runs for run in group]
    if all(r.get("timestamp") for r in combined):
        combined.sort(key=lambda r: r["timestamp"])
    return combined


def verify_task_receipt(
    repo_root: Path,
    worktree_path: Optional[Path] = None,
    branch_name: Optional[str] = None,
    base_sha: Optional[str] = None,
    task_id: Optional[str] = None,
) -> GitReceiptResult:
    """Verify cryptographic git commit, task attribution, and exit-0 audit test proof."""
    repo_path = Path(repo_root)
    wt_path = Path(worktree_path) if worktree_path else None
    target_git_dir = wt_path if (wt_path and wt_path.exists()) else repo_path

    missing_reasons: list[str] = []

    # 1. Git Commit Verification
    commits, commit_texts, diff_stat, changed_files = _collect_git_commits(
        target_git_dir=target_git_dir,
        branch_name=branch_name,
        base_sha=base_sha,
        task_id=task_id,
    )
    if not commits:
        missing_reasons.append("No git commits found ahead of base commit")

    # 2. Task Attribution
    if task_id is None or not str(task_id).strip():
        trace_linked = True
    else:
        task_num = _normalize_task_num(task_id)
        linked_in_commits = any(
            _text_references_task(txt, str(task_id), task_num)
            for txt in commit_texts
        )
        if linked_in_commits:
            trace_linked = True
        else:
            trace_linked = _check_ostk_trace_or_store_link(
                target_git_dir=target_git_dir,
                repo_root=repo_path,
                worktree_path=wt_path,
                task_id=str(task_id),
                task_num=task_num,
                commits=commits,
            )
        if not trace_linked:
            missing_reasons.append("No ostk trace or commit attribution linking task")

    # 3. Kernel Audit Exit-Zero Test Verification
    test_runs = _collect_audit_test_runs(repo_root=repo_path, worktree_path=wt_path)
    if not test_runs:
        missing_reasons.append("No test execution recorded in .ostk/audit.jsonl")
    elif test_runs[-1]["exit_code"] != 0:
        missing_reasons.append("Latest recorded test command exited non-zero")

    verified = len(missing_reasons) == 0

    return GitReceiptResult(
        verified=verified,
        commits=commits,
        diff_stat=diff_stat,
        changed_files=changed_files,
        trace_linked=trace_linked,
        test_runs=test_runs,
        missing_reasons=missing_reasons,
        repo_root=str(repo_path),
        worktree_path=str(wt_path) if wt_path is not None else None,
        branch_name=branch_name,
        base_sha=base_sha,
    )
