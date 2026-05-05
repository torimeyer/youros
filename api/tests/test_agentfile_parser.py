"""Tests for the Agentfile parser."""

import pytest

from services.agentfile_parser import (
    AgentfileConfig,
    AgentfileParseError,
    PinPolicy,
    LimitPolicy,
    build_capabilities_summary,
    build_quality_gate_instructions,
    parse_agentfile,
    serialize_agentfile,
)


# ---- Existing behavior (backward compat) -----------------------------------


def test_parse_basic_agentfile(tmp_path):
    """Parse a basic Agentfile with standard directives."""
    af = tmp_path / "test.agent"
    af.write_text(
        '# Test agent\n'
        'FROM auto\n'
        'PROMPT "You are a test agent."\n'
        'TOOL shell\n'
        'TOOL file:read\n'
        'LIMIT tokens 100000\n'
        'BOOT ostk boot\n'
        'PIN default\n'
    )
    config = parse_agentfile(af)
    assert config.model == "auto"
    assert config.prompt == "You are a test agent."
    assert config.tools == ["shell", "file:read"]
    assert config.token_limit == 100000
    assert config.boot == "ostk boot"


def test_parse_quality_gate_directives(tmp_path):
    """Parse AC, REVIEW, LIMIT test_coverage, and STANDARDS."""
    af = tmp_path / "gated.agent"
    af.write_text(
        'FROM auto\n'
        'PROMPT "Test"\n'
        'AC python3 -m pytest -x -q\n'
        'AC cd app && npx tsc -b\n'
        'REVIEW owasp,performance,accessibility\n'
        'LIMIT test_coverage 80\n'
        'STANDARDS .standards.md\n'
    )
    config = parse_agentfile(af)
    assert config.acceptance_criteria == [
        "python3 -m pytest -x -q",
        "cd app && npx tsc -b",
    ]
    assert config.review_checklists == ["owasp", "performance", "accessibility"]
    assert config.test_coverage_min == 80
    assert config.standards_path == ".standards.md"


def test_parse_empty_file(tmp_path):
    """Empty file returns default config."""
    af = tmp_path / "empty.agent"
    af.write_text("")
    config = parse_agentfile(af)
    assert config.model == "auto"
    assert config.acceptance_criteria == []


def test_parse_comments_ignored(tmp_path):
    """Comments and blank lines are skipped."""
    af = tmp_path / "comments.agent"
    af.write_text(
        '# This is a comment\n'
        '\n'
        'FROM sonnet\n'
        '# Another comment\n'
        'TOOL shell\n'
    )
    config = parse_agentfile(af)
    assert config.model == "sonnet"
    assert config.tools == ["shell"]


def test_parse_nonexistent_file(tmp_path):
    """Nonexistent file returns default config."""
    config = parse_agentfile(tmp_path / "nope.agent")
    assert config.model == "auto"


def test_build_quality_gate_instructions_with_ac():
    """Quality gate instructions include AC commands."""
    config = AgentfileConfig(
        acceptance_criteria=["pytest -x", "tsc -b"],
    )
    instructions = build_quality_gate_instructions(config)
    assert "pytest -x" in instructions
    assert "tsc -b" in instructions
    assert "MUST pass" in instructions


def test_build_quality_gate_instructions_with_review():
    """Quality gate instructions include review checklists."""
    config = AgentfileConfig(
        review_checklists=["owasp", "performance"],
    )
    instructions = build_quality_gate_instructions(config)
    assert "owasp" in instructions
    assert "performance" in instructions


def test_build_quality_gate_instructions_with_coverage():
    """Quality gate instructions include coverage requirement."""
    config = AgentfileConfig(test_coverage_min=80)
    instructions = build_quality_gate_instructions(config)
    assert "80%" in instructions


def test_build_quality_gate_instructions_empty():
    """No quality gates produces empty instructions."""
    config = AgentfileConfig()
    instructions = build_quality_gate_instructions(config)
    assert instructions == ""


