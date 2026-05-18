"""Test that _system_prompt includes ETA hint instruction text.

RED test — will fail until the ETA paragraph is added to _system_prompt.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_system_prompt_includes_eta_hint():
    """_system_prompt must instruct the model to prefix replies with ~Nm."""
    from services.chat_providers import _system_prompt

    prompt = _system_prompt()
    # The ETA paragraph must exist and reference the ~Nm prefix convention.
    assert "~" in prompt, "_system_prompt missing ~ character for ETA prefix"
    assert "ETA" in prompt or "estimate" in prompt.lower(), (
        "_system_prompt missing ETA/estimate instruction"
    )
    # Must mention the ">2" second threshold so the model knows when to apply it.
    assert "2" in prompt, "_system_prompt missing >2s threshold reference"
