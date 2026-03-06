#!/bin/bash
set -euo pipefail

# Only run in remote (Claude Code on the web) environments
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

echo "Session start hook running..."

pip install -r "$CLAUDE_PROJECT_DIR/clearance-agent/requirements.txt"

echo "Session start hook complete."
