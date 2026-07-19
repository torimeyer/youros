"""Chat skills work on both AI runtimes (→2947, spec S007).

Spec box: "Invoke a skill in chat (`/build`, `/diagnose`) works in both
runtimes." The chat panel posts a skill id to POST /api/skills/run, the
router asks ``default_provider()`` for the active runtime, and that
provider runs the skill its own way: Claude runs a native slash-command
when one exists, otherwise the skill's agentfile recipe; Gemini always
runs the recipe. Switching the saved provider setting changes which
runtime runs the skill with no code change.

Tests land in the same targeted batch as test_skills_runtime_agnostic.py.
All CLI subprocesses are mocked; no real claude or gemini process starts.
"""
