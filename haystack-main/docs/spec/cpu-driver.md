---
status: spec
version: 1
author: scottmeyer + agent
created: 2026-03-16
compounds: smp-architecture, llmos-concurrency, agent-lifecycle
implements: []
---

# CpuDriver Trait — Provider Abstraction

> The kernel talks to one trait. Providers implement it. Tool schemas, streaming, permissions, and cost all flow through the same interface.

---

## 1. CpuDriver Trait

```
trait CpuDriver: Send + Sync {
    /// One-shot: send messages, get back a stream of CpuEvents.
    /// The caller (agent_loop) owns the message history and tool execution.
    /// The driver only translates to/from the provider wire format.
    async fn inference(
        &self,
        request: InferenceRequest,
    ) -> Result<Pin<Box<dyn Stream<Item = CpuEvent>>>, CpuError>;

    /// Provider capabilities (queried once at init, cached by kernel).
    fn capabilities(&self) -> ProviderCaps;

    /// Translate kernel ToolDefs into provider-native JSON.
    /// Called by agent_loop before each inference() call.
    fn encode_tools(&self, tools: &[ToolDef]) -> Vec<serde_json::Value>;

    /// Parse provider-native tool_use blocks into kernel ToolCall structs.
    /// Inverse of encode_tools — normalizes provider response back to kernel types.
    fn decode_tool_calls(&self, raw: &[ContentBlock]) -> Vec<ToolCall>;
}
```

The driver is a **codec**, not a controller. It translates between kernel-native types and provider wire format. The agent_loop owns the turn cycle (inference -> tool execution -> append results -> repeat). The driver never executes tools, never manages history, never decides when to stop.

**InferenceRequest**: model, system prompt, messages (kernel-native), encoded tool defs, max_tokens, provider-specific hints (ExtensionMap).

**CpuEvent**: stays as-is (TextDelta, TextComplete, ToolStart, ToolResult, Usage, TurnComplete, Error) — already provider-agnostic. Providers without SSE emit a single TextComplete. The stream abstraction unifies both.

---

## 2. Provider Capabilities

```
struct ProviderCaps {
    streaming: bool,           tool_calling: bool,
    parallel_tool_calls: bool, structured_output: bool,
    cache_control: bool,       server_tools: Vec<ServerToolKind>,
    max_context_window: u64,   max_output_tokens: u64,
}
enum ServerToolKind { CodeExecution, WebSearch, Grounding }
```

| Capability | Claude | Gemini | OpenAI | Ollama |
|---|---|---|---|---|
| streaming | yes | yes | yes | yes* |
| tool_calling | yes | yes (function calling) | yes (function calling) | partial |
| parallel_tool_calls | no | yes | yes | no |
| structured_output | yes (tool_use) | yes (JSON mode) | yes (response_format) | no |
| cache_control | yes | no | no | n/a |
| server_tools | code_execution | code_execution, grounding | web_search | none |
| max_context | 200k | 1M+ | 128k | model-dependent |

Kernel reads ProviderCaps once at init. FROM auto uses caps for model selection. agent_loop skips unsupported features gracefully. *Ollama streaming is model-dependent; probed at init.

---

## 3. Permission Modes

Controls what the kernel allows at the tool-execution boundary.

```
enum PermissionMode {
    Plan,           // text only — no tools sent to provider
    Governed,       // kernel approves each tool call
    AutoAccept,     // file edits auto; destructive ops gated
    Autonomous,     // full access — bench/CI only
}
```

PermissionMode is NOT a driver concern. agent_loop sets `tool_choice` / omits tools based on mode before calling `driver.inference()`.

| Mode | Tools sent | Kernel behavior on ToolCall |
|---|---|---|
| Plan | none | n/a |
| Governed | all allowed | Queue for operator approval; 60s timeout |
| AutoAccept | all allowed | Read/Glob/Grep: auto. Bash: gated by DestructiveOpsPolicy |
| Autonomous | all allowed | Execute immediately, log to audit |

**Agentfile**: `LIMIT permissions <mode>`. Default: `supervised` (=Governed). `LIMIT destructive_ops confirm|deny|allow` sub-policy for AutoAccept/Autonomous.

