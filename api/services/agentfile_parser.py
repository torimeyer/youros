"""Agentfile parser.

Reads .agent files and extracts every directive in the ostk.ai Agentfile
spec, plus myOS-specific quality gate directives.

Core spec directives (from https://ostk.ai/docs/agentfile):
- FROM <model>: base model (required)
- PROMPT <text>: system instructions (required)
- TOOL <name>: MCP tool or capability
- SKILL <name>: skill bundle
- LIMIT <key> <value>: runtime constraint
- WORK <filter>: needle filter expression (max 1)
- INTERRUPT <event>: event trigger
- BOOT <command>: kernel boot command
- PIN <name> OR PIN <list>: <items>: capability pin
- ISOLATION <tier>: sandbox level
- ATTEST <mode>: signing mode
- BETA <flag>: feature flag

Additional myOS quality gate directives:
- AC <command>: acceptance criteria shell command
- REVIEW <checklist>: comma-separated review checklist names
- LIMIT test_coverage <N>: minimum test coverage percentage
- STANDARDS <path>: path to coding standards file

Malformed input raises ``AgentfileParseError`` with a line number and a
human sentence. Never a bare traceback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import PROJECT_ROOT

AGENTS_DIR = PROJECT_ROOT / "agents"


class AgentfileParseError(ValueError):
    """Raised when an Agentfile cannot be parsed.

    Carries the offending file path, the 1-indexed line number, and a
    human sentence describing what went wrong. Never wraps a traceback.
    """

    def __init__(self, message: str, line_number: int, path: Optional[Path] = None):
        self.line_number = line_number
        self.path = path
        location = f"line {line_number}"
        if path is not None:
            location = f"{path.name}, {location}"
        super().__init__(f"Agentfile parse error at {location}: {message}")


# ostk.ai sandbox tiers. Anything else is a parse error.
VALID_ISOLATION = {"none", "nono", "docker", "firecracker"}

# ostk.ai attestation modes. Anything else is a parse error.
VALID_ATTEST = {"self", "gpg", "sigstore", "both"}


@dataclass
class PinPolicy:
    """Parsed PIN directive.

    ostk supports two PIN forms:

    1. ``PIN default`` a single named policy name.
    2. Subkey lines that enumerate paths or verbs:
        ``PIN read: src/ tests/``
        ``PIN write: src/``
        ``PIN execute: scripts/``
        ``PIN deny: .env secrets/``
        ``PIN deny-verb: rm push``

    Both forms can coexist. ``name`` holds the named policy if set.
    The lists preserve original order.
    """

    name: str = ""
    read: list[str] = field(default_factory=list)
    write: list[str] = field(default_factory=list)
    execute: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    deny_verb: list[str] = field(default_factory=list)


@dataclass
class LimitPolicy:
    """Parsed LIMIT directive subkeys.

    Each ``LIMIT <key> <value>`` line feeds one field here. Absent keys
    stay at their default (``None`` or zero), so the UI has a stable
    shape to render against.
    """

    budget_usd: Optional[float] = None
    tokens: Optional[int] = None
    turns: Optional[int] = None
    wall_clock: Optional[str] = None  # preserved as original string (e.g. "30m", "300")
    context_pct: Optional[int] = None
    permissions: Optional[str] = None
    revival_policy: Optional[str] = None
    destructive_ops: Optional[int] = None
    # myOS extension, kept for backward compatibility.
    test_coverage: Optional[int] = None


@dataclass
class AgentfileConfig:
    """Full parsed Agentfile.

    Every field in the spec is present even when the directive is absent
    from the file, so the UI always sees the same shape. Unknown
    directives are ignored (not an error) to keep forward compatibility
    with new ostk features.
    """

    # Legacy flat fields (kept for backward compatibility with existing callers).
    name: str = ""
    model: str = "auto"
    prompt: str = ""
    tools: list[str] = field(default_factory=list)
    token_limit: int = 200000
    boot: str = ""
    pin: str = ""  # legacy flat form, mirrors pin_policy.name

    # Quality gates (myOS extensions).
    acceptance_criteria: list[str] = field(default_factory=list)
    review_checklists: list[str] = field(default_factory=list)
    test_coverage_min: Optional[int] = None
    standards_path: Optional[str] = None

    # Full spec directives.
    description: str = ""
    skills: list[str] = field(default_factory=list)
    work: str = ""
    interrupts: list[str] = field(default_factory=list)
    isolation: str = "none"
    attest: str = ""
    beta: list[str] = field(default_factory=list)
    pin_policy: PinPolicy = field(default_factory=PinPolicy)
    limits: LimitPolicy = field(default_factory=LimitPolicy)

    # Alias pointer. When an Agentfile contains only ``ALIAS <name>``,
    # this holds the target template name. The resolver in
    # get_agent_config_by_template follows it so the caller gets the
    # target config, not an empty shell.
    alias: str = ""


# Template alias map for the Tasks page "Comprehensive build" flow.
# This is a plain dict today so the spawn endpoint can resolve muscle
# memory names (e.g. Tori types "saa" but the real template is
# "comprehensive"). Tomorrow this will be loaded from a user-editable
# settings file so every myOS user can add their own aliases in the
# Settings page without touching code. The Settings UI is a follow up
# needle; do not build it here, just leave the shape stable. See
# needle 295 for context.
_BUILTIN_TEMPLATE_ALIASES: dict[str, str] = {
    # "saa" is a muscle-memory shortcut Tori types for the comprehensive
    # build pattern. Resolve it to the real template file so scripts and
    # old memory notes keep working.
    "saa": "comprehensive",
}


def get_template_aliases() -> dict[str, str]:
    """Return the current template alias map.

    Returns a shallow copy so callers cannot mutate the builtin. A
    future Settings UI will merge user-provided aliases on top of the
    builtins before returning.
    """
    return dict(_BUILTIN_TEMPLATE_ALIASES)


def _strip_quotes(value: str) -> str:
    """Strip a single layer of matching quotes from a value."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


