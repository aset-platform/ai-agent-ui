#!/usr/bin/env bash
# Migrates BYO_SECRET_KEY from .env into macOS Keychain.
# Idempotent: re-running with the same value is a no-op.
#
# Usage:
#   ./scripts/migrate_byo_secret_to_keychain.sh
#
# After a successful migration:
#   1. Remove the BYO_SECRET_KEY line from .env manually.
#   2. Run: docker compose up -d --force-recreate backend
#
# See docs/algo-trading/secrets.md for the full recipe.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

ENV_FILE="${ENV_FILE:-${ROOT}/.env}"
SLUG="byo_secret_key"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERR: $ENV_FILE not found"; exit 1
fi

VALUE="$(grep -E '^BYO_SECRET_KEY=' "$ENV_FILE" \
    | head -1 | cut -d= -f2-)"
if [[ -z "$VALUE" ]]; then
  echo "ERR: BYO_SECRET_KEY not set in $ENV_FILE"; exit 1
fi

# Strip surrounding quotes if present
VALUE="${VALUE%\"}"; VALUE="${VALUE#\"}"
VALUE="${VALUE%\'}"; VALUE="${VALUE#\'}"

"${SCRIPT_DIR}/secrets/keychain.sh" set "$SLUG" "$VALUE"
"${SCRIPT_DIR}/secrets/materialize.sh"

echo
echo "Migrated BYO_SECRET_KEY -> Keychain slug '$SLUG'"
echo "  Now remove the BYO_SECRET_KEY line from $ENV_FILE manually,"
echo "  then run: docker compose up -d --force-recreate backend"
