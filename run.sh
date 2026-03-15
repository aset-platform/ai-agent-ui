#!/usr/bin/env bash
# run.sh — Start, stop, and monitor all AI Agent UI services.
#
# Usage:
#   ./run.sh start    — start redis, backend, frontend, docs, dashboard
#   ./run.sh stop     — stop all running services
#   ./run.sh status   — show PID and URL for each service
#   ./run.sh restart  — stop then start
#
# Services:
#   redis      Token deny-list store    redis://127.0.0.1:6379
#   backend    FastAPI + agentic loop   http://127.0.0.1:8181
#   frontend   Next.js dev server       http://localhost:3000
#   docs       MkDocs material site     http://127.0.0.1:8000
#   dashboard  Plotly Dash dashboard    http://127.0.0.1:8050

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${HOME}/.ai-agent-ui/logs"
_VENV_HOME="${AI_AGENT_UI_HOME:-${HOME}/.ai-agent-ui}/venv"
# Backwards compat: fall back to old project-local venv
if [[ -x "${_VENV_HOME}/bin/python" ]]; then
    PYTHON="${_VENV_HOME}/bin/python"
    MKDOCS="${_VENV_HOME}/bin/mkdocs"
    GUNICORN="${_VENV_HOME}/bin/gunicorn"
elif [[ -x "${SCRIPT_DIR}/backend/demoenv/bin/python" ]]; then
    PYTHON="${SCRIPT_DIR}/backend/demoenv/bin/python"
    MKDOCS="${SCRIPT_DIR}/backend/demoenv/bin/mkdocs"
    GUNICORN="${SCRIPT_DIR}/backend/demoenv/bin/gunicorn"
else
    PYTHON="${_VENV_HOME}/bin/python"
    MKDOCS="${_VENV_HOME}/bin/mkdocs"
    GUNICORN="${_VENV_HOME}/bin/gunicorn"
fi
NPM="$(command -v npm 2>/dev/null || echo 'npm')"

BACKEND_PORT=8181
FRONTEND_PORT=3000
DOCS_PORT=8000
DASHBOARD_PORT=8050
REDIS_PORT=6379

# ANSI colours (disabled when not writing to a terminal)
if [[ -t 1 ]]; then
    R='\033[0;31m' G='\033[0;32m' Y='\033[1;33m' C='\033[0;36m' B='\033[1m' N='\033[0m'
else
    R='' G='' Y='' C='' B='' N=''
fi

mkdir -p "$LOG_DIR"

# ── Helpers ───────────────────────────────────────────────────────────────────

# Detect WSL environment
_is_wsl() { grep -qi microsoft /proc/version 2>/dev/null; }

# PIDs currently listening on a port (newline-separated).
# Tries lsof (macOS / full Linux), then ss (WSL2 / minimal Linux),
# then fuser as a last resort.
_pids_on_port() {
    local port="$1"
    if command -v lsof &>/dev/null; then
        lsof -ti:"$port" 2>/dev/null || true
    elif command -v ss &>/dev/null; then
        # ss -tlnp shows LISTEN sockets with process info
        ss -tlnp "sport = :${port}" 2>/dev/null \
            | grep -oP 'pid=\K[0-9]+' | sort -u || true
    elif command -v fuser &>/dev/null; then
        fuser "${port}/tcp" 2>/dev/null \
            | tr -s ' ' '\n' | grep -E '^[0-9]+$' || true
    fi
}

# Is anything listening on this port? (PID-based)
_port_up() { [[ -n "$(_pids_on_port "$1")" ]]; }

# Health probe — is a service actually responding?
# Returns 0 if responding, 1 otherwise.
_probe_port() {
    local port="$1"
    if [[ "$port" == "$REDIS_PORT" ]]; then
        redis-cli -p "$port" ping &>/dev/null 2>&1
    else
        curl -sf -o /dev/null --max-time 2 \
            "http://127.0.0.1:${port}/" 2>/dev/null
    fi
}

# Last N lines from a service log (default 5)
_tail_log() {
    local name="$1" lines="${2:-5}"
    local logfile="${LOG_DIR}/${name}.log"
    if [[ -f "$logfile" ]]; then
        tail -n "$lines" "$logfile"
    fi
}