_DURATION_RE = re.compile(r"^(\d+)(s|m|h|d)$")


def _parse_int(value: str, directive: str, line_number: int, path: Optional[Path]) -> int:
    try:
        return int(value)
    except ValueError:
        raise AgentfileParseError(
            f"{directive} expected a whole number, got '{value}'",
            line_number,
            path,
        ) from None


def _parse_float(value: str, directive: str, line_number: int, path: Optional[Path]) -> float:
    try:
        return float(value)
    except ValueError:
        raise AgentfileParseError(
            f"{directive} expected a number, got '{value}'",
            line_number,
            path,
        ) from None


def _parse_limit(
    value: str,
    config: AgentfileConfig,
    line_number: int,
    path: Optional[Path],
) -> None:
    """Parse one LIMIT line into ``config``.

    ``LIMIT <key> <value>`` where key is one of the documented subkeys.
    Unknown keys are ignored rather than raising, so myOS-specific
    extensions (like ``LIMIT test_coverage``) do not break the parser.
    """
    parts = value.split(None, 1)
    if len(parts) != 2:
        raise AgentfileParseError(
            "LIMIT expects a key and a value (e.g. 'LIMIT tokens 200000')",
            line_number,
            path,
        )
    key, val = parts[0].strip(), parts[1].strip()

    if key == "budget_usd":
        config.limits.budget_usd = _parse_float(val, "LIMIT budget_usd", line_number, path)
    elif key == "tokens":
        parsed = _parse_int(val, "LIMIT tokens", line_number, path)
        config.limits.tokens = parsed
        config.token_limit = parsed  # keep legacy field in sync
    elif key == "turns":
        config.limits.turns = _parse_int(val, "LIMIT turns", line_number, path)
    elif key == "wall_clock":
        # Accept both raw seconds ("300") and duration strings ("30m", "2h").
        # Store as the original string so the UI can decide how to format it.
        if not (_DURATION_RE.match(val) or val.isdigit()):
            raise AgentfileParseError(
                f"LIMIT wall_clock expected a number of seconds or a duration like '30m' or '2h', got '{val}'",
                line_number,
                path,
            )
        config.limits.wall_clock = val
    elif key == "context_pct":
        config.limits.context_pct = _parse_int(val, "LIMIT context_pct", line_number, path)
    elif key == "permissions":
        config.limits.permissions = val
    elif key == "revival_policy":
        config.limits.revival_policy = val
    elif key == "destructive_ops":
        # Documented values: confirm, deny, allow. Spec also hints at a
        # numeric cap. Accept both forms so we do not reject real files.
        if val.isdigit():
            config.limits.destructive_ops = int(val)
        else:
            # Stash a sentinel so the UI still sees a value without us
            # silently dropping the line.
            config.limits.destructive_ops = None
    elif key == "test_coverage":
        parsed = _parse_int(val, "LIMIT test_coverage", line_number, path)
        config.limits.test_coverage = parsed
        config.test_coverage_min = parsed  # legacy mirror
    # Unknown LIMIT keys: ignored silently. Forward-compat with new ostk keys.