def test_comprehensive_agentfile_has_quality_gates():
    """The comprehensive.agent file must have acceptance criteria.

    The canonical build pattern lives on comprehensive.agent. saa.agent
    is a thin alias stub that resolves to this file, verified below.
    """
    from config import PROJECT_ROOT
    path = PROJECT_ROOT / "agents" / "comprehensive.agent"
    if not path.exists():
        return
    config = parse_agentfile(path)
    assert len(config.acceptance_criteria) >= 1, "comprehensive.agent must have at least one AC"
    assert config.standards_path is not None, "comprehensive.agent must reference a standards file"


def test_saa_alias_resolves_to_comprehensive():
    """saa is Tori's muscle memory alias and must resolve to comprehensive.

    Uses get_agent_config_by_template which follows both the builtin
    alias map and file-level ALIAS directives, so this test covers
    both resolution paths at once.
    """
    from services.agentfile_parser import get_agent_config_by_template
    config = get_agent_config_by_template("saa")
    assert config is not None, "saa template must resolve"
    assert len(config.acceptance_criteria) >= 1, "resolved saa must carry the AC gates"
    assert config.standards_path is not None, "resolved saa must carry the standards ref"


# ---- New: NAME / DESC / MODEL directives -----------------------------------


def test_parse_name_and_desc(tmp_path):
    """NAME and DESC set identity and description."""
    af = tmp_path / "named.agent"
    af.write_text(
        'NAME "bug-hunter"\n'
        'DESC "Finds and fixes bugs."\n'
        'FROM auto\n'
        'PROMPT "do the thing"\n'
    )
    config = parse_agentfile(af)
    assert config.name == "bug-hunter"
    assert config.description == "Finds and fixes bugs."


def test_name_desc_absent_defaults_to_stem(tmp_path):
    """When NAME is absent the config.name falls back to the file stem."""
    af = tmp_path / "fallback.agent"
    af.write_text('FROM auto\nPROMPT "x"\n')
    config = parse_agentfile(af)
    assert config.name == "fallback"
    assert config.description == ""


def test_model_alias_for_from(tmp_path):
    """MODEL is accepted as an alias for FROM."""
    af = tmp_path / "model.agent"
    af.write_text('MODEL sonnet\nPROMPT "hi"\n')
    config = parse_agentfile(af)
    assert config.model == "sonnet"


# ---- New: SKILL ------------------------------------------------------------


def test_parse_skill_directive(tmp_path):
    """SKILL lines collect into a list preserving order."""
    af = tmp_path / "skilled.agent"
    af.write_text(
        'FROM auto\nPROMPT "x"\n'
        'SKILL tdd\n'
        'SKILL refactor\n'
    )
    config = parse_agentfile(af)
    assert config.skills == ["tdd", "refactor"]


def test_skill_absent(tmp_path):
    """Absent SKILL leaves an empty list."""
    af = tmp_path / "noskill.agent"
    af.write_text('FROM auto\nPROMPT "x"\n')
    config = parse_agentfile(af)
    assert config.skills == []


def test_skill_malformed_raises(tmp_path):
    """SKILL with no value is a parse error."""
    af = tmp_path / "bad.agent"
    af.write_text('FROM auto\nPROMPT "x"\nSKILL\n')
    with pytest.raises(AgentfileParseError) as excinfo:
        parse_agentfile(af)
    assert excinfo.value.line_number == 3
    assert "SKILL" in str(excinfo.value)


# ---- New: WORK -------------------------------------------------------------


def test_parse_work_directive(tmp_path):
    """WORK captures the needle filter expression."""
    af = tmp_path / "work.agent"
    af.write_text(
        'FROM auto\nPROMPT "x"\n'
        'WORK tags=rust,bugfix priority>=P1\n'
    )
    config = parse_agentfile(af)
    assert config.work == "tags=rust,bugfix priority>=P1"


def test_work_absent(tmp_path):
    """Absent WORK leaves an empty string."""
    af = tmp_path / "nowork.agent"
    af.write_text('FROM auto\nPROMPT "x"\n')
    config = parse_agentfile(af)
    assert config.work == ""


# ---- New: INTERRUPT --------------------------------------------------------


