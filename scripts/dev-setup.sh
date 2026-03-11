#!/usr/bin/env bash
# dev-setup.sh — AI tooling setup for new developers.
#
# Verifies Claude Code + Serena are configured, validates shared
# memories, creates local memory directories, and runs verification.
#
# Prerequisites: Run ./setup.sh first (Python, Node, env files).
#
# Usage:
#   ./scripts/dev-setup.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ANSI colours
if [[ -t 1 ]]; then
    R='\033[0;31m' G='\033[0;32m' Y='\033[1;33m'
    C='\033[0;36m' B='\033[1m' N='\033[0m'
else
    R='' G='' Y='' C='' B='' N=''
fi

ok()   { echo -e "  ${G}[OK]${N} $1"; }
warn() { echo -e "  ${Y}[WARN]${N} $1"; }
fail() { echo -e "  ${R}[FAIL]${N} $1"; exit 1; }
info() { echo -e "  ${C}[INFO]${N} $1"; }
step() {
    echo ""
    echo -e "${B}[$1]${N} $2"
    echo "────────────────────────────────────────────────────────────"
}

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║        AI Agent UI — Developer AI Tooling Setup           ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

PASS=0
TOTAL=0

_check() {
    local label="$1" condition="$2"
    TOTAL=$((TOTAL + 1))
    if eval "$condition" &>/dev/null; then
        ok "$label"
        PASS=$((PASS + 1))
        return 0
    else
        warn "$label"
        return 1
    fi
}

# ════════════════════════════════════════════════════════════════
# Step 1: Verify prerequisites (setup.sh already ran)
# ════════════════════════════════════════════════════════════════
step "1/7" "Verifying prerequisites"

VENV_DIR="${HOME}/.ai-agent-ui/venv"
VENV_PYTHON="$VENV_DIR/bin/python"

_check "Python virtualenv exists" \
    "[[ -f '$VENV_PYTHON' ]]" || \
    fail "Virtualenv not found. Run ./setup.sh first."

_check "Node.js available" "command -v node" || \
    fail "Node.js not found. Run ./setup.sh first."

_check "npm available" "command -v npm" || \
    fail "npm not found. Run ./setup.sh first."

_check "Git repository" \
    "[[ -d '$PROJECT_ROOT/.git' ]]" || \
    fail "Not a git repository."

_check "backend/.env exists" \
    "[[ -f '$PROJECT_ROOT/backend/.env' ]]" || \
    fail "backend/.env missing. Run ./setup.sh first."

# ════════════════════════════════════════════════════════════════
# Step 2: Verify Claude Code
# ════════════════════════════════════════════════════════════════
step "2/7" "Checking Claude Code CLI"

if command -v claude &>/dev/null; then
    CLAUDE_VER="$(claude --version 2>/dev/null || echo '?')"
    ok "Claude Code CLI found ($CLAUDE_VER)"
else
    warn "Claude Code CLI not found"
    echo ""
    echo "  Install Claude Code:"
    echo "    npm install -g @anthropic-ai/claude-code"
    echo ""
    echo "  Then re-run this script."
fi

# ════════════════════════════════════════════════════════════════
# Step 3: Check Serena MCP configuration
# ════════════════════════════════════════════════════════════════
step "3/7" "Checking Serena MCP server"

SERENA_CFG="$PROJECT_ROOT/.serena/project.yml"
if [[ -f "$SERENA_CFG" ]]; then
    ok "Serena project config found"
else
    warn "Serena project.yml not found"
    echo "  See: https://github.com/oraios/serena"
fi

MCP_CFG="$HOME/.claude/settings/mcp.json"
PROJ_MCP="$PROJECT_ROOT/.mcp.json"
SERENA_FOUND=0

if [[ -f "$MCP_CFG" ]] && \
   grep -q "serena" "$MCP_CFG" 2>/dev/null; then
    SERENA_FOUND=1
fi
if [[ -f "$PROJ_MCP" ]] && \
   grep -q "serena" "$PROJ_MCP" 2>/dev/null; then
    SERENA_FOUND=1
fi

if [[ $SERENA_FOUND -eq 1 ]]; then
    ok "Serena found in MCP configuration"
else
    warn "Serena not found in MCP config"
    echo "  Add Serena to Claude Code MCP settings."
    echo "  Check ~/.claude/settings/mcp.json or .mcp.json"
