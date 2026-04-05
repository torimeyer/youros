---
status: spec
version: 1
author: scottmeyer + agent
created: 2026-03-16
compounds: cpu-driver, smp-architecture
implements: []
---

# API Parity: Anthropic Claude vs Google Gemini

> Reference document for the CpuDriver abstraction layer. Every row is a decision point for the trait interface or its provider implementations.

---

## 1. Messages / Chat API

| Dimension | Claude (Messages API) | Gemini (generateContent) | CpuDriver Abstraction |
|---|---|---|---|
| **Endpoint** | `POST /v1/messages` | `POST /v1/models/{model}:generateContent` | `inference()` method hides endpoint |
| **Roles** | `user`, `assistant` (system is top-level param) | `user`, `model` (systemInstruction is top-level) | Kernel uses `Role::System`, `Role::User`, `Role::Assistant` — driver maps |
| **System prompt** | Top-level `system` field (string or content blocks) | Top-level `system_instruction` field (Content object) | `InferenceRequest.system` — driver places it correctly |
| **Message format** | `{role, content}` where content is string or `ContentBlock[]` | `{role, parts}` where parts is `Part[]` | Kernel `Message` has `role` + `Vec<ContentBlock>` — driver serializes |
| **Content types** | `text`, `image`, `tool_use`, `tool_result`, `thinking` | `text`, `inlineData`, `fileData`, `functionCall`, `functionResponse`, `executableCode`, `codeExecutionResult` | Kernel `ContentBlock` enum covers all; driver maps variants |
| **Alternation rule** | Strict user/assistant alternation required | Strict user/model alternation required | agent_loop enforces alternation before passing to driver |
| **Prefill** | Last message can be `assistant` to constrain output | Not supported natively | Driver for Gemini ignores assistant prefill; Claude driver passes through |
| **Max output tokens** | `max_tokens` (required param) | `maxOutputTokens` in `generationConfig` (optional) | `InferenceRequest.max_tokens` — Claude driver puts at top level, Gemini nests in generationConfig |
| **Stop sequences** | `stop_sequences` array (top-level) | `stopSequences` in `generationConfig` | `InferenceRequest.stop_sequences` — driver places correctly |
| **Stop reason** | `stop_reason`: `end_turn`, `max_tokens`, `stop_sequence`, `tool_use` | `finishReason`: `STOP`, `MAX_TOKENS`, `SAFETY`, `RECITATION` | `CpuEvent::TurnComplete { reason: StopReason }` — driver normalizes enum |
| **Temperature** | `temperature` (top-level, 0.0–1.0) | `temperature` in `generationConfig` (0.0–2.0) | `InferenceRequest.temperature` — driver places and clamps |
| **Top-p** | `top_p` (top-level) | `topP` in `generationConfig` | Same pattern |
| **Top-k** | `top_k` (top-level) | `topK` in `generationConfig` | Same pattern |
| **Usage reporting** | `usage: {input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}` | `usageMetadata: {promptTokenCount, candidatesTokenCount, cachedContentTokenCount, totalTokenCount}` | `CpuEvent::Usage` — normalize to `{input, output, cached_input, cached_write}` |

**Key difference**: Claude nests everything at top level; Gemini nests generation params inside `generationConfig`. The driver handles placement.

---

## 2. Tool / Function Calling

