#!/usr/bin/env bash
# run.sh — Docker Compose wrapper for AI Agent UI.
#
# Usage:
#   ./run.sh start    — docker compose up + native frontend
#   ./run.sh stop     — docker compose down + kill frontend
#   ./run.sh status   — show service health table
#   ./run.sh restart  — stop then start
#   ./run.sh logs     — tail service logs
#   ./run.sh doctor   — run diagnostic checks
#
# Services (via Docker Compose):
#   postgres   PostgreSQL 16 + pgvector  localhost:5432
#   redis      Redis 7 cache/sessions    localhost:6379
#   backend    FastAPI + agentic loop    localhost:8181
#   docs       MkDocs Material site      localhost:8000
#
# Native (host):
#   frontend   Next.js 16 + Turbopack    localhost:3000
#              (Can't run in Docker — lightningcss .node
#               addons fail in Alpine/Turbopack sandbox)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${HOME}/.ai-agent-ui/logs"
FRONTEND_PORT=3000
NPM="$(command -v npm 2>/dev/null || echo 'npm')"

# ANSI colours (disabled when not writing to a terminal)
if [[ -t 1 ]]; then
    R='\033[0;31m' G='\033[0;32m' Y='\033[1;33m'
    C='\033[0;36m' B='\033[1m' N='\033[0m'
else
    R='' G='' Y='' C='' B='' N=''
fi

mkdir -p "$LOG_DIR"

# ── Helpers ──────────────────────────────────────────

# PIDs listening on a port (newline-separated).
_pids_on_port() {
    local port="$1"
    if command -v lsof &>/dev/null; then
        lsof -ti:"$port" 2>/dev/null || true
    elif command -v ss &>/dev/null; then
        ss -tlnp "sport = :${port}" 2>/dev/null \
            | grep -oP 'pid=\K[0-9]+' | sort -u || true
    elif command -v fuser &>/dev/null; then
        fuser "${port}/tcp" 2>/dev/null \
            | tr -s ' ' '\n' | grep -E '^[0-9]+$' || true
    fi
}

_port_up() { [[ -n "$(_pids_on_port "$1")" ]]; }

_kill_port() {
    local port="$1" label="$2"
    local pids
    pids=$(_pids_on_port "$port")
    [[ -z "$pids" ]] && return 0
    # shellcheck disable=SC2086
    printf "  Stopping %-10s  port %-4s  PID %s\n" \
        "$label" "$port" "$(echo $pids | tr '\n' ' ')"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    local i=0
    while _port_up "$port" && (( i < 15 )); do
        sleep 0.3; (( i++ ))
    done
    pids=$(_pids_on_port "$port")
    # shellcheck disable=SC2086
    [[ -n "$pids" ]] && kill -9 $pids 2>/dev/null || true
}

# ── Docker Compose helpers ───────────────────────────

# Run docker compose from the project directory
_dc() { docker compose -f "${SCRIPT_DIR}/docker-compose.yml" \
    -f "${SCRIPT_DIR}/docker-compose.override.yml" "$@"; }

# Get container status for a service (Up, Exited, etc.)
_dc_status() {
    _dc ps --format '{{.Name}}|{{.Status}}|{{.Health}}' \
        2>/dev/null | grep "$1" | head -1
}

# ── Frontend (native) helpers ────────────────────────

_frontend_running() { _port_up "$FRONTEND_PORT"; }

_frontend_start() {
    if _frontend_running; then
        echo -e "  ${G}Frontend already running on port ${FRONTEND_PORT}${N}"
        return 0
    fi

    # Ensure node_modules exist
    if [[ ! -d "${SCRIPT_DIR}/frontend/node_modules" ]]; then
        echo "  Installing frontend dependencies..."
        (cd "${SCRIPT_DIR}/frontend" && "$NPM" install \
            >> "${LOG_DIR}/frontend.log" 2>&1)
    fi

    echo "  Launching frontend (native — Turbopack)..."
    (cd "${SCRIPT_DIR}/frontend" && exec "$NPM" run dev \
        -- --port "$FRONTEND_PORT" \
        >> "${LOG_DIR}/frontend.log" 2>&1) &
    disown "$!" 2>/dev/null || true
}