def _parse_pin(
    value: str,
    config: AgentfileConfig,
    line_number: int,
    path: Optional[Path],
) -> None:
    """Parse one PIN line into ``config.pin_policy``.

    Supports both forms:
      ``PIN default``
      ``PIN read: src/ tests/``
    """
    # Subkey form: "read: ...", "write: ...", "execute: ...", "deny: ...", "deny-verb: ..."
    match = re.match(r"^(read|write|execute|deny|deny-verb)\s*:\s*(.*)$", value, re.IGNORECASE)
    if match:
        subkey = match.group(1).lower()
        rest = match.group(2).strip()
        items = [item for item in rest.split() if item]
        target = {
            "read": config.pin_policy.read,
            "write": config.pin_policy.write,
            "execute": config.pin_policy.execute,
            "deny": config.pin_policy.deny,
            "deny-verb": config.pin_policy.deny_verb,
        }[subkey]
        target.extend(items)
        return

    # Named form: "PIN default". Also keep the legacy flat field.
    config.pin_policy.name = value
    config.pin = value


def parse_agentfile(path: Path) -> AgentfileConfig:
    """Parse an Agentfile and return its configuration.

    A missing file returns a default config with ``name`` set from the
    stem, matching the historical behavior that callers rely on.
    Malformed input raises ``AgentfileParseError``.
    """
    config = AgentfileConfig()
    config.name = path.stem

    if not path.exists():
        return config

    for raw_line_number, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split(None, 1)
        directive = parts[0].upper()
        value = parts[1].strip() if len(parts) > 1 else ""

        if directive == "FROM":
            if not value:
                raise AgentfileParseError(
                    "FROM expects a model name (e.g. 'FROM claude-sonnet-4-6')",
                    raw_line_number,
                    path,
                )
            config.model = value
        elif directive == "MODEL":
            # ostk alias, treated the same as FROM.
            config.model = value
        elif directive == "NAME":
            config.name = _strip_quotes(value)
        elif directive == "DESC":
            config.description = _strip_quotes(value)
        elif directive == "PROMPT":
            config.prompt = _strip_quotes(value)
        elif directive == "TOOL":
            if not value:
                raise AgentfileParseError(
                    "TOOL expects a tool name (e.g. 'TOOL shell')",
                    raw_line_number,
                    path,
                )
            config.tools.append(value)
        elif directive == "SKILL":
            if not value:
                raise AgentfileParseError(
                    "SKILL expects a skill name",
                    raw_line_number,
                    path,
                )
            config.skills.append(value)
        elif directive == "WORK":
            config.work = value
        elif directive == "INTERRUPT":
            if not value:
                raise AgentfileParseError(
                    "INTERRUPT expects an event trigger",
                    raw_line_number,
                    path,
                )
            config.interrupts.append(value)
        elif directive == "LIMIT":
            _parse_limit(value, config, raw_line_number, path)
        elif directive == "BOOT":
            config.boot = value
        elif directive == "PIN":
            _parse_pin(value, config, raw_line_number, path)
        elif directive == "ISOLATION":
            normalized = value.strip().lower()
            if normalized not in VALID_ISOLATION:
                allowed = ", ".join(sorted(VALID_ISOLATION))
                raise AgentfileParseError(
                    f"ISOLATION must be one of {allowed}, got '{value}'",
                    raw_line_number,
                    path,
                )
            config.isolation = normalized
        elif directive == "ATTEST":
            normalized = value.strip().lower()
            if normalized not in VALID_ATTEST:
                allowed = ", ".join(sorted(VALID_ATTEST))
                raise AgentfileParseError(
                    f"ATTEST must be one of {allowed}, got '{value}'",
                    raw_line_number,
                    path,
                )
            config.attest = normalized
        elif directive == "BETA":
            if value:
                config.beta.append(value)
        elif directive == "ALIAS":
            # Alias pointer. ``ALIAS comprehensive`` turns this file into
            # a thin redirect to ``comprehensive.agent``. The resolver in
            # get_agent_config_by_template reads this field and swaps in
            # the target config so callers never see an empty shell.
            config.alias = _strip_quotes(value).strip()
        elif directive == "AC":
            config.acceptance_criteria.append(value)
        elif directive == "REVIEW":
            config.review_checklists.extend(
                c.strip() for c in value.split(",") if c.strip()
            )
        elif directive == "STANDARDS":
            config.standards_path = value
        # Unknown directives: ignored (forward-compat). We do not raise
        # so adding a new directive in a future ostk release does not
        # break existing myOS deployments.

    return config