| Dimension | Claude | Gemini | CpuDriver Abstraction |
|---|---|---|---|
| **Tool definition location** | Top-level `tools` array | `tools[].functionDeclarations` array | `encode_tools()` — driver wraps kernel `ToolDef[]` into provider shape |
| **Tool schema** | `{name, description, input_schema}` — JSON Schema | `{name, description, parameters}` — OpenAPI-compatible JSON Schema | Kernel `ToolDef` uses JSON Schema; Claude driver renames to `input_schema`, Gemini to `parameters` |
| **Strict mode** | `strict: true` on tool def — constrains token generation to match schema exactly | No equivalent (uses response_schema for structured output, but not on function defs) | `ToolDef.strict` — Claude driver passes through, Gemini driver ignores |
| **Tool choice** | `tool_choice: {type: "auto"|"any"|"tool"|"none"}` | `toolConfig.functionCallingConfig.mode: "AUTO"|"ANY"|"NONE"` + `allowedFunctionNames` | `InferenceRequest.tool_choice` — driver maps enum values |
| **Force specific tool** | `tool_choice: {type: "tool", name: "foo"}` | `mode: "ANY", allowedFunctionNames: ["foo"]` | `ToolChoice::Specific(name)` — driver translates |
| **Parallel calls** | Supported by default; `disable_parallel_tool_use: true` to disable | Supported by default; no explicit disable flag | `InferenceRequest.disable_parallel_tool_use` — Claude passes through; Gemini driver ignores (always parallel-capable) |
| **Tool call format (response)** | `{type: "tool_use", id, name, input}` content block | `{functionCall: {name, args}}` inside parts | `decode_tool_calls()` — normalizes to `ToolCall{id, name, arguments}` |
| **Tool call ID** | Explicit `id` field on `tool_use` block | **No explicit ID** — matched by name/position | **Critical**: CpuDriver must generate synthetic IDs for Gemini tool calls. Kernel always uses IDs for correlation. |
| **Tool result format** | `{type: "tool_result", tool_use_id, content, is_error}` in user message | `{functionResponse: {name, response}}` inside parts of user message | `encode_tool_results()` — driver maps kernel `ToolResult` |
| **Error signaling (`is_error`)** | **Yes** — `is_error: true` on tool_result block | **No dedicated field** — convention is to include error info in the response object | **Critical**: Claude has explicit `is_error`. Gemini requires encoding error state inside the response JSON (e.g., `{error: "..."}`) — driver must handle both directions. |
| **Tool result content types** | String or `ContentBlock[]` (text, images) | JSON object (Gemini 3+: can include multimodal parts) | Kernel `ToolResult` holds `Vec<ContentBlock>` — Claude driver passes directly, Gemini driver serializes to JSON response object |
| **Cache control on tools** | `cache_control` field on tool defs and tool results | Via explicit context caching (separate API call) | Claude driver adds `cache_control` breakpoints; Gemini driver uses cachedContent references |
| **Thought signatures** | Not applicable | `thought_signature` on `functionCall` parts (Gemini 3+) — must be preserved round-trip | Gemini driver must preserve opaque `thought_signature` fields; kernel treats as pass-through metadata |

---

## 3. Streaming

| Dimension | Claude | Gemini | CpuDriver Abstraction |
|---|---|---|---|
| **Endpoint** | Same endpoint, `stream: true` in body | Separate endpoint: `streamGenerateContent?alt=sse` | `inference()` always returns `Stream<CpuEvent>` |
| **Transport** | Server-Sent Events (SSE) | Server-Sent Events (SSE) with `alt=sse` | Both SSE — driver parses provider-specific event shapes |
| **Event types** | `message_start`, `content_block_start`, `content_block_delta`, `content_block_stop`, `message_delta`, `message_stop`, `ping`, `error` | `data:` JSON lines, each a partial `GenerateContentResponse` | Driver maps to `CpuEvent` variants |
| **Text deltas** | `content_block_delta` with `text_delta` type | Each SSE chunk has `candidates[0].content.parts[0].text` (cumulative or delta depending on SDK) | `CpuEvent::TextDelta(text)` |
| **Tool call streaming** | `content_block_start` (type: tool_use, id, name) then `content_block_delta` with `input_json_delta` | Tool calls arrive as complete `functionCall` parts in chunks — no incremental JSON streaming | **Asymmetry**: Claude streams tool call JSON incrementally; Gemini delivers complete functionCalls. Driver must buffer Gemini tool calls and emit ToolStart + ToolResult atomically. |
| **Multiple content blocks** | Each block has `index` field for ordering | Parts array ordering within each chunk | Driver uses index (Claude) or array position (Gemini) to correlate |
| **Usage in stream** | `message_delta` event contains final `usage` | Final chunk contains `usageMetadata` | Emit `CpuEvent::Usage` on stream end |
| **Error in stream** | `error` event after 200 response | HTTP error or `finishReason: SAFETY/RECITATION` | `CpuEvent::Error` — driver must detect both HTTP-level and in-stream errors |
| **Ping/keepalive** | `ping` events | No explicit ping | Driver ignores pings; kernel timeout handles stalls |