_frontend_stop() {
    if ! _frontend_running; then return 0; fi
    _kill_port "$FRONTEND_PORT" "frontend"
}

# ── Status table ─────────────────────────────────────

_DOCKER_SERVICES=(postgres redis backend docs)
_DOCKER_PORTS=(5432 6379 8181 8000)
_DOCKER_URLS=(
    "postgresql://localhost:5432"
    "redis://localhost:6379"
    "http://localhost:8181"
    "http://localhost:8000"
)

_print_table() {
    echo ""
    printf "${B}  %-12s  %-8s  %-32s  %s${N}\n" \
        "Service" "Mode" "URL" "Status"
    printf "  %s\n" \
        "──────────────────────────────────────────────────────────────"

    # Docker services
    for i in "${!_DOCKER_SERVICES[@]}"; do
        local svc="${_DOCKER_SERVICES[$i]}"
        local port="${_DOCKER_PORTS[$i]}"
        local url="${_DOCKER_URLS[$i]}"
        local info state

        info=$(_dc_status "$svc")
        if [[ -n "$info" ]]; then
            local raw_status
            raw_status=$(echo "$info" | cut -d'|' -f2)
            local health
            health=$(echo "$info" | cut -d'|' -f3)

            if echo "$raw_status" | grep -q "Up"; then
                if [[ "$health" == "healthy" ]] \
                    || [[ -z "$health" ]]; then
                    state="${G}● up (docker)${N}"
                else
                    state="${Y}◐ ${health} (docker)${N}"
                fi
            else
                state="${R}○ down${N}"
            fi
        else
            state="${R}○ not created${N}"
        fi
        printf "  %-12s  %-8s  %-32s  " "$svc" "docker" "$url"
        echo -e "$state"
    done

    # Frontend (native)
    local fe_state
    if _frontend_running; then
        fe_state="${G}● up (native)${N}"
    else
        fe_state="${R}○ down${N}"
    fi
    printf "  %-12s  %-8s  %-32s  " \
        "frontend" "native" "http://localhost:${FRONTEND_PORT}"
    echo -e "$fe_state"
    echo ""
}

# ── Commands ─────────────────────────────────────────

do_start() {
    echo -e "${B}AI Agent UI — starting services${N}"
    echo ""

    # Check Docker is running
    if ! docker info &>/dev/null; then
        echo -e "${R}ERROR: Docker is not running.${N}"
        echo "  Start Docker Desktop and try again."
        exit 1
    fi

    # Start Docker services (postgres, redis, backend, docs)
    echo "  Starting Docker services..."
    _dc up -d --build 2>&1 | sed 's/^/    /'
    echo ""

    # Wait for backend health
    echo "  Waiting for backend health check..."
    local attempt=0
    while (( attempt < 30 )); do
        local code
        code=$(curl -s -o /dev/null -w "%{http_code}" \
            --max-time 2 "http://localhost:8181/v1/health" \
            2>/dev/null)
        if [[ "$code" == "200" ]]; then
            echo -e "  ${G}Backend healthy${N}"
            break
        fi
        sleep 2
        (( attempt++ ))
    done
    if (( attempt >= 30 )); then
        echo -e "  ${Y}Backend not responding after 60s — check logs${N}"
    fi

    # Start frontend natively (can't run in Docker)
    _frontend_start

    # Wait for frontend
    sleep 3

    _print_table

    echo -e "  Logs:    ${B}./run.sh logs [service]${N}"
    echo -e "  Stop:    ${B}./run.sh stop${N}"
    echo -e "  Status:  ${B}./run.sh status${N}"
    echo ""
}

do_stop() {
    echo -e "${B}AI Agent UI — stopping services${N}"
    echo ""

    # Stop native frontend first
    _frontend_stop

    # Stop Docker services
    echo "  Stopping Docker services..."
    _dc down 2>&1 | sed 's/^/    /'
    echo ""
    echo -e "${G}All services stopped.${N}"
}

do_status() {
    _print_table
    echo -e "  Logs: ${B}./run.sh logs [service]${N}"
    echo ""
}

# ── Logs command ─────────────────────────────────────

