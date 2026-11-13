"""Runtime-neutral agent worktree lifecycle, merge-and-reap, save-point reset, and decision pinning (->3091).

Provides workspace orchestration across both torios itself and any external git
repository without polluting tracked files or leaking branches across repos.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from services.youros_paths import youros_home

logger = logging.getLogger(__name__)

_SAFE_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")
_WORKTREE_SLUG_MAX_LEN = 30

_GIT_ENV_STRIP_KEYS = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    }
)


class MergeConflictError(ValueError):
    """Raised when merging an agent branch produces conflicts and is aborted."""


class BranchNotFoundError(ValueError):
    """Raised when the requested agent branch does not exist in the target repo."""


@dataclass
class WorktreeLease:
    """Metadata describing a provisioned git worktree and its save-point commit."""

    repo_root: Path
    worktree_path: Path
    branch_name: str
    base_sha: str
    created: bool


def _clean_git_env() -> dict[str, str]:
    """Return an environment dict without repo-binding GIT_* variables."""
    return {k: v for k, v in os.environ.items() if k not in _GIT_ENV_STRIP_KEYS}


def _run_git(
    repo_or_wt: Path,
    *args: str,
    timeout: float = 15.0,
) -> subprocess.CompletedProcess[str]:
    """Execute git -C <path> <args> with sanitized environment."""
    return subprocess.run(
        ["git", "-C", str(repo_or_wt), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=_clean_git_env(),
    )


def _is_valid_git_repo(path: Path) -> bool:
    """Return True when path is an existing directory inside a valid git repository."""
    if not path.exists() or not path.is_dir():
        return False
    try:
        res = _run_git(path, "rev-parse", "--git-dir", timeout=5.0)
        return res.returncode == 0
    except Exception:
        return False


def _safe_agent_slug(agent_name: str, max_len: int = _WORKTREE_SLUG_MAX_LEN) -> str:
    """Convert agent_name into a deterministic, filesystem-safe slug (<= 30 chars)."""
    cleaned = _SAFE_SLUG_RE.sub("-", (agent_name or "").strip()).strip("-.")
    if not cleaned:
        raise ValueError("agent_name must contain at least one alphanumeric character")
    if len(cleaned) <= max_len:
        return cleaned
    digest = hashlib.blake2s(cleaned.encode("utf-8"), digest_size=4).hexdigest()
    prefix = cleaned[: max_len - 9].rstrip("-_.")
    return f"{prefix}-{digest}"


def _ensure_youros_git_excluded(repo_root: Path) -> None:
    """Append .youros/ to <repo_root>/.git/info/exclude when .git/info exists.

    Keeps git status clean inside any external repository without touching the
    project's tracked .gitignore file.
    """
    info_dir = repo_root / ".git" / "info"
    if not info_dir.is_dir():
        # Handle linked worktrees where .git is a pointer file by checking common-dir.
        common_res = _run_git(repo_root, "rev-parse", "--git-common-dir", timeout=5.0)
        if common_res.returncode == 0 and common_res.stdout.strip():
            raw_common = Path(common_res.stdout.strip())
            common_dir = raw_common if raw_common.is_absolute() else (repo_root / raw_common)
            candidate = common_dir / "info"
            if candidate.is_dir():
                info_dir = candidate
    if not info_dir.is_dir():
        return

    exclude_path = info_dir / "exclude"
    try:
        existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
        lines = {line.strip() for line in existing.splitlines()}
        if ".youros/" in lines or ".youros" in lines:
            return
        prefix = "" if (not existing or existing.endswith("\n")) else "\n"
        exclude_path.write_text(f"{existing}{prefix}.youros/\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("spawn_pipeline.exclude_write_failed repo=%s err=%s", repo_root, exc)


def resolve_target_repo(explicit_repo: Optional[str] = None) -> Path:
    """Resolve the target git repository path for agent operations.

    Precedence:
      1. explicit_repo argument when non-empty
      2. OSTK_PROJECT_ROOT environment variable when pointing to an existing dir
      3. active_repo_path in user settings when pointing to a valid git repo
      4. config.PROJECT_ROOT fallback
    """
    if explicit_repo and str(explicit_repo).strip():
        return Path(str(explicit_repo).strip()).expanduser().resolve()

    env_root = os.environ.get("OSTK_PROJECT_ROOT", "").strip()
    if env_root:
        env_path = Path(env_root).expanduser().resolve()
        if env_path.exists() and env_path.is_dir():
            return env_path

    try:
        from services import settings_store as ss_mod

        loader = getattr(ss_mod, "load_settings", None)
        settings_data = loader() if callable(loader) else ss_mod.settings_store.load()
        active_raw = (settings_data or {}).get("active_repo_path")
        if active_raw and str(active_raw).strip():
            active_path = Path(str(active_raw).strip()).expanduser().resolve()
            if _is_valid_git_repo(active_path):
                return active_path
    except Exception as exc:
        logger.debug("spawn_pipeline.settings_repo_lookup_failed err=%s", exc)

    import config

    return Path(config.PROJECT_ROOT).expanduser().resolve()


def provision_agent_worktree(
    repo_root: Path,
    agent_name: str,
    base_ref: str = "HEAD",
) -> WorktreeLease:
    """Provision an isolated git worktree under <repo_root>/.youros/worktrees/agent-<slug>."""
    repo = Path(os.path.abspath(os.path.expanduser(str(repo_root))))
    if not _is_valid_git_repo(repo):
        raise ValueError(f"Target path is not a valid git repository: {repo}")

    safe_slug = _safe_agent_slug(agent_name)
    worktree_path = repo / ".youros" / "worktrees" / f"agent-{safe_slug}"
    branch_name = f"worktree-agent-{safe_slug}"

    _ensure_youros_git_excluded(repo)

    rev_res = _run_git(repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
    if rev_res.returncode != 0 or not rev_res.stdout.strip():
        raise ValueError(
            f"Cannot resolve base_ref '{base_ref}' in {repo}: {rev_res.stderr.strip()}"
        )
    base_sha = rev_res.stdout.strip()

    # Clean stale registered worktree metadata before provisioning.
    _run_git(repo, "worktree", "prune")

    if worktree_path.exists():
        head_branch = _run_git(worktree_path, "rev-parse", "--abbrev-ref", "HEAD")
        if head_branch.returncode == 0 and head_branch.stdout.strip() == branch_name:
            return WorktreeLease(
                repo_root=repo,
                worktree_path=worktree_path,
                branch_name=branch_name,
                base_sha=base_sha,
                created=False,
            )
        # Stale or broken directory at worktree_path: clean up before fresh add.
        _run_git(repo, "worktree", "unlock", str(worktree_path))
        _run_git(repo, "worktree", "remove", "--force", str(worktree_path))
        if worktree_path.exists():
            shutil.rmtree(worktree_path, ignore_errors=True)
        _run_git(repo, "worktree", "prune")

    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    add_res = _run_git(
        repo,
        "worktree",
        "add",
        "-B",
        branch_name,
        str(worktree_path),
        base_sha,
    )
    if add_res.returncode != 0:
        raise ValueError(
            f"git worktree add failed for '{branch_name}' in {repo}: {add_res.stderr.strip()}"
        )

    return WorktreeLease(
        repo_root=repo,
        worktree_path=worktree_path,
        branch_name=branch_name,
        base_sha=base_sha,
        created=True,
    )


def build_short_socket_env(
    base_env: dict[str, str],
    worktree_path: Path,
    agent_name: str,
) -> dict[str, str]:
    """Return a copy of base_env with OSTK_PROJECT_ROOT and a short YOUROS_SOCKET_DIR.

    Keeps the Unix domain socket directory well under 60 characters so binding
    never trips macOS's 104-byte sun_path limit even for deeply nested repos.
    """
    env = dict(base_env)
    wt_str = str(worktree_path)
    seed = f"{os.path.abspath(wt_str)}:{agent_name}"
    digest8 = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]
    sock_dir = Path(f"/tmp/yo-sock-{digest8}")
    try:
        sock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        logger.debug("spawn_pipeline.sock_dir_mkdir_failed dir=%s err=%s", sock_dir, exc)

    env["OSTK_PROJECT_ROOT"] = wt_str
    env["YOUROS_SOCKET_DIR"] = str(sock_dir)
    env.setdefault("YOUROS_AGENT_NAME", agent_name)
    return env


def _discover_worktree_for_branch(repo_root: Path, branch_name: str) -> Optional[Path]:
    """Locate the registered worktree path for branch_name via git worktree list."""
    res = _run_git(repo_root, "worktree", "list", "--porcelain")
    if res.returncode != 0:
        return None
    cur_wt: Optional[str] = None
    expected_ref = f"refs/heads/{branch_name}"
    for line in res.stdout.splitlines():
        if not line.strip():
            cur_wt = None
            continue
        if line.startswith("worktree "):
            cur_wt = line[len("worktree ") :].strip()
        elif line.startswith("branch ") and cur_wt:
            ref = line[len("branch ") :].strip()
            if ref == expected_ref and cur_wt != str(repo_root):
                return Path(cur_wt)
    return None


def _determine_target_branch(
    repo_root: Path,
    branch_name: str,
    explicit_target: Optional[str] = None,
) -> str:
    if explicit_target and explicit_target.strip():
        return explicit_target.strip()
    sym = _run_git(repo_root, "symbolic-ref", "--short", "HEAD")
    if sym.returncode == 0:
        cur = sym.stdout.strip()
        if cur and cur != branch_name:
            return cur
    for candidate in ("main", "master"):
        chk = _run_git(repo_root, "rev-parse", "--verify", f"refs/heads/{candidate}")
        if chk.returncode == 0:
            return candidate
    raise ValueError(f"Could not determine target branch for merge in {repo_root}")


def merge_and_reap_worktree(
    repo_root: Path,
    branch_name: str,
    worktree_path: Optional[Path] = None,
    target_branch: Optional[str] = None,
) -> dict:
    """Merge branch_name into repo_root's target branch and reap its worktree + branch.

    Aborts cleanly and preserves the worktree if merge conflicts occur.
    """
    repo = Path(os.path.abspath(os.path.expanduser(str(repo_root))))
    if not _is_valid_git_repo(repo):
        raise ValueError(f"Target path is not a valid git repository: {repo}")

    branch = (branch_name or "").strip()
    if not branch:
        raise ValueError("branch_name is required")

    verify_branch = _run_git(repo, "rev-parse", "--verify", f"refs/heads/{branch}")
    if verify_branch.returncode != 0:
        raise BranchNotFoundError(f"Branch '{branch}' does not exist in {repo}")

    resolved_target = _determine_target_branch(repo, branch, target_branch)
    if resolved_target == branch:
        raise ValueError(f"Cannot merge branch '{branch}' into itself")

    wt_path = (
        Path(os.path.abspath(os.path.expanduser(str(worktree_path))))
        if worktree_path is not None
        else _discover_worktree_for_branch(repo, branch)
    )

    # Ensure repo_root has resolved_target checked out before merging.
    cur_sym = _run_git(repo, "symbolic-ref", "--short", "HEAD")
    if cur_sym.returncode != 0 or cur_sym.stdout.strip() != resolved_target:
        co = _run_git(repo, "checkout", resolved_target)
        if co.returncode != 0:
            raise ValueError(
                f"Could not checkout target branch '{resolved_target}' in {repo}: {co.stderr.strip()}"
            )

    ahead_res = _run_git(repo, "rev-list", "--count", f"{resolved_target}..{branch}")
    if ahead_res.returncode != 0:
        raise ValueError(
            f"Could not compare '{resolved_target}..{branch}' in {repo}: {ahead_res.stderr.strip()}"
        )
    ahead_count = int(ahead_res.stdout.strip() or "0")

    already_absorbed = False
    if ahead_count == 0:
        already_absorbed = True
        head_res = _run_git(repo, "rev-parse", "HEAD")
        merged_sha = head_res.stdout.strip()
    else:
        email_chk = _run_git(repo, "config", "user.email")
        git_cfg_args: list[str] = []
        if email_chk.returncode != 0 or not email_chk.stdout.strip():
            git_cfg_args = ["-c", "user.name=yourOS", "-c", "user.email=youros@local"]

        merge_res = _run_git(repo, *git_cfg_args, "merge", "--no-edit", branch, timeout=30.0)
        if merge_res.returncode != 0:
            combined_out = f"{merge_res.stdout}\n{merge_res.stderr}".strip()
            _run_git(repo, "merge", "--abort")
            raise MergeConflictError(
                f"Merge conflict merging '{branch}' into '{resolved_target}': {combined_out[:300]}"
            )
        head_res = _run_git(repo, "rev-parse", "HEAD")
        merged_sha = head_res.stdout.strip()

    # Reap worktree and delete branch only after merge succeeded (or was already absorbed).
    if wt_path is not None:
        _run_git(repo, "worktree", "unlock", str(wt_path))
        if wt_path.exists():
            _run_git(repo, "worktree", "remove", "--force", str(wt_path), timeout=20.0)
            if wt_path.exists():
                shutil.rmtree(wt_path, ignore_errors=True)
    _run_git(repo, "worktree", "prune")
    _run_git(repo, "branch", "-D", branch)

    return {
        "merged": True,
        "merged_sha": merged_sha,
        "branch_name": branch,
        "target_branch": resolved_target,
        "already_absorbed": already_absorbed,
        "reaped": True,
        "repo_root": str(repo),
    }


def reset_to_save_point(
    worktree_path: Path,
    base_sha: str,
    repo_root: Optional[Path] = None,
) -> dict:
    """Hard-reset worktree_path to base_sha and remove untracked files."""
    wt = Path(os.path.abspath(os.path.expanduser(str(worktree_path))))
    if not wt.exists() or not wt.is_dir():
        raise ValueError(f"Worktree path does not exist: {wt}")

    sha = (base_sha or "").strip()
    if not sha or sha.startswith("-"):
        raise ValueError(f"Invalid base_sha: {base_sha!r}")

    verify = _run_git(wt, "rev-parse", "--verify", f"{sha}^{{commit}}")
    if verify.returncode != 0 or not verify.stdout.strip():
        raise ValueError(f"Commit '{sha}' is not valid in worktree {wt}: {verify.stderr.strip()}")
    resolved_sha = verify.stdout.strip()

    reset_res = _run_git(wt, "reset", "--hard", resolved_sha)
    if reset_res.returncode != 0:
        raise ValueError(f"git reset --hard failed in {wt}: {reset_res.stderr.strip()}")

    clean_res = _run_git(wt, "clean", "-fd")
    if clean_res.returncode != 0:
        raise ValueError(f"git clean -fd failed in {wt}: {clean_res.stderr.strip()}")

    # Optional ostk recovery check (non-fatal when ostk is absent or uninitialized).
    assess_cwd = str(repo_root) if (repo_root and Path(repo_root).is_dir()) else str(wt)
    try:
        subprocess.run(
            ["ostk", "recovery", "assess", "--dry-run"],
            cwd=assess_cwd,
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except Exception as exc:
        logger.debug("spawn_pipeline.ostk_recovery_assess_skipped cwd=%s err=%s", assess_cwd, exc)

    return {
        "reset": True,
        "head_sha": resolved_sha,
        "worktree_path": str(wt),
    }


def pin_decision(
    repo_root: Path,
    key: str,
    value: str,
    reason: Optional[str] = None,
) -> dict:
    """Record an architectural decision in ostk (best-effort) and ~/.youros/memory/decisions.jsonl."""
    clean_key = (key or "").strip()
    clean_val = (value or "").strip()
    if not clean_key or not clean_val:
        raise ValueError("Both 'key' and 'value' are required to pin a decision")

    repo = Path(os.path.abspath(os.path.expanduser(str(repo_root))))
    clean_reason = reason.strip() if isinstance(reason, str) and reason.strip() else None

    ostk_recorded = False
    if repo.exists() and repo.is_dir():
        cmd = ["ostk", "decide", clean_key, clean_val]
        if clean_reason:
            cmd.extend(["--reason", clean_reason])
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            ostk_recorded = proc.returncode == 0
        except Exception as exc:
            logger.debug("spawn_pipeline.ostk_decide_skipped repo=%s err=%s", repo, exc)

    decisions_path = youros_home() / "memory" / "decisions.jsonl"
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo),
        "key": clean_key,
        "value": clean_val,
        "reason": clean_reason,
    }
    with open(decisions_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return {
        "pinned": True,
        "key": clean_key,
        "value": clean_val,
        "reason": clean_reason,
        "repo_root": str(repo),
        "decisions_path": str(decisions_path),
        "ostk_recorded": ostk_recorded,
    }


__all__ = [
    "MergeConflictError",
    "BranchNotFoundError",
    "WorktreeLease",
    "resolve_target_repo",
    "provision_agent_worktree",
    "build_short_socket_env",
    "merge_and_reap_worktree",
    "reset_to_save_point",
    "pin_decision",
]
