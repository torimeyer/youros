# Mistral API Investigation - Integration Planner

You are creating the integration roadmap for Mistral API support in ostk.

## Your Task: Migration and Rollout Plan

Review:
- `src/cpu/mod.rs` CpuDriver trait
- `src/commands/run.rs` provider resolution
- Agentfile FROM field model selection
- `.ostk/.models` model registry

## Plan the Integration

1. Where Mistral fits in the provider hierarchy
2. Model naming conventions (mistral-large, mistral-small, etc)
3. Provider detection and routing
4. Backward compatibility considerations
5. Feature flags or gradual rollout strategy
6. Testing approach (unit, integration, benchmarks)
7. Documentation updates needed
8. Migration path for existing users

## Deliverable

Create `docs/investigations/mistral-integration-plan.md` with:
- Technical architecture
- Implementation phases
- Risk assessment
- Testing strategy
- Success metrics

## Key Question

Do we want Mistral as first-class (like anthropic/google) or via openrouter?
