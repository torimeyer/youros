# Can myOS use a Claude subscription instead of paid API tokens?

Research date: 2026-04-06
Author: background research agent (myOS)
Related ostk needle: →121

## 1. TL;DR

Short answer: yes, partially, and only for the person who owns the Claude subscription. The cleanest legitimate path is to have myOS shell out to the `claude` command line tool on the user's own machine, which uses the user's own Claude.ai login. That tool is included in Pro and Max plans and does not charge API tokens. Anthropic explicitly forbids building a product that logs users into Claude on their behalf or routes other people's traffic through a subscription, so myOS cannot act as a hosted service that spends Tori's subscription for other buyers. For each end user, myOS would need to detect whether they have Claude Code installed and use it if so, or fall back to an API key if not.

## 2. The question

Tori is a Claude Max subscriber. She uses Claude Code daily under her subscription at no extra cost. myOS's chat panel currently calls the Anthropic API directly with an API key, which charges pay-as-you-go tokens on top of her subscription. She wants to know if there is a legitimate, terms-compliant way to power the myOS chat panel using her existing subscription instead of paying twice, and whether that same approach would work if she sold myOS to other people.

## 3. Options

### Option A. Shell out to the local `claude` CLI on the user's machine

**How it works.** The user installs Claude Code and logs in once. myOS's backend runs `claude -p "<prompt>" --output-format stream-json --verbose` as a subprocess. The CLI reads the user's Claude.ai credentials from the local macOS keychain (or `~/.claude/.credentials.json` on Linux and Windows) and streams responses back. myOS parses the stream and forwards it to the chat panel.

**Verified locally.** I ran `claude auth status` and got:

```
{ "loggedIn": true, "authMethod": "claude.ai", "apiProvider": "firstParty",
  "email": "torimeyer25@gmail.com", "subscriptionType": "max" }
```

I also ran `claude -p "say hi" --output-format json` and got a structured JSON result with `"result": "Hi!"` and session metadata. The credentials live in the macOS Keychain as `Claude Code-credentials` (verified with `security find-generic-password`). No API key is involved.

**The `total_cost_usd` field is misleading.** The JSON output includes a `total_cost_usd` field even on Max, but that is a display value. Max and Pro subscribers are billed against a weekly quota, not a dollar meter. See Anthropic's rate limit docs and the Pro/Max help article (sources below).

**Pros.**
- Zero extra cost to Tori. Uses her existing Max plan.
- Same auth mechanism Anthropic designed for interactive Claude Code use. No gray area for the person using their own account on their own machine for ordinary use.
- Full access to Claude Code's built-in tools, skills, slash commands, subagents, plugins, and MCP servers for free.
- Streaming output is supported via `--output-format stream-json --verbose --include-partial-messages`.
- Structured output and JSON schemas are supported via `--output-format json --json-schema`.
- Sessions can be resumed with `--continue` or `--resume <session-id>`.
- Model is selectable with `--model sonnet` or `--model opus`.

**Cons.**
- Heavier startup than a raw API call. Each `claude -p` invocation loads hooks, plugins, and CLAUDE.md context unless you pass `--bare`. Anthropic recommends `--bare` for scripts and SDK calls and plans to make it the default in a future release.
- Counts against the shared weekly quota. Chat panel traffic plus interactive Claude Code use plus Claude on the web all draw from the same limit. Heavy use can exhaust the quota. Anthropic adjusted 5 hour limits downward during peak hours in March 2026.
- Subprocess model is slower than in-process API calls. Cold start is probably 300 to 800 ms before the first token. Cache warming mitigates this across calls in the same session.
- myOS becomes dependent on Claude Code being installed and logged in on the host machine. Requires onboarding users through a one-time install and `claude auth login`.
- Tool calls made inside `claude -p` run under Claude Code's permission model, which is different from myOS's own tool executor. Would need to decide whether myOS exposes its tools through the CLI or still runs them in the Python backend with the CLI only doing text generation.