def test_parse_interrupt_directive(tmp_path):
    """INTERRUPT lines collect into a list."""
    af = tmp_path / "int.agent"
    af.write_text(
        'FROM auto\nPROMPT "x"\n'
        'INTERRUPT event:hay.filed\n'
        'INTERRUPT event:needle.created\n'
    )
    config = parse_agentfile(af)
    assert config.interrupts == ["event:hay.filed", "event:needle.created"]


def test_interrupt_absent(tmp_path):
    af = tmp_path / "noint.agent"
    af.write_text('FROM auto\nPROMPT "x"\n')
    config = parse_agentfile(af)
    assert config.interrupts == []


def test_interrupt_malformed_raises(tmp_path):
    """INTERRUPT with no value is a parse error."""
    af = tmp_path / "bad.agent"
    af.write_text('FROM auto\nPROMPT "x"\nINTERRUPT\n')
    with pytest.raises(AgentfileParseError) as excinfo:
        parse_agentfile(af)
    assert excinfo.value.line_number == 3


# ---- New: LIMIT subkeys ----------------------------------------------------


def test_parse_limit_all_subkeys(tmp_path):
    """Every documented LIMIT subkey is parsed into the right type."""
    af = tmp_path / "limits.agent"
    af.write_text(
        'FROM auto\nPROMPT "x"\n'
        'LIMIT budget_usd 5\n'
        'LIMIT tokens 200000\n'
        'LIMIT turns 50\n'
        'LIMIT wall_clock 30m\n'
        'LIMIT context_pct 80\n'
        'LIMIT permissions governed\n'
        'LIMIT revival_policy revive\n'
        'LIMIT destructive_ops 3\n'
    )
    config = parse_agentfile(af)
    assert config.limits.budget_usd == 5.0
    assert config.limits.tokens == 200000
    assert config.limits.turns == 50
    assert config.limits.wall_clock == "30m"
    assert config.limits.context_pct == 80
    assert config.limits.permissions == "governed"
    assert config.limits.revival_policy == "revive"
    assert config.limits.destructive_ops == 3
    # Legacy token_limit still populated for backward compat.
    assert config.token_limit == 200000


def test_parse_limit_wall_clock_variants(tmp_path):
    """wall_clock accepts duration strings and raw seconds."""
    af = tmp_path / "wc.agent"
    af.write_text('FROM auto\nPROMPT "x"\nLIMIT wall_clock 2h\n')
    assert parse_agentfile(af).limits.wall_clock == "2h"

    af.write_text('FROM auto\nPROMPT "x"\nLIMIT wall_clock 300\n')
    assert parse_agentfile(af).limits.wall_clock == "300"


def test_parse_limit_wall_clock_invalid_raises(tmp_path):
    """wall_clock rejects garbage values with a clear error."""
    af = tmp_path / "wc.agent"
    af.write_text('FROM auto\nPROMPT "x"\nLIMIT wall_clock forever\n')
    with pytest.raises(AgentfileParseError) as excinfo:
        parse_agentfile(af)
    assert "wall_clock" in str(excinfo.value)
    assert excinfo.value.line_number == 3


def test_parse_limit_tokens_non_integer_raises(tmp_path):
    """LIMIT tokens with a non-integer value raises with the line number."""
    af = tmp_path / "bad.agent"
    af.write_text('FROM auto\nPROMPT "x"\nLIMIT tokens many\n')
    with pytest.raises(AgentfileParseError) as excinfo:
        parse_agentfile(af)
    assert excinfo.value.line_number == 3
    assert "tokens" in str(excinfo.value)


def test_parse_limit_absent(tmp_path):
    """No LIMIT lines leaves defaults in place."""
    af = tmp_path / "nolimit.agent"
    af.write_text('FROM auto\nPROMPT "x"\n')
    config = parse_agentfile(af)
    assert config.limits == LimitPolicy()


def test_parse_limit_missing_value_raises(tmp_path):
    """LIMIT with only a key raises a parse error."""
    af = tmp_path / "bad.agent"
    af.write_text('FROM auto\nPROMPT "x"\nLIMIT tokens\n')
    with pytest.raises(AgentfileParseError):
        parse_agentfile(af)


