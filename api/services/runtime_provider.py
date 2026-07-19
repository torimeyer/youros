"""Runtime-provider abstraction for spawning subagents (Wave 8).

Needles: →1891 (this file + the Protocol), →1892 (the ``Feature`` enum),
→1896 (each provider reports the correct feature set; switching providers
does not break spawn).

Why this exists (plain language)
--------------------------------
myOS spawns "subagents": child processes that go off and do a piece of
work — research, a code edit, a build — and report back. Today the code
that starts a subagent (in ``api/routers/agents.py``) is wired straight
to ONE runtime, the local ``claude`` Code program. There is no seam where
we could swap in a different runtime (a future Gemini CLI, say), and no
single place that answers "which features does this runtime actually
support?" — some runtimes have a plan mode and a live monitor, others do
not.

This module introduces that seam without changing any current behaviour:

  * ``Feature`` — the named capabilities a runtime may or may not have.
    The exact set is fixed by needle →1892.
  * ``RuntimeProvider`` — a ``Protocol`` (a structural interface) with two
    members every runtime must offer: ``spawn_subagent(...)`` to start a
    child agent, and ``features()`` to report its capability set so callers
    can adapt instead of crashing on a missing feature.
  * ``DefaultRuntimeProvider`` — the concrete provider that models the
    CURRENT claude-code spawn behaviour and reports the full feature set.
    It does NOT reach into ``agents.py``; instead the real spawn callable
    is injected (``spawn_fn``). The live spawn path in ``agents.py`` now
    calls ``provider.spawn_subagent`` with its in-process spawn internals
    injected (needle →1895, wired by →2945). An unconfigured default
    provider still raises ``NotImplementedError`` rather than silently
    doing nothing.
  * ``ReducedRuntimeProvider`` — a small reference provider that omits
    ``plan_mode`` and ``monitor``. It exists to prove the seam: switching
    providers changes the capability map (needle →1896 / →1917) while the
    spawn interface stays identical.

Nothing here imports ``agents.py`` or runs a subprocess. It is a pure,
self-contained interface layer that the spawn path can adopt later.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Protocol, Set, runtime_checkable, Awaitable, Any
from services.youros_paths import youros_home

# ---------------------------------------------------------------------------
# Feature enum (→1892)
# ---------------------------------------------------------------------------
class Feature(str, Enum):
    """A capability a runtime may or may not support.

    The membership of this enum is fixed by needle →1892: ``subagents``,
    ``hooks``, ``streaming``, ``isolation``, ``worktrees``, ``plan_mode``,
    ``monitor``. Subclassing ``str`` keeps the values JSON-friendly so a
    capability map can be serialised to the frontend or a log line without
    extra conversion.
    """

    SUBAGENTS = "subagents"   # can start child agents at all
    HOOKS = "hooks"           # PreToolUse / PostToolUse style hooks
    STREAMING = "streaming"   # streams token/tool events back live
    ISOLATION = "isolation"   # can run a child in an isolated workspace
    WORKTREES = "worktrees"   # git-worktree isolation specifically
    PLAN_MODE = "plan_mode"   # a plan-then-execute mode
    MONITOR = "monitor"       # a live monitor / heartbeat channel


# Convenience: the complete capability set, used by the default provider.
ALL_FEATURES: frozenset[Feature] = frozenset(Feature)


class SpawnNotSupportedError(NotImplementedError):
    """A runtime was asked to start an agent it cannot run.

    Raised by providers whose runtime has no agent-spawning support yet.
    The spawn endpoint shows the message to the user as-is, so keep it
    plain language: say what did not happen and what to do next. A
    provider must raise this rather than silently borrowing another
    runtime's spawn implementation (→2945).
    """


# ---------------------------------------------------------------------------
# Spawn request / result value objects
# ---------------------------------------------------------------------------
@dataclass
class SpawnRequest:
    """The fields a runtime needs to spawn a subagent.

    These mirror the load-bearing fields of ``api.models.schemas.AgentSpawn``
    (the body the ``POST /agents/spawn`` endpoint already accepts) so the
    default provider can wrap the current behaviour 1:1 when the real spawn
    path is wired in later. We intentionally model only the spawn-relevant
    fields here, not the whole HTTP body, to keep the interface narrow.
    """

    name: str
    prompt: str = ""
    model: str = "sonnet"
    budget: float = 2.0
    template: Optional[str] = None
    task: Optional[str] = None
    isolation: Optional[str] = None  # "worktree" | "none" | None (auto)
    token_limit: Optional[int] = None
    # Free-form extras for fields a specific runtime cares about without
    # widening the core contract (e.g. needle id, spec path, workflow run).
    extra: dict = field(default_factory=dict)


@dataclass
class SpawnResult:
    """What a runtime reports back after starting a subagent."""

    name: str
    pid: Optional[int] = None
    status: str = "running"
    worktree_path: Optional[str] = None
    transcript_path: Optional[str] = None
    detail: dict = field(default_factory=dict)


# Signature of the injected real-spawn callable. Takes a fully-formed
# SpawnRequest and returns a SpawnResult. The default provider owns the
# request-construction ergonomics; the callable just performs the spawn.
SpawnFn = Callable[[SpawnRequest], Awaitable[SpawnResult]]


# ---------------------------------------------------------------------------
# The Protocol (→1891)
# ---------------------------------------------------------------------------
@runtime_checkable
class RuntimeProvider(Protocol):
    """Structural interface every runtime must satisfy.

    Two members:

      * ``spawn_subagent(request=None, /, **fields)`` — start a child agent.
        Callers may pass a pre-built :class:`SpawnRequest` positionally, or
        pass the fields as keyword arguments and let the provider build one.
      * ``features()`` — return the set of :class:`Feature` values this
        runtime supports. Callers query this (often via ``supports``) to
        decide whether to offer plan mode, attach a monitor, and so on.

    ``@runtime_checkable`` lets ``isinstance(obj, RuntimeProvider)`` confirm
    a candidate has both members. (It checks presence, not signatures, which
    is the standard Protocol limitation.)
    """

    async def spawn_subagent(
        self, request: Optional[SpawnRequest] = None, /, **fields
    ) -> SpawnResult:
        ...

    async def invoke_skill(self, skill_id: str, **args: Any) -> None:
        """Invoke a predefined skill (e.g. handoff, review) via the runtime."""
        ...

    def features(self) -> Set[Feature]:
        ...


# ---------------------------------------------------------------------------
# Shared base with the ergonomic helpers
# ---------------------------------------------------------------------------
class _BaseRuntimeProvider:
    """Implementation detail shared by the concrete providers.

    Holds the request-normalisation logic (positional request OR kwargs)
    and the ``supports`` helper, so each concrete provider only has to
    declare its feature set and its spawn callable.
    """

    #: Concrete providers override this with their capability set.
    _features: frozenset[Feature] = frozenset()

    #: Human name of the runtime, used in user-facing error messages.
    display_name: str = "this runtime"

    def __init__(self, spawn_fn: Optional[SpawnFn] = None) -> None:
        self._spawn_fn = spawn_fn

    # -- features ---------------------------------------------------------
    def features(self) -> Set[Feature]:
        # Return a fresh set so a caller mutating the result cannot corrupt
        # the provider's own capability map.
        return set(self._features)

    def supports(self, feature: Feature) -> bool:
        return feature in self._features

    # -- spawn ------------------------------------------------------------
    @staticmethod
    def _coerce_request(
        request: Optional[SpawnRequest], fields: dict
    ) -> SpawnRequest:
        if request is not None and fields:
            # Allow overriding a prebuilt request with explicit kwargs.
            return replace(request, **fields)
        if request is not None:
            return request
        return SpawnRequest(**fields)

    async def spawn_subagent(
        self, request: Optional[SpawnRequest] = None, /, **fields
    ) -> SpawnResult:
        req = self._coerce_request(request, fields)
        if Feature.SUBAGENTS not in self._features:
            # A runtime without agent support must refuse loudly, even when
            # a spawn callable was injected: the injected callable is another
            # runtime's implementation, and running it would silently hand
            # the user a vendor they did not pick (→2945).
            raise SpawnNotSupportedError(
                f"Agent spawning is not yet supported on {self.display_name}. "
                "Chat still works there, but starting agents needs a runtime "
                "that supports them. Pick one in Settings and try again."
            )
        if self._spawn_fn is None:
            raise NotImplementedError(
                f"{type(self).__name__} has no spawn_fn wired. Inject the "
                "real spawn callable (see needle →1895 — wiring the live "
                "agents.py spawn path is a separate, deferred pass)."
            )
        return await self._spawn_fn(req)

    async def invoke_skill(self, skill_id: str, **args: Any) -> None:
        """Invoke a predefined skill via the runtime.

        Concrete providers override this to run the skill through their own
        runtime: the Claude provider runs the native slash-command
        (``claude --print /skill``); the Gemini provider runs the skill's
        agentfile recipe (``agents/<skill>.agent``) through the gemini CLI.
        The base provider has no runtime of its own, so it fails loudly rather
        than silently doing nothing.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement invoke_skill for "
            f"skill '{skill_id.lstrip('/')}'"
        )


