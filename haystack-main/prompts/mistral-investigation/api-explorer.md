# Mistral API Investigation - API Explorer

You are investigating the Mistral API for potential integration into the ostk CPU driver.

## Your Task: Document Mistral API Capabilities

Research and document:
- Available endpoints
- Authentication methods
- Model catalog and capabilities
- Streaming support details
- Tool/function calling support
- Rate limits and quotas
- Context window sizes per model
- API versioning

## Context

Examine the existing driver code at `src/cpu/` and note how it abstracts:
- `anthropic.rs` (reference implementation)
- `gemini.rs` (Google provider)
- `openrouter.rs` (multi-provider proxy)

Focus on what Mistral offers that we don't have yet, and any integration challenges.

## Deliverable

Document findings in `docs/investigations/mistral-api-audit.md`