# ---- New: ISOLATION --------------------------------------------------------


@pytest.mark.parametrize("value", ["none", "nono", "docker", "firecracker"])
def test_parse_isolation_valid(tmp_path, value):
    """All four documented ISOLATION tiers are accepted."""
    af = tmp_path / "iso.agent"
    af.write_text(f'FROM auto\nPROMPT "x"\nISOLATION {value}\n')
    config = parse_agentfile(af)
    assert config.isolation == value


def test_parse_isolation_case_insensitive(tmp_path):
    """ISOLATION values are normalized to lowercase."""
    af = tmp_path / "iso.agent"
    af.write_text('FROM auto\nPROMPT "x"\nISOLATION Docker\n')
    config = parse_agentfile(af)
    assert config.isolation == "docker"


def test_parse_isolation_absent_defaults_to_none(tmp_path):
    af = tmp_path / "iso.agent"
    af.write_text('FROM auto\nPROMPT "x"\n')
    config = parse_agentfile(af)
    assert config.isolation == "none"


def test_parse_isolation_invalid_raises(tmp_path):
    """Unknown ISOLATION values are a parse error, not a silent fallback."""
    af = tmp_path / "iso.agent"
    af.write_text('FROM auto\nPROMPT "x"\nISOLATION spaceship\n')
    with pytest.raises(AgentfileParseError) as excinfo:
        parse_agentfile(af)
    assert "ISOLATION" in str(excinfo.value)
    assert excinfo.value.line_number == 3


# ---- New: ATTEST -----------------------------------------------------------


@pytest.mark.parametrize("value", ["self", "gpg", "sigstore", "both"])
def test_parse_attest_valid(tmp_path, value):
    af = tmp_path / "attest.agent"
    af.write_text(f'FROM auto\nPROMPT "x"\nATTEST {value}\n')
    config = parse_agentfile(af)
    assert config.attest == value


def test_parse_attest_invalid_raises(tmp_path):
    af = tmp_path / "attest.agent"
    af.write_text('FROM auto\nPROMPT "x"\nATTEST pinky-swear\n')
    with pytest.raises(AgentfileParseError) as excinfo:
        parse_agentfile(af)
    assert "ATTEST" in str(excinfo.value)


def test_parse_attest_absent(tmp_path):
    af = tmp_path / "attest.agent"
    af.write_text('FROM auto\nPROMPT "x"\n')
    config = parse_agentfile(af)
    assert config.attest == ""


# ---- New: BETA -------------------------------------------------------------


def test_parse_beta_directive(tmp_path):
    af = tmp_path / "beta.agent"
    af.write_text('FROM auto\nPROMPT "x"\nBETA files-api\nBETA streaming\n')
    config = parse_agentfile(af)
    assert config.beta == ["files-api", "streaming"]


def test_parse_beta_absent(tmp_path):
    af = tmp_path / "beta.agent"
    af.write_text('FROM auto\nPROMPT "x"\n')
    config = parse_agentfile(af)
    assert config.beta == []


# ---- New: PIN (both forms) -------------------------------------------------


def test_parse_pin_named_form(tmp_path):
    """PIN default populates pin_policy.name and the legacy pin field."""
    af = tmp_path / "pin.agent"
    af.write_text('FROM auto\nPROMPT "x"\nPIN default\n')
    config = parse_agentfile(af)
    assert config.pin_policy.name == "default"
    assert config.pin == "default"


def test_parse_pin_subkeys(tmp_path):
    """PIN read/write/execute/deny/deny-verb populate pin_policy lists."""
    af = tmp_path / "pin.agent"
    af.write_text(
        'FROM auto\nPROMPT "x"\n'
        'PIN read: src/ tests/\n'
        'PIN write: src/\n'
        'PIN execute: scripts/\n'
        'PIN deny: .env secrets/\n'
        'PIN deny-verb: rm push\n'
    )
    config = parse_agentfile(af)
    assert config.pin_policy.read == ["src/", "tests/"]
    assert config.pin_policy.write == ["src/"]
    assert config.pin_policy.execute == ["scripts/"]
    assert config.pin_policy.deny == [".env", "secrets/"]
    assert config.pin_policy.deny_verb == ["rm", "push"]


