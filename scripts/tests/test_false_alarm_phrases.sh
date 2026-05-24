#!/usr/bin/env bash
# Verifies feedback_false_alarm_meta_pattern.md contains required lesson keywords.
# exit 0 = all keywords present; exit 1 = one or more missing.

MEMORY_FILE="/Users/torimeyer/.claude/projects/-Users-torimeyer-claude-torios/memory/feedback_false_alarm_meta_pattern.md"

if [ ! -f "$MEMORY_FILE" ]; then
  echo "FAIL: memory file not found at $MEMORY_FILE"
  exit 1
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