**Terms of service status.** Allowed when the user is running it on their own machine with their own login for their own use. This is exactly what Claude Code is designed to do. The Pro/Max help center article explicitly lists "Claude Code in your terminal" as a subscription benefit. The gray area is whether a third-party app like myOS invoking it as a subprocess counts as "third party use" of the subscription. Anthropic's legal page says OAuth auth is "intended exclusively for purchasers... to support ordinary use of Claude Code and other native Anthropic applications" and forbids third-party developers from offering "Claude.ai login or to route requests through Free, Pro, or Max plan credentials on behalf of their users." The key phrase is "on behalf of their users." myOS shelling out on the user's own machine with the user's own local install is different from a hosted service routing someone else's traffic. Needs verification with Anthropic for the distributed product case. For Tori personally using myOS on her own Mac, this is clearly fine.

**Effort to implement in myOS.** Medium. Add a new provider in `api/services/chat_providers.py` called `claude_cli` that wraps `subprocess.create_subprocess_exec(["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--include-partial-messages", "--bare"])`, reads lines from stdout as they arrive, parses each JSON event, and translates `text_delta` events into WebSocket `token` messages matching the existing `stream_anthropic` contract. The agent loop with tool use requires more work because Claude Code's tools and myOS's tools are different. Simplest first version: use the CLI for pure text chat, keep the existing agent loop on the API-key path, and let the user pick.

**Effort for end users.** One-time install of Node.js and Claude Code, then `claude auth login` in the browser once. For a non-engineer this is roughly ten minutes and does need terminal use. Could be automated with a post-install script that checks for `claude` on PATH and prompts to install if missing.

### Option B. Claude Agent SDK with `CLAUDE_CODE_OAUTH_TOKEN`

**How it works.** Tori runs `claude setup-token` which issues a long-lived OAuth token tied to her subscription. She sets `CLAUDE_CODE_OAUTH_TOKEN=<token>` in the myOS backend environment. myOS imports `claude_agent_sdk` and calls `query(prompt=..., options=ClaudeAgentOptions(...))`. Under the hood the SDK uses Claude Code's auth and bills against her subscription quota.

**Pros.**
- Pure Python library. No subprocess overhead. Same auth Tori uses for Claude Code.
- Gives you Claude Code's full tool loop, hooks, MCP support, subagents, and sessions as a real SDK rather than by parsing CLI output.
- Structurally closer to the existing `chat_providers.py` design where the Anthropic SDK is imported directly.

**Cons.**
- Explicitly prohibited by Anthropic for any third-party product. The Agent SDK overview page has a note that says: "Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK. Please use the API key authentication methods described in this document instead." The legal page repeats this.
- Anthropic enforced this on OpenClaw and similar tools starting April 4, 2026. Subscription traffic through these tools now draws from a paid extras bucket, not the subscription.
- For Tori's own personal development use, the `CLAUDE_CODE_OAUTH_TOKEN` workaround technically functions and was discussed in a closed Anthropic GitHub issue, but Anthropic has not explicitly blessed personal use of the token in your own Python code. There is an open feature request asking for exactly this and Anthropic has not responded.

**Terms of service status.** Gray area for personal use. Explicitly prohibited for shipping as a product to other people. Do not build a business on this.

**Effort to implement in myOS.** Low. Add `claude-agent-sdk` to `api/requirements.txt`, write a new `stream_claude_agent_sdk` method in `chat_providers.py`, read the token from the keychain, call `query()`. Probably 30 lines of code.

**Effort for end users.** Medium. Each user would need to install Claude Code and run `setup-token`, then put the token in myOS's settings.

**Recommendation for this option.** Skip it. Even though it's technically the cleanest code, the ToS risk is too high for a product Tori wants to sell. Use Option A instead.

### Option C. Tori pays, charges users a subscription

**How it works.** Tori buys an API organization from Anthropic Console and pays per token. Tori ships myOS as a product that calls her API key on behalf of all users. She charges users a monthly fee that covers the expected token cost plus margin.

