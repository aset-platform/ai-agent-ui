#!/usr/bin/env bash
# scripts/secrets/keychain.sh
#
# Thin wrapper around macOS `security` CLI for managing the
# ai-agent-ui secrets stored in the user's login Keychain.
#
# A "secret" is a generic-password entry keyed by:
#     service: ${SERVICE} (default "aset-platform")
#     account: <slug>     (e.g. "algo_kite_api_secret")
#
# The slug MUST match the docker-compose secret name and the
# load_secret() lookup in backend/secret_loader.py.
#
# Usage:
#   keychain.sh set    <slug>           # prompts (no-echo) for the value
#   keychain.sh set    <slug> <value>   # non-interactive (CI / scripted)
#   keychain.sh get    <slug>
#   keychain.sh delete <slug>
#   keychain.sh list                    # prints known slugs
#   keychain.sh slugs                   # prints the canonical slug list
#
# Companion: scripts/secrets/materialize.sh — pulls every known
# slug from Keychain into ./.secrets/<slug> for docker-compose
# secret-mount consumption.

set -euo pipefail

SERVICE="${ASET_KEYCHAIN_SERVICE:-aset-platform}"

# Canonical secret slugs known to the platform. Adding a new
# secret = add the slug here AND in docker-compose secrets +
# mount it into the consuming service.
SLUGS=(
  "algo_kite_api_secret"
)

usage() {
  sed -n '1,/^$/p' "$0" | sed 's/^# \{0,1\}//' | tail -n +2
  exit "${1:-1}"
}

require_macos() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "ERROR: keychain.sh only supports macOS." >&2
    echo "On Linux/CI use the env-var fallback (see backend/secret_loader.py)." >&2
    exit 2
  fi
}

cmd_set() {
  local slug="$1"
  local value="${2:-}"
  if [[ -z "${value}" ]]; then
    read -r -s -p "Value for ${slug}: " value
    echo
    if [[ -z "${value}" ]]; then
      echo "ERROR: empty value." >&2
      exit 1
    fi
  fi
  # -U updates if the entry already exists.
  security add-generic-password \
    -a "${slug}" \
    -s "${SERVICE}" \
    -w "${value}" \
    -U \
    >/dev/null
  echo "OK ${SERVICE}/${slug} set."
}

cmd_get() {
  local slug="$1"
  security find-generic-password \
    -a "${slug}" \
    -s "${SERVICE}" \
    -w 2>/dev/null
}

cmd_delete() {
  local slug="$1"
  security delete-generic-password \
    -a "${slug}" \
    -s "${SERVICE}" \
    >/dev/null 2>&1 \
    && echo "OK ${SERVICE}/${slug} deleted." \
    || echo "WARN ${SERVICE}/${slug} not found."
}

cmd_list() {
  for slug in "${SLUGS[@]}"; do
    if security find-generic-password \
        -a "${slug}" \
        -s "${SERVICE}" \
        -w >/dev/null 2>&1; then
      echo "✓  ${slug}"
    else
      echo "✗  ${slug}  (not set)"
    fi
  done
}

cmd_slugs() {
  printf '%s\n' "${SLUGS[@]}"
}

main() {
  require_macos
  local action="${1:-help}"
  shift || true
  case "${action}" in
    set)    [[ $# -ge 1 ]] || usage; cmd_set "$@" ;;
    get)    [[ $# -eq 1 ]] || usage; cmd_get "$@" ;;
    delete) [[ $# -eq 1 ]] || usage; cmd_delete "$@" ;;
    list)   cmd_list ;;
    slugs)  cmd_slugs ;;
    help|-h|--help) usage 0 ;;
    *) usage ;;
  esac
}

main "$@"