# Suggest a fix based on error patterns in log
_suggest_fix() {
    local name="$1"
    local logfile="${LOG_DIR}/${name}.log"
    [[ -f "$logfile" ]] || return 0
    local last100
    last100="$(tail -n 100 "$logfile" 2>/dev/null)"

    if echo "$last100" | grep -q "ModuleNotFoundError"; then
        local mod
        mod=$(echo "$last100" | grep -oP "No module named '\K[^']+")
        echo "  Fix: source ~/.ai-agent-ui/venv/bin/activate && pip install $mod"
    elif echo "$last100" | grep -q "Address already in use"; then
        echo "  Fix: ./run.sh stop  (or kill the process on that port)"
    elif echo "$last100" | grep -q "ENOSPC.*inotify"; then
        echo "  Fix: echo fs.inotify.max_user_watches=524288 | sudo tee -a /etc/sysctl.conf && sudo sysctl -p"
    elif echo "$last100" | grep -q "Too many levels of symbolic links"; then
        echo "  Fix: ./setup.sh --repair"
    elif echo "$last100" | grep -q "JWT_SECRET_KEY"; then
        echo "  Fix: Add JWT_SECRET_KEY to ~/.ai-agent-ui/backend.env"
    elif echo "$last100" | grep -q "ANTHROPIC_API_KEY"; then
        echo "  Fix: Add ANTHROPIC_API_KEY to ~/.ai-agent-ui/backend.env"
    fi
}

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

# ── Redis helpers ─────────────────────────────────────────────────────────────

_redis_running() { redis-cli -p "$REDIS_PORT" ping &>/dev/null 2>&1; }

_redis_start() {
    if _redis_running; then
        echo -e "  ${G}Redis already running on port ${REDIS_PORT}${N}"
        return 0
    fi
    if ! command -v redis-server &>/dev/null; then
        echo -e "  ${Y}redis-server not found — token store will use in-memory fallback${N}"
        return 0
    fi
    echo "  Starting Redis…"
    local os_name
    os_name="$(uname -s)"
    if [[ "$os_name" == "Darwin" ]] && command -v brew &>/dev/null; then
        brew services start redis &>/dev/null
    elif command -v systemctl &>/dev/null \
         && systemctl is-system-running &>/dev/null 2>&1; then
        sudo systemctl start redis-server 2>/dev/null || \
            sudo systemctl start redis 2>/dev/null || true
    else
        # No systemd (typical WSL2) — launch directly as daemon
        redis-server --port "$REDIS_PORT" --daemonize yes \
            --logfile "${LOG_DIR}/redis.log" 2>/dev/null
    fi
    # Wait up to 5 seconds for Redis to accept connections.
    local attempt=0
    while ! _redis_running && (( attempt < 10 )); do
        sleep 0.5
        (( attempt++ ))
    done
    if _redis_running; then
        echo -e "  ${G}Redis started on port ${REDIS_PORT}${N}"
    else
        echo -e "  ${Y}Redis failed to start — token store will use in-memory fallback${N}"
    fi
}

_redis_stop() {
    if ! _redis_running; then return 0; fi
    echo "  Stopping Redis…"
    local os_name
    os_name="$(uname -s)"
    if [[ "$os_name" == "Darwin" ]] && command -v brew &>/dev/null; then
        brew services stop redis &>/dev/null
    elif command -v systemctl &>/dev/null \
         && systemctl is-system-running &>/dev/null 2>&1; then
        sudo systemctl stop redis-server 2>/dev/null || \
            sudo systemctl stop redis 2>/dev/null || true
    else
        redis-cli -p "$REDIS_PORT" shutdown nosave 2>/dev/null || true
    fi
    echo -e "  ${G}Redis stopped${N}"
}

# ── Status table ──────────────────────────────────────────────────────────────

_SERVICE_NAMES=(redis     backend   frontend   docs      dashboard)
_SERVICE_PORTS=($REDIS_PORT $BACKEND_PORT $FRONTEND_PORT $DOCS_PORT $DASHBOARD_PORT)
_SERVICE_URLS=(
    "redis://127.0.0.1:${REDIS_PORT}"
    "http://127.0.0.1:${BACKEND_PORT}"
    "http://localhost:${FRONTEND_PORT}"
    "http://127.0.0.1:${DOCS_PORT}"
    "http://127.0.0.1:${DASHBOARD_PORT}"
)