def find_agentfile(agent_name: str) -> Optional[Path]:
    """Find the Agentfile for a given agent name.

    Checks agents/<name>.agent, then falls back to agents/saa.agent
    as the default.
    """
    # Direct match
    direct = AGENTS_DIR / f"{agent_name}.agent"
    if direct.exists():
        return direct

    # Try matching prefix (e.g. "fix-login-bug" matches "saa" pattern)
    for pattern in ["saa", "diagnose", "review", "test", "research"]:
        if pattern in agent_name:
            match = AGENTS_DIR / f"{pattern}.agent"
            if match.exists():
                return match

    # Default to saa
    default = AGENTS_DIR / "saa.agent"
    if default.exists():
        return default

    return None


def get_agent_config(agent_name: str) -> AgentfileConfig:
    """Load the AgentfileConfig for a given agent name.

    Follows file-level ALIAS directives so a stub like ``saa.agent``
    that only contains ``ALIAS comprehensive`` still resolves to the
    full comprehensive.agent config. This keeps legacy name-based
    spawns working after the saa rename.
    """
    path = find_agentfile(agent_name)
    if not path:
        return AgentfileConfig(name=agent_name)
    config = parse_agentfile(path)
    seen: set[str] = {path.stem}
    for _ in range(8):
        if not config.alias:
            return config
        target = _BUILTIN_TEMPLATE_ALIASES.get(config.alias, config.alias)
        if target in seen:
            return config
        seen.add(target)
        next_path = AGENTS_DIR / f"{target}.agent"
        if not next_path.exists():
            return config
        config = parse_agentfile(next_path)
    return config


def list_available_templates() -> list[str]:
    """Return the list of template names the spawn endpoint accepts.

    Includes every ``agents/*.agent`` stem plus every alias key from
    the built-in alias map, so the plain-language 400 error tells Tori
    every name that will actually resolve, not just the file stems.
    """
    names: set[str] = set()
    if AGENTS_DIR.exists():
        for f in AGENTS_DIR.glob("*.agent"):
            names.add(f.stem)
    names.update(_BUILTIN_TEMPLATE_ALIASES.keys())
    return sorted(names)


def get_agent_config_by_template(template_name: str) -> Optional[AgentfileConfig]:
    """Load the AgentfileConfig for an explicit template name.

    Unlike ``get_agent_config``, this does NOT fall back to ``saa`` if the
    exact file is missing. It returns ``None`` so the caller can raise a
    plain-language 400 telling the user the template name was wrong.

    Alias handling, two layers:

    1. Built-in alias map. ``saa`` resolves to ``comprehensive`` before
       any file lookup. This matches Tori's muscle memory and survives
       even if she deletes the saa.agent stub file.
    2. ALIAS directive. If the resolved file contains ``ALIAS <name>``,
       the resolver loads that target instead. Chains are followed up
       to a small depth so a typo cycle cannot lock the resolver.
    """
    aliases = _BUILTIN_TEMPLATE_ALIASES
    # Step 1: built-in alias map resolution.
    resolved = aliases.get(template_name, template_name)

    # Step 2: walk file-level ALIAS directives, guarding against cycles.
    seen: set[str] = set()
    for _ in range(8):
        if resolved in seen:
            return None
        seen.add(resolved)
        direct = AGENTS_DIR / f"{resolved}.agent"
        if not direct.exists():
            return None
        config = parse_agentfile(direct)
        if config.alias:
            # Follow the pointer, also through the built-in map.
            resolved = aliases.get(config.alias, config.alias)
            continue
        return config
    return None


def build_quality_gate_instructions(config: AgentfileConfig) -> str:
    """Build prompt instructions for quality gates.

    These are appended to the agent's prompt so it knows what checks
    it must pass before completing.
    """
    parts: list[str] = []

    if config.acceptance_criteria:
        ac_list = "\n".join(f"  - `{ac}`" for ac in config.acceptance_criteria)
        parts.append(
            "## Quality gates (mandatory before completing)\n\n"
            "You MUST pass these acceptance criteria before calling /complete:\n"
            f"{ac_list}\n"
            "Run each command. If any fails, fix the issue and re-run. "
            "Do NOT mark complete until all pass."
        )

    if config.review_checklists:
        checklists = ", ".join(config.review_checklists)
        parts.append(
            f"## Self-review checklist\n\n"
            f"Before completing, review your work against: {checklists}.\n"
            "For each item, confirm it passes or note what you fixed."
        )

    if config.test_coverage_min:
        parts.append(
            f"## Test coverage requirement\n\n"
            f"Test coverage must not drop below {config.test_coverage_min}%. "
            "Run the test suite with coverage and verify."
        )

    if config.standards_path:
        parts.append(
            f"## Coding standards\n\n"
            f"Follow the coding standards in `{config.standards_path}`. "
            "Read it before starting work."
        )

    return "\n\n".join(parts)


