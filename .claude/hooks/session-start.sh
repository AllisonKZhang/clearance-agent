#!/bin/bash
set -euo pipefail

# Only run in remote (Claude Code on the web) environments
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

echo "Session start hook running..."

# Install dependencies
pip install -r "$CLAUDE_PROJECT_DIR/clearance-agent/requirements.txt" -q

# Start Streamlit app in the background if not already running
if ! pgrep -f "streamlit run app.py" > /dev/null 2>&1; then
  cd "$CLAUDE_PROJECT_DIR/clearance-agent"
  streamlit run app.py --server.port 8501 --server.headless true &
  echo "Streamlit app started on port 8501."
else
  echo "Streamlit app already running."
fi

echo "Session start hook complete."