_print_table() {
    echo ""
    printf "${B}  %-12s  %-8s  %-32s  %s${N}\n" \
        "Service" "PID" "URL" "Status"
    printf "  %s\n" \
        "──────────────────────────────────────────────────────────"

    for i in "${!_SERVICE_NAMES[@]}"; do
        local pid state port
        port="${_SERVICE_PORTS[$i]}"
        pid=$(_pids_on_port "$port")
        local responding=0
        _probe_port "$port" && responding=1

        if [[ -n "$pid" ]] && [[ $responding -eq 1 ]]; then
            pid=$(echo "$pid" | head -1)
            state="${G}● up${N}"
        elif [[ $responding -eq 1 ]]; then
            pid="—"
            state="${Y}◐ listening${N}"
        else
            pid="—"
            state="${R}○ down${N}"
        fi
        printf "  %-12s  %-8s  %-32s  " \
            "${_SERVICE_NAMES[$i]}" "$pid" "${_SERVICE_URLS[$i]}"
        echo -e "$state"
    done
    echo ""
}

# ── Auto-migrate data from project root to ~/.ai-agent-ui ─────────────────────

_maybe_migrate_data() {
    local old_catalog="${SCRIPT_DIR}/data/iceberg/catalog.db"
    local new_catalog="${HOME}/.ai-agent-ui/data/iceberg/catalog.db"
    if [[ -f "$old_catalog" ]] && [[ ! -f "$new_catalog" ]]; then
        echo -e "${Y}  Migrating data from project root to ~/.ai-agent-ui …${N}"
        "$PYTHON" scripts/migrate_data_home.py --apply
        echo -e "${G}  Data migration complete.${N}"
        echo ""
    fi
}

# ── First-time auth initialisation ────────────────────────────────────────────

# Run Iceberg table creation + admin seed on first start (idempotent guard:
# if data/iceberg/catalog.db already exists, the function returns immediately).
_init_auth() {
    local catalog="${HOME}/.ai-agent-ui/data/iceberg/catalog.db"
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

    # Seed demo data on first run (idempotent — skips if data exists)
    if [[ "${SKIP_SEED:-}" != "1" ]]; then
        (cd "$SCRIPT_DIR" && "$PYTHON" scripts/seed_demo_data.py \
              >> "${LOG_DIR}/seed_demo.log" 2>&1) || true
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

    # Start Redis (token deny-list + OAuth state store)
    _redis_start

    # Auto-migrate data from project root to ~/.ai-agent-ui (one-time)
    _maybe_migrate_data

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
    # macOS Obj-C runtime aborts forked workers unless this is set.
    # Harmless on Linux. Gunicorn gthread uses 1 process + 4 threads
    # so parallel E2E requests are handled without blocking.
    OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES \
    _launch "dashboard" "${SCRIPT_DIR}" \
        "$GUNICORN" "dashboard.app:server" \
        --bind "127.0.0.1:${DASHBOARD_PORT}" \
        --worker-class gthread \
        --workers 1 \
        --threads 4 \
        --timeout 120 \
        --access-logfile -

    echo ""
    echo "  Waiting for services to start…"
    sleep 7

    _print_table

    # Post-launch health check — show errors for failed services
    local _any_fail=0
    for i in "${!_SERVICE_NAMES[@]}"; do
        local _sname="${_SERVICE_NAMES[$i]}"
        local _sport="${_SERVICE_PORTS[$i]}"
        if ! _probe_port "$_sport"; then
            _any_fail=1
            echo -e "  ${R}✗ ${_sname} (port ${_sport}) — not responding${N}"
            local _last
            _last="$(_tail_log "$_sname" 5)"
            if [[ -n "$_last" ]]; then
                echo "    Last log lines:"
                echo "$_last" | sed 's/^/      /'
            fi
            _suggest_fix "$_sname"
            echo ""
        fi
    done
    if [[ $_any_fail -eq 0 ]]; then
        echo -e "  ${G}All services responding.${N}"
    else
        echo -e "  ${Y}Some services failed. Run: ./run.sh doctor${N}"
    fi
    echo ""
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
    _redis_stop
    echo ""
    echo -e "${G}All services stopped.${N}"
}

do_status() {
    _print_table
    echo -e "  Logs: ${C}${LOG_DIR}/${N}"
    echo ""
}

# ── Logs command ──────────────────────────────────────────────────────────────

