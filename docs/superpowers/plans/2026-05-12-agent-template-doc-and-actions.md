# Agent-Template Doc + Action Buttons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every agent spawned from a template automatically generates a user-friendly results doc on completion and shows per-template follow-on action buttons in the Recent Agents row.

**Architecture:** At spawn time, copy `follow_on_actions` from the template definition into agent metadata so the frontend never needs a second lookup. On completion, any template-spawned agent writes a structured `.md` doc to `~/.myos/files/` and records `doc_path` in its metadata. A new `GET /api/agents/{name}/doc` endpoint serves that file. The frontend adds a "Doc" button and renders follow-on actions from `agent.follow_on_actions` instead of template-specific hardcoded checks.

**Tech Stack:** Python/FastAPI (backend), TypeScript/React/Tailwind (frontend), Vitest (frontend tests), pytest (backend tests), `scripts/run-vitest.sh` for frontend tests, `tsc -b` for type checks.

---

## File Map

| File | Change |
|------|--------|
| `api/routers/agents.py` | Spawn handler: stamp `follow_on_actions` + inject doc-prompt suffix. Complete handler: universal doc generation + stamp `doc_path`. New endpoint: `GET /api/agents/{name}/doc`. |
| `api/services/agent_templates_store.py` | Add `follow_on_actions` to every builtin template that lacks them. |
| `app/src/lib/agentUtils.ts` | Extend `AgentInfo` with `doc_path?: string`, `follow_on_actions?: FollowOnAction[]`. |
| `app/src/components/AgentDocModal.tsx` | **New file.** Modal that fetches and renders a generated doc. |
| `app/src/pages/Agents.tsx` | Recent tab: add Doc button; replace hardcoded Roadmap/CompScan checks with generic `agent.follow_on_actions`. |
| `tests/test_agents_doc.py` | **New file.** Backend tests for doc generation and the `/doc` endpoint. |
| `app/src/components/__tests__/AgentDocModal.test.tsx` | **New file.** Frontend unit tests for the modal. |

---

## Task 1: Read the spawn handler and complete handler

> You must read these before touching them. The code is large and the injection points are not obvious.

**Files:**
- Read: `api/routers/agents.py` lines 1–100 (imports, globals)
- Read: `api/routers/agents.py` — search for `template_produces_doc` to find the spawn stamping block (~10 lines around each hit)
- Read: `api/routers/agents.py` — search for `produces_doc` to find the complete handler doc-write block

- [ ] **Step 1: Find the spawn stamping block**

```bash
grep -n "template_produces_doc\|follow_on_actions\|produces_doc" api/routers/agents.py | head -40
```

Note the line numbers. You'll need them for the str_replace edits in Task 2.

- [ ] **Step 2: Find the complete handler doc-write block**

```bash
grep -n "myos/files\|produces_doc\|doc_path\|\.md" api/routers/agents.py | head -30
```

Note which lines write the `.md` file and what content they write.

- [ ] **Step 3: Find the template lookup in spawn**

```bash
grep -n "get_template\|template_obj\|template_store\|agent_templates" api/routers/agents.py | head -20
```

Note the function name used to look up a template by its name/id.

---

## Task 2: Stamp `follow_on_actions` at spawn + inject doc-prompt suffix

**Files:**
- Modify: `api/routers/agents.py` — spawn handler, the block where `template_produces_doc` is stamped

**Background:** When a template agent spawns, the backend already stamps `template` and `template_produces_doc` into `spawn_meta`. We extend this to also copy `follow_on_actions` from the template, and to append a standardized doc-instructions block to the user's prompt so every agent knows to write a structured summary.

- [ ] **Step 1: Write the failing test**

Create `tests/test_agents_doc.py`:

```python
"""Tests for template doc generation and follow_on_actions stamping."""
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def mock_template():
    return {
        "id": "builtin-test",
        "name": "Test Template",
        "produces_doc": True,
        "follow_on_actions": [
            {"label": "Build it", "prompt": "Now build: {transcript}"},
            {"label": "Share it", "prompt": "Write a summary for sharing."},
        ],
        "prompt_template": "Do the thing.",
        "model": "sonnet",
        "budget": 1.0,
    }


def test_spawn_stamps_follow_on_actions(mock_template):
    """follow_on_actions from template must be copied into spawn_meta."""
    from api.services.agent_templates_store import get_template_by_name
    from api.routers.agents import _build_spawn_meta_from_template

    with patch("api.routers.agents.get_template_by_name", return_value=mock_template):
        meta = _build_spawn_meta_from_template("Test Template", {})

    assert meta["follow_on_actions"] == mock_template["follow_on_actions"]


def test_spawn_injects_doc_prompt_suffix(mock_template):
    """All template spawns must have a doc-instructions suffix in their prompt."""
    from api.routers.agents import _build_spawn_meta_from_template

    with patch("api.routers.agents.get_template_by_name", return_value=mock_template):
        meta = _build_spawn_meta_from_template("Test Template", {})

    assert "## Summary" in meta.get("doc_prompt_suffix", "")
    assert "## Key Findings" in meta.get("doc_prompt_suffix", "")
    assert "## Recommended Next Steps" in meta.get("doc_prompt_suffix", "")
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /Users/torimeyer/claude/torios && python -m pytest tests/test_agents_doc.py::test_spawn_stamps_follow_on_actions -v 2>&1 | head -30
```

Expected: `ImportError` or `AttributeError` — `_build_spawn_meta_from_template` doesn't exist yet.

- [ ] **Step 3: Find the exact existing spawn-stamping block**

Using the line numbers from Task 1, Step 1, locate the block that looks like:

```python
spawn_meta["template_produces_doc"] = template_obj.get("produces_doc", False)
```

It may be inside an `if template_name:` or `if template_obj:` guard.

- [ ] **Step 4: Extend the spawn-stamping block**

Using `str_replace`, add `follow_on_actions` stamping and `doc_prompt_suffix` right after the existing `template_produces_doc` line. Replace:

```python
spawn_meta["template_produces_doc"] = template_obj.get("produces_doc", False)
```

With:

```python
spawn_meta["template_produces_doc"] = True  # all template runs produce a doc
spawn_meta["follow_on_actions"] = template_obj.get("follow_on_actions", [])
spawn_meta["doc_prompt_suffix"] = (
    "\n\n---\nWhen you finish your work, write a results summary using EXACTLY this format:\n\n"
    "## Summary\n[2-3 sentence overview of what you accomplished]\n\n"
    "## Key Findings\n- [Finding 1]\n- [Finding 2]\n\n"
    "## Recommended Next Steps\n- [ ] [Action 1]\n- [ ] [Action 2]\n"
)
```

- [ ] **Step 5: Also append the suffix to the agent's prompt at spawn**

In the same spawn handler, find where `prompt` is assembled for the subprocess call (likely something like `final_prompt = prompt` or passed to `claude` CLI). Append the suffix:

```python
# After assembling the prompt, before launching:
if spawn_meta.get("doc_prompt_suffix"):
    final_prompt = final_prompt + spawn_meta["doc_prompt_suffix"]
```

This ensures the agent actually writes the structured summary.

- [ ] **Step 6: Expose `_build_spawn_meta_from_template` as a testable helper**

If the stamping logic is inlined in the route handler, extract the template-stamping lines into a small helper function `_build_spawn_meta_from_template(template_name: str, overrides: dict) -> dict` so the test can call it directly. If it's already a separate function, skip this step.

- [ ] **Step 7: Run tests**

```bash
cd /Users/torimeyer/claude/torios && python -m pytest tests/test_agents_doc.py -v 2>&1 | head -40
```

Expected: both tests PASS.

- [ ] **Step 8: Commit**

```bash
git add api/routers/agents.py tests/test_agents_doc.py
git commit -m "feat(→1241): stamp follow_on_actions + doc suffix at template spawn"
```

---

## Task 3: Universal doc generation on `/complete`

**Files:**
- Modify: `api/routers/agents.py` — the `/complete` endpoint, doc-write block

