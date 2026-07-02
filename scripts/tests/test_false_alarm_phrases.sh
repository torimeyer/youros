#!/usr/bin/env bash
# Verifies feedback_false_alarm_meta_pattern.md contains required lesson keywords.
# exit 0 = all keywords present; exit 1 = one or more missing.

# Discover the memory file under the current user's Claude project dir.
# Override with FALSE_ALARM_MEMORY_FILE. Skips cleanly when absent so the
# check is portable across machines (it validates local memory content only).
MEMORY_FILE="${FALSE_ALARM_MEMORY_FILE:-}"
if [ -z "$MEMORY_FILE" ]; then
  MEMORY_FILE=$(ls "$HOME"/.claude/projects/*/memory/feedback_false_alarm_meta_pattern.md 2>/dev/null | head -1)
fi

if [ -z "$MEMORY_FILE" ] || [ ! -f "$MEMORY_FILE" ]; then
  echo "SKIP: feedback_false_alarm_meta_pattern.md not present on this machine"
  exit 0
fi

keywords=(
  "elision"
  "registration lag"
  "transcript_bytes"
  "completed_at"
  "ground truth"
  "independent"
  "no-pager"
  "current_step"
  "two reads"
  "0% CPU"
  "bridge"
)

missing_count=0
for kw in "${keywords[@]}"; do
  if ! grep -qi "$kw" "$MEMORY_FILE"; then
    echo "MISSING: '$kw'"
    missing_count=$((missing_count + 1))
  fi
done

if [ "$missing_count" -gt 0 ]; then
  echo "FAIL: $missing_count required keyword(s) missing from feedback_false_alarm_meta_pattern.md"
  exit 1
fi

echo "PASS: all required keywords present in feedback_false_alarm_meta_pattern.md"
exit 0