---

## 4. Structured Output

| Dimension | Claude | Gemini | CpuDriver Abstraction |
|---|---|---|---|
| **JSON mode** | Via `tool_use` pattern (define a tool that returns JSON) or `output_config: {format: "json_schema", schema: ...}` | `responseMimeType: "application/json"` in generationConfig | `InferenceRequest.structured_output` — driver picks mechanism |
| **Schema enforcement** | `output_config.json_schema` — constrained decoding at token level (beta) | `responseSchema` + `responseMimeType: "application/json"` — also constrained decoding | Both support grammar-constrained generation. Driver passes schema through. |
| **Schema language** | JSON Schema | JSON Schema (OpenAPI-compatible subset) | Kernel `ToolDef` and `OutputSchema` use JSON Schema; Gemini driver may need to strip unsupported keywords |
| **Strict tool schemas** | `strict: true` on tool definition — guarantees valid args | No equivalent on function declarations | `ToolDef.strict` — only meaningful for Claude |
| **Combining with tools** | Structured output works alongside tool use | **Conflict**: `response_mime_type: "application/json"` fails when function calling is active on Gemini 2.5 models | **Critical**: Gemini cannot do structured output + function calling simultaneously on some models. CpuDriver must not set responseSchema when tools are active. |
| **Enum mode** | Via schema with `enum` keyword | `responseMimeType: "text/x.enum"` + `responseSchema` | Driver maps enum schemas to provider-specific format |

---

## 5. Context Management

| Dimension | Claude | Gemini | CpuDriver Abstraction |
|---|---|---|---|
| **Prompt caching** | Inline `cache_control: {type: "ephemeral"}` breakpoints on content blocks. Auto-caching also available. Write: 1.25x (5min) or 2x (1hr). Read: 0.1x. | **Explicit**: Separate `cachedContents.create` API call, returns `cachedContent` reference. **Implicit**: Automatic on 2.5+ (min 1024–2048 tokens). Read: 0.1x (2.5+). | **Divergent models**: Claude caching is inline per-request. Gemini explicit caching is a separate lifecycle (create → reference → expire). CpuDriver needs `CacheHandle` abstraction for Gemini; Claude driver uses inline markers. |
| **Cache TTL** | 5 minutes (default) or 1 hour (explicit) | Configurable TTL on creation (default 1 hour, minimum 1 minute) | Driver manages TTL; kernel specifies intent (`CachePolicy::Ephemeral` vs `CachePolicy::Persistent`) |
| **Cache isolation** | Per-workspace (since Feb 2026) | Per-project | N/A — infrastructure concern |
| **Compaction** | **Yes** — beta `compact-2026-01-12`. Server-side context summarization. Configure trigger threshold (default 100K tokens). API auto-summarizes and replaces old messages with compaction block. | **No native API compaction**. ADK (Agent Development Kit) has client-side compaction via sliding window summarization. | **Critical asymmetry**: Claude has server-side compaction. Gemini requires client-side implementation. Kernel must implement compaction fallback in agent_loop for non-Claude providers: detect threshold → call provider for summarization → replace messages. |
| **Compaction trigger** | `context_management.edits` with `compact_20260112` strategy, configurable `trigger_tokens` | N/A (ADK uses event count, not tokens) | Kernel `CompactionPolicy { trigger_tokens, strategy }` — Claude driver delegates to API; others run kernel-side |
| **Context window** | 200K standard, 1M GA (Opus 4.6, Sonnet 4.6) | 1M standard (all current models) | `ProviderCaps.max_context_window` — queried at init |
| **Long context pricing** | 2x rates above 200K tokens | Standard rates up to 1M | Cost estimation in kernel should account for Claude's threshold |
| **Token counting** | `POST /v1/messages/count_tokens` endpoint | `POST /v1/models/{model}:countTokens` endpoint | `count_tokens()` method on CpuDriver — used before compaction decisions |

---

## 6. Code Execution

