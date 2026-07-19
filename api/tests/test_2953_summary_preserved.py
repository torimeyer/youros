"""Tests for →2953: an agent that reports finishing while its process is
still alive must not lose its final summary.

When POST /agents/{name}/complete arrives while the spawn PID is still
alive, mark_agent_complete defers the completion to protect busy agents.
Before the fix it returned without persisting body.summary, so when the
process later exited the PID-exit reconciler flipped the row to completed
with no summary. The fix parks the posted summary as pending_summary on
the row and _set_agent_status attaches it on the completed flip; a newer
/complete summary wins over the parked one.

Test cases (added in the RED step of this task):
  (a) deferred /complete retains the summary as pending
  (b) the reconciler's later completed flip attaches the pending summary
  (c) a second /complete with a newer summary wins over the parked one
  (d) the immediate-completion path still persists the summary as before
"""
