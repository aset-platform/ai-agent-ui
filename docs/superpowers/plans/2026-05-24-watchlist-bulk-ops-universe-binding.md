# Watchlist Bulk Ops + Universe Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add CSV bulk-add + typed remove-all for the user's watchlist via a ⋮ overflow menu on the dashboard `WatchlistWidget`, and extend the algo strategy universe binding so `universe.scope=watchlist` includes algo-held positions from `algo.events`.

**Architecture:** Backend grows two new endpoints (`POST /v1/tickers/bulk` multipart, `DELETE /v1/tickers/all`) backed by repo helpers `bulk_link_tickers` + `unlink_all_tickers` writing to the existing `auth.user_tickers` PG table. A new `open_algo_positions(user_id) -> set[str]` reads `algo.events` Iceberg with a 60s Redis cache. A new `_scoped_tickers_for_strategy(user, scope)` sibling helper at `backend/insights_routes.py` injects the algo-held set into the `watchlist` scope; only `resolve_universe` in `backend/algo/backtest/universe.py` switches to the new helper. Frontend adds a `WatchlistOverflowMenu` next to the existing `+` add button with two modals (`BulkAddTickersModal`, `RemoveAllTickersModal`).

**Tech Stack:** Python 3.12 (FastAPI, SQLAlchemy 2.0 async, pyarrow/iceberg), Next.js 16 + React 19 (Vitest, SWR), Playwright (E2E).

**Reference spec:** `docs/superpowers/specs/2026-05-24-watchlist-bulk-ops-universe-binding-design.md`.

