#!/usr/bin/env bash
# setup.sh — First-time installer for ai-agent-ui.
#
# Automates the entire first-run setup: Python virtualenv, Node.js
# dependencies, config files, Iceberg database initialisation, admin
# seeding, and git hook installation.
#
# Usage:
#   ./setup.sh                  # interactive — prompts for API keys
#   ./setup.sh --non-interactive # reads all secrets from env vars (CI/Docker)
#
# Safe to re-run — every step is idempotent.

set -euo pipefail

# ── Resolve script directory (works for symlinks too) ─────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── ANSI colours (disabled when stdout is not a terminal) ─────────────────────
if [[ -t 1 ]]; then
    R='\033[0;31m' G='\033[0;32m' Y='\033[1;33m' C='\033[0;36m' B='\033[1m' N='\033[0m'
else
    R='' G='' Y='' C='' B='' N=''
fi

# ── Parse flags ───────────────────────────────────────────────────────────────
NON_INTERACTIVE=0
for arg in "$@"; do
    case "$arg" in
        --non-interactive) NON_INTERACTIVE=1 ;;
        -h|--help)
            echo "Usage: ./setup.sh [--non-interactive]"
            echo ""
            echo "  --non-interactive   Read all secrets from environment variables (for CI/Docker)"
            echo "  -h, --help          Show this help message"
            exit 0
            ;;
        *)
            echo -e "${R}Unknown option: $arg${N}"
            echo "Usage: ./setup.sh [--non-interactive]"
            exit 1
            ;;
    esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────

step() {
    echo ""
    echo -e "${B}[$1]${N} $2"
    echo "────────────────────────────────────────────────────────────────"
}

ok()   { echo -e "  ${G}[OK]${N} $1"; }
warn() { echo -e "  ${Y}[WARN]${N} $1"; }
fail() { echo -e "  ${R}[FAIL]${N} $1"; exit 1; }
info() { echo -e "  ${C}[INFO]${N} $1"; }

# Detect WSL environment
_is_wsl() { grep -qi microsoft /proc/version 2>/dev/null; }

# Remove broken, circular, or chained symlinks at a path.
# Detects "too many levels of symbolic links" (ELOOP) and dangling links.
# Usage: _clean_symlink <path>
_clean_symlink() {
    local path="$1"
    # Nothing to clean if path doesn't exist at all (not even as a dangling link)
    [[ -e "$path" ]] || [[ -L "$path" ]] || return 0

    if [[ -L "$path" ]]; then
        # Test if the symlink is resolvable (catches ELOOP + dangling)
        if ! readlink -f "$path" &>/dev/null || [[ ! -e "$path" ]]; then
            warn "Removing broken/circular symlink: $path"
            rm -f "$path"
        fi
    fi
}

# Create a symlink, falling back to a copy if symlinks are not supported
# (e.g. WSL2 without Windows developer mode enabled).
# Automatically cleans broken/circular symlinks before creating.
# Usage: _try_symlink <target> <link_path>
_try_symlink() {
    local target="$1" link_path="$2"
    # Clean any broken/circular symlinks first
    _clean_symlink "$link_path"
    if ln -sf "$target" "$link_path" 2>/dev/null; then
        # Verify the new symlink actually resolves (catch ELOOP early)
        if [[ -e "$link_path" ]]; then
            return 0
        fi
        # Symlink was created but doesn't resolve — remove and copy
        rm -f "$link_path"
    fi
    # Symlink failed or unresolvable — fall back to copying
    warn "Symlinks not supported (enable Windows Developer Mode for WSL)"
    cp -f "$target" "$link_path"
    return 0
}

# Start a system service, handling the case where systemd is unavailable
# (common in WSL2).  Falls back to direct daemon launch.
# Usage: _start_redis_service
_start_redis_service() {
    if command -v systemctl &>/dev/null \
       && systemctl is-system-running &>/dev/null 2>&1; then
        sudo systemctl enable redis-server 2>/dev/null || true
        sudo systemctl start redis-server 2>/dev/null || true
    else
        # No systemd (typical WSL2) — launch directly
        redis-server --port 6379 --daemonize yes \
            --logfile "${HOME}/.ai-agent-ui/logs/redis.log" 2>/dev/null
    fi
}

prompt_required() {
    local var_name="$1" prompt_text="$2" value=""
    if [[ $NON_INTERACTIVE -eq 1 ]]; then
        value="${!var_name:-}"
        if [[ -z "$value" ]]; then
            fail "$var_name is required in --non-interactive mode. Export it before running."
        fi
        echo "$value"
        return
    fi
    while [[ -z "$value" ]]; do
        printf "  %s: " "$prompt_text"
        read -r value
        if [[ -z "$value" ]]; then
            echo -e "  ${R}This field is required. Please enter a value.${N}"
        fi
    done
    echo "$value"
}

prompt_optional() {
    local var_name="$1" prompt_text="$2" value=""
    if [[ $NON_INTERACTIVE -eq 1 ]]; then
        echo "${!var_name:-}"
        return
    fi
    printf "  %s (Enter to skip): " "$prompt_text"
    read -r value
    echo "$value"
}