def build_template_instructions(config: AgentfileConfig) -> str:
    """Build the full block of instructions a template wants injected.

    Returns a plain-language header, the template PROMPT, the TOOL list,
    the LIMIT lines, and the quality gates all in one block. Used when a
    caller asks to spawn with ``template="saa"`` so the spawned agent
    sees the plan, build, test, verify pattern and every gate before
    doing any work.

    The block is composable with ``build_quality_gate_instructions``.
    Callers who want only the gates keep calling the old function.
    Callers who want the full template envelope call this one.
    """
    parts: list[str] = []

    header = f"## Template: {config.name}"
    parts.append(header)

    if config.prompt:
        parts.append(
            "### How to work\n\n"
            f"{config.prompt}"
        )

    if config.tools:
        tool_list = "\n".join(f"  - {tool}" for tool in config.tools)
        parts.append(
            "### Tools you can use\n\n"
            f"{tool_list}"
        )

    limit_lines: list[str] = []
    if config.limits.tokens is not None:
        limit_lines.append(f"  - Token budget: {config.limits.tokens}")
    if config.limits.budget_usd is not None:
        limit_lines.append(f"  - Spend budget: ${config.limits.budget_usd:g}")
    if config.limits.wall_clock is not None:
        limit_lines.append(f"  - Time limit: {_format_wall_clock(config.limits.wall_clock)}")
    if config.limits.test_coverage is not None:
        limit_lines.append(f"  - Minimum test coverage: {config.limits.test_coverage}%")
    if config.limits.turns is not None:
        limit_lines.append(f"  - Max turns: {config.limits.turns}")
    if limit_lines:
        parts.append(
            "### Limits\n\n"
            + "\n".join(limit_lines)
        )

    gates = build_quality_gate_instructions(config)
    if gates:
        parts.append(gates)

    return "\n\n".join(parts)


def _format_wall_clock(value: Optional[str]) -> str:
    """Turn a wall_clock value into plain English for the UI.

    ``30m`` becomes ``30 minutes``, ``2h`` becomes ``2 hours``,
    ``300`` becomes ``300 seconds``. ``None`` becomes ``no time limit``.
    """
    if not value:
        return "no time limit"
    match = _DURATION_RE.match(value)
    if match:
        num = int(match.group(1))
        unit = match.group(2)
        names = {
            "s": ("second", "seconds"),
            "m": ("minute", "minutes"),
            "h": ("hour", "hours"),
            "d": ("day", "days"),
        }[unit]
        return f"{num} {names[0] if num == 1 else names[1]}"
    if value.isdigit():
        num = int(value)
        return f"{num} {'second' if num == 1 else 'seconds'}"
    return value


def _format_isolation(value: str) -> str:
    """Turn an ISOLATION value into plain English for the UI."""
    mapping = {
        "none": "none",
        "nono": "light sandbox",
        "docker": "docker container",
        "firecracker": "firecracker VM",
    }
    return mapping.get(value, value or "none")


def build_capabilities_summary(config: AgentfileConfig) -> dict:
    """Build a plain-language capabilities summary for the Agents UI.

    Returns a stable shape with friendly strings. Empty fields get a
    friendly default like "no budget limit" so the card never shows
    blanks. Tori is a PM so the words must read naturally.
    """
    writes_to = ", ".join(config.pin_policy.write) if config.pin_policy.write else "nothing"
    cannot_touch = ", ".join(config.pin_policy.deny) if config.pin_policy.deny else "no restrictions set"
    budget = f"${config.limits.budget_usd:g}" if config.limits.budget_usd is not None else "no budget limit"
    time_limit = _format_wall_clock(config.limits.wall_clock)
    sandbox = _format_isolation(config.isolation)

    return {
        "writes_to": writes_to,
        "cannot_touch": cannot_touch,
        "budget": budget,
        "time_limit": time_limit,
        "sandbox": sandbox,
    }