| Dimension | Claude | Gemini | CpuDriver Abstraction |
|---|---|---|---|
| **Server-side sandbox** | **Yes** — Programmatic Tool Calling (managed Python sandbox). Free when used with web search. | **Yes** — `code_execution` tool. Python sandbox with Matplotlib support. | `ServerToolKind::CodeExecution` in ProviderCaps |
| **Language** | Python (opinionated environment) | Python only | Both Python-only |
| **File I/O in sandbox** | Limited — sandbox has filesystem isolation | Gemini 2.0+: file input into sandbox, chart/graph output | Capability difference; kernel can probe via caps |
| **How to enable** | Anthropic-defined tool type: `code_execution` or programmatic calling beta | `tools: [{codeExecution: {}}]` in request | `InferenceRequest.server_tools` list — driver maps to provider format |
| **Result format** | Tool result content blocks | `executableCode` + `codeExecutionResult` part types in response | Driver normalizes to `CpuEvent::CodeExecution { code, output, error }` |
| **Combining with user tools** | Yes — works alongside custom tools | Yes — can combine with function calling and google_search | Both support multi-tool; no driver-level concern |

---

## 7. Vision (Image Input)

| Dimension | Claude | Gemini | CpuDriver Abstraction |
|---|---|---|---|
| **Base64 inline** | `{type: "image", source: {type: "base64", media_type, data}}` | `{inlineData: {mimeType, data}}` inside parts | Kernel `ContentBlock::Image { data, media_type }` — driver wraps |
| **URL reference** | `{type: "image", source: {type: "url", url}}` | `{fileData: {mimeType, fileUri}}` (must be GCS URI or uploaded via Files API) | **Asymmetry**: Claude accepts arbitrary URLs; Gemini requires GCS or Files API upload. Driver may need to download + inline for Gemini. |
| **Files API** | Yes — upload once, reference by `file_id` | Yes — upload via Files API, reference by `fileUri` | `FileHandle` abstraction; driver maps to provider ref format |
| **Supported formats** | JPEG, PNG, GIF, WebP | JPEG, PNG, GIF, WebP, BMP | Superset is fine; driver passes through |
| **Max images** | 600 per request (100 for 200K models) | Limited by total request size (20MB inline) | Kernel respects `ProviderCaps.max_images_per_request` |
| **Video** | Not supported | Supported (MP4, etc.) via Files API | `ProviderCaps.video_input: bool` |
| **Audio** | Supported (MP3, WAV, etc.) | Supported (MP3, WAV, FLAC, etc.) | Both support; driver handles format mapping |
| **PDF** | Supported via vision (rendered pages) or Files API | Supported — up to 1000 pages with multimodal understanding | Both support; no special handling needed |

---

## 8. Grounding / Web Search

| Dimension | Claude | Gemini | CpuDriver Abstraction |
|---|---|---|---|
| **Built-in web search** | **Yes** — `web_search_20260209` server tool. Brave Search backend. $10/1K searches + token costs. Dynamic filtering (Python post-processing of results). | **Yes** — `google_search` built-in tool. Free with API usage (subject to Gemini pricing). Grounding with Google Search provides cited results. | `ServerToolKind::WebSearch` — both support; pricing differs |
| **URL context** | **Yes** — `web_fetch` server tool (fetches and processes URL content) | **Yes** — `url_context` built-in tool (extracts content from URLs for context) | `ServerToolKind::UrlContext` — similar capability |
| **Search result format** | Tool result content blocks with citations | `groundingMetadata` with `searchEntryPoint`, `groundingChunks`, `groundingSupports` | Driver normalizes grounding results into kernel `GroundingResult { sources, citations }` |
| **Grounding metadata** | Citations in tool result text | Structured `groundingMetadata` on response candidates | Gemini provides richer grounding metadata; driver extracts and normalizes |
| **Combining with other tools** | Yes — web search + custom tools in same request | Yes — google_search + code_execution + url_context + custom tools | Both support multi-tool; no special handling |

---

## 9. Models Available (March 2026)

### Claude (Anthropic)

| Model | ID | Context | Max Output | Input $/1M | Output $/1M | Notes |
|---|---|---|---|---|---|---|
| **Opus 4.6** | `claude-opus-4-6-20260312` | 1M | 128K | $5.00 | $25.00 | Flagship. Fast mode available (6x price). |
| **Sonnet 4.6** | `claude-sonnet-4-6-20260312` | 1M | 128K | $3.00 | $15.00 | General purpose. |
| **Haiku 4.5** | `claude-haiku-4-5-20250918` | 200K | 8K | $1.00 | $5.00 | Cost-optimized, fast. |

