Complete the v1.3 tag ceremony.

Pre-checks:
1. cargo test -- confirm all suites pass (0 failures)
2. git status -- confirm everything staged
3. ostk --version must show 1.3.0

Steps:
1. git add -A
2. git commit -m "feat: v1.3.0 — ostk binary name, harness detection, boot orientation fix, operator-boot spec, trust chain spec, integration test fixes, doctest ignore"
3. git tag v1.3.0
4. Report: tag created, test count, release summary

Do NOT push. Operator will review and push manually.
