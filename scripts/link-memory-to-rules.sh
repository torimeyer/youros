#!/usr/bin/env bash
# link-memory-to-rules.sh
#
# Wave 6 of plan →1288. Adds `enforces_rule:` frontmatter to memory files
# under the Claude project's memory dir (~/.claude/projects/<project>/memory/) so the
# kernel can cite the matching rationale doc when a rule fires. Also
# generates .claude/hooks/lib/rule-to-memory.json (reverse-index used by
# the loader and the rule-settings UI).
#
# Idempotent: if `enforces_rule:` already exists in a file, it's skipped.
# Atomic per-file: writes to a temp file in the same dir, then renames.
# On YAML parse failure or any error, that file is skipped and a warning
# is printed. Nothing is corrupted.
#
# Usage:
#   bash scripts/link-memory-to-rules.sh
#
# The mapping lives inside the embedded Python (one place, easy to audit).
# macOS bash 3.2 has no associative arrays, so we drive everything from
# Python and keep the shell layer minimal.

set -eo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Claude Code keys per-project memory by the project path with '/' -> '-'.
_PROJECT_SLUG="$(printf '%s' "$REPO_ROOT" | sed 's#/#-#g')"
MEMORY_DIR="${MYOS_MEMORY_DIR:-${HOME}/.claude/projects/${_PROJECT_SLUG}/memory}"
REVERSE_INDEX="${REPO_ROOT}/.claude/hooks/lib/rule-to-memory.json"

if [[ ! -d "${MEMORY_DIR}" ]]; then
  echo "ERROR: memory dir not found: ${MEMORY_DIR}" >&2
  exit 1
fi

mkdir -p "$(dirname "${REVERSE_INDEX}")"

python3 - "${MEMORY_DIR}" "${REVERSE_INDEX}" <<'PY'
import json
import os
import sys
import tempfile

memory_dir, reverse_index_path = sys.argv[1], sys.argv[2]

# --------------------------------------------------------------------------
# Mapping: rule key -> list of memory file basenames that describe the rule
# in prose form. Built by reading every memory filename and matching the
# title/content against the 16 rule keys in default-rules.json.
#
# standing_rules_blocks is split into sub-flags (receipts_check,
# stall_death_check, zero_byte_transcript_check) so the reverse-index can
# address each sub-rule individually.
#
# Rules without a clear memory file are intentionally absent:
#   - humanfile_render (no rationale doc; the HUMANFILE is the doc)
#   - monitor_misuse_guard (no dedicated memory file)
#   - permission_deny_detector (infra, no rationale doc)
# --------------------------------------------------------------------------
RULE_TO_MEMORY = {
    "saa_must_spawn": [
        "feedback_saa_rules.md",
        "feedback_plan_is_part_of_saa.md",
        "feedback_subagent_prompt_template.md",
        "feedback_saa_fixes_preexisting_failures.md",
        "feedback_subagent_brief_pin_to_single_needle.md",
    ],
    "adhd_monitor_pairing": [
        "feedback_adhd_mode_auto_arm_monitor.md",
        "feedback_monitor_for_60s_checkins.md",
    ],
    "bash_guards": [
        "feedback_curl_http_code_pattern.md",
        "feedback_ostk_bash_raw_for_tests.md",
        "feedback_spawn_interact_for_long_commands.md",
        "feedback_no_grep_word.md",
        "feedback_monitor_zsh_status_collision.md",
    ],
    "incremental_commit_warning": [
        "feedback_no_parallel_commits_without_worktrees.md",
        "feedback_spawn_burst_commit_contamination.md",
    ],
    "post_commit_auto_close": [
        "feedback_scaffold_commits_dont_close_needles.md",
        "feedback_check_git_log_before_destructive_ops.md",
        "feedback_every_commit_needs_a_needle.md",
    ],
    "ostk_first": [
        "feedback_ostk_over_bash.md",
        "feedback_ostk_rules.md",
        "feedback_always_announce_native_fallback.md",
    ],
    "isolation_bridge": [
        "feedback_bridge_locks_contract.md",
        "feedback_bridge_spawn_409_is_lock_conflict.md",
        "feedback_subagent_cwd_must_be_worktree.md",
        "feedback_subagent_worktree_misses_uncommitted_main.md",
    ],
    "standing_rules_blocks.receipts_check": [
        "feedback_receipts_for_every_done_claim.md",
    ],
    "standing_rules_blocks.stall_death_check": [
        "feedback_never_declare_agent_dead_without_full_probe.md",
        "feedback_false_alarm_meta_pattern.md",
        "feedback_check_git_log_before_respawn.md",
    ],
    "standing_rules_blocks.zero_byte_transcript_check": [
        "feedback_zero_byte_transcript_diagnose_order.md",
        "feedback_quota_silent_fail.md",
    ],
    "needle_hygiene": [
        "feedback_needles_are_backlog_not_cleanup.md",
        "feedback_every_commit_needs_a_needle.md",
    ],
    "ghost_reaper_live_pid_check": [
        "feedback_ghost_reaper_live_pid_check.md",
    ],
    "task_state_hygiene": [
        "feedback_task_states.md",
    ],
    "scaffold_commit_alert": [
        "feedback_scaffold_commits_dont_close_needles.md",
    ],
    "keep_going_pending_tasks": [
        "feedback_pending_task_hook_not_autonomous.md",
        "feedback_dont_stall_waiting_for_subagents.md",
        "feedback_dont_defer_when_time_available.md",
    ],
}