Long context surcharge: 2x rates above 200K tokens.

### Gemini (Google)

| Model | ID | Context | Max Output | Input $/1M | Output $/1M | Notes |
|---|---|---|---|---|---|---|
| **3.1 Pro Preview** | `gemini-3.1-pro-preview` | 1M | 64K | $2.00 | $12.00 | Latest flagship. |
| **3 Flash** | `gemini-3-flash` | 1M | 64K | $0.50 | $3.00 | Fast, capable. |
| **2.5 Pro** | `gemini-2.5-pro` | 1M | 64K | $1.25 | $10.00 | Stable production. |
| **2.5 Flash** | `gemini-2.5-flash` | 1M | 64K | $0.30 | $2.50 | Budget-friendly. |
| **3.1 Flash-Lite** | `gemini-3.1-flash-lite` | 1M | 64K | $0.10 | $0.40 | Cheapest option. |

Free tier available: 5–15 RPM, 100–1000 RPD depending on model.

### Price Comparison (per 1M tokens)

| Tier | Claude | Gemini | Ratio |
|---|---|---|---|
| Flagship | $5/$25 (Opus 4.6) | $2/$12 (3.1 Pro) | Gemini ~2.1x cheaper |
| Mid-tier | $3/$15 (Sonnet 4.6) | $1.25/$10 (2.5 Pro) | Gemini ~1.5x cheaper |
| Budget | $1/$5 (Haiku 4.5) | $0.10/$0.40 (3.1 Flash-Lite) | Gemini ~10x cheaper |

---

## 10. Rate Limits

| Dimension | Claude | Gemini | CpuDriver Abstraction |
|---|---|---|---|
| **Algorithm** | Token bucket (continuous replenishment) | Fixed window (resets at intervals) | Driver exposes `RateLimitInfo` on 429 responses |
| **Dimensions** | RPM, ITPM, OTPM (per model class) | RPM, TPM, RPD, IPM (per model, per project) | Kernel tracks RPM + TPM; driver reports limits from response headers |
| **Tier 1** | ~50 RPM, 30K ITPM (after $5 credit) | 150–300 RPM, 250K TPM | Gemini much more generous at entry tier |
| **Tier 2** | ~40K ITPM, 8K OTPM | 1000+ RPM (after $250 spend) | — |
| **Free tier** | None | 5–15 RPM, 100–1000 RPD | Gemini has free tier; Claude does not |
| **Cache benefit** | Uncached tokens only count toward ITPM | Cached tokens count at reduced rate | Driver should report effective vs raw token counts |
| **Rate limit headers** | `retry-after` header on 429 | `retry-after` header on 429 | Driver parses and exposes `retry_after_ms` in error |
| **Error code** | HTTP 429 | HTTP 429 | Both standard; kernel retry logic is provider-agnostic |
| **Batch API** | Yes — 50% discount, async | Yes — 50% discount, 24hr window | `InferenceRequest.priority: Batch` — driver routes to batch endpoint |

---

## 11. Native / Built-in Tools

| Tool | Claude | Gemini | CpuDriver |
|---|---|---|---|
| **Web search** | `web_search_20260209` (Brave backend, $10/1K) | `google_search` (included in API pricing) | `ServerToolKind::WebSearch` |
| **URL fetch/context** | `web_fetch` server tool | `url_context` built-in tool | `ServerToolKind::UrlContext` |
| **Code execution** | Programmatic tool calling (Python sandbox) | `code_execution` (Python sandbox + Matplotlib) | `ServerToolKind::CodeExecution` |
| **Text editor** | `text_editor_20250124` (file editing) | Not available | Claude-specific; kernel can offer via custom tool for Gemini |
| **Computer use** | `computer_20250124` (GUI automation, beta) | `computer_use` (Gemini 3, experimental) | `ServerToolKind::ComputerUse` — both experimental |
| **File search** | Not built-in (use RAG) | Not built-in natively (use Vertex AI RAG) | N/A — kernel provides via custom tools |
| **Google Maps** | Not available | `google_maps` built-in tool | Gemini-specific |
| **Skills (Office docs)** | PowerPoint, Excel, Word, PDF skills (managed) | Not available | Claude-specific |