# ---------------------------------------------------------------------------
# Concrete providers
# ---------------------------------------------------------------------------
class DefaultRuntimeProvider(_BaseRuntimeProvider):
    """Wraps the CURRENT claude-code spawn behaviour. Full feature set.

    The real spawn callable is injected via ``spawn_fn`` so this module
    never imports ``agents.py`` (which would create an import cycle and
    drag the whole FastAPI app into a unit test). The live spawn path
    (→1895, wired by →2945) constructs this provider with the real
    callable; without one, ``spawn_subagent`` raises ``NotImplementedError``.
    """

    _features = ALL_FEATURES


class ReducedRuntimeProvider(_BaseRuntimeProvider):
    """Reference provider with a deliberately smaller capability set.

    Stands in for a runtime (e.g. a future ``gemini`` CLI, →1917) that
    lacks plan mode and a live monitor. Used to prove that swapping the
    provider changes ``features()`` without changing the spawn interface.
    """

    _features = frozenset(ALL_FEATURES - {Feature.PLAN_MODE, Feature.MONITOR})


# ---------------------------------------------------------------------------
# Skill recipe resolution (shared by providers that run skills as agentfiles)
# ---------------------------------------------------------------------------
#: Chat skill id -> recipe basename, for the few skills whose chat command
#: does not match their agentfile name. The chat command is ``/build`` but the
#: recipe shipped on disk is ``agents/builder.agent``; the resolver owns that
#: mapping in ONE place so every runtime agrees on what ``/build`` runs (→2947).
SKILL_ALIASES: dict[str, str] = {
    "build": "builder",
}


