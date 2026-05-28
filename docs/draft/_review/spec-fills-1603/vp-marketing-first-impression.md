# Fill proposal: vp-marketing-first-impression.md

## Provenance

Created 2026-05-14 by promoting plan `~/.claude/plans/my-vp-of-marketing-majestic-pony.md` (written 2026-05-01). The plan was a gap analysis for a VP of marketing meeting. The spec was written to close three concrete gaps before that meeting: source grounding for creative agents, spec-to-artifact choices beyond code, and per-agent MCP/skill visibility. Blocks 1, 2, 3 map directly to those three gaps. Build work landed across Tasks →1228, →291, →1531, →1532.

## What's missing

One canonical section: **Acceptance criteria** as a top-level section. All the AC items exist inside Block 1, Block 2, and Block 3 sub-sections, but there is no `## Acceptance criteria` heading at the spec level. The build pipeline reads top-level AC; items buried in blocks are invisible to it.

## Confidence: HIGH

Every AC item already exists. This is purely extraction and consolidation.

---

## Proposed fill — full spec with change applied

(Only the Acceptance criteria section is new. All other sections are unchanged from the live spec.)

```markdown
## Acceptance criteria

### Block 1: Source library and per-agent grounding
- [ ] A new Library surface accepts file uploads (PDF, MD, DOCX, TXT) and pasted URLs. Files land in `~/.myos/sources/<workspace>/` with a sidecar JSON containing source URL, upload time, tags.
- [ ] Library items appear in a list with title, type, size, tags. Each row has a delete affordance.
- [ ] Agentfile parser accepts a new `KNOWLEDGE <tag>` directive (one per line, multiple allowed).
- [ ] On spawn, every matching tagged source is read, full-text searched for terms from the user's input, and the top three excerpts are pre-pended to the agent's PROMPT under a "Reference material:" header.
- [ ] blog-post, headline-generator, social-post, cold-outreach-draft, and follow-up agents each gain a `KNOWLEDGE brand` directive so a brand guide tagged `brand` flows into every creative spawn automatically.
- [ ] Verification: upload a one-page brand-voice PDF tagged `brand`. Spawn blog-post. Transcript shows brand-voice excerpts in the agent's context. Output reflects the voice.

### Block 2: Spec-to-artifact chooser
- [ ] New Spec wizard adds a "What does this spec produce?" step before AC. Options: Code, Agent, Document, Slide deck, Diagram, Skill. Default is Code.
- [ ] Agent option spawns a `builder-of-agents` template that writes a `.agent` file into `~/.myos/agents/custom/<slug>.agent`.
- [ ] Document option spawns a `document-drafter` template that produces a `.docx` via fcp-gdocs MCP and lands it in Drive.
- [ ] Slide deck option spawns a `slides-drafter` template that produces a `.pptx` via fcp-slides MCP.
- [ ] Diagram option spawns a `diagram-drafter` template that produces a `.drawio` via fcp-drawio MCP.
- [ ] Skill option writes a `.skill` file into `~/.myos/skills/<slug>.skill`.
- [ ] All five new templates read attached Library sources via `KNOWLEDGE` directive from Block 1.
- [ ] Verification: draft an agent spec, pick Agent, Build it — confirm `.agent` file exists with valid syntax and runs.
- [ ] Verification: draft a document spec, pick Document — confirm `.docx` lands in Drive with library content.

### Block 3: Per-agent MCP and skill visibility
- [ ] Each marketplace agent's page lists the MCPs and Skills it declares (read from parsed Agentfile).
- [ ] Spawn-time injection of enabled MCPs is documented on the agent page.
- [ ] At least three marketplace agents (blog-post, prospect-research, follow-up) declare a non-empty `MCP` line.
- [ ] Verification: open blog-post in marketplace. See declared MCPs and Skills. Add a custom MCP via Settings. Spawn blog-post. Transcript lists the custom MCP.
```

## Items that couldn't be recovered

None — all AC items came directly from the spec body.