def test_parse_pin_preserves_order(tmp_path):
    """PIN subkey lists keep original order across multiple lines."""
    af = tmp_path / "pin.agent"
    af.write_text(
        'FROM auto\nPROMPT "x"\n'
        'PIN write: a/\n'
        'PIN write: b/\n'
        'PIN write: c/\n'
    )
    config = parse_agentfile(af)
    assert config.pin_policy.write == ["a/", "b/", "c/"]


def test_parse_pin_mixed_form(tmp_path):
    """A file can use both PIN default and PIN subkeys together."""
    af = tmp_path / "pin.agent"
    af.write_text(
        'FROM auto\nPROMPT "x"\n'
        'PIN default\n'
        'PIN write: src/\n'
        'PIN deny: .env\n'
    )
    config = parse_agentfile(af)
    assert config.pin_policy.name == "default"
    assert config.pin_policy.write == ["src/"]
    assert config.pin_policy.deny == [".env"]


def test_pin_absent(tmp_path):
    af = tmp_path / "nopin.agent"
    af.write_text('FROM auto\nPROMPT "x"\n')
    config = parse_agentfile(af)
    assert config.pin_policy == PinPolicy()


# ---- New: FROM required / malformed ---------------------------------------


def test_from_with_no_value_raises(tmp_path):
    """FROM with no model name is a parse error."""
    af = tmp_path / "bad.agent"
    af.write_text('FROM\nPROMPT "x"\n')
    with pytest.raises(AgentfileParseError) as excinfo:
        parse_agentfile(af)
    assert "FROM" in str(excinfo.value)
    assert excinfo.value.line_number == 1


def test_tool_with_no_value_raises(tmp_path):
    """TOOL with no name is a parse error."""
    af = tmp_path / "bad.agent"
    af.write_text('FROM auto\nPROMPT "x"\nTOOL\n')
    with pytest.raises(AgentfileParseError) as excinfo:
        parse_agentfile(af)
    assert excinfo.value.line_number == 3


def test_parse_error_has_human_sentence(tmp_path):
    """Parse errors never expose a bare traceback to the user."""
    af = tmp_path / "bad.agent"
    af.write_text('FROM auto\nPROMPT "x"\nISOLATION unicorn\n')
    try:
        parse_agentfile(af)
        assert False, "expected AgentfileParseError"
    except AgentfileParseError as exc:
        msg = str(exc)
        assert "line 3" in msg
        assert "bad.agent" in msg
        assert "ISOLATION" in msg


# ---- New: all built-in agents parse cleanly -------------------------------


def test_all_builtin_agents_parse_cleanly():
    """Every file in agents/*.agent must parse without raising.

    This is the contract with the rest of myOS: existing recipes must
    keep working after we extend the parser.
    """
    from config import PROJECT_ROOT
    agents_dir = PROJECT_ROOT / "agents"
    if not agents_dir.exists():
        pytest.skip("agents/ directory not present")

    files = sorted(agents_dir.glob("*.agent"))
    assert files, "expected at least one .agent file to exist"

    for path in files:
        config = parse_agentfile(path)
        # Shape stability: LimitPolicy and PinPolicy are always present.
        assert isinstance(config.limits, LimitPolicy)
        assert isinstance(config.pin_policy, PinPolicy)
        # Alias stubs (e.g. saa.agent is `ALIAS comprehensive`) carry no
        # model or prompt of their own; the fields live on the target.
        # For those we just verify the alias points to a real sibling file.
        if config.alias:
            target = agents_dir / f"{config.alias}.agent"
            assert target.exists(), f"{path.name}: alias target {target.name} missing"
            continue
        # Non-alias files must carry the full shape directly.
        assert config.model, f"{path.name}: model missing"
        assert config.prompt, f"{path.name}: prompt missing"
        assert config.name == path.stem


