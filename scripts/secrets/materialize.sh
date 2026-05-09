#!/usr/bin/env bash
# scripts/secrets/materialize.sh
#
# Pull every known secret from macOS Keychain into ./.secrets/<slug>
# so docker-compose can mount them as native Docker secrets at
# /run/secrets/<slug> inside the container.
#
# This is the local-dev analogue of a production CSI Secrets-Store
# driver: external secret store (Keychain) → container file mount.
#
# Run before `docker compose up` whenever:
#   - You add or rotate a secret in Keychain
#   - You start the stack on a fresh host (.secrets/ is gitignored)
#
# Usage:
#   ./scripts/secrets/materialize.sh                 # all known slugs
#   ./scripts/secrets/materialize.sh <slug> [<slug>]  # only the named ones
#
# Missing-from-Keychain slugs are reported but NOT fatal — the
# backend's secret_loader falls back to env vars / defaults so
# the stack still starts.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
KEYCHAIN_SH="${ROOT}/scripts/secrets/keychain.sh"
OUT_DIR="${ROOT}/.secrets"

if [[ ! -x "${KEYCHAIN_SH}" ]]; then
  echo "ERROR: ${KEYCHAIN_SH} not found or not executable." >&2
  exit 1
fi

mkdir -p "${OUT_DIR}"
chmod 700 "${OUT_DIR}"

if [[ $# -gt 0 ]]; then
  slugs=("$@")
else
  # Portable: macOS bash 3.2 lacks mapfile. Read line-by-line.
  slugs=()
  while IFS= read -r line; do
    [[ -n "${line}" ]] && slugs+=("${line}")
  done < <("${KEYCHAIN_SH}" slugs)
fi

ok=0
missing=0
for slug in "${slugs[@]}"; do
  out="${OUT_DIR}/${slug}"
  if value="$("${KEYCHAIN_SH}" get "${slug}" 2>/dev/null)" \
      && [[ -n "${value}" ]]; then
    # Write atomically — write to .tmp then mv. Avoids the
    # container reading a half-written file mid-flight.
    tmp="${out}.tmp.$$"
    printf '%s' "${value}" > "${tmp}"
    chmod 600 "${tmp}"
    mv "${tmp}" "${out}"
    echo "✓  ${slug} → ${out}"
    ok=$((ok + 1))
  else
    # Write an empty placeholder so docker-compose's `secrets:`
    # block still finds a host file to mount. The backend's
    # secret_loader treats empty files as missing and falls
    # through to the env-var fallback.
    : > "${out}"
    chmod 600 "${out}"
    echo "✗  ${slug}  (not in Keychain — wrote empty placeholder)"
    missing=$((missing + 1))
  fi
done

echo "---"
echo "Materialised: ${ok} ok, ${missing} missing."
exit 0