fi

# ════════════════════════════════════════════════════════════════
# Step 4: Verify shared memories
# ════════════════════════════════════════════════════════════════
step "4/7" "Verifying shared memories"

SHARED="$PROJECT_ROOT/.serena/memories/shared"
EXPECTED=("architecture" "conventions" "debugging"
          "onboarding" "api")

if [[ -d "$SHARED" ]]; then
    ok "Shared memories directory exists"
    MEM_CT=$(find "$SHARED" -name "*.md" | wc -l | tr -d ' ')
    info "Found $MEM_CT shared memory files"

    for dir in "${EXPECTED[@]}"; do
        if [[ -d "$SHARED/$dir" ]]; then
            ct=$(find "$SHARED/$dir" -name "*.md" \
                 | wc -l | tr -d ' ')
            ok "shared/$dir/ ($ct files)"
        else
            warn "shared/$dir/ missing"
        fi
    done
else
    warn "Shared memories directory not found"
    echo "  Pull latest from dev:"
    echo "    git fetch origin && git pull origin dev"
fi

# ════════════════════════════════════════════════════════════════
# Step 5: Create local memory directories
# ════════════════════════════════════════════════════════════════
step "5/7" "Creating local memory directories"

for dir in "session" "personal"; do
    LOCAL="$PROJECT_ROOT/.serena/memories/$dir"
    if [[ ! -d "$LOCAL" ]]; then
        mkdir -p "$LOCAL"
        touch "$LOCAL/.gitkeep"
        ok "Created memories/$dir/"
    else
        ok "memories/$dir/ already exists"
    fi
done

# ════════════════════════════════════════════════════════════════
# Step 6: Install git hooks (if not already)
# ════════════════════════════════════════════════════════════════
step "6/7" "Checking git hooks"

HOOKS="$PROJECT_ROOT/.git/hooks"
for hook in "pre-commit" "pre-push"; do
    if [[ -x "$HOOKS/$hook" ]]; then
        ok "Git $hook hook installed"
    else
        SRC="$PROJECT_ROOT/hooks/$hook"
        if [[ -f "$SRC" ]]; then
            cp "$SRC" "$HOOKS/$hook"
            chmod +x "$HOOKS/$hook"
            ok "Git $hook hook installed (just now)"
        else
            warn "hooks/$hook source not found"
        fi
    fi
done

# ════════════════════════════════════════════════════════════════
# Step 7: Verify GitHub CLI
# ════════════════════════════════════════════════════════════════
step "7/7" "Checking GitHub CLI"

if command -v gh &>/dev/null; then
    if gh auth status &>/dev/null 2>&1; then
        ok "GitHub CLI authenticated"
    else
        warn "GitHub CLI installed but not authenticated"
        echo "  Run: gh auth login"
    fi
else
    warn "GitHub CLI (gh) not installed"
    echo "  Install: brew install gh"
fi

# ════════════════════════════════════════════════════════════════
# Summary
# ════════════════════════════════════════════════════════════════
echo ""
echo "════════════════════════════════════════════════════════════════"
if [[ $PASS -eq $TOTAL ]]; then
    echo -e "${G}  All $TOTAL checks passed. AI tooling ready!${N}"
else
    echo -e "${Y}  $PASS/$TOTAL checks passed. Review warnings.${N}"
fi
echo "════════════════════════════════════════════════════════════════"
echo ""
echo -e "  ${B}AI Tooling:${N}"
echo "    Claude Code + Serena MCP — project: ai-agent-ui"
echo ""
echo -e "  ${B}Shared Memories:${N}"
echo "    .serena/memories/shared/   (git-tracked, PR-reviewed)"
echo "    .serena/memories/session/  (local, gitignored)"
echo "    .serena/memories/personal/ (local, gitignored)"
echo ""
echo -e "  ${B}Useful Commands:${N}"
echo "    /promote-memory          Promote session memory to shared"
echo "    /check-stale-memories    Check for outdated memories"
echo "    /sc:save                 Save session context"
echo ""
echo -e "  ${B}Next Steps:${N}"
echo "    1. Start a Claude Code session in the project dir"
echo "    2. Serena auto-loads shared memories as needed"
echo "    3. Use /sc:save at end of session"
echo "    4. Use /promote-memory to share insights with team"
echo ""