**HUMANFILE**: sets the ceiling. Agentfile cannot escalate beyond HUMANFILE. `max_permission: governed` clamps any Agentfile `autonomous` to governed.

---

## 4. Tool Abstraction

### 4.1 Kernel Tools vs Server Tools

```
enum ToolDef {
    Kernel(KernelTool),     // executed locally by the kernel
    Server(ServerToolDef),  // executed server-side by the provider
}

struct KernelTool {
    name: String,           // canonical: Bash, Read, Edit, Write, Glob, Grep
    description: String,
    parameters: JsonSchema,
}

struct ServerToolDef {
    kind: ServerToolKind,   // CodeExecution, WebSearch, Grounding
    provider_config: Value, // provider-specific enable flags
}
```

Kernel tools are translated by `encode_tools()` into provider schema format. Server tools pass through as provider-native config.

### 4.2 Schema Translation

| Field | Anthropic | OpenAI | Gemini |
|---|---|---|---|
| wrapper | `{name, description, input_schema}` | `{type: "function", function: {name, description, parameters}}` | `{function_declarations: [{name, description, parameters}]}` |
| schema key | `input_schema` | `parameters` | `parameters` |
| tool_choice | `{type: "tool", name}` or `"none"` | `{type: "function", function: {name}}` | `{function_calling_config: {mode, ...}}` |

Kernel stores tools in canonical (Anthropic-adjacent) format. Each driver's `encode_tools` translates outbound; `decode_tool_calls` normalizes inbound back to `ToolCall { id, name, input }`.

### 4.3 Tool Aliasing

Kernel tool names have aliases (shell -> Bash, file:read -> Read, etc.). Alias resolution stays in the kernel (`tool_schemas()` in mod.rs) and happens before the driver sees them. The driver always receives canonical names.

---

## 5. Session Management

Sessions stored in `.ostk/sessions/<agent-id>.jsonl` — one JSON object per line. agent_loop owns the message vec and appends per turn.

```
struct SessionEntry {
    role: String,  content: Vec<ContentBlock>,  // kernel-native
    timestamp: String, usage: Option<Usage>, model: Option<String>,
}
```

**Resume**: Load jsonl, deserialize into Vec<Message>, pass to agent_loop. Driver re-encodes.

**Fork**: Copy jsonl to new agent-id. Divergent history from fork point.

**Compaction** (at LIMIT context_pct, default 80%): Summarize old messages into a single block, keep last N tool-use/result pairs verbatim, archive old session as `.pre-compact`. Compaction is an agent_loop concern; driver sees a shorter message list.

---

## 6. Cost Tracking

```
struct CostLedger {
    provider: String,  model: String,
    input_tokens: u64, output_tokens: u64,
    cache_read_tokens: u64, cache_creation_tokens: u64, // Anthropic only
    estimated_cost_usd: f64,
}
```

Drivers report raw token counts via CpuEvent::Usage. Kernel maps to cost:

| Model | Input $/1M | Output $/1M | Cache Read | Cache Write |
|---|---|---|---|---|
| claude-opus-4-6 | $15 | $75 | $1.50 | $18.75 |
| claude-sonnet-4-6 | $3 | $15 | $0.30 | $3.75 |
| gpt-4o | $2.50 | $10 | n/a | n/a |
| gemini-2.0-flash | $0.075 | $0.30 | n/a | n/a |
| ollama/* | $0 | $0 | n/a | n/a |

Pricing table lives in the kernel, not the driver. Updated manually. agent_loop checks cumulative cost against `LIMIT budget_usd` after each turn; halts if exceeded. Cost events written to `.ostk/metrics.jsonl` per-turn; TUI reads for cost gauge.

---

## 7. Abstraction Boundary Summary

```
Agentfile + HUMANFILE
        │
        ▼
   agent_loop  ← owns: history, tool exec, permissions, budget, compaction, session I/O
        │
        │  .inference(request)
        ▼
   CpuDriver   ← owns: HTTP/SSE, auth, request encoding, response parsing, error normalization
        │
        │  HTTPS / gRPC / local socket
        ▼
   Provider
```

The driver is **stateless** between inference() calls. All state lives in agent_loop and the kernel filesystem.
