#!/bin/bash
set -euo pipefail

# Only run in remote (Claude Code on the web) environments
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

echo "Session start hook running..."

# No dependencies to install yet.
# When you add a package manager to your project, install dependencies here.
# Examples:
#   npm install
#   pip install -r requirements.txt
#   bundle install
#   cargo fetch

echo "Session start hook complete."
