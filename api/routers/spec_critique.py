"""Cross-model spec critique (Pre-Design Audit) and cryptographic spec receipt gate (->3095).

Mounted under prefix '/api' in main.py by the top-level orchestrator.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel

import config
from services import spawn_pipeline
from services.git_receipts import verify_task_receipt
from services.youros_paths import drafts_dir, specs_dir, youros_home

logger = logging.getLogger(__name__)

router = APIRouter(tags=["specs"])

_FILE_TOKEN_RE = re.compile(
    r"(?:`([^`\n]+\.[A-Za-z0-9]{1,8})`|([A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,8}))"
)
_INLINE_CREATE_RE = re.compile(
    r"^\s*(?:[-*+]\s+|\d+\.\s+)?(?:\*\*)?Create(?:\*\*)?\s*:\s*(.+)$",
    re.IGNORECASE,
)
_SECTION_CREATE_RE = re.compile(
    r"^\s*(?:#{1,4}\s+|[-*+]\s+)?(?:\*\*)?Create(?:\*\*)?\s*:?\s*$",
    re.IGNORECASE,
)
_OTHER_BUCKET_RE = re.compile(
    r"^\s*(?:#{1,6}\s+|(?:[-*+]\s+)?(?:\*\*)?(?:Existing|Modify|Update|Delete|Remove|Test|Verify)\b)",
    re.IGNORECASE,
)
_CHECKBOX_RE = re.compile(r"^\s*[-*+]\s+\[(?P<mark>[ xX])\]\s+(?P<text>.+)$")


class SpecCrossCritiqueRequest(BaseModel):
    reviewer_runtime: str = "gemini"
    reviewer_model: Optional[str] = None
    run_live_llm: bool = False
    repo_path: Optional[str] = None


def _validate_spec_path_arg(raw_path: str) -> None:
    cleaned = (raw_path or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Spec path is required")
    posix_parts = PurePosixPath(cleaned).parts
    if ".." in posix_parts:
        raise HTTPException(status_code=400, detail="Path traversal not allowed")


def _resolve_spec_file(spec_path: str, active_repo: Path) -> Path:
    _validate_spec_path_arg(spec_path)
    raw = spec_path.strip()

    candidates: list[Path] = []
    expanded = Path(os.path.expanduser(raw))
    if expanded.is_absolute():
        candidates.append(expanded)
    else:
        rel_clean = raw.lstrip("/")
        if ("/" + rel_clean).startswith(str(youros_home())) or ("/" + rel_clean).startswith("/Users/") or ("/" + rel_clean).startswith("/private/") or ("/" + rel_clean).startswith("/tmp/") or ("/" + rel_clean).startswith("/var/"):
            candidates.append(Path("/" + rel_clean))
        candidates.extend([
            active_repo / rel_clean,
            specs_dir() / rel_clean,
            drafts_dir() / rel_clean,
            active_repo / "docs" / "spec" / Path(rel_clean).name,
            specs_dir() / Path(rel_clean).name,
            drafts_dir() / Path(rel_clean).name,
            Path(config.PROJECT_ROOT) / rel_clean,
        ])

    allowed_roots = [
        specs_dir().resolve(),
        drafts_dir().resolve(),
        youros_home().resolve(),
        (active_repo / "docs").resolve(),
        (Path(config.PROJECT_ROOT) / "docs").resolve(),
    ]

    for cand in candidates:
        if not cand.is_file():
            continue
        resolved = cand.resolve()
        if any(
            resolved == root or str(resolved).startswith(str(root) + os.sep)
            for root in allowed_roots
        ):
            return resolved

    raise HTTPException(status_code=404, detail=f"Spec not found: {spec_path}")


def _extract_frontmatter_and_tasks(text: str) -> tuple[dict[str, str], list[str]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, []

    fm_end: Optional[int] = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            fm_end = idx
            break
    if fm_end is None:
        return {}, []

    scalars: dict[str, str] = {}
    task_ids: list[str] = []
    in_tasks = False

    for line in lines[1:fm_end]:
        stripped = line.strip()
        if stripped.startswith("tasks:"):
            in_tasks = True
            inline_part = stripped[len("tasks:") :].strip()
            if inline_part.startswith("[") and inline_part.endswith("]"):
                for item in inline_part[1:-1].split(","):
                    cleaned = item.strip().strip('"').strip("'").lstrip("\u2192").lstrip("->")
                    if cleaned:
                        task_ids.append(cleaned)
                in_tasks = False
            continue
        if in_tasks:
            m = re.match(r'^\s*-\s*"?(?:\u2192|->)?([A-Za-z0-9_-]+)"?\s*$', line)
            if m:
                task_ids.append(m.group(1))
                continue
            if stripped and not stripped.startswith("-"):
                in_tasks = False

        if ":" in stripped and not stripped.startswith("-"):
            k, v = stripped.split(":", 1)
            scalars[k.strip()] = v.strip().strip('"').strip("'")

    return scalars, task_ids


def _clean_rel_candidate(raw_path: str) -> Optional[str]:
    val = raw_path.strip().strip("`'\"").lstrip("./").strip()
    if not val or "://" in val or val.startswith("/") or val.startswith("~"):
        return None
    if "/" not in val and "." not in val:
        return None
    return val


def _extract_create_candidates(spec_text: str) -> list[str]:
    """Extract relative file paths listed under Create: directives in a spec or plan."""
    found: list[str] = []
    seen: set[str] = set()
    in_create_block = False

    for line in spec_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # Never flag lines explicitly marked Existing (reuse) or Modify
        if re.match(r"^\s*(?:[-*+]\s+)?(?:\*\*)?(?:Existing|Modify|Reuse)\b", line, re.IGNORECASE):
            in_create_block = False
            continue

        inline_match = _INLINE_CREATE_RE.match(line)
        if inline_match:
            in_create_block = False
            tail = inline_match.group(1)
            for m in _FILE_TOKEN_RE.finditer(tail):
                cand = _clean_rel_candidate(m.group(1) or m.group(2) or "")
                if cand and cand not in seen:
                    seen.add(cand)
                    found.append(cand)
            continue

        if _SECTION_CREATE_RE.match(line):
            in_create_block = True
            continue

        if in_create_block:
            if _OTHER_BUCKET_RE.match(line):
                in_create_block = False
                continue
            if re.match(r"^\s*[-*+]\s+", line):
                for m in _FILE_TOKEN_RE.finditer(line):
                    cand = _clean_rel_candidate(m.group(1) or m.group(2) or "")
                    if cand and cand not in seen:
                        seen.add(cand)
                        found.append(cand)
                continue
            in_create_block = False

        # Also catch bullets tagged with trailing (CREATE), e.g. `- api/foo.py (CREATE)`
        if re.search(r"\(\s*CREATE\s*\)", line, re.IGNORECASE):
            for m in _FILE_TOKEN_RE.finditer(line):
                cand = _clean_rel_candidate(m.group(1) or m.group(2) or "")
                if cand and cand not in seen:
                    seen.add(cand)
                    found.append(cand)

    return found


async def _run_optional_live_critique(
    runtime: str,
    model: Optional[str],
    spec_text: str,
    matched_existing_files: list[str],
    repo_root: Path,
) -> Optional[str]:
    prompt = (
        "Review this software specification concisely in 3 short plain-language bullets. "
        "Focus on pre-design audit accuracy, missing edge cases, and testable acceptance criteria. "
        "Do not include code blocks or em-dashes.\n\n"
        f"Existing files wrongly marked Create: {matched_existing_files}\n\n"
        f"Spec content:\n{spec_text[:4000]}"
    )
    try:
        if runtime == "gemini":
            from services.gemini_cli_provider import (
                _build_subprocess_env,
                _find_gemini_binary,
                resolve_gemini_cli_model,
            )

            gem_bin = _find_gemini_binary()
            if not gem_bin:
                return None
            cmd = [gem_bin, "-p", prompt, "--skip-trust"]
            mapped = resolve_gemini_cli_model(model)
            if mapped:
                cmd.extend(["-m", mapped])
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(repo_root),
                env=_build_subprocess_env(),
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=20.0)
            if proc.returncode == 0 and out:
                return out.decode("utf-8", errors="replace").strip()[:1500]
        else:
            claude_bin = shutil.which("claude")
            if not claude_bin:
                return None
            cmd = [claude_bin, "--print", prompt]
            if model:
                cmd.extend(["--model", model])
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(repo_root),
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=20.0)
            if proc.returncode == 0 and out:
                return out.decode("utf-8", errors="replace").strip()[:1500]
    except Exception as exc:
        logger.debug("spec_critique.live_llm_skipped runtime=%s err=%s", runtime, exc)
    return None


@router.post("/specs/{spec_path:path}/cross-critique")
async def cross_critique_spec(
    spec_path: str,
    body: Optional[SpecCrossCritiqueRequest] = Body(default=None),
):
    """Run a Pre-Design Audit and structural cross-critique against the active repository."""
    req = body or SpecCrossCritiqueRequest()
    norm_runtime = (req.reviewer_runtime or "gemini").strip().lower()
    if norm_runtime not in {"gemini", "claude"}:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported reviewer_runtime {req.reviewer_runtime!r}; expected 'gemini' or 'claude'.",
        )

    repo_root = spawn_pipeline.resolve_target_repo(req.repo_path)
    spec_file = _resolve_spec_file(spec_path, repo_root)
    spec_text = spec_file.read_text(encoding="utf-8", errors="replace")

    scalars, task_ids = _extract_frontmatter_and_tasks(spec_text)
    create_candidates = _extract_create_candidates(spec_text)

    matched_existing_files: list[str] = []
    findings: list[dict[str, Any]] = []

    for rel_path in create_candidates:
        full_candidate = repo_root / rel_path
        if full_candidate.is_file():
            matched_existing_files.append(rel_path)
            findings.append({
                "severity": "error",
                "category": "pre_design_audit",
                "file": rel_path,
                "message": (
                    f"File '{rel_path}' is listed under Create:, but it already exists in "
                    f"'{repo_root.name}'. Relabel it as 'Existing (reuse): {rel_path}' before building."
                ),
            })

    checkboxes = [
        {"checked": m.group("mark").lower() == "x", "text": m.group("text").strip()}
        for line in spec_text.splitlines()
        if (m := _CHECKBOX_RE.match(line))
    ]
    no_ac_needed = scalars.get("no_ac_needed", "").lower() == "true"
    if not checkboxes and not no_ac_needed:
        findings.append({
            "severity": "warning",
            "category": "acceptance_criteria",
            "message": "Spec has no testable acceptance criteria checkboxes ('- [ ] ...').",
        })

    if not task_ids:
        findings.append({
            "severity": "info",
            "category": "task_linkage",
            "message": "Spec frontmatter does not list any linked task IDs under 'tasks:' yet.",
        })

    if re.search(r"^##\s+(?:USER FEEDBACK|DECISION)\b", spec_text, re.IGNORECASE | re.MULTILINE):
        findings.append({
            "severity": "info",
            "category": "user_override",
            "message": "Spec contains a USER FEEDBACK or DECISION section that supersedes earlier text.",
        })

    live_critique_text: Optional[str] = None
    if req.run_live_llm:
        live_critique_text = await _run_optional_live_critique(
            runtime=norm_runtime,
            model=req.reviewer_model,
            spec_text=spec_text,
            matched_existing_files=matched_existing_files,
            repo_root=repo_root,
        )
        if live_critique_text:
            findings.append({
                "severity": "info",
                "category": "model_critique",
                "message": live_critique_text,
            })

    audit_clear = len(matched_existing_files) == 0
    effective_model = req.reviewer_model or (
        "gemini-3.1-pro" if norm_runtime == "gemini" else "claude-sonnet-4-5"
    )
    if audit_clear:
        summary = (
            f"Pre-Design Audit clear against {repo_root.name}: "
            f"{len(create_candidates)} Create file(s) checked, 0 collisions, "
            f"{len(checkboxes)} acceptance criterion checkbox(es), "
            f"{len(task_ids)} linked task(s)."
        )
    else:
        summary = (
            f"Pre-Design Audit blocked: {len(matched_existing_files)} file(s) listed under Create: "
            f"already exist in {repo_root.name} ({', '.join(matched_existing_files)}). "
            "Update them to Existing (reuse): before building."
        )

    return {
        "spec_path": spec_path,
        "resolved_spec_path": str(spec_file),
        "repo_path": str(repo_root),
        "reviewer_runtime": norm_runtime,
        "reviewer_model": effective_model,
        "audit_clear": audit_clear,
        "matched_existing_files": matched_existing_files,
        "created_candidates": create_candidates,
        "summary": summary,
        "findings": findings,
        "live_critique": live_critique_text,
    }


def _find_agent_worktree_for_task(task_id: str) -> tuple[Optional[Path], Optional[str], Optional[str]]:
    """Return (worktree_path, branch_name, base_sha) if an agent worked on task_id."""
    norm = re.sub(r"^(?:\u2192|->|#|t-)+", "", str(task_id).strip())
    if not norm:
        return None, None, None
    try:
        import routers.agents as agents_mod

        for _, meta in reversed(list(agents_mod.agent_metadata.items())):
            if not isinstance(meta, dict):
                continue
            cand_tid = re.sub(r"^(?:\u2192|->|#|t-)+", "", str(meta.get("task_id") or "").strip())
            cand_nid = re.sub(r"^(?:\u2192|->|#|t-)+", "", str(meta.get("needle_id") or "").strip())
            if norm in {cand_tid, cand_nid}:
                wt_raw = meta.get("worktree_path")
                wt_path = Path(wt_raw) if wt_raw else None
                branch = meta.get("branch_name") or meta.get("worktree_branch")
                bsha = meta.get("base_sha")
                return wt_path, branch, bsha
    except Exception:
        pass
    return None, None, None


@router.get("/specs/{spec_path:path}/receipts")
async def get_spec_receipts(
    spec_path: str,
    repo_path: Optional[str] = Query(default=None),
    base_sha: Optional[str] = Query(default=None),
):
    """Verify cryptographic git commits and exit-0 audit test runs for all tasks linked to a spec."""
    repo_root = spawn_pipeline.resolve_target_repo(repo_path)
    spec_file = _resolve_spec_file(spec_path, repo_root)
    spec_text = spec_file.read_text(encoding="utf-8", errors="replace")

    _, task_ids = _extract_frontmatter_and_tasks(spec_text)

    if not task_ids:
        try:
            from services.ostk import ostk

            ostk_linked = await ostk.spec_tasks(spec_path)
            for item in ostk_linked:
                tid = str(item.get("id") or "").strip().lstrip("\u2192")
                if tid and tid not in task_ids:
                    task_ids.append(tid)
        except Exception:
            pass

    task_receipts: list[dict[str, Any]] = []
    for tid in task_ids:
        wt_path, branch_name, agent_base_sha = _find_agent_worktree_for_task(tid)
        effective_base = base_sha or agent_base_sha
        receipt = await asyncio.to_thread(
            verify_task_receipt,
            repo_root=repo_root,
            worktree_path=wt_path,
            branch_name=branch_name,
            base_sha=effective_base,
            task_id=tid,
        )
        task_receipts.append({
            "task_id": tid,
            **receipt.to_dict(),
        })

    all_verified = bool(task_receipts) and all(r["verified"] for r in task_receipts)
    missing_tasks_reason = (
        "No linked tasks found on spec; cannot verify completion without task receipts."
        if not task_receipts
        else None
    )

    return {
        "spec_path": spec_path,
        "resolved_spec_path": str(spec_file),
        "repo_path": str(repo_root),
        "linked_task_count": len(task_receipts),
        "all_verified": all_verified,
        "missing_tasks_reason": missing_tasks_reason,
        "task_receipts": task_receipts,
    }