# Insertion order matters for overlap resolution. When the same memory file
# is cited by multiple rules, the first one wins for the frontmatter write
# (most-specific rule first).
ORDER = [
    "saa_must_spawn",
    "adhd_monitor_pairing",
    "bash_guards",
    "incremental_commit_warning",
    "post_commit_auto_close",
    "ostk_first",
    "isolation_bridge",
    "standing_rules_blocks.receipts_check",
    "standing_rules_blocks.stall_death_check",
    "standing_rules_blocks.zero_byte_transcript_check",
    "needle_hygiene",
    "ghost_reaper_live_pid_check",
    "task_state_hygiene",
    "scaffold_commit_alert",
    "keep_going_pending_tasks",
]

# Build reverse map: filename -> primary rule key.
memory_to_rule = {}
for rule_key in ORDER:
    for fname in RULE_TO_MEMORY[rule_key]:
        if fname not in memory_to_rule:
            memory_to_rule[fname] = rule_key


def patch_file(path: str, rule_key: str) -> str:
    """Patch a single memory file. Returns one of:
    patched, already_linked, no_frontmatter, error.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"WARN: could not read {path}: {e}", file=sys.stderr)
        return "error"

    if not text.startswith("---\n"):
        return "no_frontmatter"

    end = text.find("\n---\n", 4)
    if end == -1:
        # Tolerate trailing --- with no newline after.
        end = text.find("\n---", 4)
        if end == -1:
            return "no_frontmatter"

    fm_inner = text[4:end]
    rest = text[end:]  # starts with "\n---"

    if "enforces_rule:" in fm_inner:
        return "already_linked"

    lines = fm_inner.split("\n")

    metadata_idx = None
    type_idx = None
    for i, line in enumerate(lines):
        stripped_eol = line.rstrip()
        if stripped_eol == "metadata:" or line.startswith("metadata: "):
            metadata_idx = i
            break
        if line.startswith("type:") and type_idx is None:
            type_idx = i

    new_lines = list(lines)

    if metadata_idx is not None:
        insert_at = metadata_idx + 1
        indent = "  "
        if insert_at < len(lines):
            nxt = lines[insert_at]
            stripped = nxt.lstrip(" ")
            if len(nxt) > len(stripped) and stripped:
                indent = nxt[: len(nxt) - len(stripped)]
        new_lines.insert(insert_at, f"{indent}enforces_rule: {rule_key}")
    elif type_idx is not None:
        type_line = lines[type_idx]
        type_val = type_line.split(":", 1)[1].strip() or "feedback"
        # Preserve sibling fields (e.g. originSessionId) — they stay flat,
        # we just nest the type under a new metadata block.
        replacement = [
            "metadata:",
            f"  type: {type_val}",
            f"  enforces_rule: {rule_key}",
        ]
        new_lines = lines[:type_idx] + replacement + lines[type_idx + 1:]
    else:
        new_lines.append("metadata:")
        new_lines.append(f"  enforces_rule: {rule_key}")

    new_fm = "\n".join(new_lines)
    new_text = "---\n" + new_fm + rest

    dir_ = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".link-memory-", dir=dir_)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_text)
        os.replace(tmp_path, path)
    except OSError as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        print(f"WARN: could not write {path}: {e}", file=sys.stderr)
        return "error"

    return "patched"


# Patch every mapped memory file.
counts = {
    "patched": 0,
    "already_linked": 0,
    "no_frontmatter": 0,
    "not_found": 0,
    "error": 0,
}
for fname, rule_key in memory_to_rule.items():
    path = os.path.join(memory_dir, fname)
    if not os.path.isfile(path):
        print(f"WARN: memory file not found, skipping: {fname}", file=sys.stderr)
        counts["not_found"] += 1
        continue
    status = patch_file(path, rule_key)
    counts[status] += 1
    if status == "no_frontmatter":
        print(f"WARN: no frontmatter: {fname}", file=sys.stderr)

# Write the reverse-index (rule key -> list of memory file basenames).
# The UI from wave 5 and the loader from wave 7 both consume this.
with open(reverse_index_path, "w", encoding="utf-8") as f:
    json.dump(RULE_TO_MEMORY, f, indent=2, sort_keys=True)
    f.write("\n")

print()
print("----------------------------------------")
print("Memory-to-rules link summary")
print("----------------------------------------")
print(f"Patched:                  {counts['patched']}")
print(f"Skipped (already linked): {counts['already_linked']}")
print(f"Skipped (no frontmatter): {counts['no_frontmatter']}")
print(f"Skipped (not found):      {counts['not_found']}")
print(f"Errors:                   {counts['error']}")
print(f"Reverse-index:            {reverse_index_path}")
PY
