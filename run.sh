#!/usr/bin/env bash
# run.sh — Docker Compose wrapper for AI Agent UI.
#
# Usage:
#   ./run.sh start   [service] — start all or one service
#   ./run.sh stop    [service] — stop all or one service
#   ./run.sh restart [service] — restart (no rebuild)
#   ./run.sh build   [service] — build images only
#   ./run.sh rebuild [service] — build + restart (after code changes)
#   ./run.sh status            — show service health table
#   ./run.sh logs              — tail service logs
#   ./run.sh doctor            — run diagnostic checks
#   ./run.sh ngrok {up|down|status} — manage ngrok tunnel
#                                     (token from macOS Keychain)
#
# Services (all via Docker Compose):
#   postgres   PostgreSQL 16 + pgvector  localhost:5432
#   redis      Redis 7 cache/sessions    localhost:6379
#   backend    FastAPI + agentic loop    localhost:8181
#   frontend   Next.js 16 (Debian slim)  localhost:3000
#   docs       MkDocs Material site      localhost:8000

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${HOME}/.ai-agent-ui/logs"

# ANSI colours (disabled when not writing to a terminal)
if [[ -t 1 ]]; then
    R='\033[0;31m' G='\033[0;32m' Y='\033[1;33m'
    C='\033[0;36m' B='\033[1m' N='\033[0m'
else
    R='' G='' Y='' C='' B='' N=''
fi

mkdir -p "$LOG_DIR"

# ── Helpers ──────────────────────────────────────────

# ── Docker Compose helpers ───────────────────────────

# Run docker compose from the project directory
_dc() { docker compose -f "${SCRIPT_DIR}/docker-compose.yml" \
    -f "${SCRIPT_DIR}/docker-compose.override.yml" "$@"; }

# ── ngrok / Keychain helpers ─────────────────────────

# Read the ngrok authtoken from the macOS Keychain.
# Returns empty string if not present (caller decides whether that's OK).
_ngrok_authtoken_from_keychain() {
    security find-generic-password \
        -a ngrok_authtoken -s ai-agent-ui -w 2>/dev/null || true
}

# Returns 0 if ngrok should be auto-included with --profile live:
#   - NGROK_DOMAIN is set in .env
#   - Keychain entry exists with non-empty value
_ngrok_enabled() {
    local env_file="${SCRIPT_DIR}/.env"
    [[ -f "$env_file" ]] || return 1
    grep -q "^NGROK_DOMAIN=." "$env_file" || return 1
    [[ -n "$(_ngrok_authtoken_from_keychain)" ]] || return 1
    return 0
}

# Run docker compose with --profile live + NGROK_AUTHTOKEN injected from
# Keychain when ngrok is enabled; otherwise plain _dc.
_dc_with_ngrok() {
    if _ngrok_enabled; then
        NGROK_AUTHTOKEN="$(_ngrok_authtoken_from_keychain)" \
            _dc --profile live "$@"
    else
        _dc "$@"
    fi
}

# Get container status for a service (Up, Exited, etc.)
_dc_status() {
    # `ps -a` surfaces Created / Exited states. Without `-a`
    # docker compose only shows running containers and the
    # status table renders "not created" for a container
    # that's actually in Created or Exited state.
    _dc ps -a --format '{{.Name}}|{{.Status}}|{{.Health}}' \
        2>/dev/null | grep "$1" | head -1
}

# ── Status table ─────────────────────────────────────

_DOCKER_SERVICES=(postgres redis backend frontend docs)
_DOCKER_PORTS=(5432 6379 8181 3000 8000)
_DOCKER_URLS=(
    "postgresql://localhost:5432"
    "redis://localhost:6379"
    "http://localhost:8181"
    "http://localhost:3000"
    "http://localhost:8000"
)

_print_table() {
    echo ""
    printf "${B}  %-12s  %-32s  %s${N}\n" \
        "Service" "URL" "Status"
    printf "  %s\n" \
        "──────────────────────────────────────────────────────────"

    for i in "${!_DOCKER_SERVICES[@]}"; do
        local svc="${_DOCKER_SERVICES[$i]}"
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
                    state="${G}● up${N}"
                else
                    state="${Y}◐ ${health}${N}"
                fi
            else
                state="${R}○ down${N}"
            fi
        else
            state="${R}○ not created${N}"
        fi
        printf "  %-12s  %-32s  " "$svc" "$url"
        echo -e "$state"
    done
    echo ""
}

# ── Per-service helpers ──────────────────────────────

_validate_service() {
    local svc="$1"
    case "$svc" in
        postgres|redis|backend|docs|frontend) return 0 ;;
        *)
            echo -e "${R}Unknown service: ${svc}${N}"
            echo "  Valid: postgres redis backend docs frontend"
            exit 1
            ;;
    esac
}

_start_service() {
    local svc="$1"
    _validate_service "$svc"
    echo -e "${B}Starting ${svc}...${N}"
    _dc start "$svc" 2>&1 | sed 's/^/    /'
    echo -e "  ${G}${svc} started${N}"
}