# ---- Capabilities summary (plain-English for UI) --------------------------


def test_build_capabilities_summary_full():
    """Summary uses plain English for a fully-configured agent."""
    config = AgentfileConfig()
    config.pin_policy.write = ["src/", "tests/"]
    config.pin_policy.deny = [".env", "secrets/"]
    config.limits.budget_usd = 5.0
    config.limits.wall_clock = "30m"
    config.isolation = "docker"

    summary = build_capabilities_summary(config)
    assert summary["writes_to"] == "src/, tests/"
    assert summary["cannot_touch"] == ".env, secrets/"
    assert summary["budget"] == "$5"
    assert summary["time_limit"] == "30 minutes"
    assert summary["sandbox"] == "docker container"


def test_build_capabilities_summary_empty_defaults():
    """Empty config gets friendly defaults, not blank strings."""
    summary = build_capabilities_summary(AgentfileConfig())
    assert summary["writes_to"] == "nothing"
    assert summary["cannot_touch"] == "no restrictions set"
    assert summary["budget"] == "no budget limit"
    assert summary["time_limit"] == "no time limit"
    assert summary["sandbox"] == "none"


def test_build_capabilities_summary_wall_clock_hours():
    config = AgentfileConfig()
    config.limits.wall_clock = "2h"
    assert build_capabilities_summary(config)["time_limit"] == "2 hours"


def test_build_capabilities_summary_wall_clock_seconds():
    config = AgentfileConfig()
    config.limits.wall_clock = "300"
    assert build_capabilities_summary(config)["time_limit"] == "300 seconds"


def test_build_capabilities_summary_wall_clock_one_minute():
    """Singular noun for value of 1."""
    config = AgentfileConfig()
    config.limits.wall_clock = "1m"
    assert build_capabilities_summary(config)["time_limit"] == "1 minute"


def test_build_capabilities_summary_sandbox_nono():
    """nono becomes 'light sandbox' in plain English."""
    config = AgentfileConfig()
    config.isolation = "nono"
    assert build_capabilities_summary(config)["sandbox"] == "light sandbox"


def test_build_capabilities_summary_budget_decimal():
    """Fractional budgets are formatted without trailing zeros."""
    config = AgentfileConfig()
    config.limits.budget_usd = 2.5
    assert build_capabilities_summary(config)["budget"] == "$2.5"


# ---- serialize_agentfile (write direction) ----------------------------------


def test_serialize_then_parse_round_trip(tmp_path):
    """parse -> serialize -> parse produces the same config (round-trip).

    This is the contract every downstream system relies on: a config
    loaded from disk can be serialized and reloaded without data loss.
    """
    original_text = (
        'NAME "my-agent"\n'
        'DESC "Does useful things."\n'
        'FROM sonnet\n'
        'PROMPT "You are a helpful agent."\n'
        'TOOL shell\n'
        'TOOL file:read\n'
        'LIMIT tokens 100000\n'
        'LIMIT test_coverage 0\n'
        'BOOT ostk boot\n'
        'PIN default\n'
        'AC python3 -m pytest -x -q\n'
        'REVIEW security\n'
        'STANDARDS .standards.md\n'
    )
    first = tmp_path / "first.agent"
    first.write_text(original_text)
    config1 = parse_agentfile(first)

    serialized = serialize_agentfile(config1)
    assert "FROM sonnet" in serialized
    assert "TOOL shell" in serialized
    assert "TOOL file:read" in serialized
    assert "LIMIT tokens 100000" in serialized
    assert "AC python3" in serialized

    second = tmp_path / "second.agent"
    second.write_text(serialized)
    config2 = parse_agentfile(second)

    assert config2.model == config1.model
    assert config2.prompt == config1.prompt
    assert config2.tools == config1.tools
    assert config2.limits.tokens == config1.limits.tokens
    assert config2.limits.test_coverage == config1.limits.test_coverage
    assert config2.acceptance_criteria == config1.acceptance_criteria
    assert config2.review_checklists == config1.review_checklists
    assert config2.standards_path == config1.standards_path
    assert config2.boot == config1.boot
    assert config2.pin_policy.name == config1.pin_policy.name