---

## 12. Fast / Speed Modes

| Dimension | Claude | Gemini | CpuDriver Abstraction |
|---|---|---|---|
| **Fast mode** | `speed: "fast"` on Opus 4.6 only. 2.5x faster, 6x price ($30/$150 per 1M). | No direct equivalent. Use Flash-Lite for speed ($0.10/$0.40). | `InferenceRequest.speed` — Claude driver maps to `speed` param; Gemini driver selects faster model variant |
| **Thinking control** | Extended thinking with configurable budget (Opus/Sonnet) | `thinking_level: "minimal"|"low"|"medium"|"high"` (Gemini 3) or `thinking_budget` (Gemini 2.5) | `InferenceRequest.thinking_budget` — driver maps to provider param |
| **Mechanism** | Same model, different serving priority | Different model tiers (Flash, Flash-Lite) or thinking level adjustment | Fundamental difference: Claude fast mode = same model faster; Gemini = pick a lighter model or reduce thinking |
| **Batch API** | 50% discount, async processing, not compatible with fast mode | 50% discount, 24hr delivery window | `InferenceRequest.priority: Batch` |

---

## 13. Critical Parity Gaps for CpuDriver

### Claude has, Gemini lacks:

| Feature | Impact on CpuDriver | Mitigation |
|---|---|---|
| **`is_error` on tool results** | Gemini has no explicit error signaling on function responses | Gemini driver encodes errors as `{error: true, message: "..."}` in the response JSON. Kernel ToolResult always has `is_error`; driver translates. |
| **Server-side compaction** | Gemini has no API-level compaction | Kernel implements compaction fallback: detect threshold → generate summary via inference call → replace old messages. Cost: 1 extra inference per compaction. |
| **Inline prompt caching** | Gemini uses separate API for explicit caching; implicit caching is automatic but not controllable | Gemini driver manages `cachedContent` lifecycle. Kernel `CachePolicy` maps to: Claude=inline markers, Gemini=create/reference cached content. |
| **Tool call IDs** | Gemini functionCall has no explicit ID | Gemini driver generates deterministic IDs (e.g., hash of name + position + turn index). Kernel always correlates by ID. |
| **Streaming tool call JSON** | Gemini delivers functionCalls atomically, not incrementally | Not a problem — driver buffers and emits complete ToolStart. Claude's incremental streaming is a bonus, not a requirement. |
| **`strict: true` on tools** | Gemini has no strict mode for function declarations | Gemini driver drops `strict` flag. Schema compliance is best-effort on Gemini. |
| **Assistant prefill** | Gemini doesn't support partial assistant messages | Gemini driver strips prefill. Claude driver passes through. |
| **Fast mode (same model)** | Gemini has no equivalent priority serving | Gemini driver maps `speed: "fast"` to model swap (e.g., Flash instead of Pro). Different tradeoff: quality may change. |

### Gemini has, Claude lacks:

| Feature | Impact on CpuDriver | Mitigation |
|---|---|---|
| **Google Search grounding (free)** | Claude web search costs $10/1K searches | Cost management in kernel; prefer Gemini for search-heavy workloads |
| **URL context tool** | Claude has web_fetch but as a separate tool | Functionally equivalent; both available via `ServerToolKind::UrlContext` |
| **Video input** | Claude does not support video | `ProviderCaps.video_input` — kernel skips video for Claude |
| **Free tier** | Claude has no free tier | Gemini useful for development/testing without spend |
| **Thinking level control** | Claude has thinking budget but no named levels | Driver can map named levels to Claude's token budget: minimal=1K, low=4K, medium=16K, high=64K |
| **Structured output + function calling** | Works on Claude | **Breaks on Gemini 2.5** — structured output conflicts with function calling | Kernel must not request both simultaneously on Gemini. When tools are active, structured output goes through tool_use pattern instead. |
| **Google Maps tool** | Not available on Claude | Gemini-specific; kernel offers as optional server tool |

---

## 14. Wire Format Quick Reference

### Claude: Tool Call + Result Cycle

