"""Provider that routes chat through the local `gemini` command.

Modeled after claude_code_provider.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import WebSocket

_REPO_ROOT: Path = Path(__file__).resolve().parent.parent.parent
_gemini_log = logging.getLogger("myos.chat.gemini_cli")

# Env vars that might force API key usage or interfere with CLI OAuth auth.
BLOCKED_AUTH_ENV_KEYS: frozenset[str] = frozenset({
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
})

_DETECTION_CACHE_TTL_SECONDS: float = 600.0
_AUTH_STATUS_TIMEOUT_SECONDS: float = 3.0
_STREAM_TIMEOUT_SECONDS: float = 1800.0
_WS_HEARTBEAT_INTERVAL_S: float = 10.0

_detection_cache: dict[str, Any] = {"result": None, "expires_at": 0.0}
_detection_lock: asyncio.Lock | None = None

# Per-tab Gemini CLI session registry mapping tab_id -> session_id.
_gemini_tab_sessions: dict[str, str] = {}

_GEMINI_MODEL_REMAP: dict[str, str] = {
    "gemini-3.1-pro": "gemini-3.1-pro-preview",
    "gemini-3-flash": "gemini-3-flash-preview",
    "gemini-3.5-flash": "flash",
    "gemini-3.1-flash-lite": "flash-lite",
}

_VALID_GEMINI_CLI_MODELS: frozenset[str] = frozenset({
    "auto",
    "pro",
    "flash",
    "flash-lite",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
})

_OAUTH_FALLBACK_MARKERS: tuple[str, ...] = (
    "ineligibletiererror",
    "ineligible tier",
    "not authenticated",
    "unauthenticated",
    "authentication failed",
    "auth failure",
    "auth error",
    "sign in",
    "login required",
    "invalid_grant",
    "invalid credentials",
    "unauthorized",
)

_SESSION_NOT_FOUND_MARKERS: tuple[str, ...] = (
    "session not found",
    "no session found",
    "failed to resume session",
    "could not find session",
    "invalid session",
    "unknown session",
)


def _get_detection_lock() -> asyncio.Lock:
    global _detection_lock
    if _detection_lock is None:
        _detection_lock = asyncio.Lock()
    return _detection_lock


def clear_detection_cache() -> None:
    """Reset cached CLI availability detection state."""
    _detection_cache["result"] = None
    _detection_cache["expires_at"] = 0.0


def get_gemini_session_id(tab_id: str) -> Optional[str]:
    """Return the stored Gemini CLI session UUID for tab_id, if any."""
    return _gemini_tab_sessions.get(tab_id)


def clear_gemini_session(tab_id: str) -> None:
    """Remove any stored Gemini CLI session UUID for tab_id."""
    _gemini_tab_sessions.pop(tab_id, None)


def get_adhoc_agentfile_path() -> Path:
    """Return the path to the model-neutral fallback ad-hoc Agentfile."""
    return _REPO_ROOT / "agents" / "adhoc-default.agent"


def resolve_gemini_cli_model(model: Optional[str]) -> Optional[str]:
    """Map an incoming model tag to a valid Gemini CLI 0.57.0 -m flag value.

    Returns None when -m should be omitted so the CLI default applies.
    """
    if model is None:
        return None
    cleaned = str(model).strip().lstrip("@").strip().lower()
    if cleaned in {"", "gemini", "gemini-cli", "gemini_cli", "default"}:
        return None
    if cleaned in _GEMINI_MODEL_REMAP:
        return _GEMINI_MODEL_REMAP[cleaned]
    if cleaned in _VALID_GEMINI_CLI_MODELS:
        return cleaned
    return None


def _nvm_version_sort_key(bin_dir: Path) -> tuple[int, ...]:
    ver_str = bin_dir.parent.name.lstrip("vV")
    parts: list[int] = []
    for seg in ver_str.split("."):
        digits = "".join(ch for ch in seg if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _gemini_install_dirs() -> list[Path]:
    """Common locations where the gemini CLI (and node) may live when a
    non-interactive backend's PATH is minimal (for example under launchd).

    Discovers ~/.nvm/versions/node/*/bin sorted newest-first alongside
    npm global, ~/.local/bin, and Homebrew paths.
    """
    import os

    home = Path.home()
    dirs: list[Path] = []
    override = os.environ.get("GEMINI_CLI_PATH")
    if override:
        p = Path(override)
        dirs.append(p.parent if p.suffix or p.name == "gemini" else p)

    nvm_nodes = home / ".nvm" / "versions" / "node"
    try:
        if nvm_nodes.is_dir():
            nvm_bins = [p for p in nvm_nodes.glob("*/bin") if p.is_dir()]
            nvm_bins.sort(key=_nvm_version_sort_key, reverse=True)
            dirs.extend(nvm_bins)
    except Exception:
        pass

    dirs += [
        home / ".npm-global" / "bin",
        home / ".local" / "bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        home / "node_modules" / ".bin",
        Path("/usr/local/lib/node_modules/.bin"),
    ]
    return dirs


