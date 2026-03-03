#!/usr/bin/env bash
# run.sh — Start, stop, and monitor all AI Agent UI services.
#
# Usage:
#   ./run.sh start    — start backend, frontend, docs, dashboard
#   ./run.sh stop     — stop all running services
#   ./run.sh status   — show PID and URL for each service
#   ./run.sh restart  — stop then start
#
# Services:
#   backend    FastAPI + agentic loop   http://127.0.0.1:8181
#   frontend   Next.js dev server       http://localhost:3000
#   docs       MkDocs material site     http://127.0.0.1:8000
#   dashboard  Plotly Dash dashboard    http://127.0.0.1:8050

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="/tmp/ai-agent-ui-logs"
PYTHON="${SCRIPT_DIR}/backend/demoenv/bin/python"
MKDOCS="${SCRIPT_DIR}/backend/demoenv/bin/mkdocs"
NPM="$(command -v npm 2>/dev/null || echo 'npm')"

BACKEND_PORT=8181
FRONTEND_PORT=3000
DOCS_PORT=8000
DASHBOARD_PORT=8050

# ANSI colours (disabled when not writing to a terminal)
if [[ -t 1 ]]; then
    R='\033[0;31m' G='\033[0;32m' Y='\033[1;33m' C='\033[0;36m' B='\033[1m' N='\033[0m'
else
    R='' G='' Y='' C='' B='' N=''
fi

mkdir -p "$LOG_DIR"

# ── Helpers ───────────────────────────────────────────────────────────────────

# PIDs currently listening on a port (newline-separated)
_pids_on_port() { lsof -ti:"$1" 2>/dev/null || true; }

# Is anything listening on this port?
_port_up() { [[ -n "$(_pids_on_port "$1")" ]]; }

# Kill everything on a port: SIGTERM then SIGKILL if still alive
_kill_port() {
    local port="$1" label="$2"
    local pids
    pids=$(_pids_on_port "$port")
    [[ -z "$pids" ]] && return 0

    # shellcheck disable=SC2086
    printf "  Stopping %-10s  port %-4s  PID %s\n" "$label" "$port" "$(echo $pids | tr '\n' ' ')"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true

    local i=0
    while _port_up "$port" && (( i < 15 )); do sleep 0.3; (( i++ )); done

    pids=$(_pids_on_port "$port")
    # shellcheck disable=SC2086
    [[ -n "$pids" ]] && kill -9 $pids 2>/dev/null || true
}

# Free a port silently before starting a service on it
_free_port() {
    local pids
    pids=$(_pids_on_port "$1")
    if [[ -n "$pids" ]]; then
        echo -e "${Y}  Port $1 occupied — clearing (PID $(echo $pids | tr '\n' ' '))…${N}"
        # shellcheck disable=SC2086
        kill $pids 2>/dev/null || true
        sleep 0.5
    fi
}

# Launch a service detached from the terminal
_launch() {
    local name="$1" dir="$2"
    shift 2
    (cd "$dir" && exec "$@" >> "${LOG_DIR}/${name}.log" 2>&1) &
    disown "$!" 2>/dev/null || true
}

# ── Status table ──────────────────────────────────────────────────────────────

_print_table() {
    local names=(backend   frontend   docs      dashboard)
    local ports=($BACKEND_PORT $FRONTEND_PORT $DOCS_PORT $DASHBOARD_PORT)
    local urls=(
        "http://127.0.0.1:${BACKEND_PORT}"
        "http://localhost:${FRONTEND_PORT}"
        "http://127.0.0.1:${DOCS_PORT}"
        "http://127.0.0.1:${DASHBOARD_PORT}"
    )

    echo ""
    printf "${B}  %-12s  %-8s  %-32s  %s${N}\n" "Service" "PID" "URL" "Status"
    printf "  %s\n" "──────────────────────────────────────────────────────────────────"

    for i in "${!names[@]}"; do
        local pid state
        pid=$(_pids_on_port "${ports[$i]}")
        if [[ -n "$pid" ]]; then
            # Trim to first PID if multiple (e.g. uvicorn workers)
            pid=$(echo "$pid" | head -1)
            state="${G}● up${N}"
        else
            pid="—"
            state="${R}○ down${N}"
        fi
        printf "  %-12s  %-8s  %-32s  " "${names[$i]}" "$pid" "${urls[$i]}"
        echo -e "$state"
    done
    echo ""
}

# ── First-time auth initialisation ────────────────────────────────────────────

# Run Iceberg table creation + admin seed on first start (idempotent guard:
# if data/iceberg/catalog.db already exists, the function returns immediately).
_init_auth() {
    local catalog="${SCRIPT_DIR}/data/iceberg/catalog.db"
    if [[ -f "$catalog" ]]; then
        return 0
    fi

    echo -e "${Y}  Auth DB not found — initialising Iceberg tables…${N}"

    if [[ -z "${JWT_SECRET_KEY:-}" ]]; then
        echo -e "${R}  ERROR: JWT_SECRET_KEY is required for first-time auth setup.${N}"
        echo "  Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        echo "  Then export JWT_SECRET_KEY=<value> or add it to .env at the project root."
        exit 1
    fi

    echo "  Running auth/create_tables.py…"
    if ! (cd "$SCRIPT_DIR" && "$PYTHON" auth/create_tables.py); then
        echo -e "${R}  ERROR: auth/create_tables.py failed.  See output above.${N}"
        exit 1
    fi

    if [[ -n "${ADMIN_EMAIL:-}" ]] && [[ -n "${ADMIN_PASSWORD:-}" ]]; then
        echo "  Running scripts/seed_admin.py…"
        if ! (cd "$SCRIPT_DIR" && "$PYTHON" scripts/seed_admin.py); then
            echo -e "${R}  ERROR: scripts/seed_admin.py failed.  See output above.${N}"
            exit 1
        fi
        echo -e "${G}  Auth initialised. Admin account created: ${ADMIN_EMAIL}${N}"
    else
        echo -e "${Y}  WARNING: ADMIN_EMAIL / ADMIN_PASSWORD not set — tables created but no admin seeded.${N}"
        echo "  Run 'python scripts/seed_admin.py' manually after setting those variables."
    fi
    echo ""
}