prompt_secret() {
    local var_name="$1" prompt_text="$2" value=""
    if [[ $NON_INTERACTIVE -eq 1 ]]; then
        value="${!var_name:-}"
        if [[ -z "$value" ]]; then
            fail "$var_name is required in --non-interactive mode. Export it before running."
        fi
        echo "$value"
        return
    fi
    while [[ -z "$value" ]]; do
        printf "  %s: " "$prompt_text"
        read -rs value
        echo ""
        if [[ -z "$value" ]]; then
            echo -e "  ${R}This field is required. Please enter a value.${N}"
        fi
    done
    echo "$value"
}

prompt_optional_secret() {
    local var_name="$1" prompt_text="$2" value=""
    if [[ $NON_INTERACTIVE -eq 1 ]]; then
        echo "${!var_name:-}"
        return
    fi
    printf "  %s (Enter to skip): " "$prompt_text"
    read -rs value
    echo ""
    echo "$value"
}

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║               AI Agent UI — First-Time Setup                    ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
if [[ $NON_INTERACTIVE -eq 1 ]]; then
    info "Running in non-interactive mode (reading secrets from env vars)"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 1: Detect OS
# ══════════════════════════════════════════════════════════════════════════════
step "1/12" "Detecting operating system"

OS="unknown"
IS_WSL=0

case "$(uname -s)" in
    Darwin) OS="macos" ;;
    Linux)
        OS="linux"
        if grep -qi microsoft /proc/version 2>/dev/null; then
            IS_WSL=1
        fi
        ;;
esac

if [[ "$OS" == "macos" ]]; then
    ok "macOS detected ($(uname -m))"
elif [[ $IS_WSL -eq 1 ]]; then
    ok "Linux (WSL) detected"
elif [[ "$OS" == "linux" ]]; then
    ok "Linux detected"
else
    fail "Unsupported OS: $(uname -s). This script supports macOS, Linux, and WSL."
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 2: Check prerequisites
# ══════════════════════════════════════════════════════════════════════════════
step "2/12" "Checking prerequisites"

# git
if command -v git &>/dev/null; then
    ok "git $(git --version | cut -d' ' -f3)"
else
    fail "git is not installed. Please install git first."
fi

# curl
if command -v curl &>/dev/null; then
    ok "curl found"
else
    fail "curl is not installed. Please install curl first."
fi

# macOS: Xcode Command Line Tools
if [[ "$OS" == "macos" ]]; then
    if xcode-select -p &>/dev/null; then
        ok "Xcode Command Line Tools installed"
    else
        warn "Xcode Command Line Tools not found. Installing..."
        xcode-select --install 2>/dev/null || true
        echo "  Please complete the Xcode CLT installation dialog, then re-run this script."
        exit 1
    fi
fi

# Linux: build-essential (for compiling Python)
if [[ "$OS" == "linux" ]]; then
    if dpkg -l build-essential &>/dev/null 2>&1; then
        ok "build-essential installed"
    else
        warn "build-essential not found — Python compilation may fail."
        echo "  Install with: sudo apt-get install -y build-essential"
    fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 3: Ensure Python 3.12
# ══════════════════════════════════════════════════════════════════════════════
step "3/12" "Ensuring Python 3.12 is available"

PYTHON312=""

# Check if python3.12 already exists
if command -v python3.12 &>/dev/null; then
    PYTHON312="$(command -v python3.12)"
    ok "Python 3.12 found at $PYTHON312 ($(python3.12 --version 2>&1))"
elif [[ -f "$HOME/.pyenv/versions/3.12.9/bin/python3.12" ]]; then
    PYTHON312="$HOME/.pyenv/versions/3.12.9/bin/python3.12"
    ok "Python 3.12.9 found via pyenv"
