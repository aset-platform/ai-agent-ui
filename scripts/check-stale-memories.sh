#!/usr/bin/env bash
# check-stale-memories.sh — CI script to detect stale Serena shared memories.
#
# Scans .serena/memories/shared/*.md files for references to files and
# symbols that no longer exist in the codebase.
#
# Usage:
#   ./scripts/check-stale-memories.sh
#
# Exit codes:
#   0 — always (non-blocking warning)
#
# Designed for CI: runs on PRs that touch backend/, auth/, stocks/,
# dashboard/, or frontend/. Skip for docs-only PRs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MEMORIES_DIR="$PROJECT_ROOT/.serena/memories/shared"

# ANSI colours
if [[ -t 1 ]]; then
    R='\033[0;31m' G='\033[0;32m' Y='\033[1;33m' B='\033[1m' N='\033[0m'
else
    R='' G='' Y='' B='' N=''
fi

if [[ ! -d "$MEMORIES_DIR" ]]; then
    echo -e "${Y}[WARN]${N} No shared memories directory found at $MEMORIES_DIR"
    exit 0
fi

STALE_COUNT=0
CHECKED_COUNT=0

echo -e "${B}Checking shared memories for stale references...${N}"
echo ""

# Find all .md files in shared memories
while IFS= read -r -d '' memory_file; do
    rel_path="${memory_file#$PROJECT_ROOT/}"
    memory_name="${memory_file#$MEMORIES_DIR/}"
    memory_name="${memory_name%.md}"
    issues=""

    # Extract file path references (backtick-quoted paths with extensions)
    while IFS= read -r ref_path; do
        # Skip common non-file references
        [[ "$ref_path" == *"~/"* ]] && continue
        [[ "$ref_path" == *"http"* ]] && continue
        [[ "$ref_path" == *"localhost"* ]] && continue
        [[ "$ref_path" == *"YYYY"* ]] && continue
        [[ "$ref_path" == *"{TICKER}"* ]] && continue
        [[ "$ref_path" == *"example"* ]] && continue
        [[ "$ref_path" == *"path/"* ]] && continue
        [[ "$ref_path" == *"some_"* ]] && continue
        [[ "$ref_path" == *"my_tool"* ]] && continue

        # Check if the file exists relative to project root
        if [[ ! -f "$PROJECT_ROOT/$ref_path" ]] && \
           [[ ! -d "$PROJECT_ROOT/$ref_path" ]]; then
            issues="${issues}\n    Missing: $ref_path"
        fi
    done < <(grep -oE '`[a-zA-Z][a-zA-Z0-9_/.-]+\.(py|tsx?|jsx?|md|sh|yml|yaml|json|toml|cfg)`' "$memory_file" 2>/dev/null | tr -d '`' | sort -u || true)

    CHECKED_COUNT=$((CHECKED_COUNT + 1))

    if [[ -n "$issues" ]]; then
        STALE_COUNT=$((STALE_COUNT + 1))
        echo -e "${Y}[STALE]${N} $memory_name"
        echo -e "$issues"
        echo ""
    fi
done < <(find "$MEMORIES_DIR" -name "*.md" -print0 | sort -z)

# Summary
echo "────────────────────────────────────────"
if [[ $STALE_COUNT -eq 0 ]]; then
    echo -e "${G}All $CHECKED_COUNT shared memories are up to date.${N}"
else
    echo -e "${Y}$STALE_COUNT/$CHECKED_COUNT memories have potentially stale references.${N}"
    echo "Run /check-stale-memories for deeper AI-powered analysis."
fi

# Always exit 0 — this is a non-blocking warning
exit 0