_stop_service() {
    local svc="$1"
    _validate_service "$svc"
    echo -e "${B}Stopping ${svc}...${N}"
    _dc stop "$svc" 2>&1 | sed 's/^/    /'
    echo -e "  ${G}${svc} stopped${N}"
}

_build_service() {
    local svc="$1"
    _validate_service "$svc"
    echo -e "${B}Building ${svc}...${N}"
    _dc build "$svc" 2>&1 | sed 's/^/    /'
    echo -e "  ${G}${svc} built${N}"
}

_rebuild_service() {
    local svc="$1"
    _validate_service "$svc"
    echo -e "${B}Rebuilding and restarting ${svc}...${N}"
    _dc up -d --build "$svc" 2>&1 | sed 's/^/    /'
    echo -e "  ${G}${svc} rebuilt and running${N}"
}

# ── Commands ─────────────────────────────────────────

do_start() {
    local svc="${1:-}"
    if [[ -n "$svc" ]]; then
        _start_service "$svc"
        return
    fi

    echo -e "${B}AI Agent UI — starting services${N}"
    echo ""

    # Check Docker is running
    if ! docker info &>/dev/null; then
        echo -e "${R}ERROR: Docker is not running.${N}"
        echo "  Start Docker Desktop and try again."
        exit 1
    fi

    # Start all Docker services. Auto-includes the `live` profile (and
    # injects NGROK_AUTHTOKEN from Keychain) when ngrok is configured.
    echo "  Starting Docker services..."
    if _ngrok_enabled; then
        echo "    (ngrok detected — including --profile live)"
    fi
    _dc_with_ngrok up -d --build 2>&1 | sed 's/^/    /'
    echo ""

    # Wait for backend health
    echo "  Waiting for backend health check..."
    local attempt=0
    while (( attempt < 90 )); do
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
    if (( attempt >= 90 )); then
        echo -e "  ${Y}Backend not responding after 180s — check logs${N}"
    fi

    # Wait for frontend container
    sleep 3

    # Resume any service stuck in Created/Exited state. This
    # happens when `up -d --build` returns before a slow build
    # finishes its post-build start step, or when a
    # depends_on healthcheck races with the parent service's
    # transition to healthy. Symptom: container exists but
    # never reaches Running. Fix: explicit `start` per stuck
    # service; report which ones we resumed.
    local stuck=()
    for s in postgres redis backend frontend docs; do
        local raw
        raw=$(_dc_status "$s" | cut -d'|' -f2)
        if [[ -n "$raw" ]] && ! echo "$raw" | grep -q "Up"; then
            stuck+=("$s")
        fi
    done
    if (( ${#stuck[@]} > 0 )); then
        echo "  Resuming stuck services: ${stuck[*]}"
        for s in "${stuck[@]}"; do
            _dc start "$s" 2>&1 | sed 's/^/    /'
        done
        sleep 3
    fi

    _print_table

    echo -e "  Logs:    ${B}./run.sh logs [service]${N}"
    echo -e "  Stop:    ${B}./run.sh stop${N}"
    echo -e "  Status:  ${B}./run.sh status${N}"
    echo ""
}

do_stop() {
    local svc="${1:-}"
    if [[ -n "$svc" ]]; then
        _stop_service "$svc"
        return
    fi

    echo -e "${B}AI Agent UI — stopping services${N}"
    echo ""

    # Stop all Docker services
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
        for s in postgres redis backend frontend docs; do
            local errs
            errs=$(_dc logs --tail=200 "$s" 2>/dev/null \
                | grep -E "ERROR|CRITICAL|Traceback" \
                | tail -n 20)
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
        return
    fi

    # ./run.sh logs (all)
    echo -e "${B}Last 50 lines from all service logs:${N}"
    echo ""
    for s in postgres redis backend frontend docs; do
        echo -e "  ${C}── $s ──${N}"
        _dc logs --tail=50 "$s" 2>/dev/null | sed 's/^/    /'
        echo ""
    done
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

    # 3. .env file
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
    for s in postgres redis backend frontend docs; do
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

# ── ngrok subcommand ─────────────────────────────────

do_ngrok() {
    local action="${1:-status}"

    case "$action" in
        up)
            local token
            token="$(_ngrok_authtoken_from_keychain)"
            if [[ -z "$token" ]]; then
                echo -e "${R}ERROR:${N} no ngrok authtoken in Keychain."
                echo "  Store it with:"
                echo "    security add-generic-password -a ngrok_authtoken \\"
                echo "      -s ai-agent-ui -w '<your-token>' -U"
                exit 1
            fi
            local domain
            domain=$(grep "^NGROK_DOMAIN=" "${SCRIPT_DIR}/.env" \
                2>/dev/null | cut -d= -f2-)
            if [[ -z "$domain" ]]; then
                echo -e "${R}ERROR:${N} NGROK_DOMAIN not set in .env."
                echo "  Add: NGROK_DOMAIN=<your-domain>.ngrok-free.dev"
                exit 1
            fi
            echo -e "${B}Starting ngrok tunnel → ${domain}${N}"
            NGROK_AUTHTOKEN="$token" \
                _dc --profile live up -d ngrok 2>&1 | sed 's/^/  /'
            sleep 3
            local code
            code=$(curl -s -o /dev/null -w "%{http_code}" \
                --max-time 5 "https://${domain}/v1/health" 2>/dev/null)
            if [[ "$code" == "200" ]]; then
                echo -e "  ${G}Tunnel reachable: https://${domain}${N}"
                echo -e "  ${C}Inspector: http://localhost:4040${N}"
            else
                echo -e "  ${Y}Tunnel started but health probe got HTTP ${code}.${N}"
                echo -e "  ${Y}Backend may still be warming. Try in a few seconds.${N}"
            fi
            ;;
        down)
            echo -e "${B}Stopping ngrok...${N}"
            _dc --profile live stop ngrok 2>&1 | sed 's/^/  /'
            _dc --profile live rm -f ngrok 2>&1 | sed 's/^/  /'
            ;;
        status)
            local row
            row=$(_dc --profile live ps ngrok 2>/dev/null \
                | tail -n +2)
            if [[ -z "$row" ]]; then
                echo -e "  ${Y}ngrok not running${N}"
                _ngrok_enabled \
                    && echo -e "  Run: ${C}./run.sh ngrok up${N}" \
                    || echo -e "  Setup: store token in Keychain + set NGROK_DOMAIN in .env"
                return
            fi
            echo "$row"
            local domain
            domain=$(grep "^NGROK_DOMAIN=" "${SCRIPT_DIR}/.env" \
                2>/dev/null | cut -d= -f2-)
            if [[ -n "$domain" ]]; then
                echo -e "  ${C}URL:${N} https://${domain}"
            fi
            echo -e "  ${C}Inspector:${N} http://localhost:4040"
            ;;
        *)
            echo "Usage: $(basename "$0") ngrok {up|down|status}"
            exit 1
            ;;
    esac
}

