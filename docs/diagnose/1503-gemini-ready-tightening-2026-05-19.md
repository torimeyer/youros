# →1503 gemini-ready: tighten readiness checks

**Status:** IN PROGRESS

## Summary

Tightening `api/services/gemini_ready.py` from 6 to 9 checks.
Adding `in_repo_scope`, `ac_count_threshold`, `referenced_files_exist`.
Removing early-return logic — all checks always evaluated.
Adding GeminiReadyChip tooltip for all checks.

_(results to be filled in after implementation)_