**Background:** Currently doc writing is gated on `template_produces_doc == True` (which was False for most templates). Since Task 2 sets it to `True` for all template runs, this may already work. But the doc content extraction needs to produce the right structured output — extracting the `## Summary` through `## Recommended Next Steps` section the agent writes at the end of its transcript.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_agents_doc.py`:

```python
def test_complete_writes_doc_for_template_agent(tmp_path, monkeypatch):
    """A template agent that completes must have a doc file written."""
    import json, os
    from pathlib import Path

    # Minimal agent state mimicking a completed template run
    fake_transcript = (
        "I analyzed the data.\n\n"
        "## Summary\nI found three key patterns.\n\n"
        "## Key Findings\n- Pattern A\n- Pattern B\n\n"
        "## Recommended Next Steps\n- [ ] Do X\n- [ ] Do Y\n"
    )
    fake_transcript_path = tmp_path / "transcript.txt"
    fake_transcript_path.write_text(fake_transcript)

    files_dir = tmp_path / "files"
    files_dir.mkdir()

    monkeypatch.setenv("MYOS_FILES_DIR", str(files_dir))

    from api.routers.agents import _write_agent_doc
    doc_path = _write_agent_doc(
        agent_name="test-agent-123",
        template_name="Test Template",
        transcript_path=str(fake_transcript_path),
        files_dir=str(files_dir),
    )

    assert doc_path is not None
    assert Path(doc_path).exists()
    content = Path(doc_path).read_text()
    assert "## Summary" in content
    assert "## Key Findings" in content
    assert "## Recommended Next Steps" in content


def test_complete_stores_doc_path_in_metadata():
    """agent_metadata must have doc_path after _write_agent_doc runs."""
    # This is an integration concern; test at the HTTP layer via TestClient
    # if the codebase has one, otherwise stub here.
    pass  # Covered by the file-existence test above + manual smoke test.
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /Users/torimeyer/claude/torios && python -m pytest tests/test_agents_doc.py::test_complete_writes_doc_for_template_agent -v 2>&1 | head -30
```

Expected: `ImportError` — `_write_agent_doc` doesn't exist yet.

- [ ] **Step 3: Read the existing doc-write block carefully**

Using the line numbers from Task 1, Step 2, read the current `produces_doc` block. Understand:
- What content it writes (the full transcript? last message? something else?)
- Where it reads the transcript from
- What the `.md` file's frontmatter looks like
- Where `~/.myos/files/` path comes from

- [ ] **Step 4: Extract `_write_agent_doc` helper**

In `api/routers/agents.py`, extract the doc-writing logic into a standalone function:

```python
def _write_agent_doc(
    agent_name: str,
    template_name: str,
    transcript_path: str,
    files_dir: str | None = None,
) -> str | None:
    """Extract the Summary section from transcript and write a .md doc.
    
    Returns the absolute path of the written doc, or None on failure.
    """
    import re
    from pathlib import Path
    from datetime import datetime

    if not transcript_path or not Path(transcript_path).exists():
        return None

    transcript = Path(transcript_path).read_text(errors="replace")

    # Extract from ## Summary onward (the structured block the agent writes)
    match = re.search(r"(## Summary\b.*)", transcript, re.DOTALL)
    doc_body = match.group(1).strip() if match else transcript[-2000:].strip()

    slug = re.sub(r"[^a-z0-9]+", "-", template_name.lower()).strip("-")
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    
    out_dir = Path(files_dir or (Path.home() / ".myos" / "files"))
    out_dir.mkdir(parents=True, exist_ok=True)
    
    out_path = out_dir / f"{slug}-{ts}.md"
    frontmatter = (
        f"---\nsource: agent\ntemplate: {template_name}\n"
        f"agent: {agent_name}\ngenerated_at: {datetime.utcnow().isoformat()}Z\nkind: results\n---\n\n"
    )
    out_path.write_text(frontmatter + doc_body)
    return str(out_path)
