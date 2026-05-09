# Local-dev secrets — Keychain → docker-compose mount (CSI-style)

Mirrors a production CSI Secrets-Store driver locally on macOS so application code is environment-agnostic.

```
macOS Keychain ──► ./.secrets/<slug> ──► /run/secrets/<slug> ──► load_secret()
   (storage)        (host-side)            (container path)        (app code)
```

## Resolution order (`backend/secret_loader.load_secret`)

1. **`/run/secrets/<slug>`** (file mount, preferred) — empty file falls through.
2. **`<SLUG_UPPER_SNAKE>` env var** — fallback for CI / Linux dev.
3. The `default` argument (or None).

Cached with `lru_cache` after first read. Use `reset_cache()` in tests.

## Usage

```python
from backend.secret_loader import load_secret
api_secret = load_secret("algo_kite_api_secret", default="")
if not api_secret:
    raise HTTPException(503, detail="Configure via Keychain or env...")
```

Never read `os.environ.get(<UPPER_SLUG>)` directly — use `load_secret` so the file path is honored when present.

## Adding a new secret

1. Append slug (lowercase-snake) to `SLUGS=()` in `scripts/secrets/keychain.sh`.
2. Add top-level `secrets:` entry in `docker-compose.yml`:
   ```yaml
   secrets:
     <slug>:
       file: ./.secrets/<slug>
   ```
3. Mount under the consuming service's `secrets:` key.
4. Read via `load_secret("<slug>")`.

## Setting / rotating

```bash
./scripts/secrets/keychain.sh set <slug>     # interactive (no echo)
./scripts/secrets/materialize.sh              # Keychain → ./.secrets/*
docker compose restart backend
```

`materialize.sh` writes empty placeholder when slug not in Keychain so docker-compose's mount always resolves; the loader treats empty as missing → env fallback. Atomic write (`.tmp` + `mv`) to avoid container reading half-written file.

## Key files

- `backend/secret_loader.py`
- `scripts/secrets/keychain.sh`
- `scripts/secrets/materialize.sh`
- `.secrets/{.gitignore,README.md}` (dir checked in, contents gitignored)
- `docs/algo-trading/secrets.md` (full walkthrough)

## Production migration

Swap the docker-compose `secrets: file:` source for `external: true` references to a real CSI driver (Vault, AWS Secrets Manager, GCP Secret Manager). **Application code stays unchanged** — `load_secret(slug)` always reads `/run/secrets/<slug>`.

## Open TODO

`BYO_SECRET_KEY` (Fernet master key for at-rest encryption of api_key, access_token, BYO LLM keys) is still in `.env` plaintext. Promoting it to this flow tightens the entire encryption chain — should be done before any production deployment.