```json
// Assistant response (tool_use)
{
  "role": "assistant",
  "content": [
    {"type": "text", "text": "Let me check..."},
    {"type": "tool_use", "id": "toolu_01A", "name": "get_weather", "input": {"city": "SF"}}
  ]
}

// User message (tool_result)
{
  "role": "user",
  "content": [
    {"type": "tool_result", "tool_use_id": "toolu_01A", "content": "72°F sunny", "is_error": false}
  ]
}
```

### Gemini: Function Call + Response Cycle

```json
// Model response (functionCall)
{
  "role": "model",
  "parts": [
    {"text": "Let me check..."},
    {"functionCall": {"name": "get_weather", "args": {"city": "SF"}}}
  ]
}

// User message (functionResponse)
{
  "role": "user",
  "parts": [
    {"functionResponse": {"name": "get_weather", "response": {"temperature": "72°F", "condition": "sunny"}}}
  ]
}
```

### Key Structural Differences

| Aspect | Claude | Gemini |
|---|---|---|
| Tool call ID | Explicit `id` field | None — match by name |
| Error on result | `is_error: true/false` | Encode in response JSON |
| Result content | String or ContentBlock[] | JSON object |
| Schema field name | `input_schema` | `parameters` |
| Role name | `assistant` | `model` |
| System prompt | `system` | `system_instruction` |
| Output config | `max_tokens` (top-level) | `generationConfig.maxOutputTokens` |

---

## 15. Recommendations for CpuDriver Implementation

1. **`encode_tools()` / `decode_tool_calls()`** must handle schema field naming (`input_schema` vs `parameters`), tool call ID generation (Gemini), and `is_error` translation.

2. **Compaction must live in kernel**, not driver. Claude's server-side compaction is an optimization; the kernel needs its own compaction loop as the fallback for all other providers.

3. **Prompt caching diverges fundamentally**. Don't try to unify — let each driver manage caching in its own way. Kernel expresses intent (`CachePolicy`); driver implements mechanism.

4. **Structured output + tools conflict on Gemini** must be handled at the driver level. When tools are active, Gemini driver must not set `responseMimeType`/`responseSchema`.

5. **`ProviderCaps`** should be extended with: `compaction: bool`, `prompt_caching: CacheModel` (Inline vs Explicit vs Implicit), `fast_mode: bool`, `video_input: bool`, `thinking_control: ThinkingControl` (Budget vs Level vs None).

6. **Cost model** belongs in a separate `CostEstimator` trait, not in the driver. Pricing differences (Claude's 200K surcharge, Gemini's free tier, fast mode multipliers) are too varied for a single abstraction.

---

## Sources

- [Claude Messages API](https://platform.claude.com/docs/en/api/messages)
- [Claude Tool Use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use)
- [Claude Streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)
- [Claude Structured Outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- [Claude Prompt Caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Claude Compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)
- [Claude Vision](https://platform.claude.com/docs/en/build-with-claude/vision)
- [Claude Fast Mode](https://platform.claude.com/docs/en/build-with-claude/fast-mode)
- [Claude Rate Limits](https://platform.claude.com/docs/en/api/rate-limits)
- [Claude Pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [Claude Models Overview](https://platform.claude.com/docs/en/about-claude/models/overview)
- [Gemini API Text Generation](https://ai.google.dev/gemini-api/docs/text-generation)
- [Gemini Function Calling](https://ai.google.dev/gemini-api/docs/function-calling)
- [Gemini Structured Output](https://ai.google.dev/gemini-api/docs/structured-output)
- [Gemini Context Caching](https://ai.google.dev/gemini-api/docs/caching)
- [Gemini Code Execution](https://ai.google.dev/gemini-api/docs/code-execution)
- [Gemini Google Search Grounding](https://ai.google.dev/gemini-api/docs/google-search)
- [Gemini URL Context](https://ai.google.dev/gemini-api/docs/url-context)
- [Gemini Image Understanding](https://ai.google.dev/gemini-api/docs/image-understanding)
- [Gemini Models](https://ai.google.dev/gemini-api/docs/models)
- [Gemini Rate Limits](https://ai.google.dev/gemini-api/docs/rate-limits)
- [Gemini Pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini Developer Guide](https://ai.google.dev/gemini-api/docs/gemini-3)