```

- [ ] **Step 5: Call `_write_agent_doc` from the `/complete` handler**

In the `/complete` handler, after the existing doc-write block (or replacing it if it's now redundant), add:

```python
# Write structured doc for any template-spawned agent
if spawn_meta.get("template"):
    doc_path = _write_agent_doc(
        agent_name=name,
        template_name=spawn_meta["template"],
        transcript_path=spawn_meta.get("transcript_path", ""),
    )
    if doc_path:
        spawn_meta["doc_path"] = doc_path
        agent_metadata[name]["doc_path"] = doc_path
```

Make sure this replaces or wraps the old `produces_doc` block, not duplicates it.

- [ ] **Step 6: Run tests**

```bash
cd /Users/torimeyer/claude/torios && python -m pytest tests/test_agents_doc.py -v 2>&1 | head -50
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add api/routers/agents.py tests/test_agents_doc.py
git commit -m "feat(→1241): universal doc generation on template agent complete"
```

---

## Task 4: New `GET /api/agents/{name}/doc` endpoint

**Files:**
- Modify: `api/routers/agents.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_agents_doc.py`:

```python
def test_doc_endpoint_returns_content(tmp_path):
    """GET /api/agents/{name}/doc returns doc content when doc_path exists."""
    # Requires FastAPI TestClient — check if the codebase has a conftest.py
    # with `client` fixture. If not, test via direct function call.
    from api.routers.agents import get_agent_doc
    from pathlib import Path

    doc_file = tmp_path / "test-doc.md"
    doc_file.write_text("## Summary\nAll good.\n\n## Key Findings\n- Found stuff.\n")

    result = get_agent_doc.__wrapped__(
        name="test-agent",
        agent_metadata={"test-agent": {"doc_path": str(doc_file)}},
    )
    assert result["empty"] is False
    assert "## Summary" in result["content"]


def test_doc_endpoint_returns_empty_when_no_doc():
    """GET /api/agents/{name}/doc returns empty=True when no doc was generated."""
    from api.routers.agents import get_agent_doc

    result = get_agent_doc.__wrapped__(
        name="no-template-agent",
        agent_metadata={"no-template-agent": {}},
    )
    assert result["empty"] is True
    assert result["content"] == ""
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /Users/torimeyer/claude/torios && python -m pytest tests/test_agents_doc.py::test_doc_endpoint_returns_content -v 2>&1 | head -20
```

Expected: `ImportError` or `AttributeError`.

- [ ] **Step 3: Add the endpoint**

In `api/routers/agents.py`, after the existing `/api/agents/{name}/transcript` endpoint, add:

```python
@router.get("/agents/{name}/doc")
async def get_agent_doc(name: str):
    """Return the generated results doc for a completed template agent."""
    from pathlib import Path

    meta = agent_metadata.get(name, {})
    doc_path = meta.get("doc_path")

    if not doc_path or not Path(doc_path).exists():
        return {"content": "", "path": None, "empty": True}

    content = Path(doc_path).read_text(errors="replace")
    return {"content": content, "path": doc_path, "empty": False}
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/torimeyer/claude/torios && python -m pytest tests/test_agents_doc.py -v 2>&1 | head -50
```

Expected: all tests PASS. (The `__wrapped__` tests may need adjustment based on how the route is structured — use `client.get(...)` via TestClient if that fixture exists.)

- [ ] **Step 5: Commit**

```bash
git add api/routers/agents.py tests/test_agents_doc.py
git commit -m "feat(→1241): add GET /api/agents/{name}/doc endpoint"
```

---

## Task 5: Extend `AgentInfo` with `doc_path` and `follow_on_actions`

**Files:**
- Modify: `app/src/lib/agentUtils.ts` lines 46–82

- [ ] **Step 1: Write the failing test**

Create `app/src/components/__tests__/AgentDocModal.test.tsx` (stub — filled in Task 6):

```tsx
// Placeholder — real tests added in Task 6
describe("AgentDocModal", () => {
  it("renders nothing yet", () => {
    expect(true).toBe(true);
  });
});
```

Run:
```bash
cd /Users/torimeyer/claude/torios && bash scripts/run-vitest.sh --reporter=verbose 2>&1 | tail -10
```

Expected: suite passes (placeholder test).

- [ ] **Step 2: Add `FollowOnAction` type and extend `AgentInfo`**

In `app/src/lib/agentUtils.ts`, after the existing imports, add the type:

```typescript
export interface FollowOnAction {
  label: string;
  prompt: string;
}
```

Then in the `AgentInfo` interface (around line 46), add after `template_produces_doc`:

```typescript
  doc_path?: string;
  follow_on_actions?: FollowOnAction[];