**Pros.**
- Fully allowed. This is what the API is for.
- Zero install friction for end users. No Claude Code required.
- Tori controls the model, the rate limits, and the cost predictability.
- Compatible with every feature Anthropic ships on the API.

**Cons.**
- Tori pays Anthropic on every user request. Cost risk scales with usage. A chatty user can blow through the margin.
- Requires billing infrastructure on Tori's side (Stripe plus metering plus rate limits per user).
- Requires hosting the myOS backend somewhere, which is a bigger step than shipping a local app.
- Heavy power users will notice the token cost is higher than an equivalent Max subscription and may resent it.

**Terms of service status.** Fully allowed under Anthropic Commercial Terms. This is the standard SaaS pattern.

**Effort to implement in myOS.** Zero. This is the current implementation.

**Effort for end users.** Zero. They sign up and pay.

### Option D. Hybrid: detect Claude Code locally, fall back to API key

**How it works.** On first launch, myOS checks whether `claude` is installed on PATH and whether `claude auth status` shows `loggedIn: true`. If yes, it uses Option A. If no, it prompts the user to either install Claude Code for free use under their own subscription, or paste an API key. If they have a subscription they pay nothing extra. If they don't, they get to bring their own API key. Optionally Tori can layer Option C on top later by offering her own hosted version.

**Pros.**
- Best of both worlds. Subscribers use what they already paid for, non-subscribers can still use the product.
- No forced dependency on Claude Code for non-subscribers.
- Lets Tori start selling myOS as a self-hosted local app today without building billing infrastructure.

**Cons.**
- Two code paths to maintain in `chat_providers.py`.
- Onboarding flow needs a branching UI: "do you have a Claude subscription" then either "install Claude Code" or "paste API key."
- Still has the unresolved question from Option A about whether a third-party product invoking the local CLI is allowed. Worth a quick email to Anthropic sales for written clarification before shipping to paying customers.

**Terms of service status.** Each path is individually fine. The hybrid model doesn't add new ToS concerns beyond the Option A question.

**Effort to implement in myOS.** Medium. One new provider, one detection probe at startup, one onboarding branch, one settings toggle.

**Effort for end users.** Low. The onboarding wizard can detect what they have and do the right thing.

### Option E. Local proxy that mimics the Anthropic API

**How it works.** Run a local HTTP server that exposes the same endpoints as api.anthropic.com but internally invokes `claude -p` or the Agent SDK. myOS's chat panel points at `http://localhost:<port>` instead of api.anthropic.com. Open source projects like Meridian and LiteLLM already do this.

**Pros.**
- Keeps myOS's code basically unchanged. Just swap the base URL.
- Existing open source tools work out of the box.

**Cons.**
- Meridian is explicitly designed to route a Max subscription through third-party tools, which is the exact pattern Anthropic prohibited on April 4, 2026. Shipping myOS with Meridian bundled is a terms-of-service violation, full stop.
- Adds a second moving part (the proxy daemon) that can fail, drift, or be blocked by Anthropic at any time.
- Doesn't solve the onboarding story. Users still need Claude Code installed and logged in under the hood.

**Terms of service status.** Prohibited, at least for the Meridian-style patterns. Enforcement has already happened.

**Recommendation for this option.** Do not use. Interesting as research but the legal risk is disqualifying.

### Option F. "Sign in with Claude" OAuth flow for third parties

**How it works.** Anthropic would offer a developer OAuth flow that lets a third-party app get a token tied to a user's subscription with consent, similar to Sign in with Google.

**Status.** Does not exist. The legal page is explicit: "Anthropic does not permit third-party developers to offer Claude.ai login or to route requests through Free, Pro, or Max plan credentials on behalf of their users." There is a feature request on the claude-code repo (issue 42106) asking for it, filed April 1, 2026, with no official response.

**Recommendation.** Track the feature request. Do not plan around it.

## 4. Recommendation

Go with Option D, the hybrid. For now:

1. Keep the existing API-key path working. It is the safe default.
2. Add a `claude_cli` provider that shells out to `claude -p` with `--bare --output-format stream-json --verbose`. Use it for Tori's personal myOS install today so she stops paying API tokens on top of her Max plan.
3. Detect Claude Code at startup. If present and logged in, default new chats to the CLI provider. Otherwise use the API key provider.
4. Before shipping myOS as a product to other people, email Anthropic sales and ask for written confirmation that a user-installed product shelling out to their own local `claude` on their own machine is within terms. If yes, keep the hybrid. If no, drop the CLI path for the sold version and ship myOS with Option C, where Tori becomes a reseller of Anthropic API tokens.

Why hybrid and not pure Option A: because myOS's backend uses an agent loop with tool calls that are defined in Python (`api/services/tool_executor.py`), and rewiring that to run through Claude Code's tool system is real work. The hybrid lets us cut Tori's cost immediately for text chat while keeping the agent loop unchanged until we decide how to migrate it.

Why not Option B ever: the Agent SDK with subscription token is a product-shipping dead end because Anthropic explicitly forbids it. It's fine for personal tinkering but not for myOS's business model.

Why Option C stays as a backstop: some users won't want to install Claude Code, and Option C is the only fully unambiguous way to sell myOS as a turnkey product.

## 5. Migration sketch

File-level plan for adding the hybrid path. No code here, just what changes where.

- `api/requirements.txt`. No new dependency required. `subprocess` is in the standard library. Optionally add `claude-agent-sdk` if you later decide to test Option B as a personal-use experiment, but keep it out of production.
- `api/services/chat_providers.py`. Add a new method `stream_claude_cli(messages, websocket)` that:
  - Builds a prompt string from the `messages` list (or uses `--input-format stream-json` for multi-turn).
  - Spawns `claude -p --bare --output-format stream-json --verbose --include-partial-messages --model sonnet` via `asyncio.create_subprocess_exec`.
  - Reads lines from stdout in a loop, parses each JSON event, forwards `text_delta` events as WebSocket `token` messages, forwards `result` as the final `done` message.
  - Handles the `system/api_retry` event type for error reporting.
  - Surfaces auth failures with a clear message telling the user to run `claude auth login`.
- `api/services/chat_providers.py`. Add a helper `_claude_cli_available()` that runs `claude auth status` once at startup, caches the result, and returns True if `loggedIn == true` and `apiProvider in ("firstParty", "claude.ai")`.
- `api/routers/chat.py`. Change the provider selection logic to prefer `claude_cli` when available and no explicit API key is set. Respect a user setting that forces a specific provider.
- `api/routers/settings.py`. Add a `chat_provider` setting with options `auto`, `claude_cli`, `anthropic_api`, `gemini`. Default to `auto`.
- `app/src/components/OnboardingWizard.tsx`. Add a new Connect step branch: detect Claude Code via a backend endpoint, if present show "You're already signed in with your Claude subscription, no extra setup needed," if not present show two paths: "Install Claude Code (free if you have Pro or Max)" or "Paste an Anthropic API key."
- `app/src/pages/Settings.tsx`. Add the chat_provider dropdown and a "Claude Code status" indicator showing the detected auth state.
- Tests. Add `api/tests/test_chat_providers_claude_cli.py` that mocks subprocess and verifies the stream parsing, the auth detection, and the fallback behavior.
- Docs. Update `docs/myos-product-brief.md` with the new onboarding story.

Out of scope for v1: porting the existing agent loop (`agent_anthropic`) to run through `claude -p`. Keep the agent loop on the API-key path until we have clarity from Anthropic about whether a product can use the CLI for tool-using agents.

## 6. Open questions and things to verify

