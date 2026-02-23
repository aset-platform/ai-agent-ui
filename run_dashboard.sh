#!/bin/bash
# run_dashboard.sh — Launch the AI Stock Analysis Dash web dashboard.
#
# Usage:
#   ./run_dashboard.sh
#
# Prerequisites:
#   - Run from the project root (ai-agent-ui/) or any subdirectory;
#     the script resolves its own location automatically.
#   - demoenv must already exist (cd backend && pip install -r requirements.txt).
#
# The dashboard starts on http://127.0.0.1:8050

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Starting AI Stock Analysis Dashboard..."
echo "Project root : ${SCRIPT_DIR}"

# Activate the project virtualenv
DEMOENV="${SCRIPT_DIR}/backend/demoenv/bin/activate"
if [ ! -f "${DEMOENV}" ]; then
    echo "ERROR: virtualenv not found at ${DEMOENV}"
    echo "Run: cd backend && pip install -r requirements.txt -t demoenv/lib/..."
    exit 1
fi

# shellcheck source=/dev/null
source "${DEMOENV}"

cd "${SCRIPT_DIR}"

echo "Dashboard URL : http://127.0.0.1:8050"
echo "Press Ctrl+C to stop."
echo ""

python dashboard/app.py