```

- [ ] **Step 3: Check TypeScript compiles**

```bash
cd /Users/torimeyer/claude/torios/app && tsc -b 2>&1 | head -20
```

Expected: no errors (new optional fields don't break existing usage).

- [ ] **Step 4: Commit**

```bash
git add app/src/lib/agentUtils.ts app/src/components/__tests__/AgentDocModal.test.tsx
git commit -m "feat(→1241): extend AgentInfo with doc_path and follow_on_actions"
```

---

## Task 6: Create `AgentDocModal` component

**Files:**
- Create: `app/src/components/AgentDocModal.tsx`
- Modify: `app/src/components/__tests__/AgentDocModal.test.tsx`

**Design:** A modal that fetches `GET /api/agents/{name}/doc`, renders the markdown (using the same pattern the transcript modal uses — check how the existing transcript modal renders markdown in `Agents.tsx`), and shows a close button. Has a loading state and a graceful empty state ("No doc available yet").

- [ ] **Step 1: Read the existing transcript modal**

```bash
grep -n "TranscriptModal\|transcript.*modal\|MarkdownRenderer\|ReactMarkdown\|markdown.*render" \
  /Users/torimeyer/claude/torios/app/src/pages/Agents.tsx | head -20
```

Note the markdown rendering approach. Use the same one.

- [ ] **Step 2: Write the failing tests**

Replace `app/src/components/__tests__/AgentDocModal.test.tsx` with:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, describe, it, expect, beforeEach } from "vitest";
import { AgentDocModal } from "../AgentDocModal";

const mockFetch = vi.fn();
global.fetch = mockFetch;

describe("AgentDocModal", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it("shows loading state while fetching", () => {
    mockFetch.mockReturnValue(new Promise(() => {})); // never resolves
    render(<AgentDocModal agentName="test-agent" onClose={() => {}} />);
    expect(screen.getByRole("status")).toBeInTheDocument(); // loading spinner/text
  });

  it("renders doc content when fetch succeeds", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        content: "## Summary\nAll done.\n\n## Key Findings\n- Found things.\n",
        empty: false,
      }),
    });
    render(<AgentDocModal agentName="test-agent" onClose={() => {}} />);
    await waitFor(() => {
      expect(screen.getByText(/All done/)).toBeInTheDocument();
    });
  });

  it("shows empty state when no doc available", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ content: "", empty: true }),
    });
    render(<AgentDocModal agentName="test-agent" onClose={() => {}} />);
    await waitFor(() => {
      expect(screen.getByText(/No doc available/i)).toBeInTheDocument();
    });
  });

  it("calls onClose when close button clicked", async () => {
    const onClose = vi.fn();
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ content: "", empty: true }),
    });
    render(<AgentDocModal agentName="test-agent" onClose={onClose} />);
    await waitFor(() => screen.getByRole("button", { name: /close/i }));
    await userEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
cd /Users/torimeyer/claude/torios && bash scripts/run-vitest.sh \
  app/src/components/__tests__/AgentDocModal.test.tsx --reporter=verbose 2>&1 | tail -20
```

Expected: `Cannot find module '../AgentDocModal'`.

- [ ] **Step 4: Create `AgentDocModal.tsx`**

Create `app/src/components/AgentDocModal.tsx`:

```tsx
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";  // use whatever the codebase uses

interface Props {
  agentName: string;
  onClose: () => void;
}

interface DocResponse {
  content: string;
  empty: boolean;
  path: string | null;
}

export function AgentDocModal({ agentName, onClose }: Props) {
  const [doc, setDoc] = useState<DocResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`/api/agents/${agentName}/doc`)
      .then((r) => r.json())
      .then((data) => setDoc(data))
      .catch(() => setDoc({ content: "", empty: true, path: null }))
      .finally(() => setLoading(false));
  }, [agentName]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onClose}
    >
      <div
        className="relative bg-white dark:bg-gray-900 rounded-xl shadow-2xl w-full max-w-2xl max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
            Results Doc
          </h2>
          <button
            aria-label="Close"
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-xl leading-none"
          >
            ×
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto px-6 py-4 flex-1">
          {loading ? (
            <div role="status" className="text-sm text-gray-500 dark:text-gray-400">
              Loading…
            </div>
          ) : doc?.empty ? (
            <p className="text-sm text-gray-500 dark:text-gray-400">
              No doc available yet. The agent may not have finished or did not write a summary.
            </p>
          ) : (
            <article className="prose dark:prose-invert prose-sm max-w-none">
              <ReactMarkdown>{doc?.content ?? ""}</ReactMarkdown>
            </article>
          )}
        </div>
      </div>
    </div>
  );
}
```

**Note:** Replace `ReactMarkdown` import with whatever markdown renderer the existing transcript modal uses. Check `Agents.tsx` for the import name.

- [ ] **Step 5: Run tests**

```bash
cd /Users/torimeyer/claude/torios && bash scripts/run-vitest.sh \
  app/src/components/__tests__/AgentDocModal.test.tsx --reporter=verbose 2>&1 | tail -30
```

Expected: all 4 tests PASS.

- [ ] **Step 6: TypeScript check**

```bash
cd /Users/torimeyer/claude/torios/app && tsc -b 2>&1 | head -20
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add app/src/components/AgentDocModal.tsx app/src/components/__tests__/AgentDocModal.test.tsx
git commit -m "feat(→1241): add AgentDocModal component with loading + empty states"
```

---

## Task 7: Add Doc button and generalize follow_on_actions in Recent Agents

**Files:**
- Modify: `app/src/pages/Agents.tsx` lines ~4390–4658

**This is the most surgical task.** Agents.tsx is 5264 lines. Only two areas change:
1. Add a "Doc" button alongside the existing Transcript/Recover buttons.
2. Replace the hardcoded `isRoadmapAgent(...)` follow_on_actions check with a generic check on `agent.follow_on_actions`.

- [ ] **Step 1: Find the exact button row and follow_on_actions rendering**

```bash
grep -n "isRoadmapAgent\|follow_on_actions\|RoadmapCards\|handleRecover\|Transcript.*button\|transcript.*modal" \
  /Users/torimeyer/claude/torios/app/src/pages/Agents.tsx | head -30
```

Note the line numbers for:
- The per-row button group (where Recover and Transcript sit)
- The RoadmapCards render block
- The follow_on_actions render block (if it exists)

- [ ] **Step 2: Add `AgentDocModal` import**

At the top of `Agents.tsx`, add (alongside other component imports):

```typescript
import { AgentDocModal } from "../components/AgentDocModal";
```

- [ ] **Step 3: Add `docAgent` state**

Near the other modal-open state variables (where `transcriptAgent` or similar is defined), add:

```typescript
const [docAgent, setDocAgent] = useState<string | null>(null);
```

- [ ] **Step 4: Add the Doc button to the per-row button group**

Find the existing button group in the Recent tab row. It currently has Recover, Transcript, and Remove buttons. Using str_replace, add the Doc button after the Transcript button:

```tsx
{/* Doc button — show for completed template agents that have doc_path */}
{agent.status === "completed" && agent.template && (
  <button
    onClick={() => setDocAgent(agent.name)}
    className="px-2 py-1 text-xs rounded bg-indigo-50 text-indigo-700 hover:bg-indigo-100
               dark:bg-indigo-900/40 dark:text-indigo-300 dark:hover:bg-indigo-900/60
               border border-indigo-200 dark:border-indigo-700"
  >
    Doc
  </button>
)}
```

