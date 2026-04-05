You are a coordinator running a POST battery test against all available OpenRouter models.

**Goal:** For every model available on OpenRouter, run `ostk boot` and record whether the model can correctly interpret and respond to the llmOS boot sequence. Output results to `bench/results/post-battery-<date>.jsonl`.

**What POST means here:**
A model passes POST if, given the ostk boot context (boot.md), it:
1. Acknowledges it is an llmOS agent
2. Reports the OS state accurately
3. Uses tack grammar in its response (`:boot complete` or similar)
4. Does NOT hallucinate needles, commits, or agent states not in boot.md

**Steps:**

1. Get OpenRouter model list:
   ```
   curl -s https://openrouter.ai/api/v1/models \
     -H "Authorization: Bearer $OPENROUTER_API_KEY" \
     | jq -r '.data[].id' | head -50
   ```

2. For each model, run a single-turn POST check:
   - System prompt: contents of .ostk/boot.md
   - User message: "ostk boot"
   - Max tokens: 512
   - Record: model_id, pass/fail, response_preview (first 200 chars), latency_ms

3. Pass criteria:
   - Response contains one of: `:boot`, `boot:`, `boot complete`, `ostk`, `→` (needle sigil), `[procs]`, `[ctx]`
   - Response does NOT start with "I cannot", "As an AI", "I don't"
   - Response is under 400 tokens (no hallucination bloat)

4. Write results to bench/results/post-battery-<timestamp>.jsonl:
   ```json
   {"model": "anthropic/claude-sonnet-4-6", "pass": true, "latency_ms": 1240, "preview": ":boot complete..."}
   {"model": "google/gemini-2.0-flash", "pass": true, "latency_ms": 890, "preview": "boot: 0.87..."}
   {"model": "meta-llama/llama-3.3-70b", "pass": false, "latency_ms": 2100, "preview": "I don't have access..."}
   ```

5. Print summary table at end: model | pass/fail | latency

**Constraints:**
- Use OPENROUTER_API_KEY env var
- Skip models that fail to respond within 10s
- Max 50 models per run (most capable first)
- Rate limit: 1 request/second
- The coordinator DOES NOT use ostk run for sub-agents — it calls OpenRouter API directly for each test

**If rate limited or spend limit hit:**
Run: `ostk nudge push operator "POST battery: rate limited / spend limit hit on OpenRouter — bump limit to continue. Completed N/50 models so far. Results in bench/results/post-battery-*.jsonl"`
Then pause and wait. Do NOT retry automatically.

**If OPENROUTER_API_KEY not set:**
Run: `ostk nudge push operator "POST battery: OPENROUTER_API_KEY not set — add key to vault to run battery"`
Then exit cleanly.
