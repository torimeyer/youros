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
    is injected (``spawn_fn``). Wiring the live spawn path to call
    ``provider.spawn_subagent`` is deliberately a LATER pass (needle
    →1895) because that path is contended and needs a design decision
    first. Until it is wired, an unconfigured default provider raises
    ``NotImplementedError`` rather than silently doing nothing.
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
from typing import Callable, Optional, Protocol, Set, runtime_checkable, Awaitable

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
        if self._spawn_fn is None:
            raise NotImplementedError(
                f"{type(self).__name__} has no spawn_fn wired. Inject the "
                "real spawn callable (see needle →1895 — wiring the live "
                "agents.py spawn path is a separate, deferred pass)."
            )
        return await self._spawn_fn(req)


# ---------------------------------------------------------------------------
# Concrete providers
# ---------------------------------------------------------------------------
class DefaultRuntimeProvider(_BaseRuntimeProvider):
    """Wraps the CURRENT claude-code spawn behaviour. Full feature set.

    The real spawn callable is injected via ``spawn_fn`` so this module
    never imports ``agents.py`` (which would create an import cycle and
    drag the whole FastAPI app into a unit test). When the live spawn path
    is wired in (→1895) it will construct this provider with the real
    callable; until then ``spawn_subagent`` raises ``NotImplementedError``.
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
# Factory
# ---------------------------------------------------------------------------
def default_provider(spawn_fn: Optional[SpawnFn] = None) -> RuntimeProvider:
    """Return the provider yourOS uses by default.

    Reads the YOUROS_RUNTIME environment variable (default: "claude").
    """
    import os
    runtime = os.environ.get("YOUROS_RUNTIME", "claude").lower()

    if runtime == "gemini":
        from services.gemini_cli_provider import GeminiCliRuntimeProvider
        return GeminiCliRuntimeProvider(spawn_fn=spawn_fn)

    from services.claude_code_provider import ClaudeCodeRuntimeProvider
    return ClaudeCodeRuntimeProvider(spawn_fn=spawn_fn)


__all__ = [
    "Feature",
    "ALL_FEATURES",
    "SpawnRequest",
    "SpawnResult",
    "SpawnFn",
    "RuntimeProvider",
    "DefaultRuntimeProvider",
    "ReducedRuntimeProvider",
    "default_provider",
]