def test_serialize_minimal_config():
    """A config with only FROM and PROMPT serializes to two lines."""
    cfg = AgentfileConfig()
    cfg.model = "auto"
    cfg.prompt = "Do stuff"
    text = serialize_agentfile(cfg)
    assert "FROM auto" in text
    assert 'PROMPT "Do stuff"' in text


def test_serialize_quotes_values_with_spaces():
    """Values containing spaces are wrapped in double quotes."""
    cfg = AgentfileConfig()
    cfg.model = "auto"
    cfg.prompt = "You are a helpful agent."
    text = serialize_agentfile(cfg)
    assert '"You are a helpful agent."' in text


def test_serialize_tools_each_on_own_line():
    """Each TOOL gets its own line."""
    cfg = AgentfileConfig()
    cfg.model = "auto"
    cfg.tools = ["shell", "file:read", "file:write"]
    text = serialize_agentfile(cfg)
    assert text.count("TOOL ") == 3


def test_serialize_isolation_omitted_when_none():
    """ISOLATION=none is not emitted (it is the default)."""
    cfg = AgentfileConfig()
    cfg.model = "auto"
    cfg.isolation = "none"
    text = serialize_agentfile(cfg)
    assert "ISOLATION" not in text


def test_serialize_isolation_emitted_when_nono():
    """ISOLATION=nono (light sandbox) is emitted."""
    cfg = AgentfileConfig()
    cfg.model = "auto"
    cfg.isolation = "nono"
    text = serialize_agentfile(cfg)
    assert "ISOLATION nono" in text


def test_serialize_pin_subkeys():
    """PIN read/write/deny subkeys are each emitted as their own line."""
    cfg = AgentfileConfig()
    cfg.model = "auto"
    cfg.pin_policy.read = ["src/", "tests/"]
    cfg.pin_policy.deny = [".env"]
    text = serialize_agentfile(cfg)
    assert "PIN read: src/ tests/" in text
    assert "PIN deny: .env" in text


def test_serialize_limit_budget_usd():
    """LIMIT budget_usd is serialized without trailing zeros."""
    cfg = AgentfileConfig()
    cfg.model = "auto"
    cfg.limits.budget_usd = 3.0
    text = serialize_agentfile(cfg)
    assert "LIMIT budget_usd 3" in text


def test_serialize_all_builtin_agents_round_trip():
    """Every built-in agents/*.agent can be serialized and re-parsed cleanly."""
    from config import PROJECT_ROOT
    agents_dir = PROJECT_ROOT / "agents"
    if not agents_dir.exists():
        pytest.skip("agents/ directory not present")

    import tempfile
    for path in sorted(agents_dir.glob("*.agent")):
        config = parse_agentfile(path)
        if config.alias:
            continue  # alias stubs have no directives to round-trip
        serialized = serialize_agentfile(config)
        with tempfile.NamedTemporaryFile(suffix=".agent", mode="w", delete=False) as f:
            f.write(serialized)
            tmp = f.name
        from pathlib import Path
        config2 = parse_agentfile(Path(tmp))
        assert config2.model == config.model, f"{path.name}: model mismatch after round-trip"
        assert config2.tools == config.tools, f"{path.name}: tools mismatch after round-trip"


# ---- ALIASES directive (file-level alias registration) ---------------------


def test_aliases_empty_by_default(tmp_path):
    """AgentfileConfig.aliases is an empty list when ALIASES is absent."""
    af = tmp_path / "noalias.agent"
    af.write_text("FROM auto\nPROMPT x\n")
    config = parse_agentfile(af)
    assert config.aliases == []


def test_aliases_single(tmp_path):
    """ALIASES with a single name populates the list."""
    af = tmp_path / "agent.agent"
    af.write_text("FROM auto\nPROMPT x\nALIASES saa\n")
    config = parse_agentfile(af)
    assert config.aliases == ["saa"]


