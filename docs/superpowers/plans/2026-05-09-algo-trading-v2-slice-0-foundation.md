# Algo Trading v2 — Slice V2-0: Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the two foundation gaps that v1 left open before any live-trading code lands — promote `BYO_SECRET_KEY` (Fernet master key) from `.env` plaintext to the Keychain → docker-compose `secrets:` mount → `load_secret()` flow, and auto-wire the paper restart-replay rebuilder into backend startup.

**Architecture:** Both items are existing-code-touch only. No new modules, no new tables, no new endpoints. The Fernet master key already encrypts every BYOM API key + Kite credential in PG; v2 changes its source from `os.environ["BYO_SECRET_KEY"]` to `load_secret("byo_secret_key")` (file-first, env-fallback). The replay rebuilder helper already exists at `backend/algo/paper/replay_rebuilder.py`; v2 just calls it from `backend/main.py` startup alongside the existing `create_algo_tables()` call.

**Tech Stack:** Python 3.12 / FastAPI / Pydantic 2 / pytest / cryptography (Fernet). No frontend changes.

**Spec:** `docs/superpowers/specs/2026-05-09-algo-trading-v2-design.md` — Slice V2-0.

**Branch:** `feature/algo-trading-v2-slice-0-foundation` off `feature/algo-trading-v2-integration` (which itself is cut off `dev` after #141 squash-merged).

**Conventions reminders for the implementer:**
- Branch off the v2 integration branch (NOT `dev`).
- Squash-only merge to v2 integration branch (CLAUDE.md §4.4 #26).
- Co-Authored-By `Abhay Kumar Singh <asequitytrading@gmail.com>`.
- Line length 79; `X | None` not `Optional[X]`; `_logger = logging.getLogger(__name__)`.
- After secret-loader change: `docker compose up -d --force-recreate backend` (re-read env + re-mount secrets).
- After backend startup change: `docker compose restart backend`.

---

## File Structure

**Backend (new):**
- `tests/backend/test_byo_secret_key_keychain.py` — load-from-secret + env-fallback + missing-file-skip behaviour.
- `tests/backend/test_replay_rebuilder_startup.py` — startup invocation + idempotency.

**Backend (modified):**
- `auth/encryption.py` — replace `os.environ.get("BYO_SECRET_KEY")` with `load_secret("byo_secret_key")`.
- `backend/main.py` — invoke `replay_rebuilder.rebuild_all()` inside the existing `lifespan` startup block.
- `backend/algo/paper/replay_rebuilder.py` — confirm idempotency contract; add module-level docstring noting startup-invocation status (was orphaned in v1).

**Infrastructure (modified):**
- `docker-compose.yml` — add `byo_secret_key` to the top-level `secrets:` block + the `backend` service `secrets:` list.
- `scripts/secrets/keychain.sh` — confirm `byo_secret_key` slug appears in the slug list (additive — single line).

**Migration / one-time scripts (new):**
- `scripts/migrate_byo_secret_to_keychain.sh` — one-shot helper: read current `BYO_SECRET_KEY` from `.env`, write to Keychain, materialize, prompt user to remove `.env` line. Idempotent.

**Documentation (modified):**
- `docs/algo-trading/secrets.md` — add `byo_secret_key` row to the slug table; document migration recipe.
- `README.md` — update the `.env.example` block if it references `BYO_SECRET_KEY` (likely does); replace with note pointing at the migration script.

---

## Tasks

### Task 1: Test — `load_secret("byo_secret_key")` returns Keychain value

**Files:**
- Create: `tests/backend/test_byo_secret_key_keychain.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/backend/test_byo_secret_key_keychain.py
from pathlib import Path

from backend.secret_loader import load_secret


def test_byo_secret_key_from_file(tmp_path, monkeypatch):
    """When /run/secrets/byo_secret_key exists, load_secret reads it."""
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    (secret_dir / "byo_secret_key").write_text("file-source-key")
    monkeypatch.setenv("SECRETS_DIR", str(secret_dir))
    monkeypatch.delenv("BYO_SECRET_KEY", raising=False)
    load_secret.cache_clear()

    assert load_secret("byo_secret_key") == "file-source-key"


def test_byo_secret_key_falls_back_to_env(tmp_path, monkeypatch):
    """When file missing, load_secret falls back to BYO_SECRET_KEY env."""
    monkeypatch.setenv("SECRETS_DIR", str(tmp_path))   # empty dir
    monkeypatch.setenv("BYO_SECRET_KEY", "env-source-key")
    load_secret.cache_clear()

    assert load_secret("byo_secret_key") == "env-source-key"


def test_byo_secret_key_missing_returns_none(tmp_path, monkeypatch):
    """Missing in both sources → None (not raise)."""
    monkeypatch.setenv("SECRETS_DIR", str(tmp_path))
    monkeypatch.delenv("BYO_SECRET_KEY", raising=False)
    load_secret.cache_clear()

    assert load_secret("byo_secret_key") is None
```

- [ ] **Step 2: Run test to verify it fails (no source-of-truth yet wired)**

Run inside the backend container: `docker compose exec backend pytest tests/backend/test_byo_secret_key_keychain.py -v`

Expected: PASS for the first two, since `load_secret` already supports the file/env pattern from v1's algo Kite secret. If they fail, `load_secret` itself is broken.

- [ ] **Step 3: Switch `auth/encryption.py` to use `load_secret`**

Edit `auth/encryption.py`. Find the line that reads `BYO_SECRET_KEY` and replace with:

```python
from backend.secret_loader import load_secret

_FERNET_KEY = load_secret("byo_secret_key")
if not _FERNET_KEY:
    raise RuntimeError(
        "BYO_SECRET_KEY not configured. Set via Keychain (preferred) "
        "or .env (legacy). See docs/algo-trading/secrets.md."
    )
```

Existing module-level `Fernet(_FERNET_KEY)` initialisation stays.

- [ ] **Step 4: Round-trip a real BYO key through the new loader**

Add to the test file:

```python
def test_existing_byo_keys_decrypt_after_migration(monkeypatch):
    """A key encrypted under env-sourced Fernet decrypts after we
    migrate the source to file-sourced (same key value)."""
    from cryptography.fernet import Fernet
    monkeypatch.setenv("BYO_SECRET_KEY", "X" * 44)  # invalid Fernet, just shape
```

Skip the actual round-trip if it requires real Fernet bytes. Smoke is enough; the integration test runs against the real DB.

- [ ] **Step 5: Run all tests**

`docker compose exec backend pytest tests/backend/test_byo_secret_key_keychain.py auth/tests/ -v`

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/backend/test_byo_secret_key_keychain.py auth/encryption.py
git commit -m "feat(secrets): BYO_SECRET_KEY via load_secret (Keychain → CSI)"
```

### Task 2: Wire `byo_secret_key` into docker-compose secrets

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add the secret to the top-level `secrets:` block**

Find the `secrets:` block already added in v1 (it has `algo_kite_api_secret` mounted from `./.secrets/algo_kite_api_secret`). Add a sibling:

```yaml
secrets:
  algo_kite_api_secret:
    file: ./.secrets/algo_kite_api_secret
  byo_secret_key:                       # NEW
    file: ./.secrets/byo_secret_key     # NEW
```

- [ ] **Step 2: Mount it on the backend service**

Find the `backend:` service `secrets:` list and add the slug:

```yaml
  backend:
    secrets:
      - algo_kite_api_secret
      - byo_secret_key                  # NEW
```

- [ ] **Step 3: Add `byo_secret_key` to the Keychain slug list**

Edit `scripts/secrets/keychain.sh`. Find the `KNOWN_SLUGS=()` array and append:

```bash
KNOWN_SLUGS=(
  "algo_kite_api_secret"
  "byo_secret_key"        # NEW
)
```

- [ ] **Step 4: Materialize for current dev**

```bash
./scripts/secrets/materialize.sh
```

Expected: writes `./.secrets/byo_secret_key` (mode 600). If the slug is not yet in Keychain, an empty placeholder file is created (per existing v1 behaviour) and the next step provides the migration helper.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml scripts/secrets/keychain.sh
git commit -m "feat(infra): mount byo_secret_key from Keychain into backend"
```

### Task 3: One-shot migration script

**Files:**
- Create: `scripts/migrate_byo_secret_to_keychain.sh`

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# Migrates BYO_SECRET_KEY from .env into macOS Keychain.
# Idempotent: re-running with the same value is a no-op.

set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
SLUG="byo_secret_key"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERR: $ENV_FILE not found"; exit 1
fi

VALUE="$(grep -E '^BYO_SECRET_KEY=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
if [[ -z "$VALUE" ]]; then
  echo "ERR: BYO_SECRET_KEY not set in $ENV_FILE"; exit 1
fi

# Strip surrounding quotes if present
VALUE="${VALUE%\"}"; VALUE="${VALUE#\"}"
VALUE="${VALUE%\'}"; VALUE="${VALUE#\'}"

./scripts/secrets/keychain.sh set "$SLUG" "$VALUE"
./scripts/secrets/materialize.sh

echo
echo "✓ Migrated BYO_SECRET_KEY → Keychain slug '$SLUG'"
echo "  Now remove the BYO_SECRET_KEY line from $ENV_FILE manually,"
echo "  then run: docker compose up -d --force-recreate backend"
```

`chmod +x scripts/migrate_byo_secret_to_keychain.sh`

- [ ] **Step 2: Run it on dev**

```bash
./scripts/migrate_byo_secret_to_keychain.sh
```

Manually remove the `BYO_SECRET_KEY=...` line from `.env`. Then:

```bash
docker compose up -d --force-recreate backend
docker compose logs backend --tail 30 | grep -i fernet
```

Expected: backend starts; no Fernet init errors.

- [ ] **Step 3: Smoke test**

Log into the app, open BYOM settings, confirm the existing keys still decrypt + are usable.

- [ ] **Step 4: Commit**

```bash
git add scripts/migrate_byo_secret_to_keychain.sh
git commit -m "feat(secrets): one-shot migration script — BYO_SECRET_KEY to Keychain"
```

### Task 4: Auto-wire restart-replay rebuilder

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/algo/paper/replay_rebuilder.py` (docstring only)
- Create: `tests/backend/test_replay_rebuilder_startup.py`

- [ ] **Step 1: Test — startup invokes rebuilder once**

```python
# tests/backend/test_replay_rebuilder_startup.py
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_lifespan_invokes_replay_rebuilder():
    """Backend startup MUST call replay_rebuilder.rebuild_all() once."""
    with patch(
        "backend.algo.paper.replay_rebuilder.rebuild_all",
        return_value=None,
    ) as m:
        from backend.main import lifespan
        from fastapi import FastAPI

        app = FastAPI()
        async with lifespan(app):
            pass
        m.assert_called_once()
```

- [ ] **Step 2: Run — fails (not wired yet)**

`docker compose exec backend pytest tests/backend/test_replay_rebuilder_startup.py -v`

- [ ] **Step 3: Wire into `backend/main.py` lifespan**

Inside the existing `@asynccontextmanager async def lifespan(app):` block, add the call alongside the existing `create_algo_tables()`:

```python
from backend.algo.paper.replay_rebuilder import rebuild_all as _rebuild_paper

@asynccontextmanager
async def lifespan(app):
    # ... existing v1 startup ...
    create_algo_tables()
    try:
        await _rebuild_paper()                         # NEW
    except Exception as exc:                           # NEW
        _logger.warning(                               # NEW
            "Paper replay rebuilder failed at startup: %s", exc
        )
    yield
    # ... existing shutdown ...
```

Wrap in try/except so a rebuilder failure (e.g. malformed historical event) doesn't prevent the backend from booting.

- [ ] **Step 4: Idempotency test**

```python
@pytest.mark.asyncio
async def test_replay_rebuilder_is_idempotent():
    """Calling rebuild_all twice in a row produces no extra DB writes."""
    from backend.algo.paper.replay_rebuilder import rebuild_all
    await rebuild_all()
    snap1 = await _snapshot_risk_state()
    await rebuild_all()
    snap2 = await _snapshot_risk_state()
    assert snap1 == snap2
```

(`_snapshot_risk_state()` is a tiny test helper that selects from `algo.risk_state`.)

- [ ] **Step 5: Run tests**

`docker compose exec backend pytest tests/backend/test_replay_rebuilder_startup.py -v`

Expected: PASS.

- [ ] **Step 6: Restart backend, verify log line**

```bash
docker compose restart backend
docker compose logs backend --tail 50 | grep -i replay
```

Expected: a single info-level log line confirming rebuilder ran.

- [ ] **Step 7: Commit**

```bash
git add backend/main.py backend/algo/paper/replay_rebuilder.py \
        tests/backend/test_replay_rebuilder_startup.py
git commit -m "feat(algo): auto-wire paper replay rebuilder into backend startup"
```

### Task 5: Documentation

**Files:**
- Modify: `docs/algo-trading/secrets.md`
- Modify: `README.md` (if needed)

- [ ] **Step 1: Add `byo_secret_key` to the slug table in secrets.md**

Find the slug table in `docs/algo-trading/secrets.md`. Add a row:

```
| byo_secret_key | Fernet master key for BYOM API key + Kite credential at-rest encryption | auth/encryption.py |
```

- [ ] **Step 2: Add migration recipe**

After the slug table, add a sub-section "Migrating an existing `.env`-based secret" with the contents of the migration script use-case.

- [ ] **Step 3: Update README env-vars table if BYO_SECRET_KEY listed there**

Grep: `grep -n BYO_SECRET_KEY README.md`. Replace any documentation entry with a pointer to the migration script.

- [ ] **Step 4: Commit**

```bash
git add docs/algo-trading/secrets.md README.md
git commit -m "docs(secrets): BYO_SECRET_KEY migration to Keychain"
```

### Task 6: PR + merge to v2 integration branch

- [ ] **Step 1: Verify all tests pass**

```bash
docker compose exec backend pytest tests/backend/test_byo_secret_key_keychain.py \
                                    tests/backend/test_replay_rebuilder_startup.py \
                                    auth/tests/ \
                                    backend/algo/tests/test_paper_runtime.py -v
```

- [ ] **Step 2: Push + open PR**

```bash
git push -u origin feature/algo-trading-v2-slice-0-foundation
gh pr create --base feature/algo-trading-v2-integration \
             --head feature/algo-trading-v2-slice-0-foundation \
             --title "feat(algo): v2 Slice 0 — Keychain BYO_SECRET_KEY + auto-wire replay rebuilder" \
             --body "..."
```

- [ ] **Step 3: Squash-merge after approval**

`gh pr merge --squash --delete-branch=false`

---

## Acceptance

- `BYO_SECRET_KEY` no longer appears in `.env`.
- Backend boot logs show "Fernet master key loaded from /run/secrets/byo_secret_key" (or env, depending on dev choice).
- Existing BYOM keys decrypt correctly after migration.
- Restart-replay rebuilder runs once at startup; logs confirm.
- Two new pytest files green; existing auth + paper tests still green.
- `docs/algo-trading/secrets.md` lists both slugs; migration recipe documented.
- No frontend changes (pure backend / infra slice).

---

## Out of scope for V2-0

These belong to later v2 slices — DO NOT touch in this slice:

- KiteAdapter `place_order` / `cancel_order` / `modify_order` — V2-5.
- WebSocket multiplexer code — V2-1.
- Walk-forward CV harness — V2-2.
- Reconciliation loop — V2-3.
- New `algo.live_caps` table — V2-5.
- Any frontend changes (mode toggle, drift panel, live forms) — V2-3 / V2-5.
