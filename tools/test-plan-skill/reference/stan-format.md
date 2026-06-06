# STAN Test Plan Format Reference

The Test Plan Executor expects a markdown file containing one or more "Behavior Tables".

## Table Structure
A behavior table is identified by the header row containing "Behavior" and "Coverage".

| Behavior | Coverage | Evidence |
| :--- | :--- | :--- |
| User can login with SSO | Manual | [Link](...) |
| Dashboard renders 5 widgets | Untested | |
| API returns 403 on invalid token | Automated | `tests/test_auth.py` |

## Valid Coverage Values
- **Manual**: Requires Playwright drive.
- **Untested**: Requires Playwright drive (treated as Manual).
- **Automated**: Linked to an existing test (skipped by this executor).
- **Unimplemented**: Behavioral goal not yet coded (skipped with warning).
- **Un-Architected**: Future design goal (skipped with warning).

## rich Coverage Columns (Advanced)
Some tables use regional coverage columns. The parser aggregates these.

| Behavior | Pre-Deploy (US) | Pre-Deploy (EU) |
| :--- | :--- | :--- |
| Login works | Manual | Manual |

If any column in the row is marked "Manual" or "Untested", the executor will prioritize it for a drive run.