**Branch:** stacked on `feature/algo-portfolio-tab` (Epic B, PR #243). After Epic B merges, this branch rebases onto the new dev tip; new PR opens against dev.

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `auth/repo/ticker_repo.py` | modify | Add `bulk_link_tickers(session, user_id, tickers, source) -> tuple[list[str], list[str]]` + `unlink_all_tickers(session, user_id) -> int` |
| `auth/repo/repository.py` | modify | Wrappers — `bulk_link_tickers` + `unlink_all_tickers` (session scope managers) |
| `auth/repo/tests/test_ticker_repo_bulk.py` | create | 3 unit tests on the repo helpers |
| `auth/endpoints/ticker_routes.py` | modify | Add `POST /tickers/bulk` (multipart) + `DELETE /tickers/all` (JSON confirm); both invalidate `cache:dash:watchlist:{user_id}` |
| `auth/endpoints/tests/test_ticker_routes_bulk.py` | create | 6 HTTP-level tests |
| `backend/algo/live/open_positions.py` | create | `open_algo_positions(user_id) -> set[str]` + Redis cache + fail-open |
| `backend/algo/live/tests/test_open_positions.py` | create | 4 tests |
| `backend/insights_routes.py` | modify | Add `_scoped_tickers_for_strategy(user, scope) -> list[str]` sibling helper |
| `backend/algo/backtest/universe.py` | modify | `resolve_universe` switches from `_scoped_tickers` → `_scoped_tickers_for_strategy` |
| `backend/tests/test_scoped_tickers_for_strategy.py` | create | 3 tests |
| `frontend/lib/types/bulkTickers.ts` | create | `BulkTickerErrorRow`, `BulkTickerResponse`, `UnlinkAllResponse` TS shapes |
| `frontend/components/widgets/BulkAddTickersModal.tsx` | create | CSV upload modal (file picker, upload, result view) |
| `frontend/components/widgets/RemoveAllTickersModal.tsx` | create | Typed-confirm modal |
| `frontend/components/widgets/WatchlistOverflowMenu.tsx` | create | ⋮ button + dropdown w/ click-outside + Escape |
| `frontend/components/widgets/WatchlistWidget.tsx` | modify | Render the menu next to `+` when `activeTab === "watchlist"`; mount the two modals |
| `frontend/components/widgets/__tests__/BulkAddTickersModal.test.tsx` | create | 3 vitest tests |
| `frontend/components/widgets/__tests__/RemoveAllTickersModal.test.tsx` | create | 2 vitest tests |
| `frontend/components/widgets/__tests__/WatchlistOverflowMenu.test.tsx` | create | 2 vitest tests |
| `e2e/utils/selectors.ts` | modify | Add 7 new testids |
| `e2e/tests/frontend/watchlist-bulk-ops.spec.ts` | create | 1 E2E smoke |
| `PROGRESS.md` | modify | Dated session entry |

---

## Task 1: Repo helpers + 3 tests

**Files:**
- Modify: `auth/repo/ticker_repo.py` — add `bulk_link_tickers` + `unlink_all_tickers`
- Modify: `auth/repo/repository.py` — add wrappers
- Create: `auth/repo/tests/test_ticker_repo_bulk.py`

- [ ] **Step 1.1: Read the existing module shape**

```bash
sed -n '1,80p' auth/repo/ticker_repo.py
sed -n '125,150p' auth/repo/repository.py
```

Expected: existing `link_ticker(session, user_id, ticker, source)` returns `bool`. Repo wrapper at `repository.py:129` opens `_session_scope()` and delegates.

- [ ] **Step 1.2: Write failing tests**

Create `auth/repo/tests/test_ticker_repo_bulk.py`:

```python
"""Tests for ticker repo bulk helpers."""

from __future__ import annotations

from uuid import uuid4

import pytest

from auth.repo import ticker_repo


@pytest.mark.asyncio
async def test_bulk_link_tickers_inserts_new(db_session):
    uid = str(uuid4())
    # Create the user row so the FK passes.
    from backend.db.models.user import User
    db_session.add(
        User(
            user_id=uid,
            email=f"u-{uid}@example.com",
            full_name="Test",
        ),
    )
    await db_session.commit()

    added, already_linked = await ticker_repo.bulk_link_tickers(
        db_session,
        user_id=uid,
        tickers=["AAPL", "MSFT"],
        source="bulk_csv",
    )
    assert sorted(added) == ["AAPL", "MSFT"]
    assert already_linked == []


@pytest.mark.asyncio
async def test_bulk_link_tickers_splits_added_vs_already_linked(
    db_session,
):
    uid = str(uuid4())
    from backend.db.models.user import User
    db_session.add(
        User(
            user_id=uid,
            email=f"u-{uid}@example.com",
            full_name="Test",
        ),
    )
    await db_session.commit()

    # Pre-link MSFT.
    await ticker_repo.link_ticker(
        db_session, uid, "MSFT", source="manual",
    )

    added, already_linked = await ticker_repo.bulk_link_tickers(
        db_session,
        user_id=uid,
        tickers=["AAPL", "MSFT", "GOOG"],
        source="bulk_csv",
    )
    assert sorted(added) == ["AAPL", "GOOG"]
    assert already_linked == ["MSFT"]


@pytest.mark.asyncio
async def test_unlink_all_tickers_returns_row_count(
    db_session,
):
    uid = str(uuid4())
    other = str(uuid4())
    from backend.db.models.user import User
    db_session.add_all([
        User(
            user_id=uid,
            email=f"u-{uid}@example.com",
            full_name="A",
        ),
        User(
            user_id=other,
            email=f"u-{other}@example.com",
            full_name="B",
        ),
    ])
    await db_session.commit()

    for t in ["AAPL", "MSFT", "GOOG", "TSLA"]:
        await ticker_repo.link_ticker(db_session, uid, t)
    await ticker_repo.link_ticker(db_session, other, "NVDA")
    await ticker_repo.link_ticker(db_session, other, "AMZN")

    removed = await ticker_repo.unlink_all_tickers(
        db_session, uid,
    )
    assert removed == 4

    # Other user's rows untouched.
    others = await ticker_repo.get_user_tickers(
        db_session, other,
    )
    assert sorted(others) == ["AMZN", "NVDA"]
```

- [ ] **Step 1.3: Run tests — expect failure**

```bash
docker compose exec backend python -m pytest \
  auth/repo/tests/test_ticker_repo_bulk.py -v
```

Expected: ImportError / AttributeError on `bulk_link_tickers` / `unlink_all_tickers`.

If the `db_session` fixture doesn't exist in this project, fall back to using `disposable_pg_session()` and a savepoint rollback fixture — copy the pattern from `backend/algo/live/tests/test_budget_repo.py`. Document the choice in the report.

- [ ] **Step 1.4: Add the repo helpers**

In `auth/repo/ticker_repo.py`, after the existing `get_all_user_tickers` function, append:

```python
async def bulk_link_tickers(
    session: AsyncSession,
    user_id: str,
    tickers: list[str],
    source: str = "bulk_csv",
) -> tuple[list[str], list[str]]:
    """Bulk-insert tickers for a user.

    Returns (added, already_linked). Tickers MUST be
    pre-validated + pre-normalised (upper-case, trimmed).

    One round-trip: INSERT INTO auth.user_tickers
    (user_id, ticker, linked_at, source) VALUES …
    ON CONFLICT (user_id, ticker) DO NOTHING
    RETURNING ticker.
    """
    if not tickers:
        return ([], [])

    from sqlalchemy.dialects.postgresql import insert

    now = datetime.now(timezone.utc)
    rows = [
        {
            "user_id": user_id,
            "ticker": t,
            "linked_at": now,
            "source": source,
        }
        for t in tickers
    ]
    stmt = (
        insert(UserTicker)
        .values(rows)
        .on_conflict_do_nothing(
            index_elements=["user_id", "ticker"],
        )
        .returning(UserTicker.ticker)
    )
    result = await session.execute(stmt)
    await session.commit()
    inserted = [row[0] for row in result.all()]
    inserted_set = set(inserted)
    already = [t for t in tickers if t not in inserted_set]
    log.info(
        "Bulk-linked %d new (skipped %d) for user %s",
        len(inserted), len(already), user_id,
    )
    return (inserted, already)


async def unlink_all_tickers(
    session: AsyncSession,
    user_id: str,
) -> int:
    """Unlink every ticker for a user.

    Returns the count of rows deleted.
    """
    result = await session.execute(
        delete(UserTicker).where(
            UserTicker.user_id == user_id,
        ),
    )
    await session.commit()
    removed = result.rowcount or 0
    log.info(
        "Unlinked all %d tickers for user %s",
        removed, user_id,
    )
    return removed
```

- [ ] **Step 1.5: Add the repo wrappers**

In `auth/repo/repository.py`, after the existing `unlink_ticker` wrapper at line ~138, append:

```python
    async def bulk_link_tickers(
        self,
        user_id: str,
        tickers: list[str],
        source: str = "bulk_csv",
    ) -> tuple[list[str], list[str]]:
        async with self._session_scope() as s:
            return await ticker_repo.bulk_link_tickers(
                s, user_id, tickers, source,
            )

    async def unlink_all_tickers(
        self, user_id: str,
    ) -> int:
        async with self._session_scope() as s:
            return await ticker_repo.unlink_all_tickers(
                s, user_id,
            )
```

- [ ] **Step 1.6: Run tests — expect 3 PASS**

```bash
docker compose exec backend python -m pytest \
  auth/repo/tests/test_ticker_repo_bulk.py -v
```

Expected: 3 passed.

- [ ] **Step 1.7: Commit**

```bash
git add auth/repo/ticker_repo.py \
        auth/repo/repository.py \
        auth/repo/tests/test_ticker_repo_bulk.py
git commit -m "$(cat <<'EOF'
feat(watchlist-bulk): repo helpers for bulk add + remove all

Single-round-trip ON CONFLICT DO NOTHING bulk insert; pre-
validated input. unlink_all_tickers wipes the user's row set
and returns the count. Repo wrappers follow the existing
session-scope pattern.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 2: HTTP routes + 6 tests

**Files:**
- Modify: `auth/endpoints/ticker_routes.py` — add `POST /tickers/bulk` (multipart) + `DELETE /tickers/all`
- Create: `auth/endpoints/tests/test_ticker_routes_bulk.py`

- [ ] **Step 2.1: Write failing tests**

Create `auth/endpoints/tests/test_ticker_routes_bulk.py`:

```python
"""HTTP-level tests for /v1/tickers/bulk + /v1/tickers/all."""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from auth.endpoints.ticker_routes import (
    _bulk_link_impl,
    _unlink_all_impl,
    BulkTickerResponse,
)


def _csv_bytes(rows: list[str], header: str = "ticker") -> bytes:
    lines = [header, *rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


@pytest.mark.asyncio
async def test_bulk_link_happy_path_via_csv_file():
    uid = str(uuid4())
    fake_repo = MagicMock()
    fake_repo.bulk_link_tickers = AsyncMock(
        return_value=(["AAPL", "MSFT", "RELIANCE.NS"], []),
    )
    csv = _csv_bytes(["AAPL", "MSFT", "RELIANCE.NS"])
    with patch(
        "auth.endpoints.ticker_routes._helpers._get_repo",
        return_value=fake_repo,
    ), patch(
        "auth.endpoints.ticker_routes._invalidate_watchlist_cache",
    ):
        out = await _bulk_link_impl(
            user_id=uid,
            csv_bytes=csv,
            filename="test.csv",
        )
    assert isinstance(out, BulkTickerResponse)
    assert sorted(out.added) == [
        "AAPL", "MSFT", "RELIANCE.NS",
    ]
    assert out.errors == []
    assert out.total_rows == 3


@pytest.mark.asyncio
async def test_bulk_link_skips_already_linked():
    uid = str(uuid4())
    fake_repo = MagicMock()
    fake_repo.bulk_link_tickers = AsyncMock(
        return_value=(["AAPL", "GOOG"], ["MSFT"]),
    )
    csv = _csv_bytes(["AAPL", "MSFT", "GOOG"])
    with patch(
        "auth.endpoints.ticker_routes._helpers._get_repo",
        return_value=fake_repo,
    ), patch(
        "auth.endpoints.ticker_routes._invalidate_watchlist_cache",
    ):
        out = await _bulk_link_impl(
            user_id=uid,
            csv_bytes=csv,
            filename="test.csv",
        )
    assert sorted(out.added) == ["AAPL", "GOOG"]
    assert out.skipped_already_linked == ["MSFT"]
    assert out.errors == []


@pytest.mark.asyncio
async def test_bulk_link_reports_invalid_tickers():
    uid = str(uuid4())
    fake_repo = MagicMock()
    fake_repo.bulk_link_tickers = AsyncMock(
        return_value=(["AAPL", "RELIANCE.NS"], []),
    )
    csv = _csv_bytes(["AAPL", "BAD$$", "", "RELIANCE.NS"])
    with patch(
        "auth.endpoints.ticker_routes._helpers._get_repo",
        return_value=fake_repo,
    ), patch(
        "auth.endpoints.ticker_routes._invalidate_watchlist_cache",
    ):
        out = await _bulk_link_impl(
            user_id=uid,
            csv_bytes=csv,
            filename="test.csv",
        )
    assert sorted(out.added) == ["AAPL", "RELIANCE.NS"]
    # 2 invalid rows: "BAD$$" and "" (empty)
    assert len(out.errors) == 2
    rows = sorted(e.row for e in out.errors)
    assert rows == [3, 4]
    assert out.total_rows == 4


@pytest.mark.asyncio
async def test_bulk_link_rejects_csv_without_ticker_column():
    uid = str(uuid4())
    csv = "symbol,name\nAAPL,Apple\n".encode("utf-8")
    with patch(
        "auth.endpoints.ticker_routes._helpers._get_repo",
        return_value=MagicMock(),
    ):
        with pytest.raises(HTTPException) as exc:
            await _bulk_link_impl(
                user_id=uid,
                csv_bytes=csv,
                filename="bad.csv",
            )
    assert exc.value.status_code == 400
    assert "ticker" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_bulk_link_rejects_over_5000_rows():
    uid = str(uuid4())
    csv = _csv_bytes([f"TKR{i}" for i in range(5001)])
    with patch(
        "auth.endpoints.ticker_routes._helpers._get_repo",
        return_value=MagicMock(),
    ):
        with pytest.raises(HTTPException) as exc:
            await _bulk_link_impl(
                user_id=uid,
                csv_bytes=csv,
                filename="too-big.csv",
            )
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_unlink_all_requires_exact_confirm_phrase():
    uid = str(uuid4())
    fake_repo = MagicMock()
    fake_repo.unlink_all_tickers = AsyncMock(return_value=4)
    with patch(
        "auth.endpoints.ticker_routes._helpers._get_repo",
        return_value=fake_repo,
    ), patch(
        "auth.endpoints.ticker_routes._invalidate_watchlist_cache",
    ):
        # Wrong phrase.
        with pytest.raises(HTTPException) as exc:
            await _unlink_all_impl(
                user_id=uid, confirm="remove all",
            )
        assert exc.value.status_code == 400
        # Exact phrase.
        out = await _unlink_all_impl(
            user_id=uid, confirm="REMOVE ALL",
        )
        assert out.removed == 4
```

- [ ] **Step 2.2: Run tests — expect failure**

```bash
docker compose exec backend python -m pytest \
  auth/endpoints/tests/test_ticker_routes_bulk.py -v
```

Expected: ImportError on `_bulk_link_impl`, `_unlink_all_impl`, `BulkTickerResponse`.

- [ ] **Step 2.3: Implement the routes**

Edit `auth/endpoints/ticker_routes.py`. After the existing `unlink_ticker` route handler (around line 240), insert these module-level types + impl functions + handlers:

First, add at the top of the file (near the other imports):

```python
import csv as _csv
import io
from typing import Any
from fastapi import File, UploadFile

from pydantic import BaseModel, ConfigDict
```

Then add the response models alongside the existing `LinkTickerRequest` class:

```python
class BulkTickerErrorRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row: int
    ticker: str
    reason: str


class BulkTickerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    added: list[str]
    skipped_already_linked: list[str]
    errors: list[BulkTickerErrorRow]
    total_rows: int


class UnlinkAllRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: str


class UnlinkAllResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    removed: int


_BULK_ROW_CAP = 5000


def _invalidate_watchlist_cache(user_id: str) -> None:
    """Best-effort invalidation of the dashboard watchlist
    cache key for this user. Failures are non-fatal — the
    TTL will eventually expire the stale entry."""
    try:
        from cache import get_cache
        c = get_cache()
        if c is not None:
            c.invalidate(
                f"cache:dash:watchlist:{user_id}",
            )
    except Exception:  # noqa: BLE001
        _logger.warning(
            "watchlist cache invalidate failed",
            exc_info=True,
        )
```

Then add the `_bulk_link_impl` function (module-level, takes raw bytes so it's testable without a UploadFile harness):

```python
async def _bulk_link_impl(
    *,
    user_id: str,
    csv_bytes: bytes,
    filename: str,
) -> BulkTickerResponse:
    """Pure async impl. Parses the CSV, normalises, validates,
    delegates to repo.bulk_link_tickers, builds per-row report.
    """
    try:
        text = csv_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"CSV must be UTF-8: {exc}",
        ) from exc

    reader = _csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration as exc:
        raise HTTPException(
            status_code=400,
            detail="empty CSV",
        ) from exc

    header_lc = [h.strip().lower() for h in header]
    if "ticker" not in header_lc:
        raise HTTPException(
            status_code=400,
            detail=(
                "missing required column 'ticker' "
                f"(found: {header_lc})"
            ),
        )
    ticker_col = header_lc.index("ticker")

    # Pre-scan row count to enforce the cap before
    # allocating large structures.
    rows_raw = list(reader)
    if len(rows_raw) > _BULK_ROW_CAP:
        raise HTTPException(
            status_code=413,
            detail=(
                f"CSV exceeds {_BULK_ROW_CAP}-row limit; "
                "please split it and try again."
            ),
        )

    valid: list[str] = []
    errors: list[BulkTickerErrorRow] = []
    seen_in_batch: set[str] = set()
    # CSV row indices are 1-based and include the header,
    # so data rows start at index 2.
    for i, row in enumerate(rows_raw, start=2):
        if not row or ticker_col >= len(row):
            errors.append(BulkTickerErrorRow(
                row=i, ticker="", reason="empty row",
            ))
            continue
        raw = (row[ticker_col] or "").strip()
        if not raw:
            errors.append(BulkTickerErrorRow(
                row=i, ticker="", reason="empty ticker",
            ))
            continue
        norm = raw.upper()
        err = validate_ticker(norm)
        if err is not None:
            errors.append(BulkTickerErrorRow(
                row=i, ticker=raw, reason=err,
            ))
            continue
        if norm in seen_in_batch:
            errors.append(BulkTickerErrorRow(
                row=i, ticker=raw,
                reason="duplicate in batch",
            ))
            continue
        seen_in_batch.add(norm)
        valid.append(norm)

    repo = _helpers._get_repo()
    added, already_linked = await repo.bulk_link_tickers(
        user_id, valid, source="bulk_csv",
    )
    _invalidate_watchlist_cache(user_id)

    _logger.info(
        "bulk_link user=%s file=%s added=%d skipped=%d "
        "errors=%d",
        user_id, filename,
        len(added), len(already_linked), len(errors),
    )
    return BulkTickerResponse(
        added=added,
        skipped_already_linked=already_linked,
        errors=errors,
        total_rows=len(rows_raw),
    )


async def _unlink_all_impl(
    *,
    user_id: str,
    confirm: str,
) -> UnlinkAllResponse:
    """Pure async impl. Enforces the literal confirm phrase."""
    if confirm != "REMOVE ALL":
        raise HTTPException(
            status_code=400,
            detail="confirmation phrase mismatch",
        )
    repo = _helpers._get_repo()
    removed = await repo.unlink_all_tickers(user_id)
    _invalidate_watchlist_cache(user_id)
    _logger.info(
        "unlink_all user=%s removed=%d",
        user_id, removed,
    )
    return UnlinkAllResponse(removed=removed)
```

Then add the route handlers (still in `ticker_routes.py`, after the existing `unlink_ticker` handler):

```python
@router.post(
    "/tickers/bulk",
    response_model=BulkTickerResponse,
)
async def bulk_link_tickers(
    file: UploadFile = File(...),
    user: UserContext = Depends(get_current_user),
) -> BulkTickerResponse:
    """Bulk-link tickers from a CSV file. Returns a per-row
    report — added, already-linked, errors."""
    body = await file.read()
    return await _bulk_link_impl(
        user_id=user.user_id,
        csv_bytes=body,
        filename=file.filename or "upload.csv",
    )


@router.delete(
    "/tickers/all",
    response_model=UnlinkAllResponse,
)
async def unlink_all_tickers(
    body: UnlinkAllRequest,
    user: UserContext = Depends(get_current_user),
) -> UnlinkAllResponse:
    """Unlink every ticker for the current user. Requires
    body.confirm == "REMOVE ALL" (case-sensitive)."""
    return await _unlink_all_impl(
        user_id=user.user_id,
        confirm=body.confirm,
    )
```

- [ ] **Step 2.4: Run tests — expect 6 PASS**

```bash
docker compose exec backend python -m pytest \
  auth/endpoints/tests/test_ticker_routes_bulk.py -v
```

Expected: 6 passed.

- [ ] **Step 2.5: Restart backend (new routes)**

```bash
docker compose restart backend && sleep 5
```

- [ ] **Step 2.6: Smoke-test the route registration**

```bash
docker compose exec backend python -c "
from auth.api import get_ticker_router
r = get_ticker_router()
for route in r.routes:
    if 'bulk' in getattr(route, 'path', '') or 'all' in getattr(route, 'path', ''):
        print(route.methods, route.path)
"
```

Expected output includes:
```
{'POST'} /tickers/bulk
{'DELETE'} /tickers/all
```

- [ ] **Step 2.7: Commit**

```bash
git add auth/endpoints/ticker_routes.py \
        auth/endpoints/tests/test_ticker_routes_bulk.py
git commit -m "$(cat <<'EOF'
feat(watchlist-bulk): /v1/tickers/bulk + /v1/tickers/all

POST /v1/tickers/bulk accepts multipart CSV with a `ticker`
column; returns per-row diagnostic (added,
skipped_already_linked, errors). Hard cap 5000 rows → 413.
Missing column / parse failure → 400. Returns 200 even when
all rows error.

DELETE /v1/tickers/all requires body.confirm == "REMOVE
ALL" (case-sensitive) — otherwise 400. Both endpoints
invalidate cache:dash:watchlist:{user_id} on success.

Lift-to-module-level _impl pattern so unit tests exercise
the handler logic without an UploadFile harness.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 3: `open_algo_positions` helper + 4 tests

**Files:**
- Create: `backend/algo/live/open_positions.py`
- Create: `backend/algo/live/tests/test_open_positions.py`

- [ ] **Step 3.1: Write failing tests**

Create `backend/algo/live/tests/test_open_positions.py`:

```python
"""Tests for open_algo_positions."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.live.open_positions import (
    open_algo_positions,
)


def _event_row(
    sym: str, side: str, qty: int,
    ts_ns: int, dry_run: bool = False,
) -> dict:
    import json
    return {
        "ts_ns": ts_ns,
        "payload_json": json.dumps({
            "symbol": sym,
            "side": side,
            "qty": qty,
            "dry_run": dry_run,
        }),
    }


@pytest.mark.asyncio
async def test_empty_when_no_events(monkeypatch):
    async def fake_query(*args, **kwargs):
        return []
    monkeypatch.setattr(
        "backend.algo.live.open_positions.query_iceberg_table",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr(
        "backend.algo.live.open_positions.get_cache",
        lambda: None,
    )
    out = await open_algo_positions(uuid4())
    assert out == set()


@pytest.mark.asyncio
async def test_net_long_only_returned(monkeypatch):
    rows = [
        _event_row("A.NS", "BUY", 10, ts_ns=1),
        _event_row("B.NS", "BUY", 10, ts_ns=2),
        _event_row("C.NS", "BUY", 10, ts_ns=3),
        _event_row("B.NS", "SELL", 10, ts_ns=4),
    ]
    monkeypatch.setattr(
        "backend.algo.live.open_positions.query_iceberg_table",
        lambda *a, **kw: rows,
    )
    monkeypatch.setattr(
        "backend.algo.live.open_positions.get_cache",
        lambda: None,
    )
    out = await open_algo_positions(uuid4())
    assert out == {"A.NS", "C.NS"}


@pytest.mark.asyncio
async def test_dry_run_fills_ignored(monkeypatch):
    rows = [
        _event_row("A.NS", "BUY", 10, ts_ns=1, dry_run=True),
        _event_row("B.NS", "BUY", 5, ts_ns=2),
    ]
    monkeypatch.setattr(
        "backend.algo.live.open_positions.query_iceberg_table",
        lambda *a, **kw: rows,
    )
    monkeypatch.setattr(
        "backend.algo.live.open_positions.get_cache",
        lambda: None,
    )
    out = await open_algo_positions(uuid4())
    assert out == {"B.NS"}


@pytest.mark.asyncio
async def test_cache_hit_skips_iceberg(monkeypatch):
    import json
    fake_cache = MagicMock()
    fake_cache.get = MagicMock(
        return_value=json.dumps(["AAPL.NS", "MSFT"]),
    )
    monkeypatch.setattr(
        "backend.algo.live.open_positions.get_cache",
        lambda: fake_cache,
    )
    iceberg_spy = MagicMock(
        side_effect=AssertionError("should not be called"),
    )
    monkeypatch.setattr(
        "backend.algo.live.open_positions.query_iceberg_table",
        iceberg_spy,
    )
    out = await open_algo_positions(uuid4())
    assert out == {"AAPL.NS", "MSFT"}
    iceberg_spy.assert_not_called()
```

- [ ] **Step 3.2: Run tests — expect ImportError**

```bash
docker compose exec backend python -m pytest \
  backend/algo/live/tests/test_open_positions.py -v
```

- [ ] **Step 3.3: Implement the helper**

Create `backend/algo/live/open_positions.py`:

```python
"""Algo-held tickers derived from algo.events.

Used by `_scoped_tickers_for_strategy(scope="watchlist")` so
strategies with `universe.scope=watchlist` can always iterate
over (and exit) positions opened by the algo runtime.

Read-only. Iceberg query is the authoritative source — no
Kite fallback. Fail-open: empty set on any read failure.
"""

from __future__ import annotations

import asyncio
import json
import logging
from uuid import UUID

from backend.cache import get_cache
from backend.stocks.duckdb_engine import query_iceberg_table

_logger = logging.getLogger(__name__)

_LOOKBACK_SINCE = "2024-01-01"
_CACHE_TTL_S = 60


async def open_algo_positions(user_id: UUID) -> set[str]:
    """Tickers with net long qty > 0 across all live algo
    fills since 2024-01-01.

    Reads from ``algo.events`` (mode='live',
    type='order_filled_live'). Net qty per symbol =
    sum(qty if side=BUY else -qty); ignores
    payload.dry_run = True rows.

    Cached in Redis at ``cache:algo:open_positions:{user_id}``
    with 60s TTL. Returns empty set on any failure
    (fail-open — universe simply doesn't include algo-held
    tickers in that case, which is the safe degradation).
    """
    cache = get_cache()
    cache_key = (
        f"cache:algo:open_positions:{user_id}"
    )
    if cache is not None:
        cached_raw = cache.get(cache_key)
        if cached_raw:
            try:
                return set(json.loads(cached_raw))
            except (ValueError, TypeError):
                pass

    try:
        rows = await asyncio.to_thread(
            query_iceberg_table,
            "algo.events",
            "SELECT ts_ns, payload_json "
            "FROM events "
            "WHERE user_id = ? "
            "  AND mode = 'live' "
            "  AND type = 'order_filled_live' "
            "  AND ts_date >= ? "
            "ORDER BY ts_ns ASC",
            [str(user_id), _LOOKBACK_SINCE],
        )
    except Exception:  # noqa: BLE001
        _logger.warning(
            "open_algo_positions iceberg read failed "
            "for user=%s",
            user_id, exc_info=True,
        )
        return set()

    net: dict[str, int] = {}
    for row in rows:
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except (ValueError, TypeError):
            continue
        if payload.get("dry_run"):
            continue
        sym = payload.get("symbol") or ""
        if not sym:
            continue
        side = (payload.get("side") or "").upper()
        try:
            qty = int(payload.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        if side == "BUY":
            net[sym] = net.get(sym, 0) + qty
        elif side == "SELL":
            net[sym] = net.get(sym, 0) - qty

    out = {sym for sym, q in net.items() if q > 0}

    if cache is not None:
        try:
            cache.set(
                cache_key,
                json.dumps(sorted(out)),
                ttl=_CACHE_TTL_S,
            )
        except Exception:  # noqa: BLE001
            _logger.warning(
                "open_algo_positions cache set failed",
                exc_info=True,
            )
    return out
```

NOTE: `query_iceberg_table` import path — verify with `grep "def query_iceberg_table\|^from.*query_iceberg_table" backend/ -r --include="*.py" | head -5`. The current `_fetch_strategy_attribution` in `backend/algo/routes/live.py` imports it from somewhere; mirror that exact path. If it's not at `backend.stocks.duckdb_engine`, adjust accordingly.

- [ ] **Step 3.4: Run tests — expect 4 PASS**

```bash
docker compose exec backend python -m pytest \
  backend/algo/live/tests/test_open_positions.py -v
```

Expected: 4 passed.

- [ ] **Step 3.5: Commit**

```bash
git add backend/algo/live/open_positions.py \
        backend/algo/live/tests/test_open_positions.py
git commit -m "$(cat <<'EOF'
feat(watchlist-bulk): open_algo_positions helper

Reads algo.events Iceberg (mode='live',
type='order_filled_live', since 2024-01-01), computes net
qty per symbol (BUY +qty, SELL -qty, ignores dry_run),
returns symbols with net > 0. 60s Redis cache. Fail-open on
read failure.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 4: `_scoped_tickers_for_strategy` + wire `resolve_universe`

**Files:**
- Modify: `backend/insights_routes.py` — add `_scoped_tickers_for_strategy`
- Modify: `backend/algo/backtest/universe.py` — swap the `_scoped_tickers` call
- Create: `backend/tests/test_scoped_tickers_for_strategy.py`

- [ ] **Step 4.1: Write failing tests**

Create `backend/tests/test_scoped_tickers_for_strategy.py`:

```python
"""Tests for _scoped_tickers_for_strategy."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from backend.insights_routes import (
    _scoped_tickers_for_strategy,
)


class _FakeUser:
    def __init__(self):
        self.user_id = str(uuid4())
        self.role = "pro"


@pytest.mark.asyncio
async def test_scope_watchlist_includes_algo_open():
    user = _FakeUser()
    with patch(
        "backend.insights_routes._scoped_tickers",
        AsyncMock(return_value=["A.NS"]),
    ), patch(
        "backend.algo.live.open_positions.open_algo_positions",
        AsyncMock(return_value={"B.NS"}),
    ):
        out = await _scoped_tickers_for_strategy(
            user, "watchlist",
        )
    assert "A.NS" in out
    assert "B.NS" in out


@pytest.mark.asyncio
async def test_scope_portfolio_does_not_inject():
    user = _FakeUser()
    with patch(
        "backend.insights_routes._scoped_tickers",
        AsyncMock(return_value=["X.NS"]),
    ), patch(
        "backend.algo.live.open_positions.open_algo_positions",
        AsyncMock(return_value={"NEVER_INJECT.NS"}),
    ) as algo_spy:
        out = await _scoped_tickers_for_strategy(
            user, "portfolio",
        )
    assert out == ["X.NS"]
    algo_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_scope_discovery_does_not_inject():
    user = _FakeUser()
    with patch(
        "backend.insights_routes._scoped_tickers",
        AsyncMock(return_value=["X.NS", "Y.NS"]),
    ), patch(
        "backend.algo.live.open_positions.open_algo_positions",
        AsyncMock(return_value={"NEVER_INJECT.NS"}),
    ) as algo_spy:
        out = await _scoped_tickers_for_strategy(
            user, "discovery",
        )
    assert sorted(out) == ["X.NS", "Y.NS"]
    algo_spy.assert_not_awaited()
```

- [ ] **Step 4.2: Run tests — expect failure**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_scoped_tickers_for_strategy.py -v
```

Expected: ImportError on `_scoped_tickers_for_strategy`.

- [ ] **Step 4.3: Add the sibling helper**

Edit `backend/insights_routes.py`. After the existing
`_scoped_tickers` function (around line 173-205) and the
`_get_user_tickers` shim (line 206-211), add:

```python
async def _scoped_tickers_for_strategy(
    user: UserContext,
    scope: TickerScope,
) -> list[str]:
    """Like :func:`_scoped_tickers` but injects algo-held
    positions into the ``watchlist`` scope so a strategy with
    ``universe.scope=watchlist`` can always iterate over
    (and exit) positions it currently holds.

    Other scopes delegate verbatim — the algo runtime is the
    only caller that needs the injection. Insights tabs keep
    calling :func:`_scoped_tickers` directly.
    """
    base = await _scoped_tickers(user, scope)
    if scope != "watchlist":
        return base
    # Lazy import to avoid a cycle when this module loads.
    from backend.algo.live.open_positions import (
        open_algo_positions,
    )
    algo_held = await open_algo_positions(user.user_id)
    return _dedup(base, sorted(algo_held))
```

- [ ] **Step 4.4: Wire `resolve_universe`**

Edit `backend/algo/backtest/universe.py`. Locate the line:

```python
from backend.insights_routes import _scoped_tickers
```

Change it to:

```python
from backend.insights_routes import (
    _scoped_tickers_for_strategy,
)
```

Then find the call at line ~185:

```python
candidates = await _scoped_tickers(user=user, scope=scope)
```

Change to:

```python
candidates = await _scoped_tickers_for_strategy(
    user=user, scope=scope,
)
```

- [ ] **Step 4.5: Run new + regression tests**

```bash
docker compose exec backend python -m pytest \
  backend/tests/test_scoped_tickers_for_strategy.py \
  backend/algo/backtest/tests/test_universe_min_adtv_filter.py \
  -v 2>&1 | tail -10
```

Expected: all green.

- [ ] **Step 4.6: Restart backend**

```bash
docker compose restart backend && sleep 5
```

The `resolve_universe` callsite is imported by the algo runtime + paper + live. Restart ensures the new helper is picked up.

- [ ] **Step 4.7: Commit**

```bash
git add backend/insights_routes.py \
        backend/algo/backtest/universe.py \
        backend/tests/test_scoped_tickers_for_strategy.py
git commit -m "$(cat <<'EOF'
feat(watchlist-bulk): inject algo-held into strategy universe

_scoped_tickers_for_strategy is a strictly-additive sibling
to _scoped_tickers that injects algo-held positions from
algo.events into the watchlist scope. Algo runtime
(resolve_universe) switches to the new helper; insights tabs
keep calling _scoped_tickers unchanged.

Closes the silent footgun where an algo opens a Kite
position on a ticker not in the user's watchlist — the
strategy could never iterate to it on the next bar to fire
an exit signal.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 5: `BulkAddTickersModal` + 3 vitest tests

**Files:**
- Create: `frontend/lib/types/bulkTickers.ts`
- Create: `frontend/components/widgets/BulkAddTickersModal.tsx`
- Create: `frontend/components/widgets/__tests__/BulkAddTickersModal.test.tsx`

- [ ] **Step 5.1: Create TS types**

Create `frontend/lib/types/bulkTickers.ts`:

```typescript
// Mirrors auth/endpoints/ticker_routes.py.

export interface BulkTickerErrorRow {
  row: number;
  ticker: string;
  reason: string;
}

export interface BulkTickerResponse {
  added: string[];
  skipped_already_linked: string[];
  errors: BulkTickerErrorRow[];
  total_rows: number;
}

export interface UnlinkAllResponse {
  removed: number;
}
```

- [ ] **Step 5.2: Write failing vitest tests**

Create `frontend/components/widgets/__tests__/BulkAddTickersModal.test.tsx`:

```typescript
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { BulkAddTickersModal } from "../BulkAddTickersModal";

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from "@/lib/apiFetch";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("BulkAddTickersModal", () => {
  it("renders drop zone; Upload button disabled until file selected", () => {
    render(
      <BulkAddTickersModal
        onClose={vi.fn()}
        onUploaded={vi.fn()}
      />,
    );
    const uploadBtn = screen.getByTestId(
      "bulk-add-tickers-upload-button",
    );
    expect(
      (uploadBtn as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("posts multipart form and renders result view", async () => {
    (apiFetch as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        ok: true,
        json: async () => ({
          added: ["AAPL"],
          skipped_already_linked: [],
          errors: [],
          total_rows: 1,
        }),
      });

    const onUploaded = vi.fn();
    render(
      <BulkAddTickersModal
        onClose={vi.fn()}
        onUploaded={onUploaded}
      />,
    );

    const input = screen.getByTestId(
      "bulk-add-tickers-file-input",
    ) as HTMLInputElement;
    const file = new File(
      ["ticker\nAAPL\n"], "test.csv",
      { type: "text/csv" },
    );
    fireEvent.change(input, { target: { files: [file] } });

    fireEvent.click(
      screen.getByTestId("bulk-add-tickers-upload-button"),
    );

    await waitFor(() => {
      expect(
        screen.getByTestId(
          "bulk-add-tickers-result-added-count",
        ),
      ).toBeDefined();
    });
    expect(onUploaded).toHaveBeenCalledOnce();
  });

  it("renders first 100 errors with truncation tail", async () => {
    const errors = Array.from({ length: 150 }).map(
      (_, i) => ({
        row: i + 2,
        ticker: `BAD${i}`,
        reason: "invalid format",
      }),
    );
    (apiFetch as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        ok: true,
        json: async () => ({
          added: [],
          skipped_already_linked: [],
          errors,
          total_rows: 150,
        }),
      });

    render(
      <BulkAddTickersModal
        onClose={vi.fn()}
        onUploaded={vi.fn()}
      />,
    );

    const input = screen.getByTestId(
      "bulk-add-tickers-file-input",
    ) as HTMLInputElement;
    const file = new File(
      ["ticker\nBAD0\n"], "test.csv",
    );
    fireEvent.change(input, { target: { files: [file] } });

    fireEvent.click(
      screen.getByTestId("bulk-add-tickers-upload-button"),
    );

    await waitFor(() => {
      const list = screen.getByTestId(
        "bulk-add-tickers-result-errors-list",
      );
      expect(list.textContent).toContain("BAD0");
      expect(list.textContent).toContain("50 more");
    });
  });
});
```

- [ ] **Step 5.3: Run tests — expect failure**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend && \
  npx vitest run \
  components/widgets/__tests__/BulkAddTickersModal.test.tsx
```

Expected: ERROR — `BulkAddTickersModal` not exported.

- [ ] **Step 5.4: Implement the modal**

Create `frontend/components/widgets/BulkAddTickersModal.tsx`:

```tsx
"use client";

import { useRef, useState } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type {
  BulkTickerErrorRow,
  BulkTickerResponse,
} from "@/lib/types/bulkTickers";

interface Props {
  onClose: () => void;
  onUploaded: () => void; // parent SWR mutate()
}

const MAX_VISIBLE_ERRORS = 100;

export function BulkAddTickersModal(
  { onClose, onUploaded }: Props,
) {
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<
    BulkTickerResponse | null
  >(null);
  const [err, setErr] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function handleUpload() {
    if (!file) return;
    setSubmitting(true);
    setErr(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await apiFetch(
        `${API_URL}/tickers/bulk`,
        { method: "POST", body: fd },
      );
      if (!r.ok) {
        const body = await r.text();
        setErr(`Upload failed: ${r.status} ${body}`);
        return;
      }
      const data = (await r.json()) as BulkTickerResponse;
      setResult(data);
      onUploaded();
    } catch (exc) {
      setErr(
        exc instanceof Error
          ? `Upload failed: ${exc.message}`
          : "Upload failed",
      );
    } finally {
      setSubmitting(false);
    }
  }

  const visibleErrors: BulkTickerErrorRow[] =
    result?.errors.slice(0, MAX_VISIBLE_ERRORS) ?? [];
  const truncatedCount =
    result && result.errors.length > MAX_VISIBLE_ERRORS
      ? result.errors.length - MAX_VISIBLE_ERRORS
      : 0;

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40"
      data-testid="bulk-add-tickers-modal"
    >
      <div className="bg-white dark:bg-slate-900 rounded-md p-4 w-[520px] max-w-[95vw] space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">
            Bulk add tickers from CSV
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="text-xs underline"
          >
            Close
          </button>
        </div>

        {result === null ? (
          <>
            <p className="text-xs text-slate-500">
              Format: CSV with a <code>ticker</code> column.
              Up to 5,000 rows.
            </p>
            <label
              className="block rounded border border-dashed border-slate-300 dark:border-slate-600 px-4 py-6 text-center text-xs cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800"
            >
              <input
                ref={inputRef}
                type="file"
                accept=".csv"
                className="hidden"
                data-testid="bulk-add-tickers-file-input"
                onChange={(e) => {
                  const f = e.target.files?.[0] ?? null;
                  setFile(f);
                }}
              />
              {file
                ? `Selected: ${file.name} (${(file.size / 1024).toFixed(0)} KB)`
                : "📄 Drop .csv file here, or click to browse"}
            </label>
            {err && (
              <p className="text-xs text-rose-600">{err}</p>
            )}
            <div className="flex gap-2 justify-end">
              <button
                type="button"
                onClick={onClose}
                className="rounded border px-3 py-1.5 text-sm"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleUpload}
                disabled={!file || submitting}
                data-testid="bulk-add-tickers-upload-button"
                className="rounded bg-indigo-600 text-white px-3 py-1.5 text-sm disabled:opacity-50"
              >
                {submitting ? "Uploading…" : "Upload"}
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="space-y-1 text-xs">
              <p
                data-testid="bulk-add-tickers-result-added-count"
                className="text-emerald-700 dark:text-emerald-400 font-medium"
              >
                ✓ {result.added.length} ticker
                {result.added.length === 1 ? "" : "s"} added
              </p>
              <p className="text-slate-500">
                ⊝ {result.skipped_already_linked.length}{" "}
                already in your watchlist
              </p>
              <p className="text-rose-600">
                ✗ {result.errors.length} error
                {result.errors.length === 1 ? "" : "s"}
              </p>
            </div>
            {visibleErrors.length > 0 && (
              <div
                className="rounded border border-rose-200 dark:border-rose-800 max-h-48 overflow-y-auto p-2 text-[11px] font-mono"
                data-testid="bulk-add-tickers-result-errors-list"
              >
                {visibleErrors.map((e, i) => (
                  <div key={`${e.row}-${i}`}>
                    Row {e.row} · {e.ticker || "—"} ·{" "}
                    {e.reason}
                  </div>
                ))}
                {truncatedCount > 0 && (
                  <div className="text-slate-500 mt-1">
                    … {truncatedCount} more
                  </div>
                )}
              </div>
            )}
            <div className="flex justify-end">
              <button
                type="button"
                onClick={onClose}
                className="rounded bg-indigo-600 text-white px-3 py-1.5 text-sm"
              >
                Close
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 5.5: Run tests — expect 3 PASS**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend && \
  npx vitest run \
  components/widgets/__tests__/BulkAddTickersModal.test.tsx
```

Expected: 3 passed.

- [ ] **Step 5.6: Lint**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend && \
  npx eslint \
  components/widgets/BulkAddTickersModal.tsx \
  components/widgets/__tests__/BulkAddTickersModal.test.tsx \
  lib/types/bulkTickers.ts \
  --fix
```

Expected: 0 errors.

- [ ] **Step 5.7: Commit**

```bash
git add frontend/lib/types/bulkTickers.ts \
        frontend/components/widgets/BulkAddTickersModal.tsx \
        frontend/components/widgets/__tests__/BulkAddTickersModal.test.tsx
git commit -m "$(cat <<'EOF'
feat(watchlist-bulk-ui): BulkAddTickersModal

CSV file picker → POST /v1/tickers/bulk multipart → result
view with per-row error truncation at 100 + "N more" tail.
Calls onUploaded() after success so the parent widget can
SWR mutate().

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 6: `RemoveAllTickersModal` + `WatchlistOverflowMenu` + 4 vitest tests

**Files:**
- Create: `frontend/components/widgets/RemoveAllTickersModal.tsx`
- Create: `frontend/components/widgets/__tests__/RemoveAllTickersModal.test.tsx`
- Create: `frontend/components/widgets/WatchlistOverflowMenu.tsx`
- Create: `frontend/components/widgets/__tests__/WatchlistOverflowMenu.test.tsx`

- [ ] **Step 6.1: Implement `RemoveAllTickersModal`**

Create `frontend/components/widgets/RemoveAllTickersModal.tsx`:

```tsx
"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type { UnlinkAllResponse } from "@/lib/types/bulkTickers";

interface Props {
  currentCount: number;
  onClose: () => void;
  onRemoved: () => void; // parent SWR mutate()
}

export function RemoveAllTickersModal(
  { currentCount, onClose, onRemoved }: Props,
) {
  const [val, setVal] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const enabled = val === "REMOVE ALL";

  async function handleConfirm() {
    if (!enabled) return;
    setSubmitting(true);
    setErr(null);
    try {
      const r = await apiFetch(
        `${API_URL}/tickers/all`,
        {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm: "REMOVE ALL" }),
        },
      );
      if (!r.ok) {
        const body = await r.text();
        setErr(`Remove failed: ${r.status} ${body}`);
        return;
      }
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      const _data = (await r.json()) as UnlinkAllResponse;
      onRemoved();
      onClose();
    } catch (exc) {
      setErr(
        exc instanceof Error
          ? `Remove failed: ${exc.message}`
          : "Remove failed",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40"
      data-testid="remove-all-tickers-modal"
    >
      <div className="bg-white dark:bg-slate-900 rounded-md p-4 w-96 space-y-3">
        <h3 className="text-sm font-semibold">
          Remove all tickers from watchlist
        </h3>
        <p className="text-xs text-slate-500">
          This will remove all {currentCount.toLocaleString("en-IN")}{" "}
          ticker{currentCount === 1 ? "" : "s"} from your
          watchlist. Holdings (Portfolio) and algo positions
          are NOT affected.
        </p>
        <label className="flex flex-col gap-1 text-xs">
          <span>Type "REMOVE ALL" to confirm:</span>
          <input
            type="text"
            value={val}
            onChange={(e) => setVal(e.target.value)}
            data-testid="remove-all-tickers-input"
            className="rounded border border-slate-300 dark:border-slate-600 px-2 py-1 font-mono"
            placeholder="REMOVE ALL"
          />
        </label>
        {err && (
          <p className="text-xs text-rose-600">{err}</p>
        )}
        <div className="flex gap-2 justify-end">
          <button
            type="button"
            onClick={onClose}
            className="rounded border px-3 py-1.5 text-sm"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={!enabled || submitting}
            data-testid="remove-all-tickers-confirm-button"
            className="rounded bg-rose-600 text-white px-3 py-1.5 text-sm disabled:opacity-50"
          >
            {submitting
              ? "Removing…"
              : `Remove all ${currentCount.toLocaleString("en-IN")}`}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 6.2: Write tests for `RemoveAllTickersModal`**

Create `frontend/components/widgets/__tests__/RemoveAllTickersModal.test.tsx`:

```typescript
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { RemoveAllTickersModal } from "../RemoveAllTickersModal";

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from "@/lib/apiFetch";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RemoveAllTickersModal", () => {
  it("Confirm button disabled until exact phrase typed", () => {
    render(
      <RemoveAllTickersModal
        currentCount={42}
        onClose={vi.fn()}
        onRemoved={vi.fn()}
      />,
    );
    const btn = screen.getByTestId(
      "remove-all-tickers-confirm-button",
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);

    const input = screen.getByTestId(
      "remove-all-tickers-input",
    );
    // Wrong case — still disabled.
    fireEvent.change(input, {
      target: { value: "remove all" },
    });
    expect(btn.disabled).toBe(true);

    // Exact phrase — enabled.
    fireEvent.change(input, {
      target: { value: "REMOVE ALL" },
    });
    expect(btn.disabled).toBe(false);
  });

  it("posts DELETE and calls onRemoved on success", async () => {
    (apiFetch as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        ok: true,
        json: async () => ({ removed: 5 }),
      });
    const onRemoved = vi.fn();
    const onClose = vi.fn();
    render(
      <RemoveAllTickersModal
        currentCount={5}
        onClose={onClose}
        onRemoved={onRemoved}
      />,
    );
    fireEvent.change(
      screen.getByTestId("remove-all-tickers-input"),
      { target: { value: "REMOVE ALL" } },
    );
    fireEvent.click(
      screen.getByTestId(
        "remove-all-tickers-confirm-button",
      ),
    );
    await waitFor(() => {
      expect(onRemoved).toHaveBeenCalledOnce();
      expect(onClose).toHaveBeenCalledOnce();
    });
  });
});
```

- [ ] **Step 6.3: Implement `WatchlistOverflowMenu`**

Create `frontend/components/widgets/WatchlistOverflowMenu.tsx`:

```tsx
"use client";

import { useEffect, useRef, useState } from "react";

interface Props {
  onBulkAdd: () => void;
  onRemoveAll: () => void;
}

export function WatchlistOverflowMenu(
  { onBulkAdd, onRemoveAll }: Props,
) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    function onDocClick(e: MouseEvent) {
      if (cancelled) return;
      if (
        ref.current
        && !ref.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("click", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      cancelled = true;
      document.removeEventListener("click", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        data-testid="dashboard-watchlist-overflow-button"
        className="p-1 rounded-md text-gray-400 hover:text-indigo-600 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
        aria-label="More watchlist actions"
        title="More actions"
      >
        <svg
          className="w-4 h-4"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="12" cy="5" r="1" />
          <circle cx="12" cy="12" r="1" />
          <circle cx="12" cy="19" r="1" />
        </svg>
      </button>
      {open && (
        <div
          className="absolute right-0 mt-1 w-44 rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow z-[60] text-xs"
          data-testid="dashboard-watchlist-overflow-menu"
          role="menu"
        >
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onBulkAdd();
            }}
            data-testid="dashboard-watchlist-bulk-add-item"
            className="block w-full text-left px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-800"
          >
            Bulk add tickers…
          </button>
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onRemoveAll();
            }}
            data-testid="dashboard-watchlist-remove-all-item"
            className="block w-full text-left px-3 py-2 text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-950/30"
          >
            Remove all…
          </button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 6.4: Write tests for `WatchlistOverflowMenu`**

Create `frontend/components/widgets/__tests__/WatchlistOverflowMenu.test.tsx`:

```typescript
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { WatchlistOverflowMenu } from "../WatchlistOverflowMenu";

afterEach(() => cleanup());

describe("WatchlistOverflowMenu", () => {
  it("opens menu on button click and closes on Escape", () => {
    render(
      <WatchlistOverflowMenu
        onBulkAdd={vi.fn()}
        onRemoveAll={vi.fn()}
      />,
    );
    expect(
      screen.queryByTestId(
        "dashboard-watchlist-overflow-menu",
      ),
    ).toBeNull();

    fireEvent.click(
      screen.getByTestId(
        "dashboard-watchlist-overflow-button",
      ),
    );
    expect(
      screen.getByTestId(
        "dashboard-watchlist-overflow-menu",
      ),
    ).toBeDefined();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(
      screen.queryByTestId(
        "dashboard-watchlist-overflow-menu",
      ),
    ).toBeNull();
  });

  it("Bulk add item click invokes onBulkAdd once", () => {
    const onBulkAdd = vi.fn();
    render(
      <WatchlistOverflowMenu
        onBulkAdd={onBulkAdd}
        onRemoveAll={vi.fn()}
      />,
    );
    fireEvent.click(
      screen.getByTestId(
        "dashboard-watchlist-overflow-button",
      ),
    );
    fireEvent.click(
      screen.getByTestId(
        "dashboard-watchlist-bulk-add-item",
      ),
    );
    expect(onBulkAdd).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 6.5: Run all vitest tests for the modals + menu**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend && \
  npx vitest run \
  components/widgets/__tests__/BulkAddTickersModal.test.tsx \
  components/widgets/__tests__/RemoveAllTickersModal.test.tsx \
  components/widgets/__tests__/WatchlistOverflowMenu.test.tsx
```

Expected: 7 passed (3 + 2 + 2).

- [ ] **Step 6.6: Lint**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend && \
  npx eslint \
  components/widgets/RemoveAllTickersModal.tsx \
  components/widgets/WatchlistOverflowMenu.tsx \
  components/widgets/__tests__/RemoveAllTickersModal.test.tsx \
  components/widgets/__tests__/WatchlistOverflowMenu.test.tsx \
  --fix
```

Expected: 0 errors.

- [ ] **Step 6.7: Commit**

```bash
git add frontend/components/widgets/RemoveAllTickersModal.tsx \
        frontend/components/widgets/WatchlistOverflowMenu.tsx \
        frontend/components/widgets/__tests__/RemoveAllTickersModal.test.tsx \
        frontend/components/widgets/__tests__/WatchlistOverflowMenu.test.tsx
git commit -m "$(cat <<'EOF'
feat(watchlist-bulk-ui): RemoveAllTickersModal + OverflowMenu

Typed-confirm modal: destructive button disabled until input
EXACTLY equals "REMOVE ALL" (case-sensitive). Calls
onRemoved after success.

OverflowMenu is the ⋮ button + dropdown with click-outside
+ Escape close behavior. Two items: Bulk add tickers… +
Remove all… (the latter rendered in rose-600).

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 7: Wire menu into `WatchlistWidget` + E2E + PROGRESS + PR

**Files:**
- Modify: `frontend/components/widgets/WatchlistWidget.tsx`
- Modify: `e2e/utils/selectors.ts`
- Create: `e2e/tests/frontend/watchlist-bulk-ops.spec.ts`
- Modify: `PROGRESS.md`

- [ ] **Step 7.1: Patch `WatchlistWidget.tsx`**

Edit `frontend/components/widgets/WatchlistWidget.tsx`. Three changes:

(a) Add imports near the existing algo-related imports:

```typescript
import { BulkAddTickersModal } from "./BulkAddTickersModal";
import { RemoveAllTickersModal } from "./RemoveAllTickersModal";
import { WatchlistOverflowMenu } from "./WatchlistOverflowMenu";
```

(b) Add local state for the modals near the existing `useState` calls inside `WatchlistWidget`:

```typescript
const [bulkAddOpen, setBulkAddOpen] = useState(false);
const [removeAllOpen, setRemoveAllOpen] = useState(false);
```

(c) Render the menu in the header strip — find the existing `+` add button (around line 271 where `activeTab === "portfolio" && onAddStock && (` renders the add icon). Add a sibling block that renders the overflow menu ONLY on the watchlist tab. The menu sits to the right of the `+` button when both are visible (the `+` is portfolio-only today, so on the watchlist tab the menu is the only icon).

Locate the parent `<div className="flex items-center gap-2">` that wraps the `+` icon + the count chip (around line 270). Insert this block just before the count chip `<span>`:

```tsx
{activeTab === "watchlist" && (
  <WatchlistOverflowMenu
    onBulkAdd={() => setBulkAddOpen(true)}
    onRemoveAll={() => setRemoveAllOpen(true)}
  />
)}
```

(d) Mount the two modals at the BOTTOM of the outermost return, after the existing tab body branches:

```tsx
{bulkAddOpen && (
  <BulkAddTickersModal
    onClose={() => setBulkAddOpen(false)}
    onUploaded={() => {
      onRefresh?.();
    }}
  />
)}
{removeAllOpen && (
  <RemoveAllTickersModal
    currentCount={data.value?.tickers?.length ?? 0}
    onClose={() => setRemoveAllOpen(false)}
    onRemoved={() => {
      onRefresh?.();
    }}
  />
)}
```

`onRefresh` is the existing prop passed by `DashboardClient`; it triggers an SWR mutate on the watchlist payload.

- [ ] **Step 7.2: Add E2E testids**

Edit `e2e/utils/selectors.ts`. Inside the `FE = { ... }` object, add:

```typescript
dashboardWatchlistOverflowButton: "dashboard-watchlist-overflow-button",
dashboardWatchlistOverflowMenu: "dashboard-watchlist-overflow-menu",
dashboardWatchlistBulkAddItem: "dashboard-watchlist-bulk-add-item",
dashboardWatchlistRemoveAllItem: "dashboard-watchlist-remove-all-item",
bulkAddTickersModal: "bulk-add-tickers-modal",
bulkAddTickersFileInput: "bulk-add-tickers-file-input",
removeAllTickersModal: "remove-all-tickers-modal",
```

- [ ] **Step 7.3: Write the E2E smoke spec**

Create `e2e/tests/frontend/watchlist-bulk-ops.spec.ts`:

```typescript
import { test, expect } from "@playwright/test";
import { FE } from "../../utils/selectors";

test.use({ storageState: ".auth/superuser.json" });

test.describe("Watchlist bulk ops", () => {
  test(
    "overflow menu opens both modals",
    async ({ page }) => {
      await page.goto("/dashboard");
      // Land on the Watchlist tab (Portfolio is default).
      // Existing testid: dashboard-watchlist-table wraps
      // the whole widget; the tab buttons live inside.
      await page.getByRole(
        "button", { name: /watchlist/i },
      ).click();
      await page.getByTestId(
        FE.dashboardWatchlistOverflowButton,
      ).click();
      await expect(
        page.getByTestId(
          FE.dashboardWatchlistOverflowMenu,
        ),
      ).toBeVisible();
      // Bulk add modal mounts.
      await page.getByTestId(
        FE.dashboardWatchlistBulkAddItem,
      ).click();
      await expect(
        page.getByTestId(FE.bulkAddTickersModal),
      ).toBeVisible();
    },
  );
});
```

- [ ] **Step 7.4: Run the spec (optional)**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/e2e && \
  npx playwright test watchlist-bulk-ops.spec.ts \
  --project=frontend-chromium 2>&1 | tail -10
```

Env-dependent — if the frontend isn't running, skip. The spec is committed for CI.

Type-check:
```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/e2e && \
  npx tsc --noEmit 2>&1 | grep "watchlist-bulk-ops" | head -5
```
Expect: no errors specific to this file.

- [ ] **Step 7.5: Add PROGRESS.md entry**

Edit `PROGRESS.md`. Insert at the very top (above ALL existing 2026-05-24 entries):

```markdown
### 2026-05-24 — Watchlist bulk ops + universe binding (Epic C)

Two-part epic closing out the algo trading dashboard arc
(A budget reservation → B portfolio tab → C bulk ops +
universe).

**Bulk ops** — new ⋮ overflow menu in the dashboard
WatchlistWidget. Bulk-add via CSV upload (`POST
/v1/tickers/bulk` multipart, hard cap 5000 rows, per-row
diagnostic with added/skipped/errors). Remove-all via
typed "REMOVE ALL" confirmation (`DELETE
/v1/tickers/all`). Both endpoints invalidate
`cache:dash:watchlist:{user_id}` on success.

**Universe binding** — new `_scoped_tickers_for_strategy`
sibling helper at `backend/insights_routes.py` injects
algo-held positions (derived from `algo.events`) into the
`watchlist` scope. Only `resolve_universe` in
`backend/algo/backtest/universe.py` switches to the new
helper; insights tabs (Risk, Sectors, Dividends,
Piotroski, Targets) keep calling `_scoped_tickers`
unchanged. Closes the silent footgun where an algo opens
a Kite position the strategy could never iterate to.

`open_algo_positions(user_id)` reads `algo.events` (BUY
fills minus SELLs, since 2024-01-01), 60s Redis cache,
fail-open on read failure.

Out of scope (v1): CSV bulk-remove, text-area paste,
portfolio bulk import, multi-select table view, dedicated
/watchlist page, async/job-id flow, extension to insights
tabs.

Spec: `docs/superpowers/specs/2026-05-24-watchlist-bulk-ops-universe-binding-design.md`
Plan: `docs/superpowers/plans/2026-05-24-watchlist-bulk-ops-universe-binding.md`

---
```

- [ ] **Step 7.6: Commit + push + open PR**

```bash
git add frontend/components/widgets/WatchlistWidget.tsx \
        e2e/utils/selectors.ts \
        e2e/tests/frontend/watchlist-bulk-ops.spec.ts \
        PROGRESS.md
git commit -m "$(cat <<'EOF'
feat(watchlist-bulk-ui): wire menu into WatchlistWidget + E2E

Renders WatchlistOverflowMenu next to the existing + button
on the watchlist tab. Mounts both modals; on success they
call onRefresh() so the parent SWR-revalidates the watchlist.
E2E smoke verifies the menu opens + the bulk-add modal
mounts. PROGRESS entry summarises Epic C.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"

# PR opens against dev. If Epic B (PR #243) hasn't merged
# yet, the base may need to be feature/algo-portfolio-tab
# until B lands; check before pushing.
git push -u origin feature/algo-portfolio-tab

gh pr view 243 --json mergedAt -q '.mergedAt' || true
# If B is merged, base=dev; else base=feature/algo-portfolio-tab
# is not an option (this branch IS that branch). Sort the
# stacking before opening the PR.

# Once dev is the right base:
gh pr create \
  --base dev \
  --title "Watchlist bulk ops + universe binding (Epic C, v1)" \
  --body "$(cat <<'EOF'
## Summary

- New `POST /v1/tickers/bulk` (multipart CSV) + `DELETE /v1/tickers/all` (typed-confirm) endpoints
- New ⋮ overflow menu on the dashboard `WatchlistWidget` with `BulkAddTickersModal` + `RemoveAllTickersModal`
- Per-row CSV diagnostic — added / skipped_already_linked / errors with row index + reason
- 5,000-row hard cap (HTTP 413)
- `_scoped_tickers_for_strategy` sibling helper injects algo-held positions into `universe.scope=watchlist`; only `resolve_universe` switches to it; insights tabs unchanged
- `open_algo_positions` reads `algo.events`, 60 s Redis cache, fail-open
- `cache:dash:watchlist:{user_id}` invalidated on both bulk paths

Spec: `docs/superpowers/specs/2026-05-24-watchlist-bulk-ops-universe-binding-design.md`
Plan: `docs/superpowers/plans/2026-05-24-watchlist-bulk-ops-universe-binding.md`

## Test plan

- [x] Backend: 16 tests (3 repo + 6 routes + 4 open_positions + 3 scoped-for-strategy) — all green
- [x] Frontend: 7 Vitest tests (3 BulkAdd + 2 RemoveAll + 2 OverflowMenu) — all green
- [ ] E2E: `cd e2e && npx playwright test watchlist-bulk-ops.spec.ts --project=frontend-chromium`
- [ ] Manual: upload a 200-row CSV with 5 invalid rows, confirm per-row diagnostic
- [ ] Manual: run a live algo BUY on a ticker NOT in watchlist; verify the next paper iteration of a `universe=watchlist` strategy includes it
- [ ] Manual: type-confirm `"REMOVE ALL"`; confirm wipes watchlist; portfolio + algo positions untouched

## Out of scope (deferred)

- CSV bulk-remove
- Text-area paste
- Portfolio bulk import (separate epic)
- Multi-select table view
- Dedicated /watchlist page
- Async/job-id flow
- Extension to insights tabs

## Completes the A→B→C arc

- **A** Order Budget Reservation (PR #242, merged)
- **B** Algo Portfolio dashboard tab (PR #243, merged)
- **C** Watchlist bulk ops + universe binding (this PR)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review

**1. Spec coverage**

Walking the spec section-by-section:

| Spec | Implementation |
|---|---|
| §3 #1 — Universe extension `watchlist ∪ holdings ∪ algo_open_positions` | Task 3 (`open_algo_positions`) + Task 4 (`_scoped_tickers_for_strategy` + `resolve_universe` wire) |
| §3 #2 — CSV multipart bulk-add | Task 2 (`POST /v1/tickers/bulk`) |
| §3 #3 — Skip invalid + per-row diagnostic | Task 2 `_bulk_link_impl` error loop |
| §3 #4 — Typed "REMOVE ALL" confirmation | Task 2 `_unlink_all_impl` + Task 6 `RemoveAllTickersModal` |
| §3 #5 — ⋮ overflow menu in WatchlistWidget | Task 6 `WatchlistOverflowMenu` + Task 7 wire |
| §3 #6 — Endpoint shapes | Task 2 |
| §3 #7 — Algo runtime only (not insights tabs) | Task 4 — `resolve_universe` is the only caller switching to the new helper |
| §3 #8 — `algo.events` Iceberg source | Task 3 |
| §6 — Pydantic shapes | Task 2 (`BulkTickerResponse`, `BulkTickerErrorRow`, `UnlinkAllResponse`) |
| §6 — TS mirror | Task 5 `frontend/lib/types/bulkTickers.ts` |
| §7 — Overflow menu + 2 modals | Task 5, Task 6, Task 7 |
| §7 — Empty state unchanged | Task 7 leaves existing watchlist empty state intact |
| §8 — `cache:algo:open_positions:{user_id}` 60s TTL | Task 3 |
| §8 — `cache:dash:watchlist:{user_id}` invalidation on bulk paths | Task 2 `_invalidate_watchlist_cache` |
| §8 — SWR `mutate()` after bulk success | Task 7 calls `onRefresh?.()` from both modals |
| §9 — 16 backend tests | Task 1 (3) + Task 2 (6) + Task 3 (4) + Task 4 (3) = 16 |
| §9 — 7 frontend tests | Task 5 (3) + Task 6 (4) = 7 |
| §9 — 1 E2E smoke | Task 7 |
| §11 — `auth.user_tickers` schema unchanged | Task 1 uses the existing model |
| §11 — `algo.live_caps.allowed_tickers` independent | No task touches `safety.py` |
| §11 — `_scoped_tickers` signature unchanged | Task 4 adds a sibling, doesn't modify |
| §11 — `_LOOKBACK_SINCE = "2024-01-01"` | Task 3 |
| §11 — Fail-open on Iceberg/Redis failure | Task 3 — both wrapped `try/except` paths return empty set |
| §11 — CSV semantics best-effort | Task 2 — per-row errors, never reject whole batch |
| §11 — `"REMOVE ALL"` literal case-sensitive | Task 2 + Task 6 both enforce |
| §11 — 200 even when all-errors | Task 2 — `_bulk_link_impl` only raises on parse-failures (400) or size cap (413) |
| §11 — No new env vars | All knobs are code constants (`_LOOKBACK_SINCE`, `_CACHE_TTL_S`, `_BULK_ROW_CAP`, `MAX_VISIBLE_ERRORS`) |

All spec requirements have implementation steps.

**2. Placeholder scan**

No "TBD" / "TODO" / "implement later" in any task. Every step has actual content. Step 3.3 includes a NOTE asking the implementer to verify the `query_iceberg_table` import path by grep — but the alternative path (`backend.algo.routes.live`'s existing import) is documented inline; the implementer doesn't have to guess.

**3. Type consistency**

- Backend repo helpers: `bulk_link_tickers(session, user_id, tickers, source) -> tuple[list[str], list[str]]` (Task 1) consumed identically by `_bulk_link_impl` in Task 2.
- `unlink_all_tickers(session, user_id) -> int` (Task 1) consumed by `_unlink_all_impl` in Task 2 returning `UnlinkAllResponse(removed=removed)`.
- `BulkTickerResponse` shape (Task 2) mirrored verbatim in `frontend/lib/types/bulkTickers.ts` (Task 5).
- `_scoped_tickers_for_strategy(user, scope) -> list[str]` (Task 4) — same return shape as the existing `_scoped_tickers` it wraps.
- `open_algo_positions(user_id) -> set[str]` (Task 3) — caller in Task 4 wraps with `sorted(...)` before passing to `_dedup` (which accepts `list[str]`).
- FE component prop shapes: `BulkAddTickersModal({onClose, onUploaded})`, `RemoveAllTickersModal({currentCount, onClose, onRemoved})`, `WatchlistOverflowMenu({onBulkAdd, onRemoveAll})` — all consumed identically in Task 7's `WatchlistWidget` patch.
- TestIDs cross-reference: backend handlers don't have testids; FE testids (`bulk-add-tickers-modal`, `remove-all-tickers-input`, `dashboard-watchlist-overflow-button`, etc.) defined in components match exactly the strings in `e2e/utils/selectors.ts` (Task 7.2) and the E2E spec (Task 7.3).

All type/name consistency holds.