- [ ] **Step 5: Render `AgentDocModal` when `docAgent` is set**

Find where other modals (transcript, etc.) are rendered (usually near the bottom of the component's JSX return). Add:

```tsx
{docAgent && (
  <AgentDocModal
    agentName={docAgent}
    onClose={() => setDocAgent(null)}
  />
)}
```

- [ ] **Step 6: Generalize follow_on_actions**

Find the existing block that checks `isRoadmapAgent(agent)` or similar to render follow-on action buttons. Replace the template-specific guard with a generic check:

**Old pattern (approximate):**
```tsx
{isRoadmapAgent(agent) && agent.status === "completed" && agent.follow_on_actions && (
  <div className="...">
    {agent.follow_on_actions.map(...)}
  </div>
)}
```

**New pattern:**
```tsx
{agent.status === "completed" &&
  agent.follow_on_actions &&
  agent.follow_on_actions.length > 0 && (
    <div className="mt-2 flex flex-wrap gap-2">
      <span className="text-xs text-gray-500 dark:text-gray-400 self-center">What's next:</span>
      {agent.follow_on_actions.map((action, i) => (
        <button
          key={i}
          onClick={() => handleFollowOnAction(agent, action)}
          className="px-3 py-1 text-xs rounded-full bg-purple-50 text-purple-700
                     hover:bg-purple-100 dark:bg-purple-900/40 dark:text-purple-300
                     dark:hover:bg-purple-900/60 border border-purple-200 dark:border-purple-700"
        >
          {action.label}
        </button>
      ))}
    </div>
  )}
```

Where `handleFollowOnAction(agent, action)` is the existing handler that injects the transcript — verify its name from the current code.

- [ ] **Step 7: TypeScript check**

```bash
cd /Users/torimeyer/claude/torios/app && tsc -b 2>&1 | head -20
```

Fix any type errors. The most likely one: `agent.follow_on_actions` may need the `FollowOnAction[]` type imported.

- [ ] **Step 8: Run frontend tests**

```bash
cd /Users/torimeyer/claude/torios && bash scripts/run-vitest.sh --reporter=verbose 2>&1 | tail -20
```

Expected: all tests pass. Fix any regressions before committing.

- [ ] **Step 9: Commit**

```bash
git add app/src/pages/Agents.tsx
git commit -m "feat(→1241): doc button + generic follow_on_actions in Recent Agents"
```

---

## Task 8: Add `follow_on_actions` to all builtin templates

**Files:**
- Modify: `api/services/agent_templates_store.py`

**Background:** Every builtin template that currently has no `follow_on_actions` gets at least 2 sensible ones. Templates that already have them (Roadmap, Competitive Scan) are left as-is.

- [ ] **Step 1: List templates that lack follow_on_actions**

```bash
python3 -c "
from api.services.agent_templates_store import BUILTIN_AGENT_TEMPLATES
for t in BUILTIN_AGENT_TEMPLATES:
    foa = t.get('follow_on_actions', [])
    print(t['name'], '->', len(foa), 'actions')
" 2>&1
```

- [ ] **Step 2: Add actions for each template**

For each template with 0 actions, add a `"follow_on_actions"` key. Use str_replace for each. Generic patterns:

**Builder:**
```python
"follow_on_actions": [
    {"label": "Write tests", "prompt": "Write comprehensive tests for what was just built. Context from previous run:\n{transcript}"},
    {"label": "Code review", "prompt": "Do a thorough code review of what was built and list issues by severity. Context:\n{transcript}"},
],
```

**PM / Roadmap (if missing):**
```python
"follow_on_actions": [
    {"label": "Build it", "prompt": "Implement the top-priority item from this roadmap: {transcript}"},
    {"label": "Prioritize", "prompt": "Re-score the roadmap items by effort vs impact. Context:\n{transcript}"},
],
```

**Researcher / Investigator:**
```python
"follow_on_actions": [
    {"label": "Go deeper", "prompt": "Dig deeper on the most important finding. Prior research:\n{transcript}"},
    {"label": "Write it up", "prompt": "Write a concise 1-page brief summarizing the research findings:\n{transcript}"},
],
```

**Debugger / Fixer:**
```python
"follow_on_actions": [
    {"label": "Add regression test", "prompt": "Write a regression test for the bug that was fixed:\n{transcript}"},
    {"label": "Root cause doc", "prompt": "Write a short post-mortem for the bug: what broke, why, and how it was fixed:\n{transcript}"},
],
```

- [ ] **Step 3: Verify no template has 0 actions**

```bash
python3 -c "
from api.services.agent_templates_store import BUILTIN_AGENT_TEMPLATES
missing = [t['name'] for t in BUILTIN_AGENT_TEMPLATES if not t.get('follow_on_actions')]
print('Missing follow_on_actions:', missing or 'none')
" 2>&1
```

Expected: `Missing follow_on_actions: none`.

- [ ] **Step 4: Run all backend tests**

```bash
cd /Users/torimeyer/claude/torios && python -m pytest tests/ -v 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add api/services/agent_templates_store.py
git commit -m "feat(→1241): add follow_on_actions to all builtin templates"
```

---

## Task 9: Smoke test + close Task

- [ ] **Step 1: Start the backend and frontend**

```bash
bash /Users/torimeyer/claude/torios/scripts/dev-backend.sh &
bash /Users/torimeyer/claude/torios/scripts/dev-frontend.sh &
```

- [ ] **Step 2: Run the e2e smoke test**

```bash
cd /Users/torimeyer/claude/torios && bash scripts/e2e_smoke.sh 2>&1 | tail -30
```

Expected: all checks pass. Fix any failures before proceeding.

- [ ] **Step 3: Manual golden path (do this in the app)**

1. Open the app → Agents → Templates tab.
2. Pick any template. Fill in the form. Click "Spawn agent."
3. Watch the agent run in the Recent tab.
4. When it completes: verify a **"Doc"** button appears on the row.
5. Click "Doc" → the modal opens showing `## Summary`, `## Key Findings`, `## Recommended Next Steps`.
6. Verify follow-on action buttons appear below the row (at least 2, from the template's `follow_on_actions`).
7. Click a follow-on action → a new agent spawns with the transcript injected into its prompt.

- [ ] **Step 4: Close the Task**

```bash
ostk work close "→1241"
```

---

## Risks & Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `Agents.tsx` (5264 lines) str_replace hits wrong context | Medium | Use 3–4 lines of context in old_str, not just 1. Read the target lines first. |
| Agent doesn't write `## Summary` block (prompt not appended) | Medium | The `doc_prompt_suffix` is appended in Task 2 Step 5. Verify it's actually in the final prompt via a transcript check. |
| Transcript ends before `## Summary` (agent ran out of budget) | Low | The `_write_agent_doc` fallback uses the last 2000 chars. Acceptable. |
| `ReactMarkdown` import name differs from what `AgentDocModal` uses | Low | Check `Agents.tsx` imports in Task 6 Step 1. Use the same import. |
| `follow_on_actions` handler name (`handleFollowOnAction`) is wrong | Medium | Verify in Task 7 Step 6 by reading the existing follow-on button click handler. |
| Old `produces_doc` flag on some templates explicitly set to `False` | Low | Task 2 sets `template_produces_doc = True` unconditionally for all template spawns — the old flag is now ignored. |

## Effort Estimate

| Task | Estimate |
|------|---------|
| Task 1: Read spawn + complete | 10 min |
| Task 2: Stamp at spawn | 25 min |
| Task 3: Universal doc generation | 30 min |
| Task 4: `/doc` endpoint | 15 min |
| Task 5: `AgentInfo` types | 10 min |
| Task 6: `AgentDocModal` | 30 min |
| Task 7: Agents.tsx changes | 35 min |
| Task 8: Template data | 20 min |
| Task 9: Smoke + close | 15 min |
| **Total** | **~3 hours** |
