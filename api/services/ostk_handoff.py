"""Cross-runtime agent and task handoff built on native ostk handoff and work activate (->3095).

Reuses the exact git worktree, branch, and save-point base SHA across Claude and Gemini
subagents while carrying cryptographic git commits, failing test audit entries, and
pinned architectural decisions in the handoff brief.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Optional

from services import spawn_pipeline
from services.event_bus import AGENT_COMPLETED, AGENT_DELTA, AGENT_SPAWNED, bus as _event_bus
from services.runtime_provider import (
    SpawnNotSupportedError,
    SpawnRequest,
    SpawnResult,
)
from services.youros_paths import youros_home

logger = logging.getLogger(__name__)

_claude_handoff_tasks: set[asyncio.Task[Any]] = set()

_CLAUDE_STRIP_ENV_KEYS = frozenset({
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_API_KEY",
    "CLAUDECODE",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_ENTRYPOINT",
    "AI_AGENT",
})

_TEST_CMD_RE = re.compile(
    r"(?:\bpytest\b|\bvitest\b|run-vitest\.sh|\bnpm\s+(?:run\s+)?test\b|\btsc\s+-b\b|\bcargo\s+test\b|\bgo\s+test\b)"
)

_CONTEXT_LOAD_POINTER_RE = re.compile(r"\bostk\s+context-load\s+([a-zA-Z0-9_.:/-]+)")


def _normalize_context_pages(
    context_pages: Optional[list[str]],
    transition_note: Optional[str] = None,
) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for raw_name in context_pages or []:
        if not isinstance(raw_name, str):
            raise ValueError("Context page names must be strings.")
        cleaned = raw_name.strip()
        if not cleaned:
            continue
        if cleaned.startswith("-") or any(ch.isspace() or ord(ch) < 32 for ch in cleaned):
            raise ValueError(f"Invalid context page name: {raw_name!r}")
        if cleaned not in seen:
            seen.add(cleaned)
            ordered.append(cleaned)

    if transition_note and "ostk context-load" in transition_note:
        for match in _CONTEXT_LOAD_POINTER_RE.finditer(transition_note):
            cand = match.group(1).strip().strip("`'\",.;)")
            if cand and not cand.startswith("-") and cand != "<name>" and cand not in seen:
                seen.add(cand)
                ordered.append(cand)

    return ordered


def _normalize_task_num(raw_task_id: Optional[str]) -> str:
    if not raw_task_id:
        return ""
    return re.sub(r"^(?:\u2192|->|#|t-)+", "", str(raw_task_id).strip())


def _find_claude_binary() -> Optional[str]:
    found = shutil.which("claude")
    if found:
        return found
    home = Path.home()
    for cand in (
        home / ".local" / "bin" / "claude",
        Path("/opt/homebrew/bin/claude"),
        Path("/usr/local/bin/claude"),
    ):
        try:
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)
        except Exception:
            continue
    return None


def _build_claude_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _CLAUDE_STRIP_ENV_KEYS}
    return env


def _mint_handoff_agent_name(
    existing_names: set[str],
    source_agent: Optional[str],
    task_num: str,
    target_runtime: str,
) -> str:
    if source_agent:
        base = re.sub(r"-to-(?:gemini|claude)(?:-\d+)?$", "", source_agent)
        stem = f"{base}-to-{target_runtime}"
    elif task_num:
        stem = f"task-{task_num}-{target_runtime}"
    else:
        stem = f"handoff-{target_runtime}"

    if stem not in existing_names:
        return stem
    idx = 2
    while f"{stem}-{idx}" in existing_names:
        idx += 1
    return f"{stem}-{idx}"


def _find_latest_agent_for_task(
    agent_metadata: dict[str, dict],
    task_num: str,
) -> tuple[Optional[str], Optional[dict]]:
    if not task_num:
        return None, None
    best_name: Optional[str] = None
    best_meta: Optional[dict] = None
    for name, meta in reversed(list(agent_metadata.items())):
        if not isinstance(meta, dict):
            continue
        cand_tid = _normalize_task_num(meta.get("task_id"))
        cand_nid = _normalize_task_num(meta.get("needle_id"))
        cand_extra = [_normalize_task_num(x) for x in (meta.get("needle_ids") or [])]
        if task_num in {cand_tid, cand_nid, *cand_extra}:
            best_name = name
            best_meta = meta
            break
    return best_name, best_meta


def _run_ostk_handoff(
    target_cwd: Path,
    target_arg: str,
    transition_text: str,
) -> dict[str, Any]:
    cmd = ["ostk", "handoff", target_arg, "--transition", transition_text, "--json"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(target_cwd),
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        raw_out = (proc.stdout or "").strip()
        parsed: Any = None
        if raw_out:
            try:
                parsed = json.loads(raw_out)
            except json.JSONDecodeError:
                parsed = {"raw": raw_out[:1000]}
        return {
            "invoked": True,
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "envelope": parsed,
            "stderr": (proc.stderr or "").strip()[:500],
        }
    except Exception as exc:
        logger.debug("ostk_handoff.cli_degraded cwd=%s err=%s", target_cwd, exc)
        return {
            "invoked": True,
            "ok": False,
            "returncode": -1,
            "envelope": None,
            "stderr": str(exc)[:300],
        }


def _run_ostk_work_activate(target_cwd: Path, task_num: str) -> dict[str, Any]:
    if not task_num:
        return {"invoked": False, "ok": False, "briefing": ""}
    cmd = ["ostk", "work", "activate", f"\u2192{task_num}"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(target_cwd),
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        return {
            "invoked": True,
            "ok": proc.returncode == 0,
            "briefing": (proc.stdout or "").strip()[:2000],
            "stderr": (proc.stderr or "").strip()[:300],
        }
    except Exception as exc:
        logger.debug("ostk_handoff.work_activate_degraded cwd=%s err=%s", target_cwd, exc)
        return {
            "invoked": True,
            "ok": False,
            "briefing": "",
            "stderr": str(exc)[:300],
        }


def _collect_worktree_git_log(target_cwd: Path, base_sha: Optional[str]) -> tuple[str, str]:
    """Return (head_sha, git_log_oneline)."""
    head_sha = ""
    git_log = ""
    if not target_cwd.is_dir():
        return head_sha, git_log
    try:
        hp = subprocess.run(
            ["git", "-C", str(target_cwd), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if hp.returncode == 0 and hp.stdout.strip():
            head_sha = hp.stdout.strip()
    except Exception:
        pass

    try:
        log_args = (
            ["git", "-C", str(target_cwd), "log", "--oneline", f"{base_sha}..HEAD"]
            if base_sha
            else ["git", "-C", str(target_cwd), "log", "-n", "5", "--oneline"]
        )
        lp = subprocess.run(
            log_args,
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if lp.returncode == 0 and lp.stdout.strip():
            git_log = lp.stdout.strip()[:2000]
    except Exception:
        pass
    return head_sha, git_log


def _collect_latest_failing_test(repo_root: Path, worktree_path: Optional[Path]) -> str:
    candidates: list[Path] = []
    if worktree_path is not None:
        candidates.append(worktree_path / ".ostk" / "audit.jsonl")
    candidates.append(repo_root / ".ostk" / "audit.jsonl")

    latest_failure = ""
    seen: set[Path] = set()
    for audit_file in candidates:
        if not audit_file.is_file():
            continue
        try:
            resolved = audit_file.resolve()
        except OSError:
            resolved = audit_file
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            lines = audit_file.read_text(encoding="utf-8", errors="replace").splitlines()
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
            cmd_str = ""
            for k in ("cmd", "command", "shell_command"):
                v = entry.get(k)
                if isinstance(v, str) and v.strip():
                    cmd_str = v.strip()
                    break
            if not cmd_str or not _TEST_CMD_RE.search(cmd_str):
                continue
            rc = entry.get("exit_code")
            if rc is None:
                rc = entry.get("returncode")
            if rc is not None and int(rc) != 0:
                detail = str(
                    entry.get("stderr")
                    or entry.get("output")
                    or entry.get("error")
                    or ""
                ).strip()
                snippet = f" ({detail[:400]})" if detail else ""
                latest_failure = f"Command `{cmd_str}` exited {rc}{snippet}"
    return latest_failure


def _collect_pinned_decisions(repo_root: Path) -> list[str]:
    decisions_path = youros_home() / "memory" / "decisions.jsonl"
    if not decisions_path.is_file():
        return []
    repo_str = str(repo_root.resolve())
    items: list[str] = []
    try:
        for raw_line in decisions_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            entry_repo = str(entry.get("repo_root") or "").strip()
            if entry_repo:
                try:
                    if str(Path(entry_repo).resolve()) != repo_str:
                        continue
                except Exception:
                    continue
            key = str(entry.get("key") or "").strip()
            val = str(entry.get("value") or "").strip()
            reason = str(entry.get("reason") or "").strip()
            if key and val:
                suffix = f" (reason: {reason})" if reason else ""
                items.append(f"- {key} = {val}{suffix}")
    except OSError:
        return []
    return items[-10:]


def _compose_handoff_prompt(
    *,
    source_agent: Optional[str],
    source_runtime: Optional[str],
    target_runtime: str,
    task_id: Optional[str],
    transition_note: Optional[str],
    worktree_path: Path,
    branch_name: Optional[str],
    base_sha: Optional[str],
    head_sha: str,
    git_log: str,
    ostk_handoff_info: dict[str, Any],
    work_activate_info: dict[str, Any],
    latest_failing_test: str,
    pinned_decisions: list[str],
    prior_prompt: str,
    context_pages: Optional[list[str]] = None,
) -> str:
    sections: list[str] = [
        f"[CROSS-RUNTIME HANDOFF -> {target_runtime.upper()}]",
        (
            f"You are continuing work handed off from "
            f"{source_agent or 'prior session'}"
            f"{f' ({source_runtime})' if source_runtime else ''}."
        ),
        (
            f"Reused Git Worktree: {worktree_path}\n"
            f"Branch: {branch_name or 'HEAD'}\n"
            f"Save-Point Base SHA: {base_sha or 'unknown'}\n"
            f"Current HEAD SHA: {head_sha or 'unknown'}\n"
            "IMPORTANT: Do NOT reset or discard existing commits on this branch. Build directly on top of HEAD."
        ),
    ]
    if task_id:
        sections.append(f"Task ID: {task_id}")
    if transition_note:
        sections.append(f"Transition Instructions:\n{transition_note.strip()}")
    if context_pages and (
        not transition_note
        or "Pinned ostk context pages (load via ostk context-load <name>):" not in transition_note
    ):
        pointer_lines = [f"- ostk context-load {page}" for page in context_pages]
        sections.append(
            "Pinned ostk context pages (load via ostk context-load <name>):\n"
            + "\n".join(pointer_lines)
        )
    if git_log:
        sections.append(f"Existing Commits Ahead of Base:\n{git_log}")
    if work_activate_info.get("briefing"):
        sections.append(f"ostk work activate Briefing:\n{work_activate_info['briefing']}")
    if ostk_handoff_info.get("envelope"):
        sections.append(
            f"ostk handoff Kernel Envelope:\n{json.dumps(ostk_handoff_info['envelope'], ensure_ascii=False)}"
        )
    if latest_failing_test:
        sections.append(f"Latest Failing Test Receipt (.ostk/audit.jsonl):\n{latest_failing_test}")
    if pinned_decisions:
        sections.append("Pinned Architectural Decisions:\n" + "\n".join(pinned_decisions))
    if prior_prompt:
        sections.append(f"Original Task Brief:\n{prior_prompt[:1500]}")
    return "\n\n".join(sections)


async def _spawn_claude_reusing_worktree(req: SpawnRequest) -> SpawnResult:
    """Spawn a Claude CLI subagent directly inside an existing reused worktree."""
    import routers.agents as agents_mod

    claude_bin = _find_claude_binary()
    if not claude_bin:
        raise SpawnNotSupportedError(
            "Claude CLI binary was not found on this machine. "
            "Install Claude Code or switch your target runtime to Gemini in Settings."
        )

    extra = dict(req.extra or {})
    repo_root = spawn_pipeline.resolve_target_repo(req.repo_path)
    wt_raw = extra.get("reuse_worktree_path")
    worktree_path = Path(str(wt_raw)).expanduser().resolve() if wt_raw else repo_root
    branch_name = extra.get("reuse_branch_name") or extra.get("branch_name")
    base_sha = req.base_sha or extra.get("base_sha")

    resolved_model = req.model or "claude-sonnet-4-5"
    if resolved_model in {"", "default", "gemini", "gemini-3.1-pro"}:
        resolved_model = "claude-sonnet-4-5"

    composed_prompt = req.prompt or ""
    try:
        mailbox_short = agents_mod.agent_mailbox_instruction_short(req.name, model=resolved_model)
        if mailbox_short:
            composed_prompt = f"{composed_prompt}\n\n{mailbox_short}"
    except Exception:
        pass

    cmd: list[str] = [
        claude_bin,
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
        "--model",
        resolved_model,
        composed_prompt,
    ]

    proc_env = spawn_pipeline.build_short_socket_env(
        _build_claude_env(),
        worktree_path,
        req.name,
    )
    proc_env["CLAUDE_PROJECT_DIR"] = str(worktree_path)

    safe_name = spawn_pipeline._safe_agent_slug(req.name)
    transcripts_dir = youros_home() / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = transcripts_dir / f"{safe_name}.jsonl"
    transcript_path.touch()

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(worktree_path),
        env=proc_env,
        start_new_session=True,
    )
    pid = getattr(proc, "pid", None)
    now_iso = datetime.now(timezone.utc).isoformat()

    meta_record: dict[str, Any] = {
        "status": "running",
        "spawned_at": now_iso,
        "last_heartbeat_at": now_iso,
        "model": resolved_model,
        "runtime": "claude",
        "budget": str(req.budget),
        "tokens_used": 0,
        "source": str(extra.get("source") or "handoff"),
        "isolation": "worktree",
        "repo_path": str(repo_root),
        "worktree_path": str(worktree_path),
        "branch_name": branch_name,
        "worktree_branch": branch_name,
        "base_sha": base_sha,
        "pid": pid,
        "transcript_path": str(transcript_path),
        "transcript_path_source": "caller",
        "prompt": (req.prompt or "")[:4000],
    }
    if extra.get("task_id"):
        meta_record["task_id"] = extra["task_id"]
    if extra.get("needle_id"):
        meta_record["needle_id"] = extra["needle_id"]
    if extra.get("handed_off_from"):
        meta_record["handed_off_from"] = extra["handed_off_from"]

    agents_mod.agent_metadata[req.name] = meta_record
    agents_mod.active_agents[req.name] = proc

    await _event_bus.publish(
        AGENT_SPAWNED,
        {
            "name": req.name,
            "runtime": "claude",
            "model": resolved_model,
            "status": "running",
            "pid": pid,
            "worktree_path": str(worktree_path),
        },
    )
    await _event_bus.publish(AGENT_DELTA, {"name": req.name, "status": "running"})

    async def _watch_claude_handoff() -> None:
        try:
            if proc.stdout is not None:
                with open(transcript_path, "ab") as tfh:
                    while True:
                        line = await proc.stdout.readline()
                        if not line:
                            break
                        tfh.write(line)
                        if not line.endswith(b"\n"):
                            tfh.write(b"\n")
                        tfh.flush()
            if proc.stderr is not None:
                await proc.stderr.read()
            rc = await proc.wait()
        except Exception:
            rc = getattr(proc, "returncode", 1) or 1

        done_iso = datetime.now(timezone.utc).isoformat()
        final_status = "completed" if rc == 0 else "failed"
        agents_mod.active_agents.pop(req.name, None)
        live = agents_mod.agent_metadata.get(req.name)
        if live is not None and live.get("status") == "running":
            live["status"] = final_status
            live["completed_at"] = done_iso
            live["last_heartbeat_at"] = done_iso
            live["exit_code"] = rc
        await _event_bus.publish(AGENT_DELTA, {"name": req.name, "status": final_status, "terminal": True})
        await _event_bus.publish(
            AGENT_COMPLETED,
            {"name": req.name, "status": final_status, "runtime": "claude", "exit_code": rc},
        )

    watcher = asyncio.create_task(_watch_claude_handoff())
    _claude_handoff_tasks.add(watcher)
    watcher.add_done_callback(_claude_handoff_tasks.discard)

    return SpawnResult(
        name=req.name,
        pid=pid,
        status="running",
        worktree_path=str(worktree_path),
        transcript_path=str(transcript_path),
        branch_name=branch_name,
        base_sha=base_sha,
        repo_path=str(repo_root),
        detail={
            "result": f"Agent '{req.name}' spawned on Claude via handoff",
            "name": req.name,
            "pid": pid,
            "status": "running",
            "runtime": "claude",
            "model": resolved_model,
            "transcript": str(transcript_path),
            "worktree_path": str(worktree_path),
            "branch_name": branch_name,
            "base_sha": base_sha,
            "repo_path": str(repo_root),
        },
    )


async def execute_runtime_handoff(
    *,
    target_runtime: str,
    agent_name: Optional[str] = None,
    target_model: Optional[str] = None,
    task_id: Optional[str] = None,
    transition_note: Optional[str] = None,
    repo_path: Optional[str] = None,
    worktree_path: Optional[str] = None,
    branch_name: Optional[str] = None,
    base_sha: Optional[str] = None,
    context_pages: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Execute a cross-runtime handoff preserving worktree, commits, and ostk state."""
    import routers.agents as agents_mod

    norm_runtime = str(target_runtime or "").strip().lower()
    if norm_runtime not in {"gemini", "claude"}:
        raise ValueError(
            f"Unsupported target_runtime {target_runtime!r}; expected 'gemini' or 'claude'."
        )

    effective_context_pages = _normalize_context_pages(context_pages, transition_note)

    source_agent: Optional[str] = None
    source_meta: Optional[dict[str, Any]] = None

    if agent_name:
        canonical = agents_mod._resolve_agent_name(agent_name)
        source_meta = agents_mod.agent_metadata.get(canonical)
        if source_meta is None and not worktree_path:
            raise LookupError(f"Agent {agent_name!r} not found")
        source_agent = canonical
    elif task_id:
        tnum = _normalize_task_num(task_id)
        source_agent, source_meta = _find_latest_agent_for_task(agents_mod.agent_metadata, tnum)

    effective_task_id = (
        task_id
        or (source_meta or {}).get("task_id")
        or (source_meta or {}).get("needle_id")
    )
    task_num = _normalize_task_num(effective_task_id)

    explicit_repo = repo_path or (source_meta or {}).get("repo_path")
    resolved_repo = spawn_pipeline.resolve_target_repo(explicit_repo)

    new_agent_name = _mint_handoff_agent_name(
        existing_names=set(agents_mod.agent_metadata.keys()),
        source_agent=source_agent,
        task_num=task_num,
        target_runtime=norm_runtime,
    )

    raw_wt = worktree_path or (source_meta or {}).get("worktree_path")
    effective_branch = (
        branch_name
        or (source_meta or {}).get("branch_name")
        or (source_meta or {}).get("worktree_branch")
    )
    effective_base_sha = base_sha or (source_meta or {}).get("base_sha")

    if raw_wt and Path(raw_wt).expanduser().exists():
        effective_wt_path = Path(raw_wt).expanduser().resolve()
    else:
        lease = await asyncio.to_thread(
            spawn_pipeline.provision_agent_worktree,
            resolved_repo,
            new_agent_name,
            effective_base_sha or "HEAD",
        )
        effective_wt_path = lease.worktree_path
        effective_branch = lease.branch_name
        effective_base_sha = lease.base_sha

    target_cwd = effective_wt_path if effective_wt_path.is_dir() else resolved_repo
    target_arg = (target_model or "").strip() or norm_runtime
    transition_text = (
        (transition_note or "").strip()
        or f"Cross-runtime handoff from {source_agent or f'task {task_num}'} to {norm_runtime}"
    )

    ostk_handoff_info, work_activate_info = await asyncio.gather(
        asyncio.to_thread(_run_ostk_handoff, target_cwd, target_arg, transition_text),
        asyncio.to_thread(_run_ostk_work_activate, target_cwd, task_num),
    )

    if effective_context_pages:
        from services import ostk_context_pages

        for page_name in effective_context_pages:
            try:
                await ostk_context_pages.pin_context_page(page_name, cwd=target_cwd)
                await ostk_context_pages.restore_context_page(page_name, cwd=target_cwd)
            except Exception as exc:
                logger.debug(
                    "ostk_handoff.context_page_degraded page=%s err=%s",
                    page_name,
                    exc,
                )

    head_sha, git_log = await asyncio.to_thread(
        _collect_worktree_git_log,
        target_cwd,
        effective_base_sha,
    )
    latest_failing_test = await asyncio.to_thread(
        _collect_latest_failing_test,
        resolved_repo,
        effective_wt_path,
    )
    pinned_decisions = await asyncio.to_thread(_collect_pinned_decisions, resolved_repo)

    source_runtime = (source_meta or {}).get("runtime")
    prior_prompt = str((source_meta or {}).get("prompt") or "")

    handoff_prompt = _compose_handoff_prompt(
        source_agent=source_agent,
        source_runtime=source_runtime,
        target_runtime=norm_runtime,
        task_id=str(effective_task_id) if effective_task_id else None,
        transition_note=transition_note,
        worktree_path=effective_wt_path,
        branch_name=effective_branch,
        base_sha=effective_base_sha,
        head_sha=head_sha,
        git_log=git_log,
        ostk_handoff_info=ostk_handoff_info,
        work_activate_info=work_activate_info,
        latest_failing_test=latest_failing_test,
        pinned_decisions=pinned_decisions,
        prior_prompt=prior_prompt,
        context_pages=effective_context_pages,
    )

    default_model_for_runtime = (
        target_model.strip()
        if (target_model and target_model.strip())
        else ("gemini-3.1-pro" if norm_runtime == "gemini" else "claude-sonnet-4-5")
    )

    spawn_req = SpawnRequest(
        name=new_agent_name,
        prompt=handoff_prompt,
        model=default_model_for_runtime,
        isolation="worktree",
        repo_path=str(resolved_repo),
        base_sha=effective_base_sha,
        task=f"Handoff ({norm_runtime}) for {effective_task_id or source_agent or new_agent_name}",
        extra={
            "reuse_worktree_path": str(effective_wt_path),
            "reuse_branch_name": effective_branch,
            "branch_name": effective_branch,
            "base_sha": effective_base_sha,
            "task_id": str(effective_task_id) if effective_task_id else None,
            "needle_id": str(effective_task_id) if effective_task_id else None,
            "source": "handoff",
            "handed_off_from": source_agent,
        },
    )

    if norm_runtime == "gemini":
        from services.gemini_cli_provider import GeminiCliRuntimeProvider

        spawn_res = await GeminiCliRuntimeProvider().spawn_subagent(spawn_req)
    else:
        from services.claude_code_provider import ClaudeCodeRuntimeProvider

        provider = ClaudeCodeRuntimeProvider(spawn_fn=_spawn_claude_reusing_worktree)
        spawn_res = await provider.spawn_subagent(spawn_req)

    handoff_ts = datetime.now(timezone.utc).isoformat()

    if source_meta is not None:
        source_meta["handed_off_to"] = new_agent_name
        source_meta["handed_off_at"] = handoff_ts

    new_meta = agents_mod.agent_metadata.get(new_agent_name)
    if new_meta is not None:
        new_meta["handed_off_from"] = source_agent
        new_meta["source"] = "handoff"
        new_meta["runtime"] = norm_runtime
        new_meta["worktree_path"] = str(effective_wt_path)
        new_meta["branch_name"] = effective_branch
        new_meta["worktree_branch"] = effective_branch
        new_meta["base_sha"] = effective_base_sha
        new_meta["repo_path"] = str(resolved_repo)
        if effective_task_id:
            new_meta["task_id"] = str(effective_task_id)
            new_meta["needle_id"] = str(effective_task_id)

    handoffs_path = youros_home() / "memory" / "handoffs.jsonl"
    handoffs_path.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "timestamp": handoff_ts,
        "from_agent": source_agent,
        "to_agent": new_agent_name,
        "from_runtime": source_runtime,
        "target_runtime": norm_runtime,
        "target_model": (new_meta or {}).get("model") or default_model_for_runtime,
        "task_id": str(effective_task_id) if effective_task_id else None,
        "repo_path": str(resolved_repo),
        "worktree_path": str(effective_wt_path),
        "branch_name": effective_branch,
        "base_sha": effective_base_sha,
        "head_sha": head_sha,
        "transition_note": transition_note,
        "context_pages": effective_context_pages,
        "ostk_handoff": ostk_handoff_info,
        "ostk_work_activate": work_activate_info,
        "pid": spawn_res.pid,
        "transcript_path": spawn_res.transcript_path,
    }
    with open(handoffs_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    return {
        "ok": True,
        "from_agent": source_agent,
        "to_agent": new_agent_name,
        "from_runtime": source_runtime,
        "target_runtime": norm_runtime,
        "target_model": record["target_model"],
        "task_id": record["task_id"],
        "repo_path": str(resolved_repo),
        "worktree_path": str(effective_wt_path),
        "branch_name": effective_branch,
        "base_sha": effective_base_sha,
        "head_sha": head_sha,
        "pid": spawn_res.pid,
        "status": spawn_res.status,
        "transcript_path": spawn_res.transcript_path,
        "context_pages": effective_context_pages,
        "ostk_handoff": ostk_handoff_info,
        "ostk_work_activate": work_activate_info,
        "handoffs_path": str(handoffs_path),
    }


__all__ = ["execute_runtime_handoff"]