else
    info "Python 3.12 not found — installing via pyenv"

    # Install pyenv if not present
    if ! command -v pyenv &>/dev/null; then
        info "Installing pyenv..."
        if [[ "$OS" == "macos" ]]; then
            if command -v brew &>/dev/null; then
                brew install pyenv
            else
                fail "Homebrew not found. Install Homebrew first: https://brew.sh"
            fi
        else
            # Linux / WSL
            curl -fsSL https://pyenv.run | bash
        fi

        # Set up pyenv in current shell
        export PYENV_ROOT="${PYENV_ROOT:-$HOME/.pyenv}"
        export PATH="$PYENV_ROOT/bin:$PATH"
        eval "$(pyenv init -)" 2>/dev/null || true
    fi

    # Ensure pyenv is available
    if ! command -v pyenv &>/dev/null; then
        export PYENV_ROOT="${PYENV_ROOT:-$HOME/.pyenv}"
        export PATH="$PYENV_ROOT/bin:$PATH"
        eval "$(pyenv init -)" 2>/dev/null || true
    fi

    if ! command -v pyenv &>/dev/null; then
        fail "pyenv installation failed. Install Python 3.9 manually and re-run."
    fi

    # Install Python build dependencies
    if [[ "$OS" == "macos" ]]; then
        info "Installing Python build dependencies via Homebrew..."
        brew install openssl readline sqlite3 xz zlib 2>/dev/null || true
    else
        info "Installing Python build dependencies via apt..."
        sudo apt-get update -qq
        sudo apt-get install -y -qq \
            build-essential libssl-dev zlib1g-dev libbz2-dev \
            libreadline-dev libsqlite3-dev wget llvm libncurses5-dev \
            libncursesw5-dev xz-utils tk-dev libffi-dev liblzma-dev 2>/dev/null || true
    fi

    # Install Python 3.12.9
    info "Installing Python 3.12.9 via pyenv (this may take a few minutes)..."
    pyenv install 3.12.9 --skip-existing

    PYTHON312="$HOME/.pyenv/versions/3.12.9/bin/python3.12"
    if [[ -f "$PYTHON312" ]]; then
        ok "Python 3.12.9 installed via pyenv"
    else
        fail "Python 3.12 installation failed. Check pyenv output above."
    fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 4: Create virtualenv
# ══════════════════════════════════════════════════════════════════════════════
step "4/12" "Creating Python virtualenv (~/.ai-agent-ui/venv)"

VENV_DIR="${APP_DATA_HOME:-$HOME/.ai-agent-ui}/venv"

# Migrate: if old venv exists at backend/demoenv but new one does not,
# move it and leave a symlink for backwards compatibility.
OLD_VENV_DIR="$SCRIPT_DIR/backend/demoenv"
_clean_symlink "$OLD_VENV_DIR"
if [[ -d "$OLD_VENV_DIR" ]] && [[ ! -L "$OLD_VENV_DIR" ]] && [[ ! -d "$VENV_DIR" ]]; then
    info "Migrating virtualenv from backend/demoenv → $VENV_DIR"
    mv "$OLD_VENV_DIR" "$VENV_DIR"
    _try_symlink "$VENV_DIR" "$OLD_VENV_DIR"
    ok "Virtualenv migrated (link left at backend/demoenv)"
fi
VENV_PYTHON="$VENV_DIR/bin/python"

if [[ -f "$VENV_PYTHON" ]]; then
    # Verify it's actually Python 3.12.x
    VENV_VERSION="$("$VENV_PYTHON" --version 2>&1)"
    if [[ "$VENV_VERSION" == *"3.12"* ]]; then
        ok "Virtualenv already exists ($VENV_VERSION)"
    else
        warn "Virtualenv exists but is $VENV_VERSION (expected 3.12.x) — recreating"
        rm -rf "$VENV_DIR"
        "$PYTHON312" -m venv "$VENV_DIR"
        ok "Virtualenv recreated with $("$VENV_PYTHON" --version 2>&1)"
    fi
else
    "$PYTHON312" -m venv "$VENV_DIR"
    ok "Virtualenv created ($("$VENV_PYTHON" --version 2>&1))"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 5: Install Python dependencies
# ══════════════════════════════════════════════════════════════════════════════
step "5/12" "Installing Python dependencies"

REQUIREMENTS="$SCRIPT_DIR/backend/requirements.txt"
if [[ ! -f "$REQUIREMENTS" ]]; then
    fail "backend/requirements.txt not found"
fi

info "Upgrading pip..."
"$VENV_PYTHON" -m pip install --upgrade pip --quiet

info "Installing packages from requirements.txt (this may take a few minutes)..."
"$VENV_PYTHON" -m pip install -r "$REQUIREMENTS" --quiet

REQUIREMENTS_DEV="$SCRIPT_DIR/backend/requirements-dev.txt"
if [[ -f "$REQUIREMENTS_DEV" ]]; then
    info "Installing dev/test dependencies from requirements-dev.txt..."
    "$VENV_PYTHON" -m pip install -r "$REQUIREMENTS_DEV" --quiet
fi

ok "Python dependencies installed"

# ══════════════════════════════════════════════════════════════════════════════
# Step 6: Check Node.js
# ══════════════════════════════════════════════════════════════════════════════
step "6/12" "Checking Node.js"

if command -v node &>/dev/null; then
    NODE_VERSION="$(node --version)"
    # Extract major version number (v18.17.0 -> 18)
    NODE_MAJOR="${NODE_VERSION#v}"
    NODE_MAJOR="${NODE_MAJOR%%.*}"
    if [[ "$NODE_MAJOR" -ge 18 ]]; then
        ok "Node.js $NODE_VERSION"
    else
        fail "Node.js $NODE_VERSION is too old. Version 18.17+ required."
    fi
else
    fail "Node.js is not installed. Install Node.js 18.17+ from https://nodejs.org or via nvm/fnm."
fi

if command -v npm &>/dev/null; then
    ok "npm $(npm --version)"