def _build_subprocess_env(api_key: Optional[str] = None) -> dict[str, str]:
    import os

    env = dict(os.environ)
    for k in BLOCKED_AUTH_ENV_KEYS:
        env.pop(k, None)
    if api_key:
        env["GEMINI_API_KEY"] = api_key
    extra = [str(d) for d in _gemini_install_dirs() if d.is_dir()]
    if extra:
        env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    return env


def _gemini_signed_in() -> bool:
    """True when ~/.gemini/oauth_creds.json exists as a non-empty valid JSON object."""
    try:
        creds_path = Path.home() / ".gemini" / "oauth_creds.json"
        if not creds_path.is_file():
            return False
        raw = creds_path.read_text(encoding="utf-8").strip()
        if not raw:
            return False
        parsed = json.loads(raw)
        return isinstance(parsed, dict)
    except Exception:
        return False


def _resolve_gemini_api_key() -> str:
    """Return a configured Gemini API key from env or settings_store, or empty string."""
    import os

    for env_key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "gemini_api_key"):
        val = os.environ.get(env_key, "").strip()
        if val:
            return val
    try:
        from services.settings_store import settings_store

        val = str(settings_store.get("gemini_api_key", "") or "").strip()
        if val:
            return val
    except Exception:
        pass
    return ""


def _find_gemini_binary() -> Optional[str]:
    import os

    # 1. Standard PATH lookup (works when the backend inherited a full PATH).
    found = shutil.which("gemini")
    if found:
        return found
    # 2. Fall back to common install dirs (including NVM) that launchd PATH may miss.
    for d in _gemini_install_dirs():
        cand = d / "gemini"
        try:
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)
        except Exception:
            continue
    return None


async def is_gemini_cli_available(force: bool = False) -> bool:
    """Fast, zero-ping local check for Gemini CLI binary and credentials."""
    now = time.monotonic()
    if not force and _detection_cache["result"] is not None and now < _detection_cache["expires_at"]:
        return bool(_detection_cache["result"])

    async with _get_detection_lock():
        now = time.monotonic()
        if not force and _detection_cache["result"] is not None and now < _detection_cache["expires_at"]:
            return bool(_detection_cache["result"])

        try:
            gemini_path = _find_gemini_binary()
            if not gemini_path:
                result = False
            else:
                result = bool(_gemini_signed_in() or _resolve_gemini_api_key())
        except Exception:
            result = False

        _detection_cache["result"] = result
        _detection_cache["expires_at"] = time.monotonic() + _DETECTION_CACHE_TTL_SECONDS
        return result


# ---------------------------------------------------------------------------
# RuntimeProvider implementation (→1891, →1892, →2145)
# ---------------------------------------------------------------------------
from services.runtime_provider import (  # noqa: E402
    Feature,
    SpawnNotSupportedError,
    SpawnResult,
    _BaseRuntimeProvider,
)

# Gemini CLI streams JSONL events and spawns subagents inside .youros/worktrees/
# managed by spawn_pipeline (->3095). Plan mode, hooks, and live monitor remain
# Claude-specific capabilities.
_GEMINI_CLI_FEATURES = frozenset({
    Feature.STREAMING,
    Feature.SUBAGENTS,
    Feature.ISOLATION,
    Feature.WORKTREES,
})

