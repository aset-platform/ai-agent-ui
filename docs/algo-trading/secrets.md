# Algo Trading — Secret management

The algo-trading module needs at least one external secret —
the Kite Connect `api_secret` used during the OAuth handshake.
Storing it in plain text in `.env` works but is not how secrets
are handled in production. The pattern below mirrors a Kubernetes
Secrets-Store CSI driver locally on macOS:

```
macOS Keychain  ──►  ./.secrets/<slug>  ──►  /run/secrets/<slug>  ──►  load_secret()
   (storage)         (host-side mount)        (container path)         (app code)
```

In production, only the **first hop** changes — a real CSI driver
materialises secrets from Vault/AWS/GCP into the same
`/run/secrets/<slug>` path. Application code (`load_secret(...)`)
is identical across environments.

## Resolution order

`backend/secret_loader.load_secret(slug)` tries, in order:

1. **`/run/secrets/<slug>`** — preferred. Used by docker-compose +
   real CSI drivers. Empty file = treat as missing.
2. **`<SLUG_UPPER_SNAKE>` env var** — fallback for CI or hosts
   without the file mount.
3. The `default` argument (or `None`).

## Setting a secret on macOS

```bash
# Interactive prompt (no echo to terminal):
./scripts/secrets/keychain.sh set algo_kite_api_secret

# Non-interactive (CI / scripted):
./scripts/secrets/keychain.sh set algo_kite_api_secret <value>

# Verify it's stored:
./scripts/secrets/keychain.sh list

# Read it back (you'll be prompted by macOS for permission):
./scripts/secrets/keychain.sh get algo_kite_api_secret
```

The wrapper writes a generic-password entry under the
`aset-platform` service in your **login** Keychain.
You can override the service via `ASET_KEYCHAIN_SERVICE=...`.

## Materialising before `docker compose up`

```bash
# Pull every known slug from Keychain into ./.secrets/<slug>:
./scripts/secrets/materialize.sh

# Or just one:
./scripts/secrets/materialize.sh algo_kite_api_secret
```

Each file is written with mode 600. Slugs that are not yet in
Keychain are written as empty placeholders so docker-compose's
mount still resolves; the backend's loader treats the empty file
as missing and falls through to env var.

## Adding a new secret slug

1. Add the slug to the `SLUGS=()` list at the top of
   `scripts/secrets/keychain.sh`.
2. Add a top-level `secrets:` entry in `docker-compose.yml`
   pointing at `./.secrets/<slug>`.
3. Add the slug under the consuming service's `secrets:` key.
4. Read it from Python with `load_secret("<slug>")`.

## CI / Linux

`security` doesn't exist outside macOS. CI workflows just set
the env var directly:

```yaml
env:
  ALGO_KITE_API_SECRET: ${{ secrets.ALGO_KITE_API_SECRET }}
```

The loader's env-var fallback picks it up. No script changes
required.

## Rotation

```bash
./scripts/secrets/keychain.sh set algo_kite_api_secret <new value>
./scripts/secrets/materialize.sh
docker compose restart backend
```

The backend's `load_secret` cache is process-local and resets on
restart.