else
    fail "npm not found. It should come with Node.js."
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 7: Install frontend dependencies
# ══════════════════════════════════════════════════════════════════════════════
step "7/12" "Installing frontend dependencies"

FRONTEND_DIR="$SCRIPT_DIR/frontend"

if [[ -d "$FRONTEND_DIR/node_modules" ]]; then
    ok "node_modules already exists — skipping (delete frontend/node_modules to force reinstall)"
else
    info "Running npm ci in frontend/..."
    (cd "$FRONTEND_DIR" && npm ci --loglevel=warn)
    ok "Frontend dependencies installed"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 8: Create project directories
# ══════════════════════════════════════════════════════════════════════════════
step "8/12" "Creating project directories"

APP_DATA_HOME="${HOME}/.ai-agent-ui"
DIRS=(
    "${APP_DATA_HOME}/data/iceberg"
    "${APP_DATA_HOME}/data/iceberg/warehouse"
    "${APP_DATA_HOME}/data/raw"
    "${APP_DATA_HOME}/data/forecasts"
    "${APP_DATA_HOME}/data/cache"
    "${APP_DATA_HOME}/data/avatars"
    "${APP_DATA_HOME}/data/metadata"
    "${APP_DATA_HOME}/data/processed"
    "${APP_DATA_HOME}/logs"
    "${APP_DATA_HOME}/charts/analysis"
    "${APP_DATA_HOME}/charts/forecasts"
)

for d in "${DIRS[@]}"; do
    mkdir -p "$d"
done

ok "All directories created"

# ══════════════════════════════════════════════════════════════════════════════
# Step 9: Prompt for API keys and secrets
# ══════════════════════════════════════════════════════════════════════════════
step "9/12" "Configuring API keys and secrets"

# Auto-generate JWT_SECRET_KEY
JWT_SECRET_KEY="$("$VENV_PYTHON" -c "import secrets; print(secrets.token_hex(32))")"
ok "JWT_SECRET_KEY auto-generated (64 hex chars)"

# Required
echo ""
echo -e "  ${B}Required:${N}"
ANTHROPIC_API_KEY="$(prompt_secret "ANTHROPIC_API_KEY" "Anthropic API key (sk-ant-...)")"
ok "ANTHROPIC_API_KEY set"

# Optional LLM / tools
echo ""
echo -e "  ${B}Optional (press Enter to skip):${N}"
GROQ_API_KEY="$(prompt_optional_secret "GROQ_API_KEY" "Groq API key (LLM fallback)")"
SERPAPI_API_KEY="$(prompt_optional_secret "SERPAPI_API_KEY" "SerpAPI key (web search tool)")"

# Optional SSO
echo ""
echo -e "  ${B}Optional — Google SSO:${N}"
GOOGLE_CLIENT_ID="$(prompt_optional "GOOGLE_CLIENT_ID" "Google Client ID")"
GOOGLE_CLIENT_SECRET="$(prompt_optional_secret "GOOGLE_CLIENT_SECRET" "Google Client Secret")"

echo ""
echo -e "  ${B}Optional — Facebook SSO:${N}"
FACEBOOK_APP_ID="$(prompt_optional "FACEBOOK_APP_ID" "Facebook App ID")"
FACEBOOK_APP_SECRET="$(prompt_optional_secret "FACEBOOK_APP_SECRET" "Facebook App Secret")"

# Optional admin seed
echo ""
echo -e "  ${B}Optional — Superuser account:${N}"
ADMIN_EMAIL="$(prompt_optional "ADMIN_EMAIL" "Admin email")"
ADMIN_PASSWORD=""
ADMIN_FULL_NAME=""

