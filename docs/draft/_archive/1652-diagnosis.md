# →1652 clear_costs_caches teardown hang — diagnosis notes

Fixture: `api/tests/conftest.py:206 clear_costs_caches`
Hang: `tmp_snapshot.parent.rmdir()` or `tmp_snapshot.exists()` blocks on `os.stat()`

## Findings so far

- Python 3.11.15 (cpython-3.11-macos-aarch64-none), venv shared from main repo
- `tempfile.mkdtemp()` → `/var/folders/hx/.../T/tmpXXXXX` (local APFS, should not hang)
- Background refresh thread in `costs.py:_refresh_savings_async` may write to `tmp_snapshot` during teardown
- Race: thread can create/write `tmp_snapshot` AFTER `exists()` returns False but BEFORE `rmdir()`
- Fix: replace fragile unlink+rmdir sequence with `shutil.rmtree(..., ignore_errors=True)` in a daemon thread with join timeout to handle any potential OS-level block
