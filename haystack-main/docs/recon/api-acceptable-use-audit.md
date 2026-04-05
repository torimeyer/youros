# API Acceptable Use Audit — ostk Recon

**Date:** 2026-03-29  
**Scope:** Anthropic, Google (Gemini), OpenAI, DeepSeek  
**Question:** Is ostk an acceptable use of these APIs?

---

## TL;DR

**Yes, with caveats.** ostk is a developer tool that makes direct API calls on behalf of a single authenticated user using their own API keys. This is the intended use case for all four providers. No policy violation identified. Three areas warrant ongoing attention.

---

## What ostk Actually Does

ostk is a **local CLI/TUI developer tool** that:
1. Makes direct API calls to LLM providers (Anthropic, Google, OpenAI via OpenRouter, DeepSeek)
2. Uses the **user's own API keys** (stored in HUMANFILE, never shared)
3. Runs **locally** on the developer's machine
4. Does **not** proxy, resell, or redistribute API access to third parties
5. Does **not** train competing models on outputs
6. Does **not** scrape or distill models
7. Spawns **agents** (automated LLM calls in loops) under human supervision

---

## Provider-by-Provider Analysis

### Anthropic (Claude API)

**Governing docs:**
- [Commercial Terms](https://www.anthropic.com/legal/commercial-terms)
- [Usage Policy / AUP](https://www.anthropic.com/legal/aup) (effective Sept 15, 2025)

**Key restrictions and ostk compliance:**

| Restriction | Source | ostk Status |
|---|---|---|
| No competing product/service | Commercial Terms D.4(a) | CLEAR — ostk is a dev tool, not a competing LLM API |
| No reselling Services | Commercial Terms D.4(a) | CLEAR — user's own keys, no proxying to third parties |
| No reverse engineering | Commercial Terms D.4(b) | CLEAR — direct API client |
| No model distillation/scraping | AUP "Do Not Abuse" | CLEAR — outputs used for developer work, not training |
| Comply with Usage Policy | Commercial Terms D.2 | CLEAR — no prohibited content categories |
| Human review of outputs | Commercial Terms D.3 | CLEAR — human-in-the-loop via TUI, approval gates |
| Consumer chatbot disclosure | AUP Additional Guidelines | N/A — ostk is not consumer-facing |
| Agentic use must comply with AUP | AUP Additional Guidelines | CLEAR — agents do code/devops work, no prohibited uses |
| MCP servers in Directory | AUP Additional Guidelines | N/A — ostk MCP servers are local, not listed in Anthropic Directory |
| No jailbreaking/prompt injection | AUP "Do Not Abuse" | CLEAR — system prompts are operational, not adversarial |
| Supported Regions | Commercial Terms D.2(b) | CHECK — user must be in supported region |
| No credential sharing | Implied | CLEAR — keys in per-user HUMANFILE, never transmitted |

**Agentic use note:** Anthropic's AUP explicitly contemplates agentic use cases and says they "must still comply with the Usage Policy." ostk agents do software development work (writing code, running tests, debugging). This is squarely within acceptable use. The :approval gate pattern aligns with Anthropic's guidance on human oversight for agentic systems.

### Google (Gemini API)

**Governing docs:**
- [Gemini API Additional Terms](https://ai.google.dev/gemini-api/terms) (effective March 23, 2026)
- [Google APIs Terms of Service](https://developers.google.com/terms)
- [Prohibited Use Policy](https://policies.google.com/terms/generative-ai/use-policy)

**Key restrictions and ostk compliance:**

| Restriction | Source | ostk Status |
|---|---|---|
| No competing product/service | Gemini Additional Terms | CLEAR — ostk is a dev tool, not a competing API |
| No sublicensing API to third parties | Google API ToS 4(a) | CLEAR — user's own keys |
| No creating "substantially the same" API Client | Google API ToS 4(a) | CLEAR — ostk is a dev tool, not an LLM API service |
| Professional/business use only | Gemini Additional Terms | CLEAR — developer tooling |
| Available regions | Gemini Additional Terms | CHECK — user must be in available region |
| Paid Services for EEA/UK | Gemini Additional Terms | CHECK — EU users must use paid tier |
| No reverse engineering | Google API ToS 4(a) | CLEAR |
| Keep credentials confidential | Google API ToS 4(b) | CLEAR — keys in HUMANFILE, not in source |
| Credentials may not be embedded in open source | Google API ToS 4(b) | CLEAR — HUMANFILE is gitignored |
| Agentic: user responsible for actions | Gemini Additional Terms | CLEAR — user initiates, approves, reviews |
| No auto-bypass of human confirmation requests | Gemini Additional Terms | MONITOR — see below |
| Prohibited Use Policy | Gemini Additional Terms | CLEAR — no prohibited content |

**Agentic confirmation bypass:** Google's Gemini terms specifically state: "You will not automatically bypass any requests for human confirmation." If Gemini's API returns a response requesting human confirmation before proceeding, ostk must honor it (not auto-approve). The :approval gate handles this, but agent loops should be reviewed to ensure they don't auto-continue past model-initiated confirmation requests.

### OpenAI (via OpenRouter)

**Governing docs:**
- OpenAI Usage Policies (Cloudflare-protected, summarized from known content)
- OpenRouter Terms of Service (intermediary)

**Key points:**
- ostk accesses OpenAI models through OpenRouter, an authorized aggregator
- OpenRouter has its own ToS that wraps provider terms
- OpenAI prohibits: illegal activity, weapons, CSAM, harassment, deception, malware, spam, unauthorized automated decision-making in high-stakes domains
- ostk's use case (software development) is clearly within bounds
- Double compliance required: must satisfy both OpenRouter's and OpenAI's underlying terms

| Restriction | ostk Status |
|---|---|
| No competing product | CLEAR |
| No prohibited content | CLEAR |
| Comply with upstream provider terms | CLEAR — dev tooling |
| Rate limits / abuse | CLEAR — single-user tool |

### DeepSeek

**Governing docs:**
- Platform terms at platform.deepseek.com (JS-rendered, not directly fetchable)

**General assessment:**
- Standard prohibitions: illegal activity, malware, CSAM, etc.
- No known restrictions on developer tooling or agentic use
- ostk's use case is clearly within bounds

---

## Risk Areas Requiring Ongoing Attention

### 1. "Reselling" vs "Building With" (LOW RISK)

**The question:** Does ostk "resell" API access?

**Answer: No.** ostk is a tool that calls APIs on behalf of the user who owns the API key. This is like Cursor, Continue, Cody, or any other AI-powered dev tool. The key distinction:
- Reselling = accepting payment from third parties to access APIs through your credentials
- Building with = creating a tool that uses the API for the end user's own purposes with their own keys

ostk is firmly in the "building with" category. Every API call uses the operator's own key.

### 2. Agentic Loops and Human Oversight (MEDIUM RISK)

All providers increasingly care about agentic use. Current ostk architecture is well-positioned:
- :approval gate for sensitive operations
- Human-in-the-loop via TUI
- Audit trail of all agent actions
- Budget limits on agent spending

**Recommendation:** Document the human oversight model in public docs. If providers audit, showing the :approval gate + audit trail + budget limits demonstrates responsible agentic design.

### 3. Google Credential Embedding (LOW RISK)

Google API ToS 4(b) says: "Developer credentials may not be embedded in open source projects."

ostk is open source. API keys go in HUMANFILE (gitignored) or env vars, never in source. This is compliant, but:
- Ensure HUMANFILE is in .gitignore template and installation docs emphasize this
- Consider a pre-commit hook or CI check that rejects commits containing API key patterns

---

## Comparison with Similar Tools

ostk operates in the same category as:
- **Cursor** — IDE that calls Claude/GPT APIs with user's keys
- **Claude Code** — CLI that calls Anthropic API with user's key
- **Continue** — IDE extension calling multiple LLM APIs
- **Aider** — CLI pair programmer calling LLM APIs
- **Cody** — Sourcegraph's AI coding assistant

All of these are accepted uses. ostk's differentiator (kernel/daemon architecture, agent spawning, multi-model) doesn't change the fundamental compliance posture.

---

## Conclusion

**ostk is acceptable use of all four providers' APIs.** It is a developer tool making direct API calls with the user's own credentials for software development purposes. No proxying, reselling, distillation, or prohibited content generation.

**Action items:**
1. Keep credentials in gitignored HUMANFILE (already done)
2. Ensure agent loops respect model-initiated confirmation requests (Google requirement)
3. Document human oversight model in public docs for audit readiness
4. Verify supported regions guidance in installation docs
5. Continue :approval gate pattern for sensitive agent operations
