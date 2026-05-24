# →1564 Clarity-1: Readiness rubric reduction

Reduce `api/services/gemini_ready.py` readiness checks:
- Tasks: keep only 3 checks (outcome_concrete, in_repo_scope, is_unblocked)
- Specs: keep only 5 checks (has_ac_checkboxes, no_vague_ac, has_file_paths, referenced_files_exist, in_repo_scope)
- Add `outcome_concrete` check (runs _VAGUE_TOKENS_RE over title + description)
- Narrow `_VAGUE_TOKENS_RE`: drop false-positive tokens (either, consider, explore, review, depends)
- Keep dropped check functions in file for one release (backwards compat)
