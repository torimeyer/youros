"""Regression test for →2619: shipped spec templates must be person-agnostic.

The junk draft probe-draft-loc-1780595950.md (June 4) exposed that the
canonical spec template stamped the operator's name ("tori") into every
new draft. The privacy scrub commit eaa93b33 neutralized the copy in
api/routers/specs.py but missed api/services/spec_templates.py. This test
guards both: the generated template body and the module sources must never
contain the operator's name again.

Word-boundary matching is deliberate: "directories" contains the letters
"tori", so a bare substring check would false-positive.
"""

import re
from pathlib import Path

from services.spec_templates import (
    BUILTIN_SPEC_TEMPLATES,
    canonical_spec_template_body,
)

_OPERATOR_NAME = re.compile(r"\btori\b", re.IGNORECASE)

_API_DIR = Path(__file__).resolve().parents[1]


def test_canonical_template_body_has_no_operator_name():
    body = canonical_spec_template_body()
    match = _OPERATOR_NAME.search(body)
    assert match is None, (
        f"canonical_spec_template_body() contains the operator's name: "
        f"...{body[max(0, match.start() - 40):match.end() + 40]!r}..."
    )


def test_canonical_template_body_keeps_all_sections():
    body = canonical_spec_template_body()
    for heading in [
        "## Problem",
        "## Goals",
        "## Non-goals",
        "## Solution",
        "## Acceptance criteria",
        "## USER FEEDBACK",
        "## DECISION",
        "## References",
    ]:
        assert heading in body, f"canonical template lost section {heading!r}"


def test_builtin_plan_templates_have_no_operator_name():
    for template in BUILTIN_SPEC_TEMPLATES:
        blob = repr(template)
        match = _OPERATOR_NAME.search(blob)
        assert match is None, (
            f"plan template {template['id']!r} contains the operator's name: "
            f"...{blob[max(0, match.start() - 40):match.end() + 40]!r}..."
        )


def test_template_module_sources_have_no_operator_name():
    for rel in ("services/spec_templates.py", "routers/specs.py"):
        source = (_API_DIR / rel).read_text(encoding="utf-8")
        match = _OPERATOR_NAME.search(source)
        assert match is None, (
            f"{rel} contains the operator's name near: "
            f"...{source[max(0, match.start() - 60):match.end() + 60]!r}..."
        )