do_logs() {
    local svc="${1:-}" flag="${2:-}"
    local all_services="postgres redis backend docs frontend"

    # ./run.sh logs --errors
    if [[ "$svc" == "--errors" ]]; then
        echo -e "${B}Errors across Docker service logs:${N}"
        echo ""
        for s in postgres redis backend docs; do
            local errs
            errs=$(_dc logs --tail=200 "$s" 2>/dev/null \
                | grep -E "ERROR|CRITICAL|Traceback" \
                | tail -n 20)
            if [[ -n "$errs" ]]; then
                echo -e "  ${R}── $s (docker) ──${N}"
                echo "$errs" | sed 's/^/    /'
                echo ""
            fi
        done
        # Frontend native log
        local fe_log="${LOG_DIR}/frontend.log"
        if [[ -f "$fe_log" ]]; then
            local fe_errs
            fe_errs=$(grep -E "ERROR|error|Error" \
                "$fe_log" 2>/dev/null | tail -n 20)
            if [[ -n "$fe_errs" ]]; then
                echo -e "  ${R}── frontend (native) ──${N}"
                echo "$fe_errs" | sed 's/^/    /'
                echo ""
            fi
        fi
        return
    fi

    # ./run.sh logs <service>
    if [[ -n "$svc" ]]; then
        if ! echo "$all_services" | grep -qw "$svc"; then
            echo -e "${R}Unknown service: $svc${N}"
            echo "  Valid: $all_services"
            return 1
        fi
        if [[ "$svc" == "frontend" ]]; then
            # Frontend logs are local files
            if [[ "$flag" == "--errors" ]]; then
                grep -E "ERROR|error|Error" \
                    "${LOG_DIR}/frontend.log" 2>/dev/null \
                    | tail -n 30 || echo "  No errors found."
            else
                tail -n 50 "${LOG_DIR}/frontend.log" \
                    2>/dev/null \
                    || echo "  No log: ${LOG_DIR}/frontend.log"
            fi
        else
            # Docker service logs
            if [[ "$flag" == "--errors" ]]; then
                _dc logs --tail=200 "$svc" 2>/dev/null \
                    | grep -E "ERROR|CRITICAL|Traceback" \
                    | tail -n 30 \
                    || echo "  No errors found."
            elif [[ "$flag" == "-f" ]] \
                || [[ "$flag" == "--follow" ]]; then
                _dc logs -f "$svc" 2>/dev/null
            else
                _dc logs --tail=50 "$svc" 2>/dev/null
            fi
        fi
        return
    fi

    # ./run.sh logs (all)
    echo -e "${B}Last 50 lines from all service logs:${N}"
    echo ""
    for s in postgres redis backend docs; do
        echo -e "  ${C}── $s (docker) ──${N}"
        _dc logs --tail=50 "$s" 2>/dev/null | sed 's/^/    /'
        echo ""
    done
    echo -e "  ${C}── frontend (native) ──${N}"
    tail -n 50 "${LOG_DIR}/frontend.log" 2>/dev/null \
        | sed 's/^/    /' \
        || echo "    No log file."
    echo ""
}

# ── Doctor command ───────────────────────────────────

