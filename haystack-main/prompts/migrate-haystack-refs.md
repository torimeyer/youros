Replace all os-tack/haystack references with os-tack/haystack in the haystack repo.

Files to update (confirmed by grep):
- docs/draft/responsible-disclosure-anthropic.md
- docs/ostk-walkthrough.md
- docs/insights-session-2026-03-08.md
- docs/spec/KERNEL_UPDATE_PROTOCOL_v1.1.md
- docs/spec/KERNEL_UPDATE_PROTOCOL.md
- docs/spec/trust-chain.md
- docs/spec/tui-console.md
- docs/draft/eject-the-harness.md
- docs/draft/emergent-os.md
- docs/draft/needle-bench-universal.md
- prompts/governance-docs-writer.md
- prompts/trust-chain.md
- .ostk/offers/20260311.md
- .ostk/session-snapshot-2026-03-09.md
- .ostk/tori-boot-v1.0.2.md
- .ostk/verify-1.md
- .github/workflows/bench.yml

For each file: replace all occurrences of:
  os-tack/haystack  →  os-tack/haystack
  os-tack           →  os-tack  (catch-all for org refs)

Also update the git remote:
  git remote set-url origin git@github.com:os-tack/haystack.git

Then: git add -A && git commit -m "chore: migrate repo references to os-tack org"
Report: files changed, lines replaced.