do_logs() {
    local svc="${1:-}" flag="${2:-}"
    local all_services="redis backend frontend docs dashboard"

    # ./run.sh logs --errors
    if [[ "$svc" == "--errors" ]]; then
        echo -e "${B}Errors across all service logs:${N}"
        echo ""
        for s in $all_services; do
            local lf="${LOG_DIR}/${s}.log"
            [[ -f "$lf" ]] || continue
            local errs
            errs=$(grep -E "ERROR|CRITICAL|Traceback" \
                "$lf" 2>/dev/null | tail -n 20)
            if [[ -n "$errs" ]]; then
                echo -e "  ${R}── $s ──${N}"
                echo "$errs" | sed 's/^/    /'
                echo ""
            fi
        done
        return
    fi

    # ./run.sh logs <service>
    if [[ -n "$svc" ]]; then
        if ! echo "$all_services" | grep -qw "$svc"; then
            echo -e "${R}Unknown service: $svc${N}"
            echo "  Valid: $all_services"
            return 1
        fi
        # ./run.sh logs <service> --errors
        if [[ "$flag" == "--errors" ]]; then
            grep -E "ERROR|CRITICAL|Traceback" \
                "${LOG_DIR}/${svc}.log" 2>/dev/null \
                | tail -n 30 || echo "  No errors found."
        else
            tail -n 50 "${LOG_DIR}/${svc}.log" 2>/dev/null \
                || echo "  No log file: ${LOG_DIR}/${svc}.log"
        fi
        return
    fi

    # ./run.sh logs (all)
    echo -e "${B}Last 50 lines from all service logs:${N}"
    echo ""
    for s in $all_services; do
        local lf="${LOG_DIR}/${s}.log"
        [[ -f "$lf" ]] || continue
        echo -e "  ${C}── $s ──${N}"
        tail -n 50 "$lf" | sed 's/^/    /'
        echo ""
    done
}

# ── Doctor command ────────────────────────────────────────────────────────────

do_doctor() {
    echo -e "${B}AI Agent UI — diagnostics${N}"
    echo "──────────────────────────────────────────────────────────"

    local _pass=0 _fail=0 _warn=0

    _doc_pass() { echo -e "  ${G}[PASS]${N} $1"; _pass=$((_pass+1)); }
    _doc_fail() { echo -e "  ${R}[FAIL]${N} $1"; _fail=$((_fail+1)); }
    _doc_warn() { echo -e "  ${Y}[WARN]${N} $1"; _warn=$((_warn+1)); }

    # 1. Python virtualenv
    if [[ -f "$PYTHON" ]]; then
        _doc_pass "Python virtualenv ($("$PYTHON" --version 2>&1))"
    else
        _doc_fail "Python virtualenv not found at $PYTHON"
        echo "        Fix: ./setup.sh"
    fi

    # 2. backend/.env exists and readable
    local _benv="${SCRIPT_DIR}/backend/.env"
    if [[ -f "$_benv" ]] || [[ -L "$_benv" ]]; then
        if [[ -r "$_benv" ]]; then
            _doc_pass "backend/.env exists and readable"
        else
            _doc_fail "backend/.env exists but not readable"
            echo "        Fix: ./setup.sh --repair"
        fi
    else
        _doc_fail "backend/.env missing"
        echo "        Fix: ./setup.sh --repair"
    fi

    # 3. Required env vars
    if [[ -f "$_benv" ]]; then
        set -a; source "$_benv" 2>/dev/null; set +a
    fi
    if [[ -n "${JWT_SECRET_KEY:-}" ]]; then
        _doc_pass "JWT_SECRET_KEY is set"
    else
        _doc_fail "JWT_SECRET_KEY is empty"
        echo "        Fix: Add to ~/.ai-agent-ui/backend.env"
    fi
    if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
        _doc_pass "ANTHROPIC_API_KEY is set"
    else
        _doc_fail "ANTHROPIC_API_KEY is empty"
        echo "        Fix: Add to ~/.ai-agent-ui/backend.env"
    fi

    # 4. Node.js
    if command -v node &>/dev/null; then
        local _nv
        _nv="$(node --version)"
        local _nm="${_nv#v}"; _nm="${_nm%%.*}"
        if [[ "$_nm" -ge 18 ]]; then
            _doc_pass "Node.js $_nv"
        else
            _doc_fail "Node.js $_nv too old (need 18+)"
        fi
    else
        _doc_fail "Node.js not installed"
        echo "        Fix: Install from https://nodejs.org"
    fi

    # 5. node_modules
    if [[ -d "${SCRIPT_DIR}/frontend/node_modules" ]]; then
        _doc_pass "frontend/node_modules exists"
    else
        _doc_fail "frontend/node_modules missing"
        echo "        Fix: cd frontend && npm ci"
    fi

    # 6. Redis
    if _redis_running; then
        _doc_pass "Redis responding"
    elif command -v redis-server &>/dev/null; then
        _doc_warn "Redis installed but not responding"
        echo "        Fix: ./run.sh start  (or redis-server --daemonize yes)"
    else
        _doc_warn "redis-server not installed (in-memory fallback will be used)"
    fi

    # 7. Port checks
    for i in "${!_SERVICE_NAMES[@]}"; do
        local _sn="${_SERVICE_NAMES[$i]}"
        local _sp="${_SERVICE_PORTS[$i]}"
        local _pid
        _pid=$(_pids_on_port "$_sp")
        if [[ -n "$_pid" ]]; then
            if _probe_port "$_sp"; then
                _doc_pass "Port $_sp ($_sn) — responding (PID $(echo $_pid | head -1))"
            else
                _doc_warn "Port $_sp ($_sn) — PID $(echo $_pid | head -1) but not responding"
            fi
        elif _probe_port "$_sp"; then
            _doc_pass "Port $_sp ($_sn) — responding"
        fi
        # Don't report "port free" — that's normal when services aren't running
    done

    # 8. Iceberg catalog
    local _cat="$HOME/.ai-agent-ui/data/iceberg/catalog.db"
    if [[ -f "$_cat" ]]; then
        _doc_pass "Iceberg catalog exists"
    else
        _doc_fail "Iceberg catalog missing"
        echo "        Fix: ./run.sh start  (auto-creates on first run)"
    fi

    # 9. Scan logs for errors
    local _log_errs=0
    for s in "${_SERVICE_NAMES[@]}"; do
        local _lf="${LOG_DIR}/${s}.log"
        [[ -f "$_lf" ]] || continue
        local _last100
        _last100="$(tail -n 100 "$_lf" 2>/dev/null)"
        if echo "$_last100" | grep -qE "ERROR|CRITICAL|Traceback"; then
            _log_errs=$((_log_errs + 1))
            local _top_err
            _top_err=$(echo "$_last100" \
                | grep -E "ERROR|CRITICAL|Traceback" \
                | tail -1)
            _doc_warn "$s log has errors: $_top_err"
            _suggest_fix "$s"
        fi
    done
    if [[ $_log_errs -eq 0 ]]; then
        _doc_pass "No recent errors in service logs"
    fi

    # Summary
    echo ""
    echo "──────────────────────────────────────────────────────────"
    printf "  %s passed" "$_pass"
    [[ $_fail -gt 0 ]] && printf ", ${R}%s failed${N}" "$_fail"
    [[ $_warn -gt 0 ]] && printf ", ${Y}%s warnings${N}" "$_warn"
    echo ""
    echo ""
}

