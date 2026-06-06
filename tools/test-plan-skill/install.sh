#!/bin/bash
# Install script for test-plan skill

SKILL_DIR="${HOME}/.claude/skills/test-plan"
mkdir -p "$SKILL_DIR/scripts" "$SKILL_DIR/reference"

echo "Installing test-plan skill..."

# Copy files
cp tools/test-plan-skill/SKILL.md "$SKILL_DIR/"
cp tools/test-plan-skill/scripts/parse-stan.ts "$SKILL_DIR/scripts/"
cp tools/test-plan-skill/reference/stan-format.md "$SKILL_DIR/reference/"

# Check prerequisites
if ! command -v playwright-cli &> /dev/null; then
    echo "Warning: playwright-cli not found."
    echo "Run: npm install -g @playwright/cli@latest && playwright-cli install --skills"
fi

echo "Done. test-plan skill is now available in $SKILL_DIR"