# ── Entry point ──────────────────────────────────────

case "${1:-help}" in
    start)   do_start "${2:-}" ;;
    stop)    do_stop "${2:-}" ;;
    status)  do_status ;;
    restart)
        if [[ -n "${2:-}" ]]; then
            _stop_service "$2"; sleep 1; _start_service "$2"
        else
            do_stop; sleep 1; do_start
        fi
        ;;
    build)
        if [[ -n "${2:-}" ]]; then
            _build_service "$2"
        else
            echo -e "${B}Building all services...${N}"
            _dc build 2>&1 | sed 's/^/    /'
            echo -e "  ${G}All services built${N}"
        fi
        ;;
    rebuild)
        if [[ -n "${2:-}" ]]; then
            _rebuild_service "$2"
        else
            echo -e "${B}Rebuilding all services...${N}"
            _dc up -d --build 2>&1 | sed 's/^/    /'
            echo -e "  ${G}All services rebuilt and running${N}"
        fi
        ;;
    logs)    do_logs "${2:-}" "${3:-}" ;;
    doctor)  do_doctor ;;
    ngrok)   do_ngrok "${2:-status}" ;;
    *)
        echo -e "${B}Usage:${N} $(basename "$0") {start|stop|restart|build|rebuild|status|logs|doctor|ngrok} [service|action]"
        echo ""
        echo "  start   [service]  Start all or one service"
        echo "  stop    [service]  Stop all or one service"
        echo "  restart [service]  Restart all or one service (no rebuild)"
        echo "  build   [service]  Build images only (no restart)"
        echo "  rebuild [service]  Build + restart (use after code changes)"
        echo "  status             Show health for each service"
        echo "  logs               Tail service logs"
        echo "  doctor             Run diagnostic checks"
        echo ""
        echo "  Examples:"
        echo "    ./run.sh start              Start all services"
        echo "    ./run.sh restart frontend   Restart only frontend (no rebuild)"
        echo "    ./run.sh rebuild frontend   Rebuild + restart frontend"
        echo "    ./run.sh rebuild backend    Rebuild + restart backend"
        echo "    ./run.sh build              Build all images"
        echo "    ./run.sh stop redis         Stop only Redis"
        echo ""
        echo "  Logs usage:"
        echo "    ./run.sh logs              All service logs (last 50 lines)"
        echo "    ./run.sh logs backend      Single service log"
        echo "    ./run.sh logs backend -f   Follow (tail -f) a Docker service"
        echo "    ./run.sh logs --errors     Errors across all logs"
        echo "    ./run.sh logs backend --errors  Errors for one service"
        echo ""
        echo "  Services (all Docker Compose):"
        printf "    %-12s  %s\n" "postgres" "PostgreSQL 16 →  localhost:5432"
        printf "    %-12s  %s\n" "redis"    "Redis 7       →  localhost:6379"
        printf "    %-12s  %s\n" "backend"  "FastAPI       →  localhost:8181"
        printf "    %-12s  %s\n" "frontend" "Next.js 16    →  localhost:3000"
        printf "    %-12s  %s\n" "docs"     "MkDocs        →  localhost:8000"
        exit 1
        ;;
esac
