#!/bin/bash
# UserPromptSubmit: Rule 4 from 2026-04-27 retro.
# Emits the data-shape meta-rule. Also baked into the standing-rules numbered list.
#
# Tori-personal gate: this is a tori-personal retro rule. No-op unless
# ~/.myos/config.json has "enable_tori_rules": true.
TORI_CONFIG="${MYOS_CONFIG_PATH:-$HOME/.myos/config.json}"
if [ ! -f "$TORI_CONFIG" ] || ! grep -q '"enable_tori_rules"[[:space:]]*:[[:space:]]*true' "$TORI_CONFIG" 2>/dev/null; then
    exit 0
fi

echo ""
echo "DATA-SHAPE META-RULE (rule 4 from 2026-04-27 retro):"
echo "  If iterating over N things is slow, ask why there are N first. Reduce N before optimizing the loop."
exit 0