# ── First-time stocks Iceberg initialisation ──────────────────────────────────

# Create the 8 stocks.* Iceberg tables (idempotent — safe to run on every start).
# Must be called AFTER _init_auth so catalog.db already exists.
_init_stocks() {
    echo "  Initialising stocks Iceberg tables…"
    if ! (cd "$SCRIPT_DIR" && "$PYTHON" stocks/create_tables.py \
              >> "${LOG_DIR}/init_stocks.log" 2>&1); then
        echo -e "${Y}  WARNING: stocks/create_tables.py had issues. See ${LOG_DIR}/init_stocks.log${N}"
        echo    "  The Insights dashboard pages may show empty data until resolved."
    else
        echo -e "${G}  stocks Iceberg tables ready.${N}"
    fi
}

# ── Commands ──────────────────────────────────────────────────────────────────

do_start() {
    echo -e "${B}AI Agent UI — starting services${N}"
    echo ""

    if [[ ! -f "$PYTHON" ]]; then
        echo -e "${R}ERROR: demoenv not found at ${PYTHON}${N}"
        echo "  Run: cd backend && python3.12 -m venv demoenv && pip install -r requirements.txt"
        exit 1
    fi

    # Source backend/.env so shell-level checks can see the keys.
    # The file may be a symlink to ~/.ai-agent-ui/backend.env.
    if [[ -f "${SCRIPT_DIR}/backend/.env" ]]; then
        set -a
        # shellcheck disable=SC1091
        source "${SCRIPT_DIR}/backend/.env"
        set +a
    fi

    if [[ -z "${GROQ_API_KEY:-}" ]]; then
        echo -e "${Y}  WARNING: GROQ_API_KEY not set — backend will start but chat will fail${N}"
        echo ""
    fi

    # First-time auth DB initialisation (no-op if already initialised)
    _init_auth

    # Ensure stocks Iceberg tables exist (idempotent — safe every start)
    _init_stocks

    # Free any stale processes on our ports
    _free_port "$BACKEND_PORT"
    _free_port "$FRONTEND_PORT"
    _free_port "$DOCS_PORT"
    _free_port "$DASHBOARD_PORT"

    echo "  Launching backend…"
    _launch "backend" "${SCRIPT_DIR}/backend" \
        "$PYTHON" -m uvicorn main:app --port "$BACKEND_PORT"

    echo "  Launching frontend…"
    _launch "frontend" "${SCRIPT_DIR}/frontend" \
        "$NPM" run dev -- --port "$FRONTEND_PORT"

    echo "  Launching docs…"
    _launch "docs" "${SCRIPT_DIR}" \
        "$MKDOCS" serve --dev-addr "127.0.0.1:${DOCS_PORT}"

    echo "  Launching dashboard…"
    _launch "dashboard" "${SCRIPT_DIR}" \
        "$PYTHON" dashboard/app.py

    echo ""
    echo "  Waiting for services to start…"
    sleep 7

    _print_table
    echo -e "  Logs:    ${C}${LOG_DIR}/${N}"
    echo -e "  Stop:    ${B}./run.sh stop${N}"
    echo -e "  Status:  ${B}./run.sh status${N}"
}

do_stop() {
    echo -e "${B}AI Agent UI — stopping services${N}"
    echo ""
    _kill_port "$BACKEND_PORT"   "backend"
    _kill_port "$FRONTEND_PORT"  "frontend"
    _kill_port "$DOCS_PORT"      "docs"
    _kill_port "$DASHBOARD_PORT" "dashboard"
    echo ""
    echo -e "${G}All services stopped.${N}"
}

do_status() {
    _print_table
    echo -e "  Logs: ${C}${LOG_DIR}/${N}"
    echo ""
}

# ── Entry point ───────────────────────────────────────────────────────────────

case "${1:-help}" in
    start)   do_start   ;;
    stop)    do_stop    ;;
    status)  do_status  ;;
    restart) do_stop; sleep 1; do_start ;;
    *)
        echo -e "${B}Usage:${N} $(basename "$0") {start|stop|status|restart}"
        echo ""
        echo "  start    Start all four services in the background"
        echo "  stop     Stop all running services"
        echo "  status   Show PID and URL for each service"
        echo "  restart  Stop then start"
        echo ""
        echo "  Services:"
        printf "    %-12s  %s\n" "backend"   "FastAPI + agentic loop  →  http://127.0.0.1:${BACKEND_PORT}"
        printf "    %-12s  %s\n" "frontend"  "Next.js dev server      →  http://localhost:${FRONTEND_PORT}"
        printf "    %-12s  %s\n" "docs"      "MkDocs material site    →  http://127.0.0.1:${DOCS_PORT}"
        printf "    %-12s  %s\n" "dashboard" "Plotly Dash dashboard   →  http://127.0.0.1:${DASHBOARD_PORT}"
        exit 1
        ;;
esac