# Strong references to background subagent watcher tasks so asyncio GC never
# drops an active stdout-streaming task mid-run.
_gemini_spawn_tasks: set[asyncio.Task[Any]] = set()


class GeminiCliRuntimeProvider(_BaseRuntimeProvider):
    """RuntimeProvider for the local gemini CLI with spawn_pipeline worktrees."""

    _features = _GEMINI_CLI_FEATURES
    display_name = "Gemini"

    async def spawn_subagent(self, request=None, /, **fields) -> SpawnResult:
        """Spawn a detached Gemini CLI subagent via spawn_pipeline (->3095).

        CRITICAL SAFETY INVARIANT (->2945): never invoke ``self._spawn_fn``,
        which holds the Claude-only bespoke spawner injected by agents.py.
        """
        from datetime import datetime, timezone
        import subprocess as _sp

        from services import spawn_pipeline
        from services.agentfile_parser import (
            _find_any_agentfile,
            get_template_aliases,
            parse_agentfile,
        )
        from services.event_bus import (
            AGENT_COMPLETED,
            AGENT_DELTA,
            AGENT_SPAWNED,
            bus as _event_bus,
        )
        from services.youros_paths import youros_home

        req = self._coerce_request(request, fields)

        gemini_bin = _find_gemini_binary()
        if not gemini_bin:
            raise SpawnNotSupportedError(
                "Gemini CLI binary was not found on this machine. "
                "Install @google/gemini-cli or switch your provider to Claude in Settings to start agents."
            )

        extra = dict(req.extra or {})
        repo_root = spawn_pipeline.resolve_target_repo(req.repo_path)

        reuse_wt_raw = extra.get("reuse_worktree_path")
        worktree_path: Optional[Path] = None
        branch_name: Optional[str] = None
        base_sha: Optional[str] = req.base_sha or extra.get("base_sha")

        if reuse_wt_raw:
            worktree_path = Path(str(reuse_wt_raw)).expanduser().resolve()
            cwd_path = worktree_path
            isolation_mode = "worktree"
            branch_name = extra.get("reuse_branch_name") or extra.get("branch_name")
            if not branch_name and worktree_path.is_dir():
                br = _sp.run(
                    ["git", "-C", str(worktree_path), "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    check=False,
                )
                if br.returncode == 0 and br.stdout.strip():
                    branch_name = br.stdout.strip()
            if not base_sha and repo_root.is_dir():
                bs = _sp.run(
                    ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    check=False,
                )
                if bs.returncode == 0 and bs.stdout.strip():
                    base_sha = bs.stdout.strip()
        elif req.isolation != "none":
            lease = await asyncio.to_thread(
                spawn_pipeline.provision_agent_worktree,
                repo_root,
                req.name,
                base_sha or "HEAD",
            )
            worktree_path = lease.worktree_path
            cwd_path = worktree_path
            branch_name = lease.branch_name
            base_sha = lease.base_sha
            isolation_mode = "worktree"
        else:
            cwd_path = repo_root
            isolation_mode = "none"
            if not base_sha and repo_root.is_dir():
                try:
                    bs = _sp.run(
                        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                        capture_output=True,
                        text=True,
                        timeout=5.0,
                        check=False,
                    )
                    if bs.returncode == 0 and bs.stdout.strip():
                        base_sha = bs.stdout.strip()
                except Exception:
                    pass

        mapped_cli_model = resolve_gemini_cli_model(req.model)
        raw_model_lower = str(req.model or "").strip().lower()
        if mapped_cli_model:
            resolved_model = mapped_cli_model
        elif "gemini" in raw_model_lower or raw_model_lower in _VALID_GEMINI_CLI_MODELS:
            resolved_model = str(req.model).strip()
        else:
            resolved_model = "gemini-default"

        ostk_signed_af = extra.get("ostk_agentfile_path")
        if not ostk_signed_af and req.template and str(req.template).startswith(".ostk/agents/"):
            cand_af = cwd_path / str(req.template)
            if cand_af.is_file():
                ostk_signed_af = str(cand_af)

        if ostk_signed_af:
            ostk_bin = shutil.which("ostk") or "ostk"
            cmd: list[str] = [ostk_bin, "agentfile", "run", str(ostk_signed_af)]
        else:
            if req.template:
                aliases = get_template_aliases()
                stem = aliases.get(req.template, req.template)
                recipe_path = _find_any_agentfile(stem)
                if recipe_path is None:
                    raise ValueError(
                        f"No agentfile found for '{req.template}' on the Gemini runtime."
                    )
            else:
                recipe_path = get_adhoc_agentfile_path()

            recipe_prompt = ""
            if recipe_path and recipe_path.is_file():
                cfg = parse_agentfile(recipe_path)
                recipe_prompt = cfg.prompt or ""

            user_prompt = req.prompt or ""
            if "{{prompt}}" in recipe_prompt:
                composed_prompt = recipe_prompt.replace("{{prompt}}", user_prompt)
            elif recipe_prompt and user_prompt:
                composed_prompt = f"{recipe_prompt}\n\nTask:\n{user_prompt}"
            else:
                composed_prompt = user_prompt or recipe_prompt

            if worktree_path is not None:
                wt_header = (
                    f"[WORKTREE] Your isolated git worktree is: {worktree_path} "
                    f"(branch: {branch_name or 'HEAD'}).\n"
                    "Make all file edits inside this worktree and commit your changes before finishing."
                )
                composed_prompt = f"{wt_header}\n\n{composed_prompt}".strip()

            try:
                from routers.agents import agent_mailbox_instruction_short

                mailbox_short = agent_mailbox_instruction_short(req.name, model=resolved_model)
                if mailbox_short:
                    composed_prompt = f"{composed_prompt}\n\n{mailbox_short}"
            except Exception:
                pass

            # Never pass Gemini CLI's -w flag; worktrees are provisioned and reaped by spawn_pipeline.
            cmd = [
                gemini_bin,
                "-p",
                composed_prompt,
                "--output-format",
                "stream-json",
                "--skip-trust",
                "--approval-mode",
                "yolo",
            ]
            if mapped_cli_model is not None:
                cmd.extend(["-m", mapped_cli_model])

        api_key = None if _gemini_signed_in() else (_resolve_gemini_api_key() or None)
        base_env = _build_subprocess_env(api_key=api_key)
        proc_env = spawn_pipeline.build_short_socket_env(base_env, cwd_path, req.name)

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
            cwd=str(cwd_path),
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
            "runtime": "gemini",
            "budget": str(req.budget),
            "tokens_used": 0,
            "source": str(extra.get("source") or "api"),
            "isolation": isolation_mode,
            "repo_path": str(repo_root),
            "worktree_path": str(worktree_path) if worktree_path is not None else None,
            "branch_name": branch_name,
            "worktree_branch": branch_name,
            "base_sha": base_sha,
            "pid": pid,
            "transcript_path": str(transcript_path),
            "transcript_path_source": "caller",
            "prompt": (req.prompt or "")[:4000],
        }
        if req.template:
            meta_record["template"] = req.template
        if req.task:
            meta_record["task"] = req.task
        if extra.get("description"):
            meta_record["description"] = extra["description"]
        if extra.get("task_id"):
            meta_record["task_id"] = extra["task_id"]
        if extra.get("needle_id"):
            meta_record["needle_id"] = extra["needle_id"]
        if extra.get("handed_off_from"):
            meta_record["handed_off_from"] = extra["handed_off_from"]

        try:
            import routers.agents as agents_mod

            agents_mod.agent_metadata[req.name] = meta_record
            agents_mod.active_agents[req.name] = proc
        except Exception as exc:
            _gemini_log.warning("gemini.spawn.metadata_register_failed name=%s err=%s", req.name, exc)

        await _event_bus.publish(
            AGENT_SPAWNED,
            {
                "name": req.name,
                "runtime": "gemini",
                "model": resolved_model,
                "status": "running",
                "pid": pid,
                "worktree_path": str(worktree_path) if worktree_path is not None else None,
            },
        )
        await _event_bus.publish(AGENT_DELTA, {"name": req.name, "status": "running"})

        async def _watch_gemini_subagent() -> None:
            tokens_used = 0
            stderr_buf = bytearray()

            async def _drain_stdout() -> None:
                nonlocal tokens_used
                if proc.stdout is None:
                    return
                with open(transcript_path, "ab") as tfh:
                    while True:
                        line = await proc.stdout.readline()
                        if not line:
                            break
                        tfh.write(line)
                        if not line.endswith(b"\n"):
                            tfh.write(b"\n")
                        tfh.flush()
                        try:
                            ev = json.loads(line.decode("utf-8", errors="replace"))
                            if isinstance(ev, dict) and ev.get("type") == "result":
                                stats = ev.get("stats")
                                if isinstance(stats, dict):
                                    tokens_used += int(stats.get("input_tokens", 0) or 0) + int(
                                        stats.get("output_tokens", 0) or 0
                                    )
                        except Exception:
                            pass
                        hb_now = datetime.now(timezone.utc).isoformat()
                        meta_record["last_heartbeat_at"] = hb_now

            async def _drain_stderr() -> None:
                if proc.stderr is None:
                    return
                while True:
                    chunk = await proc.stderr.read(4096)
                    if not chunk:
                        break
                    if len(stderr_buf) < 8192:
                        stderr_buf.extend(chunk[: 8192 - len(stderr_buf)])

            try:
                await asyncio.gather(_drain_stdout(), _drain_stderr(), return_exceptions=True)
                rc = await proc.wait()
            except Exception as exc:
                _gemini_log.warning("gemini.spawn.watcher_error name=%s err=%s", req.name, exc)
                rc = getattr(proc, "returncode", 1)
                if rc is None:
                    rc = 1

            done_iso = datetime.now(timezone.utc).isoformat()
            final_status = "completed" if rc == 0 else "failed"
            if tokens_used:
                meta_record["tokens_used"] = tokens_used

            try:
                import routers.agents as agents_mod

                agents_mod.active_agents.pop(req.name, None)
                live_meta = agents_mod.agent_metadata.get(req.name)
                if live_meta is not None and live_meta.get("status") == "running":
                    extra_done: dict[str, Any] = {
                        "completed_at": done_iso,
                        "last_heartbeat_at": done_iso,
                        "exit_code": rc,
                    }
                    if rc != 0 and stderr_buf:
                        extra_done["error"] = stderr_buf.decode("utf-8", errors="replace").strip()[:500]
                    live_meta["status"] = final_status
                    live_meta.update(extra_done)
            except Exception as exc:
                _gemini_log.warning("gemini.spawn.complete_update_failed name=%s err=%s", req.name, exc)

            await _event_bus.publish(
                AGENT_DELTA,
                {"name": req.name, "status": final_status, "terminal": True},
            )
            await _event_bus.publish(
                AGENT_COMPLETED,
                {"name": req.name, "status": final_status, "runtime": "gemini", "exit_code": rc},
            )

        watcher = asyncio.create_task(_watch_gemini_subagent())
        _gemini_spawn_tasks.add(watcher)
        watcher.add_done_callback(_gemini_spawn_tasks.discard)

        wt_str = str(worktree_path) if worktree_path is not None else None
        return SpawnResult(
            name=req.name,
            pid=pid,
            status="running",
            worktree_path=wt_str,
            transcript_path=str(transcript_path),
            branch_name=branch_name,
            base_sha=base_sha,
            repo_path=str(repo_root),
            detail={
                "result": f"Agent '{req.name}' spawned on Gemini",
                "name": req.name,
                "pid": pid,
                "status": "running",
                "runtime": "gemini",
                "model": resolved_model,
                "transcript": str(transcript_path),
                "worktree_path": wt_str,
                "branch_name": branch_name,
                "base_sha": base_sha,
                "repo_path": str(repo_root),
            },
        )

    async def invoke_skill(self, skill_id: str, **args: Any) -> None:
        """Run a skill on the gemini runtime via its agentfile recipe.

        Claude runs skills as native slash-commands; the gemini CLI has none,
        so it runs the skill's model-neutral recipe (the PROMPT in
        ``agents/<skill>.agent``) as a one-shot ``gemini -p`` prompt. This keeps
        skills working when the active runtime is Gemini instead of Claude.
        """
        import asyncio
        import logging
        from pathlib import Path

        from services.runtime_provider import resolve_skill_agentfile
        from services.agentfile_parser import parse_agentfile

        logger = logging.getLogger(__name__)
        sid = skill_id.lstrip("/").strip()

        recipe_path = resolve_skill_agentfile(sid)
        if recipe_path is None:
            raise ValueError(
                f"Unknown skill '{sid}' for GeminiCliRuntimeProvider: "
                f"no agents/{sid}.agent recipe found"
            )

        config = parse_agentfile(recipe_path)
        prompt = config.prompt or ""

        gemini_bin = _find_gemini_binary()
        if not gemini_bin:
            logger.warning("skills.run: gemini CLI not found for skill=%s", sid)
            return

        repo_root = Path(__file__).resolve().parent.parent.parent
        cmd = [gemini_bin, "-p", prompt, "--skip-trust"]
        logger.info("skills.run: gemini skill=%s recipe=%s", sid, recipe_path.name)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_build_subprocess_env(),
                cwd=str(repo_root),
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            if proc.returncode not in (0, None):
                logger.warning(
                    "skills.run: gemini skill=%s exit=%s stderr=%s",
                    sid, proc.returncode, stderr.decode(errors="replace")[:500],
                )
        except asyncio.TimeoutError:
            logger.warning("skills.run: gemini skill=%s timed out after 300s", sid)
        except Exception as exc:
            logger.error("skills.run: gemini skill=%s error=%s", sid, exc)