- Does Anthropic consider a user-installed product that shells out to `claude -p` on the user's own machine to be a "third-party tool routing on behalf of the user"? The legal text is ambiguous. The OpenClaw enforcement suggests Anthropic is willing to block this class of pattern when it sees it. Written clarification from Anthropic sales is the safest path before shipping myOS as a paid product. This is the single biggest open question and it should not block Tori's personal use today.
- What does `claude -p` with `--bare` actually bill against on a Max plan? The JSON output shows `total_cost_usd` but the real metering is weekly quota. Verify by running a small workload on Max, checking the Claude.ai usage page, and confirming no API dollars were charged.
- Does the `ANTHROPIC_API_KEY` environment variable override the subscription when both are present? Yes, according to the authentication precedence doc. myOS's backend currently sets `ANTHROPIC_API_KEY`, which means just installing the CLI isn't enough. myOS will need to explicitly unset that variable in the subprocess environment when calling `claude -p`, or use `--bare` carefully (bare mode actually forces API-key auth, which is the opposite of what we want here, so do NOT pass `--bare` on the CLI path). Needs a careful test.
- Correction to the migration sketch above: drop `--bare`. Bare mode forces API key auth. Use plain `claude -p --output-format stream-json --verbose --include-partial-messages --model sonnet` and explicitly strip `ANTHROPIC_API_KEY` from the subprocess environment so the subscription path wins.
- What's the cold-start latency of `claude -p` in practice? Run a benchmark before wiring it to the chat panel. If first-token latency is above 1 second, Tori will notice.
- Can the CLI's stream-json output include partial message deltas in the same shape every time? Need a quick compatibility test across a few CLI versions to make sure parsing doesn't break on upgrades.
- Is there any way to do tool calls via the CLI path cleanly from Python, or does the tool loop need to live inside `claude -p`? Worth a separate experiment.
- If Tori sells myOS, is it legal to distribute a bundled installer that also installs Claude Code? Almost certainly yes because Claude Code is free and user-installed, but confirm with Anthropic.
- Does an open-source personal-use version of myOS that uses the CLI path have any license restrictions from Anthropic? No known restriction, but worth confirming.

## 7. Sources

Local verification:

- `/Users/torimeyer/claude/torios/api/services/chat_providers.py` lines 112 to 143, current `stream_anthropic` implementation that uses an API key.
- `claude --version` output: `2.1.94 (Claude Code)`.
- `claude auth status` output showing `"authMethod": "claude.ai"`, `"apiProvider": "firstParty"`, `"subscriptionType": "max"`.
- `claude -p "say hi" --output-format json` output returning a result with `total_cost_usd`, `session_id`, and token usage.
- `security find-generic-password -s "Claude Code-credentials"` confirming credentials stored in macOS Keychain.
- `claude --help` and `claude -p --help` showing the full CLI surface including `--output-format`, `--json-schema`, `--bare`, `--model`, `--continue`, `--resume`, `--mcp-config`, `--agents`.

Anthropic official docs:

- [Agent SDK overview](https://platform.claude.com/docs/en/agent-sdk/overview). Contains the note that third-party developers may not offer Claude.ai login or rate limits for their products including those built on the Agent SDK.
- [Claude Code Authentication](https://code.claude.com/docs/en/authentication). Describes OAuth vs API key, authentication precedence, macOS Keychain storage, and that subscription OAuth is the default for Claude Pro, Max, Team, and Enterprise users.
- [Claude Code Legal and compliance](https://code.claude.com/docs/en/legal-and-compliance). Contains the verbatim language: "OAuth authentication is intended exclusively for purchasers of Claude Free, Pro, Max, Team, and Enterprise subscription plans and is designed to support ordinary use of Claude Code and other native Anthropic applications" and "Anthropic does not permit third-party developers to offer Claude.ai login or to route requests through Free, Pro, or Max plan credentials on behalf of their users."
- [Run Claude Code programmatically](https://code.claude.com/docs/en/headless) (formerly "headless mode"). Documents `-p`, `--output-format`, `--json-schema`, streaming, session continuation, and `--bare`. States that `--bare` skips OAuth and keychain reads so auth must come from `ANTHROPIC_API_KEY` or `apiKeyHelper`.
- [Using Claude Code with your Pro or Max plan](https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan). Confirms Pro and Max include Claude Code in the terminal, and warns that if `ANTHROPIC_API_KEY` is set it overrides the subscription.
- [Anthropic Consumer Terms of Service](https://www.anthropic.com/legal/consumer-terms). Section 3 prohibits accessing Services "through automated or non-human means, whether through a bot, script, or otherwise" except when using an Anthropic API key or where otherwise explicitly permitted. Section 2 prohibits account sharing.
- [Anthropic Usage Policy](https://www.anthropic.com/legal/aup). Agentic use cases must still comply with the Usage Policy. No explicit allow or deny on local subprocess invocation.

Third-party reporting on the enforcement story:

- [The Register 2026-04-06: Anthropic closes door on subscription use of OpenClaw](https://www.theregister.com/2026/04/06/anthropic_closes_door_on_subscription/). Quotes Anthropic: "Starting April 4, third-party tools will draw from extra usage instead of subscription limits. Using Claude subscriptions with third-party tools isn't permitted under our Terms of Service." Confirms Claude.ai, Claude Code, and Cowork remain covered by the subscription.
- [TechCrunch 2026-04-04: Claude Code subscribers will need to pay extra for OpenClaw usage](https://techcrunch.com/2026/04/04/anthropic-says-claude-code-subscribers-will-need-to-pay-extra-for-openclaw-support/).
- [VentureBeat: Anthropic cuts off the ability to use Claude subscriptions with OpenClaw and third-party AI agents](https://venturebeat.com/technology/anthropic-cuts-off-the-ability-to-use-claude-subscriptions-with-openclaw-and).
- [The New Stack: Anthropic Agent SDK confusion](https://thenewstack.io/anthropic-agent-sdk-confusion/). Context on the February 2026 documentation update and subsequent Anthropic clarification.
- [OpenClaw.report: Anthropic bans OAuth tokens from consumer plans in third-party tools](https://openclaw.report/ecosystem/anthropic-bans-oauth-tokens-third-party-tools).
- [Winbuzzer 2026-02-19: Anthropic bans Claude subscription OAuth in third-party apps](https://winbuzzer.com/2026/02/19/anthropic-bans-claude-subscription-oauth-in-third-party-apps-xcxwbn/).

Community and feature requests:

- [GitHub issue anthropics/claude-agent-sdk-python#559](https://github.com/anthropics/claude-agent-sdk-python/issues/559). Closed February 16, 2026. Users discovered the `CLAUDE_CODE_OAUTH_TOKEN` env var workaround. No official Anthropic endorsement in the thread.
- [GitHub issue anthropics/claude-code#42106](https://github.com/anthropics/claude-code/issues/42106). Open as of April 2026. Feature request for official personal-use allowance of OAuth tokens with the Agent SDK. No Anthropic response yet.
- [Hacker News discussion](https://news.ycombinator.com/item?id=47118260) on the Agent SDK OAuth ban, with community debate about scope.

Open-source projects that wrap Claude Code:

- [weidwonder/claude_agent_sdk_oauth_demo](https://github.com/weidwonder/claude_agent_sdk_oauth_demo). Shows the `claude setup-token` plus `CLAUDE_CODE_OAUTH_TOKEN` pattern with the Agent SDK. Personal-use demo, not endorsed by Anthropic.
- [rynfar/meridian](https://github.com/rynfar/meridian). Local proxy that translates Anthropic and OpenAI API calls into Agent SDK calls to use a Max subscription across third-party tools. Directly targeted by Anthropic's April 2026 enforcement.
- [LiteLLM: Claude Code Max subscription](https://docs.litellm.ai/docs/tutorials/claude_code_max_subscription). Gateway pattern for forwarding OAuth tokens. Affected by the same enforcement.

Rate limit context:

- [Anthropic: weekly rate limits introduced August 2025](https://www.theregister.com/2026/03/31/anthropic_claude_code_limits/). Context on shared weekly quotas for Claude Pro and Max covering both Claude.ai and Claude Code.