do_doctor() {
    echo -e "${B}AI Agent UI — diagnostics${N}"
    echo "──────────────────────────────────────────────────"

    local _pass=0 _fail=0 _warn=0

    _doc_pass() {
        echo -e "  ${G}[PASS]${N} $1"
        _pass=$((_pass+1))
    }
    _doc_fail() {
        echo -e "  ${R}[FAIL]${N} $1"
        _fail=$((_fail+1))
    }
    _doc_warn() {
        echo -e "  ${Y}[WARN]${N} $1"
        _warn=$((_warn+1))
    }

    # 1. Docker running
    if docker info &>/dev/null; then
        _doc_pass "Docker is running"
    else
        _doc_fail "Docker is not running"
        echo "        Fix: Start Docker Desktop"
    fi

    # 2. Docker Compose services
    for svc in postgres redis backend docs; do
        local info
        info=$(_dc_status "$svc")
        if [[ -n "$info" ]]; then
            local raw_status
            raw_status=$(echo "$info" | cut -d'|' -f2)
            local health
            health=$(echo "$info" | cut -d'|' -f3)
            if echo "$raw_status" | grep -q "Up"; then
                if [[ "$health" == "healthy" ]] \
                    || [[ -z "$health" ]]; then
                    _doc_pass "$svc container up and healthy"
                else
                    _doc_warn "$svc container up but $health"
                fi
            else
                _doc_fail "$svc container not running"
                echo "        Fix: ./run.sh start"
            fi
        else
            _doc_fail "$svc container not found"
            echo "        Fix: ./run.sh start"
        fi
    done

    # 3. Frontend (native)
    if _frontend_running; then
        _doc_pass "Frontend (native) responding on port $FRONTEND_PORT"
    else
        _doc_warn "Frontend not running"
        echo "        Fix: ./run.sh start"
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

    # 6. .env file
    local _env="${SCRIPT_DIR}/.env"
    if [[ -f "$_env" ]] || [[ -L "$_env" ]]; then
        _doc_pass ".env file exists"
    else
        _doc_fail ".env file missing"
        echo "        Fix: cp .env.example .env and fill in values"
    fi

    # 7. Iceberg catalog
    local _cat="$HOME/.ai-agent-ui/data/iceberg/catalog.db"
    if [[ -f "$_cat" ]]; then
        _doc_pass "Iceberg catalog exists"
    else
        _doc_fail "Iceberg catalog missing"
        echo "        Fix: ./run.sh start (backend creates on first run)"
    fi

    # 8. Backend health endpoint
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" \
        --max-time 2 "http://localhost:8181/v1/health" \
        2>/dev/null)
    if [[ "$code" == "200" ]]; then
        _doc_pass "Backend /v1/health responding"
    else
        _doc_warn "Backend /v1/health not responding (HTTP $code)"
    fi

    # 9. Docker logs errors
    local _log_errs=0
    for s in postgres redis backend docs; do
        local errs
        errs=$(_dc logs --tail=100 "$s" 2>/dev/null \
            | grep -E "ERROR|CRITICAL|Traceback" | tail -1)
        if [[ -n "$errs" ]]; then
            _log_errs=$((_log_errs + 1))
            _doc_warn "$s log has errors: $errs"
        fi
    done
    if [[ $_log_errs -eq 0 ]]; then
        _doc_pass "No recent errors in Docker logs"
    fi

    # Summary
    echo ""
    echo "──────────────────────────────────────────────────"
    printf "  %s passed" "$_pass"
    [[ $_fail -gt 0 ]] && printf ", ${R}%s failed${N}" "$_fail"
    [[ $_warn -gt 0 ]] && printf ", ${Y}%s warnings${N}" "$_warn"
    echo ""
    echo ""
}

# ── Entry point ──────────────────────────────────────

case "${1:-help}" in
    start)   do_start ;;
    stop)    do_stop ;;
    status)  do_status ;;
    restart) do_stop; sleep 1; do_start ;;
    logs)    do_logs "${2:-}" "${3:-}" ;;
    doctor)  do_doctor ;;
    *)
        echo -e "${B}Usage:${N} $(basename "$0") {start|stop|status|restart|logs|doctor}"
        echo ""
        echo "  start      Start all services (Docker + native frontend)"
        echo "  stop       Stop all services"
        echo "  status     Show health for each service"
        echo "  restart    Stop then start"
        echo "  logs       Tail service logs"
        echo "  doctor     Run diagnostic checks"
        echo ""
        echo "  Logs usage:"
        echo "    ./run.sh logs              All service logs (last 50 lines)"
        echo "    ./run.sh logs backend      Single service log"
        echo "    ./run.sh logs backend -f   Follow (tail -f) a Docker service"
        echo "    ./run.sh logs --errors     Errors across all logs"
        echo "    ./run.sh logs backend --errors  Errors for one service"
        echo ""
        echo "  Services (Docker Compose):"
        printf "    %-12s  %s\n" "postgres" "PostgreSQL 16 →  localhost:5432"
        printf "    %-12s  %s\n" "redis"    "Redis 7       →  localhost:6379"
        printf "    %-12s  %s\n" "backend"  "FastAPI       →  localhost:8181"
        printf "    %-12s  %s\n" "docs"     "MkDocs        →  localhost:8000"
        echo ""
        echo "  Service (Native — Turbopack can't run in Docker):"
        printf "    %-12s  %s\n" "frontend" "Next.js 16    →  localhost:3000"
        exit 1
        ;;
esac
