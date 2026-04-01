#!/usr/bin/env bash
# ------------------------------------------------------------------
# perf-check.sh — Local Lighthouse CI runner (pre-PR gate)
#
# Usage:
#   cd frontend && npm run perf:check
#   # or directly:
#   bash scripts/perf-check.sh
#
# LHCI autorun handles build, server start, audit, and cleanup.
# ------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Set Chrome path for macOS if not already set
if [ -z "${CHROME_PATH:-}" ]; then
  CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  if [ -f "$CHROME" ]; then
    export CHROME_PATH="$CHROME"
  fi
fi

echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}  Lighthouse CI — Performance Check${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

cd "$PROJECT_ROOT"

# LHCI autorun: builds, starts server, runs audits, asserts, uploads
npx @lhci/cli autorun 2>&1
LHCI_EXIT=$?

echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
if [ "$LHCI_EXIT" -eq 0 ]; then
  echo -e "${GREEN}  ✓ All Lighthouse assertions passed!${NC}"
  echo -e "${GREEN}  Safe to raise PR.${NC}"
else
  echo -e "${RED}  ✗ Lighthouse assertions failed!${NC}"
  echo -e "${RED}  Fix performance issues before raising PR.${NC}"
fi
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

exit "$LHCI_EXIT"