# ── Entry point ───────────────────────────────────────────────────────────────

case "${1:-help}" in
    start)   do_start   ;;
    stop)    do_stop    ;;
    status)  do_status  ;;
    restart) do_stop; sleep 1; do_start ;;
    logs)    do_logs "${2:-}" "${3:-}" ;;
    doctor)  do_doctor  ;;
    *)
        echo -e "${B}Usage:${N} $(basename "$0") {start|stop|status|restart|logs|doctor}"
        echo ""
        echo "  start    Start all services in the background"
        echo "  stop     Stop all running services"
        echo "  status   Show PID, URL, and health for each service"
        echo "  restart  Stop then start"
        echo "  logs     Tail service logs"
        echo "  doctor   Run diagnostic checks with fix suggestions"
        echo ""
        echo "  Logs usage:"
        echo "    ./run.sh logs              All service logs (last 50 lines)"
        echo "    ./run.sh logs backend      Single service log"
        echo "    ./run.sh logs --errors     Errors across all logs"
        echo "    ./run.sh logs backend --errors  Errors for one service"
        echo ""
        echo "  Services:"
        printf "    %-12s  %s\n" "redis"     "Token store →  redis://127.0.0.1:${REDIS_PORT}"
        printf "    %-12s  %s\n" "backend"   "FastAPI     →  http://127.0.0.1:${BACKEND_PORT}"
        printf "    %-12s  %s\n" "frontend"  "Next.js     →  http://localhost:${FRONTEND_PORT}"
        printf "    %-12s  %s\n" "docs"      "MkDocs      →  http://127.0.0.1:${DOCS_PORT}"
        printf "    %-12s  %s\n" "dashboard" "Plotly Dash →  http://127.0.0.1:${DASHBOARD_PORT}"
        exit 1
        ;;
esac