if [[ -n "$ADMIN_EMAIL" ]]; then
    # Validate admin password
    while true; do
        ADMIN_PASSWORD="$(prompt_optional_secret "ADMIN_PASSWORD" "Admin password (min 8 chars, 1 digit)")"
        if [[ -z "$ADMIN_PASSWORD" ]]; then
            warn "No password provided — skipping admin account creation"
            ADMIN_EMAIL=""
            break
        fi
        if [[ ${#ADMIN_PASSWORD} -lt 8 ]]; then
            echo -e "  ${R}Password must be at least 8 characters.${N}"
            continue
        fi
        if ! [[ "$ADMIN_PASSWORD" =~ [0-9] ]]; then
            echo -e "  ${R}Password must contain at least one digit.${N}"
            continue
        fi
        break
    done
    if [[ -n "$ADMIN_EMAIL" ]]; then
        ADMIN_FULL_NAME="$(prompt_optional "ADMIN_FULL_NAME" "Admin full name")"
        [[ -z "$ADMIN_FULL_NAME" ]] && ADMIN_FULL_NAME="Admin User"
    fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 10: Generate config files
# ══════════════════════════════════════════════════════════════════════════════
step "10/12" "Generating config files"

# ── External env directory ────────────────────────────────────────────────────
# Secrets live outside the repo so git checkout / merge never overwrites them.
# The project files (backend/.env, frontend/.env.local) are symlinks.
ENV_HOME="$HOME/.ai-agent-ui"
mkdir -p "$ENV_HOME"

BACKEND_ENV_REAL="$ENV_HOME/backend.env"
FRONTEND_ENV_REAL="$ENV_HOME/frontend.env.local"
BACKEND_ENV_LINK="$SCRIPT_DIR/backend/.env"
FRONTEND_ENV_LINK="$SCRIPT_DIR/frontend/.env.local"

# ── backend/.env ──────────────────────────────────────────────────────────────

_write_backend_env() {
    cat > "$BACKEND_ENV_REAL" <<ENVEOF
# Generated by setup.sh — $(date -u +"%Y-%m-%d %H:%M:%S UTC")
# Master env file — lives at ~/.ai-agent-ui/backend.env
# backend/.env is a symlink to this file.

# ── LLM Keys ──────────────────────────────────────────────────────────
ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
GROQ_API_KEY=${GROQ_API_KEY:-}
SERPAPI_API_KEY=${SERPAPI_API_KEY:-}

# ── Auth / JWT ────────────────────────────────────────────────────────
JWT_SECRET_KEY=$JWT_SECRET_KEY
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=7

# ── Admin seed (used by scripts/seed_admin.py) ───────────────────────
ADMIN_EMAIL=${ADMIN_EMAIL:-}
ADMIN_PASSWORD=${ADMIN_PASSWORD:-}
ADMIN_FULL_NAME=${ADMIN_FULL_NAME:-Admin User}

# ── Google SSO ────────────────────────────────────────────────────────
GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID:-}
GOOGLE_CLIENT_SECRET=${GOOGLE_CLIENT_SECRET:-}

# ── Facebook SSO ──────────────────────────────────────────────────────
FACEBOOK_APP_ID=${FACEBOOK_APP_ID:-}
FACEBOOK_APP_SECRET=${FACEBOOK_APP_SECRET:-}

# ── OAuth ─────────────────────────────────────────────────────────────
OAUTH_REDIRECT_URI=http://localhost:3000/auth/oauth/callback

# ── Redis ─────────────────────────────────────────────────────────────
# Token deny-list + OAuth state store. Empty = in-memory fallback.
REDIS_URL=redis://localhost:6379/0

# ── Data Retention ────────────────────────────────────────────────────
# Days to keep data (0 = keep forever). Dry-run by default.
RETENTION_ENABLED=False
RETENTION_DRY_RUN=True
RETENTION_LLM_USAGE_DAYS=90
RETENTION_ANALYSIS_SUMMARY_DAYS=365
RETENTION_FORECAST_RUNS_DAYS=180
RETENTION_COMPANY_INFO_DAYS=365

# ── WebSocket ────────────────────────────────────────────────────────
WS_AUTH_TIMEOUT_SECONDS=10
WS_PING_INTERVAL_SECONDS=30

# ── Logging / Runtime ─────────────────────────────────────────────────
LOG_LEVEL=INFO
LOG_TO_FILE=True
AGENT_TIMEOUT_SECONDS=900
ENVEOF
    ok "~/.ai-agent-ui/backend.env written"
}

# Migrate: if backend/.env is a real file (not a symlink), move it out
if [[ -f "$BACKEND_ENV_LINK" ]] && [[ ! -L "$BACKEND_ENV_LINK" ]]; then
    if [[ ! -f "$BACKEND_ENV_REAL" ]]; then
        mv "$BACKEND_ENV_LINK" "$BACKEND_ENV_REAL"
        info "Migrated existing backend/.env to ~/.ai-agent-ui/backend.env"
    else
        rm "$BACKEND_ENV_LINK"
        info "Removed in-repo backend/.env (master copy already exists)"
    fi
fi

# Write master file if it doesn't exist (or overwrite if requested)
if [[ -f "$BACKEND_ENV_REAL" ]]; then
    if [[ $NON_INTERACTIVE -eq 1 ]]; then
        info "~/.ai-agent-ui/backend.env exists — overwriting (non-interactive)"
        _write_backend_env
    else
        printf "  ~/.ai-agent-ui/backend.env already exists. Overwrite? [y/N]: "
        read -r OVERWRITE
        if [[ "$OVERWRITE" =~ ^[Yy] ]]; then
            _write_backend_env
        else
            ok "~/.ai-agent-ui/backend.env kept unchanged"
        fi
    fi
else
    _write_backend_env
fi

# Create symlink (or copy): backend/.env → ~/.ai-agent-ui/backend.env
# Clean broken/circular symlinks before checking
_clean_symlink "$BACKEND_ENV_LINK"
if [[ -L "$BACKEND_ENV_LINK" ]]; then
    LINK_TARGET="$(readlink "$BACKEND_ENV_LINK")"
    if [[ "$LINK_TARGET" == "$BACKEND_ENV_REAL" ]]; then
        ok "backend/.env symlink OK"
    else
        rm "$BACKEND_ENV_LINK"
        _try_symlink "$BACKEND_ENV_REAL" "$BACKEND_ENV_LINK"
        ok "backend/.env link updated"
    fi
elif [[ -f "$BACKEND_ENV_LINK" ]]; then
    # A regular file exists (previous copy-fallback) — refresh it
    cp -f "$BACKEND_ENV_REAL" "$BACKEND_ENV_LINK"
    ok "backend/.env copy refreshed"
else
    _try_symlink "$BACKEND_ENV_REAL" "$BACKEND_ENV_LINK"
    if [[ -L "$BACKEND_ENV_LINK" ]]; then
        ok "backend/.env → ~/.ai-agent-ui/backend.env (symlink)"
    else
        ok "backend/.env → ~/.ai-agent-ui/backend.env (copy)"
    fi
fi

# ── frontend/.env.local ──────────────────────────────────────────────────────
FRONTEND_ENV_EXAMPLE="$SCRIPT_DIR/frontend/.env.local.example"

# Migrate: if frontend/.env.local is a real file, move it out
if [[ -f "$FRONTEND_ENV_LINK" ]] && [[ ! -L "$FRONTEND_ENV_LINK" ]]; then
    if [[ ! -f "$FRONTEND_ENV_REAL" ]]; then
        mv "$FRONTEND_ENV_LINK" "$FRONTEND_ENV_REAL"
        info "Migrated existing frontend/.env.local to ~/.ai-agent-ui/"
    else
        rm "$FRONTEND_ENV_LINK"
        info "Removed in-repo frontend/.env.local (master copy exists)"
    fi
fi

# Write master file if it doesn't exist
if [[ ! -f "$FRONTEND_ENV_REAL" ]]; then
    if [[ -f "$FRONTEND_ENV_EXAMPLE" ]]; then
        cp "$FRONTEND_ENV_EXAMPLE" "$FRONTEND_ENV_REAL"
    else
        cat > "$FRONTEND_ENV_REAL" <<FEEOF
NEXT_PUBLIC_BACKEND_URL=http://127.0.0.1:8181
NEXT_PUBLIC_DASHBOARD_URL=http://127.0.0.1:8050
NEXT_PUBLIC_DOCS_URL=http://127.0.0.1:8000
FEEOF
    fi
    ok "~/.ai-agent-ui/frontend.env.local written"
else
    ok "~/.ai-agent-ui/frontend.env.local already exists"
fi

# Create symlink (or copy): frontend/.env.local → ~/.ai-agent-ui/frontend.env.local
# Clean broken/circular symlinks before checking
_clean_symlink "$FRONTEND_ENV_LINK"
if [[ -L "$FRONTEND_ENV_LINK" ]]; then
    LINK_TARGET="$(readlink "$FRONTEND_ENV_LINK")"
    if [[ "$LINK_TARGET" == "$FRONTEND_ENV_REAL" ]]; then
        ok "frontend/.env.local symlink OK"
    else
        rm "$FRONTEND_ENV_LINK"
        _try_symlink "$FRONTEND_ENV_REAL" "$FRONTEND_ENV_LINK"
        ok "frontend/.env.local link updated"
    fi
elif [[ -f "$FRONTEND_ENV_LINK" ]]; then
    cp -f "$FRONTEND_ENV_REAL" "$FRONTEND_ENV_LINK"
    ok "frontend/.env.local copy refreshed"
else
    _try_symlink "$FRONTEND_ENV_REAL" "$FRONTEND_ENV_LINK"
    if [[ -L "$FRONTEND_ENV_LINK" ]]; then
        ok "frontend/.env.local → ~/.ai-agent-ui/frontend.env.local (symlink)"
    else
        ok "frontend/.env.local → ~/.ai-agent-ui/frontend.env.local (copy)"
    fi
fi

# ── .pyiceberg.yaml ──────────────────────────────────────────────────────────
PYICEBERG_YAML="$SCRIPT_DIR/.pyiceberg.yaml"

if [[ -f "$PYICEBERG_YAML" ]]; then
    ok ".pyiceberg.yaml already exists — skipping"
else
    cat > "$PYICEBERG_YAML" <<ICEEOF
catalog:
  local:
    type: sql
    uri: sqlite:///${HOME}/.ai-agent-ui/data/iceberg/catalog.db
    warehouse: file:///${HOME}/.ai-agent-ui/data/iceberg/warehouse
ICEEOF
    ok ".pyiceberg.yaml created (warehouse: ${HOME}/.ai-agent-ui/data/iceberg/warehouse)"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 11: Install and configure Redis
# ══════════════════════════════════════════════════════════════════════════════
step "11/12" "Installing Redis (token store backend)"

REDIS_INSTALLED=0

if command -v redis-server &>/dev/null; then
    ok "Redis already installed ($(redis-server --version | grep -oE 'v=[0-9.]+'))"
    REDIS_INSTALLED=1
else
    info "Redis not found — installing…"
    if [[ "$OS" == "macos" ]]; then
        if command -v brew &>/dev/null; then
            brew install redis 2>/dev/null
            ok "Redis installed via Homebrew"
            REDIS_INSTALLED=1
        else
            warn "Homebrew not found — cannot install Redis automatically"
            warn "Install manually: brew install redis"
        fi
    elif [[ "$OS" == "linux" ]]; then
        if command -v apt-get &>/dev/null; then
            sudo apt-get update -qq && sudo apt-get install -y -qq redis-server
            ok "Redis installed via apt"
            REDIS_INSTALLED=1
        else
            warn "apt-get not found — install Redis manually for your distro"
        fi
    fi
fi

# Start Redis as a background service if installed
if [[ $REDIS_INSTALLED -eq 1 ]]; then
    if redis-cli ping &>/dev/null 2>&1; then
        ok "Redis is already running"
    else
        info "Starting Redis service…"
        if [[ "$OS" == "macos" ]]; then
            brew services start redis 2>/dev/null
        else
            _start_redis_service
        fi
        # Wait up to 5 seconds for Redis to accept connections
        _attempt=0
        while ! redis-cli ping &>/dev/null 2>&1 && (( _attempt < 10 )); do
            sleep 0.5
            (( _attempt++ ))
        done
        if redis-cli ping &>/dev/null 2>&1; then
            ok "Redis started and responding to PING"
        else
            warn "Redis installed but not responding — check 'redis-cli ping'"
        fi
    fi

    # Configure AOF persistence for deny-list durability.
    # Without AOF, revoked tokens could be lost on restart
    # (RDB snapshots are too infrequent for a small key count).
    _aof_status="$(redis-cli config get appendonly 2>/dev/null | tail -1)"
    if [[ "$_aof_status" != "yes" ]]; then
        info "Enabling AOF persistence for token deny-list durability…"
        redis-cli config set appendonly yes &>/dev/null
        redis-cli config set appendfsync everysec &>/dev/null
        # config rewrite fails if Redis was started without a config file
        # (common on WSL2 with direct daemonize). This is non-fatal.
        redis-cli config rewrite &>/dev/null || true
        ok "AOF enabled (appendfsync=everysec) — deny-list survives restarts"
    else
        ok "AOF persistence already enabled"
    fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 12: Initialise Iceberg + seed admin + install hooks
# ══════════════════════════════════════════════════════════════════════════════
step "12/12" "Initialising database, git hooks, and running verification"

# Export env vars so init scripts can find them
export JWT_SECRET_KEY
export ADMIN_EMAIL="${ADMIN_EMAIL:-}"
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
export ADMIN_FULL_NAME="${ADMIN_FULL_NAME:-Admin User}"
export ANTHROPIC_API_KEY

# ── Iceberg tables ────────────────────────────────────────────────────────────
info "Creating auth Iceberg tables..."
if (cd "$SCRIPT_DIR" && "$VENV_PYTHON" auth/create_tables.py 2>&1); then
    ok "auth tables created"
else
    warn "auth/create_tables.py had issues (may already exist)"
fi

info "Running auth schema migration..."
if (cd "$SCRIPT_DIR" && "$VENV_PYTHON" auth/migrate_users_table.py 2>&1); then
    ok "auth migration complete"
else
    warn "auth/migrate_users_table.py had issues (may already be migrated)"
fi

info "Creating stocks Iceberg tables..."
if (cd "$SCRIPT_DIR" && "$VENV_PYTHON" stocks/create_tables.py 2>&1); then
    ok "stocks tables created"
else
    warn "stocks/create_tables.py had issues (may already exist)"
fi

info "Running stocks metadata backfill..."
if (cd "$SCRIPT_DIR" && "$VENV_PYTHON" stocks/backfill_metadata.py 2>&1); then
    ok "stocks metadata backfill complete"
else
    warn "stocks/backfill_metadata.py had issues (may already be backfilled)"
fi

# ── Seed admin ────────────────────────────────────────────────────────────────
if [[ -n "$ADMIN_EMAIL" ]] && [[ -n "$ADMIN_PASSWORD" ]]; then
    info "Seeding admin account ($ADMIN_EMAIL)..."
    if (cd "$SCRIPT_DIR" && "$VENV_PYTHON" scripts/seed_admin.py 2>&1); then
        ok "Admin account seeded"
    else
        warn "scripts/seed_admin.py had issues (admin may already exist)"
    fi
else
    info "No admin credentials provided — skipping admin seed"
fi

# ── Seed demo data ────────────────────────────────────────────────────────────
if [[ "${SKIP_SEED:-}" != "1" ]]; then
    info "Seeding demo data (5 tickers + 2 users)..."
    if (cd "$SCRIPT_DIR" && "$VENV_PYTHON" scripts/seed_demo_data.py 2>&1); then
        ok "Demo data seeded (admin@demo.local / Admin123!, test@demo.local / Test1234!)"
    else
        warn "scripts/seed_demo_data.py had issues (data may already exist)"
    fi
else
    info "SKIP_SEED=1 — skipping demo data seed"
fi

# ── Git hooks ─────────────────────────────────────────────────────────────────
info "Installing git hooks..."
HOOKS_DIR="$SCRIPT_DIR/.git/hooks"
if [[ -d "$HOOKS_DIR" ]]; then
    cp "$SCRIPT_DIR/hooks/pre-commit" "$HOOKS_DIR/pre-commit"
    chmod +x "$HOOKS_DIR/pre-commit"
    cp "$SCRIPT_DIR/hooks/pre-push" "$HOOKS_DIR/pre-push"
    chmod +x "$HOOKS_DIR/pre-push"
    ok "Git hooks installed (pre-commit + pre-push)"
else
    warn ".git/hooks directory not found — are you in a git repository?"
fi

# ── Verification ──────────────────────────────────────────────────────────────
echo ""
echo -e "${B}Verification:${N}"
echo "────────────────────────────────────────────────────────────────"

PASS=0
TOTAL=0

_check() {
    local label="$1"
    local condition="$2"
    TOTAL=$((TOTAL + 1))
    if eval "$condition" &>/dev/null; then
        echo -e "  ${G}[PASS]${N} $label"
        PASS=$((PASS + 1))
    else
        echo -e "  ${R}[FAIL]${N} $label"
    fi
}

_check "Python virtualenv" "[[ -f '$VENV_PYTHON' ]]"
_check "Key packages (fastapi)" "'$VENV_PYTHON' -c 'import fastapi'"
_check "Key packages (langchain)" "'$VENV_PYTHON' -c 'import langchain'"
_check "Key packages (pyiceberg)" "'$VENV_PYTHON' -c 'import pyiceberg'"
_check "Key packages (dash)" "'$VENV_PYTHON' -c 'import dash'"
_check "Key packages (slowapi)" "'$VENV_PYTHON' -c 'import slowapi'"
_check "Frontend node_modules" "[[ -d '$FRONTEND_DIR/node_modules' ]]"
# Symlink OR copy — both are valid (copy is the WSL2 fallback)
_check "backend/.env (linked)" "([[ -L '$BACKEND_ENV_LINK' ]] || [[ -f '$BACKEND_ENV_LINK' ]]) && [[ -f '$BACKEND_ENV_REAL' ]]"
_check "frontend/.env.local (linked)" "([[ -L '$FRONTEND_ENV_LINK' ]] || [[ -f '$FRONTEND_ENV_LINK' ]]) && [[ -f '$FRONTEND_ENV_REAL' ]]"
_check ".pyiceberg.yaml" "[[ -f '$PYICEBERG_YAML' ]]"
_check "Iceberg catalog" "[[ -f '$HOME/.ai-agent-ui/data/iceberg/catalog.db' ]]"
_check "Git pre-commit hook" "[[ -x '$HOOKS_DIR/pre-commit' ]]"
_check "Git pre-push hook" "[[ -x '$HOOKS_DIR/pre-push' ]]"
_check "Redis server" "command -v redis-server"
_check "Redis responding" "redis-cli ping"
_check "Redis AOF persistence" "[[ \$(redis-cli config get appendonly 2>/dev/null | tail -1) == 'yes' ]]"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════════════"
if [[ $PASS -eq $TOTAL ]]; then
    echo -e "${G}  All $TOTAL checks passed. Setup complete!${N}"
else
    echo -e "${Y}  $PASS/$TOTAL checks passed. Review any failures above.${N}"
fi
echo "════════════════════════════════════════════════════════════════════"
echo ""
echo -e "  ${B}Next steps:${N}"
echo ""
echo "    1. Start all services:"
echo -e "       ${C}./run.sh start${N}"
echo ""
echo "    2. Open the app:"
echo -e "       ${C}http://localhost:3000${N}"
echo ""
echo -e "  ${B}Env files (safe from git):${N}"
echo "    ~/.ai-agent-ui/backend.env         (secrets + config)"
echo "    ~/.ai-agent-ui/frontend.env.local  (service URLs)"
echo "    backend/.env → symlink to above"
echo "    frontend/.env.local → symlink to above"
echo ""
echo -e "  ${B}Service URLs:${N}"
echo "    Frontend:   http://localhost:3000"
echo "    Backend:    http://127.0.0.1:8181"
echo "    Dashboard:  http://127.0.0.1:8050"
echo "    Docs:       http://127.0.0.1:8000"
echo ""
echo -e "  ${B}Useful commands:${N}"
echo "    ./run.sh start     Start all services"
echo "    ./run.sh stop      Stop all services"
echo "    ./run.sh status    Check service status"
echo ""
if [[ $IS_WSL -eq 1 ]]; then
    echo -e "  ${B}WSL2 notes:${N}"
    echo "    - If symlinks failed, env files were copied instead."
    echo "      After editing ~/.ai-agent-ui/backend.env, re-run setup.sh"
    echo "      or manually copy to backend/.env."
    echo "    - Access services from Windows browser at http://localhost:<port>"
    echo "    - To enable symlinks: Settings > Privacy > Developer Mode > ON"
    echo ""
fi
