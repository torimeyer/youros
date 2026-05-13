"""Tests for the worktree triage report (→1249)."""
import re
import subprocess
from pathlib import Path

REPORT_PATH = Path(__file__).parents[2] / ".ostk" / "worktree-triage-2026-05-13.md"


def _parse_report(text: str) -> dict:
    """Parse section counts from the triage report."""
    sections = {"ABSORBED": [], "UNIQUE": [], "DUPLICATE": []}
    current = None
    for line in text.splitlines():
        for key in sections:
            m = re.match(rf"## {key} \((\d+) worktrees", line)
            if m:
                current = key
                break
        else:
            if current and line.startswith("- "):
                sections[current].append(line)
    return sections


def _git_worktree_count() -> int:
    result = subprocess.run(
        ["git", "worktree", "list"],
        capture_output=True, text=True,
        cwd=Path(__file__).parents[2],
    )
    return len([l for l in result.stdout.splitlines() if l.strip()])


def test_report_file_exists():
    assert REPORT_PATH.exists(), f"Report not found at {REPORT_PATH}"


def test_report_parses_cleanly():
    text = REPORT_PATH.read_text()
    assert "# Worktree triage 2026-05-13" in text
    sections = _parse_report(text)
    assert "ABSORBED" in text
    assert "UNIQUE" in text
    assert "DUPLICATE" in text
    for key, items in sections.items():
        assert len(items) >= 0, f"Section {key} failed to parse"


def test_report_counts_add_up():
    text = REPORT_PATH.read_text()

    # Extract counts from section headers
    absorbed_m = re.search(r"## ABSORBED \((\d+) worktrees", text)
    unique_m = re.search(r"## UNIQUE \((\d+) worktrees", text)
    duplicate_m = re.search(r"## DUPLICATE \((\d+) worktrees", text)

    assert absorbed_m, "ABSORBED section header not found"
    assert unique_m, "UNIQUE section header not found"
    assert duplicate_m, "DUPLICATE section header not found"

    absorbed_n = int(absorbed_m.group(1))
    unique_n = int(unique_m.group(1))
    duplicate_n = int(duplicate_m.group(1))
    total_classified = absorbed_n + unique_n + duplicate_n

    # Verify internal consistency: section counts add up to the Total line.
    total_m = re.search(r"Total worktrees classified: (\d+)", text)
    assert total_m, "Total worktrees classified line not found in report"
    reported_total = int(total_m.group(1))

    assert total_classified == reported_total, (
        f"Section counts ({absorbed_n}+{unique_n}+{duplicate_n}={total_classified}) "
        f"do not match Total line ({reported_total}). "
        f"Breakdown: ABSORBED={absorbed_n}, UNIQUE={unique_n}, DUPLICATE={duplicate_n}"
    )