# ---------------------------------------------------------------------------


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, list):
        parts: list[str] = []
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "text":
                parts.append(str(blk.get("text", "")))
        return "\n".join(p for p in parts if p)
    return str(content or "")


def _extract_last_user_content(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return _message_content_to_text(msg.get("content", ""))
    if messages:
        return _message_content_to_text(messages[-1].get("content", ""))
    return ""


def _flatten_messages(messages: list[dict], system_prompt: Optional[str]) -> str:
    prompt_lines: list[str] = []
    if system_prompt:
        prompt_lines.append(f"System: {system_prompt}")

    for m in messages:
        role = m.get("role", "user")
        content = _message_content_to_text(m.get("content", ""))
        prompt_lines.append(f"{role.capitalize()}: {content}")

    prompt_lines.append("Assistant:")
    return "\n\n".join(prompt_lines)


def _is_oauth_auth_or_tier_failure(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _OAUTH_FALLBACK_MARKERS)


def _is_session_not_found_failure(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _SESSION_NOT_FOUND_MARKERS)


async def stream_chat(
    messages: list[dict],
    websocket: WebSocket,
    system_prompt: Optional[str] = None,
    **kwargs: Any,
) -> str:
    gemini_path = _find_gemini_binary()
    if not gemini_path:
        await websocket.send_json({"type": "error", "data": "Gemini CLI not found."})
        return ""

    tab_id: Optional[str] = kwargs.get("tab_id")
    resolved_model: Optional[str] = resolve_gemini_cli_model(kwargs.get("model"))

    stored_sid = get_gemini_session_id(tab_id) if tab_id else None
    is_resume = bool(tab_id and stored_sid and len(messages) > 1)
    active_sid: Optional[str] = None

    if is_resume and stored_sid:
        active_sid = stored_sid
        session_args = ["--resume", stored_sid]
        prompt = _extract_last_user_content(messages)
    elif tab_id:
        active_sid = str(uuid.uuid4())
        session_args = ["--session-id", active_sid]
        if len(messages) == 1 and not system_prompt:
            prompt = _extract_last_user_content(messages)
        else:
            prompt = _flatten_messages(messages, system_prompt)
    else:
        session_args = []
        prompt = _flatten_messages(messages, system_prompt)

    async def _run_attempt(
        attempt_prompt: str,
        attempt_session_args: list[str],
        api_key: Optional[str] = None,
    ) -> dict[str, Any]:
        t_start = time.perf_counter()
        args: list[str] = [
            gemini_path,
            "-p",
            attempt_prompt,
            "--output-format",
            "stream-json",
            "--skip-trust",
            "--approval-mode",
            "yolo",
        ]
        if resolved_model is not None:
            args.extend(["-m", resolved_model])
        if attempt_session_args:
            args.extend(attempt_session_args)

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_build_subprocess_env(api_key=api_key),
                cwd=str(_REPO_ROOT),
                limit=32 * 1024 * 1024,
            )
        except Exception as exc:
            return {
                "start_error": f"Failed to start Gemini CLI: {exc}",
                "full_text": "",
                "timed_out": False,
                "pending_error": None,
                "error_type": "",
                "stderr_text": "",
                "returncode": -1,
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "init_session_id": None,
                "init_model": None,
                "spawn_ms": None,
                "init_ms": None,
                "first_token_ms": None,
                "t_start": t_start,
            }

        spawn_ms = (time.perf_counter() - t_start) * 1000.0
        full_text = ""
        timed_out = False
        pending_error: Optional[str] = None
        error_type = ""
        usage = {"input_tokens": 0, "output_tokens": 0}
        init_session_id: Optional[str] = None
        init_model: Optional[str] = None
        init_ms: Optional[float] = None
        first_token_ms: Optional[float] = None

        async def _read_stdout() -> None:
            nonlocal full_text, pending_error, error_type, usage
            nonlocal init_session_id, init_model, init_ms, first_token_ms
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                try:
                    event = json.loads(line.decode("utf-8", errors="replace"))
                except Exception:
                    continue
                if not isinstance(event, dict):
                    continue

                etype = event.get("type")
                if etype == "init":
                    if init_ms is None:
                        init_ms = (time.perf_counter() - t_start) * 1000.0
                    sid_val = event.get("session_id")
                    if isinstance(sid_val, str) and sid_val.strip():
                        init_session_id = sid_val.strip()
                    mod_val = event.get("model")
                    if isinstance(mod_val, str) and mod_val.strip():
                        init_model = mod_val.strip()
                elif etype == "message":
                    role = event.get("role")
                    if role == "user":
                        # Ignore echoed user messages in 0.57.0 StreamJsonFormatter.
                        continue
                    if role == "assistant" or role is None:
                        chunk = event.get("content", "")
                        if chunk:
                            if first_token_ms is None:
                                first_token_ms = (time.perf_counter() - t_start) * 1000.0
                            full_text += chunk
                            await websocket.send_json({"type": "token", "data": chunk})
                elif etype == "token":
                    # Legacy stream format compatibility.
                    token = event.get("data", "")
                    if token and first_token_ms is None:
                        first_token_ms = (time.perf_counter() - t_start) * 1000.0
                    full_text += token
                    await websocket.send_json({"type": "token", "data": token})
                elif etype == "tool_use":
                    await websocket.send_json({
                        "type": "tool_use",
                        "tool": event.get("tool_name"),
                        "tool_id": event.get("tool_id"),
                        "input": event.get("parameters", {}),
                    })
                elif etype == "tool_result":
                    await websocket.send_json({
                        "type": "tool_result",
                        "tool_id": event.get("tool_id"),
                        "status": event.get("status"),
                    })
                elif etype == "result":
                    stats = event.get("stats")
                    if isinstance(stats, dict):
                        usage = {
                            "input_tokens": int(stats.get("input_tokens", 0) or 0),
                            "output_tokens": int(stats.get("output_tokens", 0) or 0),
                        }
                    if event.get("status") == "error":
                        err_obj = event.get("error")
                        if isinstance(err_obj, dict):
                            error_type = str(err_obj.get("type") or "")
                            if pending_error is None:
                                pending_error = str(
                                    err_obj.get("message")
                                    or err_obj.get("type")
                                    or "Gemini CLI error"
                                )
                        elif err_obj and pending_error is None:
                            pending_error = str(err_obj)
                elif etype == "error":
                    msg = event.get("message") or event.get("data") or "Gemini CLI error"
                    pending_error = str(msg)

        try:
            await asyncio.wait_for(_read_stdout(), timeout=_STREAM_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            timed_out = True

        rc = await proc.wait()
        stderr_text = ""
        if proc.stderr is not None:
            try:
                stderr_bytes = await proc.stderr.read()
                if stderr_bytes:
                    stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
            except Exception:
                stderr_text = ""

        return {
            "start_error": None,
            "full_text": full_text,
            "timed_out": timed_out,
            "pending_error": pending_error,
            "error_type": error_type,
            "stderr_text": stderr_text,
            "returncode": rc,
            "usage": usage,
            "init_session_id": init_session_id,
            "init_model": init_model,
            "spawn_ms": spawn_ms,
            "init_ms": init_ms,
            "first_token_ms": first_token_ms,
            "t_start": t_start,
        }

    outcome = await _run_attempt(prompt, session_args, api_key=None)
    if outcome["start_error"]:
        await websocket.send_json({"type": "error", "data": outcome["start_error"]})
        return ""

    # Fallback 1: If --resume failed because the session was not found, clear the
    # stale tab session and run a clean new --session-id turn with full context.
    if (
        is_resume
        and tab_id
        and not outcome["timed_out"]
        and not outcome["full_text"].strip()
    ):
        combined_resume_err = " ".join(
            part for part in (outcome["pending_error"] or "", outcome["stderr_text"]) if part
        )
        if _is_session_not_found_failure(combined_resume_err):
            _gemini_log.info(
                "Gemini CLI session %s not found for tab %s; starting fresh session.",
                stored_sid,
                tab_id,
            )
            clear_gemini_session(tab_id)
            is_resume = False
            active_sid = str(uuid.uuid4())
            session_args = ["--session-id", active_sid]
            prompt = _flatten_messages(messages, system_prompt)
            outcome = await _run_attempt(prompt, session_args, api_key=None)
            if outcome["start_error"]:
                await websocket.send_json({"type": "error", "data": outcome["start_error"]})
                return ""

    # Fallback 2: If OAuth exited non-zero with empty output due to IneligibleTierError
    # or auth failure and a Gemini API key is configured, retry once with GEMINI_API_KEY.
    if (
        not outcome["timed_out"]
        and outcome["returncode"] not in (0, None)
        and not outcome["full_text"].strip()
    ):
        combined_auth_err = " ".join(
            part
            for part in (
                outcome["error_type"],
                outcome["pending_error"] or "",
                outcome["stderr_text"],
            )
            if part
        )
        fallback_api_key = _resolve_gemini_api_key()
        if fallback_api_key and _is_oauth_auth_or_tier_failure(combined_auth_err):
            _gemini_log.info(
                "Gemini CLI OAuth failed; retrying once with GEMINI_API_KEY."
            )
            outcome = await _run_attempt(prompt, session_args, api_key=fallback_api_key)
            if outcome["start_error"]:
                await websocket.send_json({"type": "error", "data": outcome["start_error"]})
                return ""

    full_text: str = outcome["full_text"]

    if outcome["timed_out"]:
        await websocket.send_json({"type": "error", "data": "Gemini CLI timed out."})
        return full_text

    if outcome["pending_error"] is not None:
        await websocket.send_json({"type": "error", "data": outcome["pending_error"]})
        return full_text

    if not full_text.strip():
        rc = outcome["returncode"]
        stderr_snippet = outcome["stderr_text"][:300]
        if rc not in (0, None) and stderr_snippet:
            raise RuntimeError(f"Gemini CLI returned no tokens (exit {rc}): {stderr_snippet}")
        raise RuntimeError("Gemini CLI returned no tokens")

    if tab_id:
        final_sid = outcome["init_session_id"] or active_sid
        if final_sid:
            _gemini_tab_sessions[tab_id] = final_sid

    await websocket.send_json({"type": "done", "usage": outcome["usage"]})

    try:
        from services.chat_latency import TurnLatencySample, record_turn_latency

        total_ms = (time.perf_counter() - outcome["t_start"]) * 1000.0
        record_turn_latency(
            TurnLatencySample(
                provider="gemini_cli",
                model=resolved_model or outcome["init_model"] or "default",
                tab_id=tab_id,
                warm_session=bool(is_resume),
                spawn_ms=outcome["spawn_ms"],
                init_ms=outcome["init_ms"],
                first_token_ms=outcome["first_token_ms"],
                total_ms=total_ms,
            )
        )
    except Exception:
        pass

    return full_text
