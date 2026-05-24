# SDD Best Practices: Broader Research
*Research date: 2026-05-16. Covers Amazon PR/FAQ, Google design docs, RFC-style specs, Agile AC patterns, AI-assisted SDD, and anti-patterns. GitHub Spec Kit covered separately.*

---

## 1. Amazon Working Backwards — PR/FAQ

**Sources:** [workingbackwards.com](https://workingbackwards.com/resources/working-backwards-pr-faq/) · [coda.io/colin-bryar](https://coda.io/@colin-bryar/working-backwards-how-write-an-amazon-pr-faq) · [medium/intrico-io](https://medium.com/intrico-io/strategy-tool-amazons-pr-faq-72b3e49aa167)

- **Hard length cap: 6 pages** (plus optional data appendix). No bullet lists, no graphics in the body.
- **Part 1 — Press Release (~1 page):** Headline, sub-headline, problem paragraph, solution paragraph(s), leadership quote, customer quote, call to action. Written as if the product already launched.
- **Part 2 — External FAQ:** Plain-language Q&A for customers/press. No jargon.
- **Part 3 — Internal FAQ:** Leadership/stakeholder Q&A covering risk, financials, legal, operations, and every hard technical problem that must be solved.
- Narrative meetings open with 20 minutes of silent reading — no deck, no presenter preamble.
- Document is iterated many times before green-light; the approval of the doc *is* the project decision.
- Bars: bullet points, graphics, vague solution prose, missing customer quote, missing risk assessment.

**Most portable idea:** Write the press release before any code. Forces you to name the customer problem and the measurable benefit in language a non-engineer can understand.

---

## 2. Google Design Docs

**Sources:** [industrialempathy.com/design-docs-at-google](https://www.industrialempathy.com/posts/design-docs-at-google/) · [lodely.com](https://www.lodely.com/blog/design-docs-at-google)

- **No strict length rule,** but the guidance is "as short as possible, as long as necessary." Practically: 1–10 pages.
- **Canonical sections:** Context/Background · Goals · Non-Goals · Design (overview → details, trade-offs explicit) · Alternatives Considered · Risks & Cross-Cutting Concerns (security, privacy) · Success Metrics.
- **Non-Goals are first-class.** They are not negated goals ("shouldn't crash") but explicitly descoped items that could reasonably have been included (e.g., "ACID compliance: non-goal for v1").
- **Alternatives Considered** is mandatory and must include "do nothing" as a baseline.
- **Success Metrics** section separates strong docs from weak ones — without it, "finished" can still be seen as failure.
- Review process: share with "canary" readers first, then broader audience via inline comments.

**Most portable idea:** Non-Goals as a named section. Explicitly descoping prevents scope creep and keeps the design conversation focused.

---

## 3. RFC-Style Specs (IETF · Rust RFCs · Python PEPs)

**Sources:** [rust-lang/rfcs template](https://github.com/rust-lang/rfcs/blob/master/0000-template.md) · [peps.python.org/pep-0001](https://peps.python.org/pep-0001/) · [RFC 7322](https://datatracker.ietf.org/doc/html/rfc7322)

**Shared sections across all three:**

| Section | Rust RFC | Python PEP | IETF RFC |
|---|---|---|---|
| Summary / Abstract | ✅ | ✅ | ✅ |
| Motivation | ✅ | ✅ | ✅ (Introduction) |
| Detailed design / Specification | ✅ | ✅ | ✅ (Body) |
| Drawbacks / Backwards compatibility | ✅ | ✅ | implicit |
| Rationale & Alternatives | ✅ | ✅ (Rejected Ideas) | — |
| Unresolved Questions | ✅ | — | — |
| Future Possibilities | ✅ (RFC 2561) | — | — |
| Security implications | — | ✅ | ✅ (IANA/Security) |
| Prior Art | ✅ (RFC 2333) | — | — |

- IETF mandates **RFC 2119 MUST/SHOULD/MAY** capitalization for testable requirements.
- Rust RFCs explicitly require **guide-level explanation** — teach the feature as if it already exists.
- Python PEPs require **Backwards Compatibility** section for any breaking change; rejection is automatic without it.

**Most portable idea:** "Unresolved Questions" as a named section. Forces the author to surface known unknowns before implementation begins instead of discovering them mid-build.

---

## 4. Agile / User-Story Acceptance Criteria

**Sources:** [testrail.com/blog/acceptance-criteria-agile](https://www.testrail.com/blog/acceptance-criteria-agile/) · [parallelhq.com/blog/given-when-then](https://www.parallelhq.com/blog/given-when-then-acceptance-criteria) · [altexsoft.com](https://www.altexsoft.com/blog/acceptance-criteria-purposes-formats-and-best-practices/)

- **Given/When/Then (Gherkin):** Given [precondition] · When [action] · Then [verifiable outcome]. Maps directly to automated test cases (Cucumber, SpecFlow).
- **INVEST checklist:** Independent · Negotiable · Valuable · Estimable · Small (1–3 AC per story max) · **Testable** (no "fast," "user-friendly," "good UX" without numbers).
- **Definition of Done (DoD) ≠ Acceptance Criteria.** AC is story-specific (does login work?). DoD is universal (code reviewed, documented, deployed to staging).
- Happy path + unhappy path both required in Gherkin scenarios.
- Checklist format (bullet rules) is better than Given/When/Then for non-flow requirements like form validation or NFRs.

**Most portable idea:** INVEST "T" — every AC must be testable. If you can't write a test for it, it's a wish, not a requirement.

---

## 5. AI-Assisted Spec-Driven Development

**Sources:** [thoughtworks.com/spec-driven-development](https://www.thoughtworks.com/en-us/insights/blog/agile-engineering-practices/spec-driven-development-unpacking-2025-new-engineering-practices) · [augmentcode.com/guides/claude-code-spec-driven-development](https://www.augmentcode.com/claude-code-spec-driven-development) · [nvarma.com/blog](https://www.nvarma.com/blog/2026-03-01-spec-driven-development-claude-code/) · [github.com/gotalab/cc-sdd](https://github.com/gotalab/cc-sdd)

- **Core workflow:** Specify → Plan → Implement → Validate. Requirements in Markdown; AI generates a plan; human reviews; agent implements against approved plan.
- **`CLAUDE.md` as index, not container.** Top-level file is a map to deeper spec files (architecture, models, test hierarchy, scenarios). Reduces hallucination surface.
- **Given/When/Then in specs** — domain ubiquitous language, covering critical path without enumerating every edge case. Machine-readable specs reduce token waste and reduce LLM drift.
- **Human-in-the-loop is non-negotiable.** Spec = thinking tool for the human. Agent is builder; human is architect. Without blueprints, agents improvise.
- **Known failure mode:** Claude Code has skipped CLAUDE.md instructions even when its own reasoning trace correctly identified the violation. Behavioral drift is real; automated spec-to-implementation verification doesn't exist yet in any mainstream tool.
- Emerging pattern: specs as contracts between multiple agents (planner, implementer, reviewer all anchored to the same spec file).

**Most portable idea:** Treat the spec file as a contract, not a prompt. It lives beside code in the repo; it is versioned, reviewed, and can reject a PR if AC are unmet.

---

## 6. Anti-Patterns: Why Specs Fail

**Sources:** [augmentcode.com/guides/ai-spec-template](https://www.augmentcode.com/guides/ai-spec-template) · [kiro.dev/blog/deep-spec-analysis](https://kiro.dev/blog/deep-spec-analysis/) · [medium/srinathperera architecture anti-patterns](https://medium.com/@srinathperera/a-deeper-look-at-software-architecture-anti-patterns-9ace30f59354)

| Smell | Risk | Fix |
|---|---|---|
| **Vague NFRs** ("fast," "secure") | Missed expectations, last-minute rework | Quantify: "P95 < 300ms at 5k RPS from 3 regions" |
| **Untestable AC** ("intuitive," "good UX") | No definition of done | Given/When/Then with measurable Then clause |
| **No error paths / no rollback plan** | Production failures with no recovery | Explicitly doc failure modes, degraded states, rollback steps |
| **Happy-path only** | Edge cases hit production | Require unhappy-path scenarios alongside happy path |
| **Prescriptive architecture** ("use class X, inject Y") | Conflicts with real codebase; AI picks worst-of-both | Describe *what*, not *how*; point to existing code examples |
| **Missing scope boundary** (no "Not Included") | Feature creep, unrequested additions | Explicit "Out of Scope" or "Non-Goals" section |
| **No success metrics** | "Finished" ≠ "succeeded" | Add measurable success criteria before kickoff |
| **Implementation leaks** | Spec describes DB schema instead of behavior | Keep spec at behavior/outcome level; schema is implementation |

**Most portable idea:** "Not Included" / "Non-Goals" is the cheapest anti-scope-creep tool in existence. One section, ~3 bullets, saves weeks.

---

## Synthesis: The 10 Spec Elements That Show Up Everywhere

Ranked by cross-source frequency, with overhead cost marked **(heavy / medium / light)**.

| Rank | Element | Sources | Cost |
|---|---|---|---|
| 1 | **Problem / Motivation** — why this exists, what pain it solves | All 6 | light |
| 2 | **Goals** — measurable, not adjective-based | Amazon, Google, Agile, AI-SDD | light |
| 3 | **Non-Goals / Out of Scope** — explicitly descoped items | Google, Amazon, Agile, Anti-patterns | light |
| 4 | **Testable Acceptance Criteria** — Given/When/Then or checklist, with numbers | Agile, RFC, AI-SDD, Anti-patterns | medium |
| 5 | **Alternatives Considered** — what else was evaluated and why rejected | Google, Rust RFC, PEP, Amazon Internal FAQ | medium |
| 6 | **Unresolved Questions** — named unknowns before implementation starts | Rust RFC, Google, AI-SDD | light |
| 7 | **Risks & Failure Modes** — what breaks, and rollback/mitigation plan | All 6 | medium |
| 8 | **Success Metrics** — how you'll know it worked post-launch | Google, AI-SDD, Agile DoD | medium |
| 9 | **Context / Background** — just enough to onboard a new reader | All 6 | light |
| 10 | **Backwards Compatibility / Migration notes** — what breaks for existing users | PEP, Rust RFC, AI-SDD, Anti-patterns | heavy (only when needed) |

**Cheapest wins (light overhead, highest cross-source frequency):** Problem statement, Goals, Non-Goals, Context, Unresolved Questions. Five bullets you can add to any spec in under 10 minutes that prevent the five most common failure modes.