def resolve_skill_agentfile(skill_id: str) -> Optional[Path]:
    """Resolve a skill id to its agentfile recipe path, or None if not found.

    A skill's model-neutral recipe lives in an agentfile. Providers without a
    native skill mechanism (e.g. the Gemini CLI) run that recipe through their
    own runtime. We look first in the repo's ``agents/`` directory (built-in
    skills shipped with yourOS), then in the user's ``~/.youros/skills/``
    directory (user-defined skills). The id may carry a leading slash
    (``/review``) which is stripped, and may be an alias (``build`` ->
    ``builder.agent``, see :data:`SKILL_ALIASES`).
    """
    sid = skill_id.lstrip("/").strip()
    if not sid:
        return None
    sid = SKILL_ALIASES.get(sid, sid)

    candidates: list[Path] = []
    try:
        from config import PROJECT_ROOT
        candidates.append(Path(PROJECT_ROOT) / "agents" / f"{sid}.agent")
    except Exception:
        pass
    candidates.append(youros_home() / "skills" / f"{sid}.agent")

    for path in candidates:
        if path.exists():
            return path
    return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def default_provider(spawn_fn: Optional[SpawnFn] = None) -> RuntimeProvider:
    """Return the provider yourOS uses by default.

    The user's saved ``default_provider`` setting is the source of truth:
    it holds "claude" or "gemini", the value the Settings page provider
    toggle writes (→2945). The ``YOUROS_RUNTIME`` environment variable is
    a TEST-ONLY override: when a suite sets it explicitly it wins, so
    tests can pin a runtime without touching the user's saved settings
    (see the S007 verification step "Repeat with YOUROS_RUNTIME=gemini").
    Unrecognised values and settings read failures fall back to claude,
    the runtime that is always present.
    """
    import os

    runtime = os.environ.get("YOUROS_RUNTIME", "").strip().lower()
    if not runtime:
        try:
            from services.settings_store import settings_store
            runtime = str(
                settings_store.get("default_provider", "claude") or "claude"
            ).strip().lower()
        except Exception:
            runtime = "claude"

    if runtime == "gemini":
        from services.gemini_cli_provider import GeminiCliRuntimeProvider
        return GeminiCliRuntimeProvider(spawn_fn=spawn_fn)

    from services.claude_code_provider import ClaudeCodeRuntimeProvider
    return ClaudeCodeRuntimeProvider(spawn_fn=spawn_fn)


__all__ = [
    "Feature",
    "ALL_FEATURES",
    "SpawnNotSupportedError",
    "SpawnRequest",
    "SpawnResult",
    "SpawnFn",
    "RuntimeProvider",
    "DefaultRuntimeProvider",
    "ReducedRuntimeProvider",
    "SKILL_ALIASES",
    "resolve_skill_agentfile",
    "default_provider",
]