def test_aliases_comma_separated(tmp_path):
    """ALIASES parses a comma-separated list into individual entries."""
    af = tmp_path / "agent.agent"
    af.write_text("FROM auto\nPROMPT x\nALIASES foo, bar, baz\n")
    config = parse_agentfile(af)
    assert config.aliases == ["foo", "bar", "baz"]


def test_aliases_space_separated(tmp_path):
    """ALIASES parses a space-separated list as well."""
    af = tmp_path / "agent.agent"
    af.write_text("FROM auto\nPROMPT x\nALIASES foo bar\n")
    config = parse_agentfile(af)
    assert config.aliases == ["foo", "bar"]


def test_aliases_mixed_separators(tmp_path):
    """ALIASES handles mixed comma-and-space separators."""
    af = tmp_path / "agent.agent"
    af.write_text("FROM auto\nPROMPT x\nALIASES foo, bar baz\n")
    config = parse_agentfile(af)
    assert config.aliases == ["foo", "bar", "baz"]


def test_aliases_serialize_round_trip(tmp_path):
    """ALIASES survives a parse -> serialize -> parse round-trip."""
    af = tmp_path / "agent.agent"
    af.write_text("FROM auto\nPROMPT x\nALIASES saa, sa\n")
    config = parse_agentfile(af)

    serialized = serialize_agentfile(config)
    assert "ALIASES saa, sa" in serialized

    af2 = tmp_path / "agent2.agent"
    af2.write_text(serialized)
    config2 = parse_agentfile(af2)
    assert config2.aliases == ["saa", "sa"]


def test_old_alias_pointer_still_works(tmp_path):
    """Single-line ALIAS <name> (pointer-stub form) still parses correctly."""
    af = tmp_path / "stub.agent"
    af.write_text("FROM auto\nALIAS builder\n")
    config = parse_agentfile(af)
    assert config.alias == "builder"
    assert config.aliases == []  # ALIAS != ALIASES


def test_lookup_by_alias_resolves_canonical(tmp_path):
    """get_agent_config_by_template resolves an alias to the canonical agent."""
    from services.agentfile_parser import get_agent_config_by_template, AGENTS_DIR

    # builder.agent should carry ALIASES saa and resolve to its full config.
    config = get_agent_config_by_template("saa")
    assert config is not None, "saa must resolve via ALIASES on builder.agent"
    assert config.name == "builder"
    assert len(config.acceptance_criteria) >= 1, "builder config must carry AC gates"
    assert config.standards_path is not None, "builder config must carry standards ref"


def test_lookup_elit_resolves_explain_plain():
    """elit resolves to explain-plain via ALIASES on explain-plain.agent."""
    from services.agentfile_parser import get_agent_config_by_template

    config = get_agent_config_by_template("elit")
    assert config is not None, "elit must resolve via ALIASES on explain-plain.agent"
    assert config.name == "explain-plain"


def test_builder_agent_has_aliases_saa():
    """builder.agent declares ALIASES saa so personal shortcuts ship on the canonical file."""
    from config import PROJECT_ROOT

    path = PROJECT_ROOT / "agents" / "builder.agent"
    if not path.exists():
        pytest.skip("builder.agent not present")
    config = parse_agentfile(path)
    assert "saa" in config.aliases, "builder.agent must declare ALIASES saa"


def test_explain_plain_agent_has_aliases_elit():
    """explain-plain.agent declares ALIASES elit."""
    from config import PROJECT_ROOT

    path = PROJECT_ROOT / "agents" / "explain-plain.agent"
    if not path.exists():
        pytest.skip("explain-plain.agent not present")
    config = parse_agentfile(path)
    assert "elit" in config.aliases, "explain-plain.agent must declare ALIASES elit"


def test_stub_files_deleted():
    """saa.agent and elit.agent must not exist; their aliases live on canonical files."""
    from config import PROJECT_ROOT

    assert not (PROJECT_ROOT / "agents" / "saa.agent").exists(), \
        "saa.agent should be deleted; alias is declared on builder.agent"
    assert not (PROJECT_ROOT / "agents" / "elit.agent").exists(), \
        "elit.agent should be deleted; alias is declared on explain-plain.agent"


