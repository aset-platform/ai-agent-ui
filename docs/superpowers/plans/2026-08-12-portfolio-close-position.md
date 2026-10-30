# Portfolio Close-Position + Realized-P&L History — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user close (full or partial) a sold portfolio position from the list, recording lot-accurate realized P&L, and view a "Portfolio (Closed)" history alongside "Portfolio (Open)".

**Architecture:** Close appends a `side="SELL"` row to the existing Iceberg `portfolio_transactions` (append-only) and inserts a realized-P&L record into a new PG table `portfolio_closed_positions`. `get_portfolio_holdings` is updated to net BUY−SELL per ticker so the open quantity drops. Frontend adds a close row-icon → `ClosePositionModal` (via `PortfolioActionsProvider`) and splits the Portfolio tab into Open/Closed in `WatchlistWidget`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 async ORM, Alembic, PyIceberg/DuckDB (existing portfolio ledger), Next.js 16 / React 19, SWR, vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-08-12-portfolio-close-position-design.md` (note §2a — per-ticker aggregate + SELL-netting mechanism).

## Global Constraints

- Backend: line ≤79 chars (black/isort/flake8); `X | None` not `Optional`; no bare `print()` (module `_logger`); no bare `except`. Iceberg writes propagate errors. Tools return error strings, routes raise `HTTPException`.
- Realized P&L: `realized_pnl = (sell_price − avg_buy_price)·quantity − fees`; `realized_pnl_pct = realized_pnl / (avg_buy_price·quantity)` (when cost > 0). Cost basis = the ticker's displayed **weighted avg** buy price at close time, stored on the closed record.
- Close is **per-ticker** (the row clicked), `quantity` capped at the ticker's net open quantity.
- Cache: invalidate `cache:portfolio:{user_id}`, `cache:portfolio:perf:{user_id}:*`, `cache:portfolio:forecast:{user_id}:*` on close (mirror the add handler); add `cache:portfolio:closed:{user_id}`.
- Frontend: `apiFetch` (never bare fetch); SWR hooks in `frontend/hooks/`; `data-testid` on every new interactive element; currency via `WatchlistWidget`'s local `currencySymbol(code)` helper (no hardcoded ₹). `<Image/>` not `<img>`.
- Backend tests: `docker compose exec -T backend python -m pytest <path> -v`. Frontend: `cd frontend && npx vitest run <path>`.
- **This feature needs a backend restart to load the new route/model/migration** (§6.2) — the implementer runs the migration; the human coordinates the restart (drops live Kite WS).

---

### Task 1: PG table `portfolio_closed_positions` — model + migration

**Files:**
- Create: `backend/db/models/portfolio_close.py`
- Create: `backend/db/migrations/versions/2026_08_12_portfolio_closed_positions.py`
- Test: `backend/tests/test_portfolio_close_model.py`

**Interfaces:**
- Produces: ORM model `PortfolioClosedPosition` (`Base`, `__tablename__="portfolio_closed_positions"`) with columns per the spec §4; consumed by Task 2's repo.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_portfolio_close_model.py
from backend.db.models.portfolio_close import PortfolioClosedPosition


def test_model_table_and_columns():
    cols = set(PortfolioClosedPosition.__table__.columns.keys())
    assert PortfolioClosedPosition.__tablename__ == "portfolio_closed_positions"
    assert {
        "id", "user_id", "ticker", "quantity", "buy_price", "sell_price",
        "buy_date", "sell_date", "fees", "realized_pnl", "realized_pnl_pct",
        "currency", "market", "sell_transaction_id", "notes", "created_at",
    } <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T backend python -m pytest backend/tests/test_portfolio_close_model.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.db.models.portfolio_close`.

- [ ] **Step 3: Write the model**

```python
# backend/db/models/portfolio_close.py
"""Realized-P&L record for a closed (sold) portfolio position."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class PortfolioClosedPosition(Base):
    __tablename__ = "portfolio_closed_positions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    buy_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    sell_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    buy_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sell_date: Mapped[date] = mapped_column(Date, nullable=False)
    fees: Mapped[float] = mapped_column(
        Numeric(18, 4), nullable=False, server_default="0"
    )
    realized_pnl: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    realized_pnl_pct: Mapped[float | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    sell_transaction_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

Confirm `backend/db/base.py` exports `Base` and the model is imported where models are registered (check `backend/db/models/__init__.py` — add `from .portfolio_close import PortfolioClosedPosition` if that file re-exports models, mirroring the existing entries).

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T backend python -m pytest backend/tests/test_portfolio_close_model.py -v`
Expected: PASS.

- [ ] **Step 5: Write the Alembic migration**

Generate then hand-verify (autogenerate may miss the index):
```bash
docker compose exec -T -e PYTHONPATH=. backend alembic revision -m "portfolio_closed_positions"
```
Edit the new file to match (revision slug `2026_08_12_portfolio_closed`, `down_revision` = current head — find via `docker compose exec -T -e PYTHONPATH=. backend alembic heads`):
```python
def upgrade() -> None:
    op.create_table(
        "portfolio_closed_positions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("buy_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("sell_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("buy_date", sa.Date, nullable=True),
        sa.Column("sell_date", sa.Date, nullable=False),
        sa.Column("fees", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("realized_pnl", sa.Numeric(18, 4), nullable=False),
        sa.Column("realized_pnl_pct", sa.Numeric(12, 4), nullable=True),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("sell_transaction_id", sa.String(36), nullable=True),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_pcp_user_sell_date", "portfolio_closed_positions",
                    ["user_id", "sell_date"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_pcp_user_sell_date", "portfolio_closed_positions")
    op.drop_table("portfolio_closed_positions")
```

- [ ] **Step 6: Apply + verify the migration**

Run:
```bash
docker compose exec -T backend rm -f /app/backend/db/migrations/versions/__pycache__/*.pyc
docker compose exec -T -e PYTHONPATH=. backend alembic upgrade head
docker compose exec -T postgres psql -U app -d aiagent -c "\d portfolio_closed_positions" | head
```
Expected: table exists with the columns + index.

- [ ] **Step 7: Commit**

```bash
git add backend/db/models/portfolio_close.py \
  backend/db/migrations/versions/2026_08_12_portfolio_closed_positions.py \
  backend/tests/test_portfolio_close_model.py backend/db/models/__init__.py
git commit -m "feat(portfolio): portfolio_closed_positions table + model"
```

---

### Task 2: PG repo for closed positions

**Files:**
- Create: `auth/repo/portfolio_close_repo.py`
- Test: `backend/tests/test_portfolio_close_repo.py`

**Interfaces:**
- Consumes: `PortfolioClosedPosition` (Task 1).
- Produces:
  - `async def add_closed_position(session: AsyncSession, data: dict) -> dict` — inserts a row (`data` keys match the model columns; `id` generated if absent), commits, returns the row as a plain dict.
  - `async def list_closed_positions(session: AsyncSession, user_id: str) -> list[dict]` — rows for the user, `sell_date DESC, created_at DESC`, as dicts.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_portfolio_close_repo.py
import pytest

from auth.repo import portfolio_close_repo as repo


@pytest.mark.asyncio
async def test_add_and_list_round_trip(pg_session):  # pg_session: AsyncSession fixture
    row = await repo.add_closed_position(pg_session, {
        "user_id": "u1", "ticker": "DLF.NS", "quantity": 20,
        "buy_price": 746.26, "sell_price": 800.0, "sell_date": "2026-08-12",
        "fees": 0, "realized_pnl": 1074.8, "realized_pnl_pct": 0.036,
        "currency": "INR", "market": "india", "sell_transaction_id": "t1",
    })
    assert row["id"] and row["ticker"] == "DLF.NS"
    rows = await repo.list_closed_positions(pg_session, "u1")
    assert any(r["ticker"] == "DLF.NS" and float(r["realized_pnl"]) == 1074.8
               for r in rows)
```

If no `pg_session` fixture exists, add one to `backend/tests/conftest.py` using `get_session_factory()` with a rollback per test — check `conftest.py` for an existing async-session fixture pattern first and reuse it; if none exists, the implementer creates a minimal one (open session, yield, rollback).

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T backend python -m pytest backend/tests/test_portfolio_close_repo.py -v`
Expected: FAIL — import error / fixture missing.

- [ ] **Step 3: Write the repo**

```python
# auth/repo/portfolio_close_repo.py
"""Async PG repo for portfolio_closed_positions (realized P&L)."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models.portfolio_close import PortfolioClosedPosition


def _to_dict(m: PortfolioClosedPosition) -> dict[str, Any]:
    return {c.name: getattr(m, c.name) for c in m.__table__.columns}


def _coerce_date(v: Any) -> date | None:
    if v is None or isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


async def add_closed_position(
    session: AsyncSession, data: dict[str, Any]
) -> dict[str, Any]:
    row = PortfolioClosedPosition(
        id=data.get("id") or str(uuid.uuid4()),
        user_id=data["user_id"],
        ticker=data["ticker"],
        quantity=data["quantity"],
        buy_price=data["buy_price"],
        sell_price=data["sell_price"],
        buy_date=_coerce_date(data.get("buy_date")),
        sell_date=_coerce_date(data["sell_date"]),
        fees=data.get("fees", 0),
        realized_pnl=data["realized_pnl"],
        realized_pnl_pct=data.get("realized_pnl_pct"),
        currency=data["currency"],
        market=data["market"],
        sell_transaction_id=data.get("sell_transaction_id"),
        notes=data.get("notes"),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _to_dict(row)


async def list_closed_positions(
    session: AsyncSession, user_id: str
) -> list[dict[str, Any]]:
    result = await session.execute(
        select(PortfolioClosedPosition)
        .where(PortfolioClosedPosition.user_id == user_id)
        .order_by(
            PortfolioClosedPosition.sell_date.desc(),
            PortfolioClosedPosition.created_at.desc(),
        )
    )
    return [_to_dict(m) for m in result.scalars().all()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T backend python -m pytest backend/tests/test_portfolio_close_repo.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add auth/repo/portfolio_close_repo.py backend/tests/test_portfolio_close_repo.py backend/tests/conftest.py
git commit -m "feat(portfolio): closed-positions PG repo (add + list)"
```

---

### Task 3: Net BUY−SELL in `get_portfolio_holdings`

**Files:**
- Modify: `stocks/repository.py` (`get_portfolio_holdings`, ~line 4379-4457)
- Test: `backend/tests/test_portfolio_holdings_netting.py`

**Interfaces:**
- Produces: `get_portfolio_holdings(user_id)` now returns net quantity (BUY sum − SELL sum) per (ticker, currency, market); `avg_price` still from BUY lots; rows with net `quantity > 0` only. Consumed by the existing `/portfolio` list + Task 4.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_portfolio_holdings_netting.py
import pandas as pd
from unittest.mock import patch

from stocks.repository import StockRepository


def test_sell_reduces_net_quantity():
    rows = pd.DataFrame([
        {"user_id": "u1", "ticker": "DLF.NS", "side": "BUY", "quantity": 45.0,
         "price": 746.26, "currency": "INR", "market": "india"},
        {"user_id": "u1", "ticker": "DLF.NS", "side": "SELL", "quantity": 20.0,
         "price": 800.0, "currency": "INR", "market": "india"},
    ])
    repo = StockRepository.__new__(StockRepository)  # skip heavy __init__
    with patch.object(StockRepository, "_table_to_df", return_value=rows), \
         patch("backend.db.duckdb_engine.query_iceberg_df",
               side_effect=Exception("force fallback")), \
         patch.object(StockRepository, "_load_table",
                      side_effect=Exception("force df path")):
        out = repo.get_portfolio_holdings("u1")
    r = out[out["ticker"] == "DLF.NS"].iloc[0]
    assert float(r["quantity"]) == 25.0          # 45 - 20
    assert abs(float(r["avg_price"]) - 746.26) < 1e-6   # avg from BUY only
```

(The implementer verifies the exact fallback path the mock forces reaches the `_table_to_df` branch; adjust the patches to hit the same code the netting is added to. The behavioral assertion — SELL reduces net qty, avg from BUY — is the contract.)

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T backend python -m pytest backend/tests/test_portfolio_holdings_netting.py -v`
Expected: FAIL — SELL currently ignored (qty would be 45 or the row filtered out).

- [ ] **Step 3: Implement netting**

In `get_portfolio_holdings`, stop filtering `side='BUY'` at read; read all rows for the user, then compute per (ticker, currency, market): `buy_qty`/`invested` from BUY rows, `sell_qty` from SELL rows, `net_qty = buy_qty − sell_qty`, `avg_price = invested / buy_qty`, keep `net_qty > 0`. Concretely, change the DuckDB query to drop `AND side = ?` (read BUY+SELL), and replace the grouping block (`stocks/repository.py:4446-4457`) with:

```python
df["signed_qty"] = df.apply(
    lambda r: r["quantity"] if str(r["side"]).upper() == "BUY"
    else -r["quantity"], axis=1
)
buys = df[df["side"].str.upper() == "BUY"].copy()
buys["invested"] = buys["quantity"] * buys["price"]
inv = (buys.groupby(["ticker", "currency", "market"])
       .agg(buy_qty=("quantity", "sum"), invested=("invested", "sum"))
       .reset_index())
net = (df.groupby(["ticker", "currency", "market"])["signed_qty"]
       .sum().reset_index(name="quantity"))
grouped = inv.merge(net, on=["ticker", "currency", "market"], how="right")
grouped["invested"] = grouped["invested"].fillna(0.0)
grouped["buy_qty"] = grouped["buy_qty"].fillna(0.0)
grouped["avg_price"] = grouped.apply(
    lambda r: (r["invested"] / r["buy_qty"]) if r["buy_qty"] > 0 else 0.0,
    axis=1,
)
grouped = grouped[grouped["quantity"] > 0]
return grouped[
    ["ticker", "quantity", "avg_price", "currency", "market", "invested"]
].reset_index(drop=True)
```

Apply the same "read all sides" change to the PyIceberg + `_table_to_df` fallbacks (drop the `EqualTo("side","BUY")` filter; keep the `user_id` filter).

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T backend python -m pytest backend/tests/test_portfolio_holdings_netting.py -v`
Expected: PASS. Also run existing portfolio tests: `docker compose exec -T backend python -m pytest backend/algo/tests/test_portfolio_routes.py -v` (confirm no regressions).

- [ ] **Step 5: Commit**

```bash
git add stocks/repository.py backend/tests/test_portfolio_holdings_netting.py
git commit -m "feat(portfolio): net BUY-SELL in get_portfolio_holdings"
```

---

### Task 4: Close + closed-list endpoints

**Files:**
- Modify: `auth/endpoints/ticker_routes.py` (add two routes + request models near the existing `AddPortfolioRequest`)
- Test: `backend/tests/test_portfolio_close_routes.py`

**Interfaces:**
- Consumes: `_get_stock_repo()` (`get_portfolio_holdings`, `add_portfolio_transaction`), `portfolio_close_repo` (Task 2), `get_session_factory()`.
- Produces:
  - `POST /portfolio/{ticker}/close` body `ClosePositionRequest{quantity, sell_price, sell_date, fees=0, notes=None}` → 200 `{detail, realized_pnl, realized_pnl_pct, closed_id}`; 404 if ticker not held; 400 if `quantity` invalid.
  - `GET /portfolio/closed` → `{closed: [...], totals: {realized_pnl, ...}}`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_portfolio_close_routes.py
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch


@pytest.mark.asyncio
async def test_close_full_position_records_realized_pnl():
    from auth.endpoints import ticker_routes as tr

    holdings = pd.DataFrame([{
        "ticker": "DLF.NS", "quantity": 45.0, "avg_price": 746.26,
        "currency": "INR", "market": "india", "invested": 33581.7,
    }])
    stock_repo = MagicMock()
    stock_repo.get_portfolio_holdings.return_value = holdings
    added = {}

    def _add(txn):
        added.update(txn)
    stock_repo.add_portfolio_transaction.side_effect = _add

    closed_rows = []

    async def _add_closed(session, data):
        data = {**data, "id": "c1"}
        closed_rows.append(data)
        return data

    user = MagicMock(user_id="u1")
    with patch.object(tr, "_get_stock_repo", return_value=stock_repo), \
         patch("auth.repo.portfolio_close_repo.add_closed_position", _add_closed), \
         patch.object(tr, "_close_session_scope") as scope:
        scope.return_value.__aenter__.return_value = MagicMock()
        resp = await tr.close_portfolio_position(
            "DLF.NS",
            tr.ClosePositionRequest(quantity=45, sell_price=800.0,
                                    sell_date="2026-08-12"),
            user=user,
        )

    assert added["side"] == "SELL" and added["quantity"] == 45
    # realized = (800 - 746.26) * 45 = 2418.30
    assert round(resp["realized_pnl"], 2) == 2418.30
    assert closed_rows[0]["buy_price"] == 746.26
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T backend python -m pytest backend/tests/test_portfolio_close_routes.py -v`
Expected: FAIL — `close_portfolio_position` / `ClosePositionRequest` don't exist.

- [ ] **Step 3: Implement the routes**

Add near the other portfolio models/routes in `auth/endpoints/ticker_routes.py`. Model:
```python
class ClosePositionRequest(BaseModel):
    quantity: float
    sell_price: float
    sell_date: str
    fees: float = 0.0
    notes: str | None = None
```
A small session-scope helper (mirrors `helpers._get_repo`'s use of `get_session_factory`):
```python
def _close_session_scope():
    from backend.db.engine import get_session_factory
    return get_session_factory()()   # returns an AsyncSession context manager
```
Handler:
```python
@router.post("/portfolio/{ticker}/close")
async def close_portfolio_position(
    ticker: str,
    body: ClosePositionRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    """Close (full/partial) a held position: record realized P&L."""
    from auth.repo import portfolio_close_repo
    tkr = ticker.upper().strip()
    stock_repo = _get_stock_repo()
    holdings = stock_repo.get_portfolio_holdings(user.user_id)
    row = holdings[holdings["ticker"] == tkr]
    if row.empty:
        raise HTTPException(status_code=404, detail="Not held")
    open_qty = float(row.iloc[0]["quantity"])
    avg_price = float(row.iloc[0]["avg_price"])
    ccy = str(row.iloc[0]["currency"])
    mkt = str(row.iloc[0]["market"])
    if body.quantity <= 0 or body.quantity > open_qty:
        raise HTTPException(
            status_code=400,
            detail=f"quantity must be 0 < q <= {open_qty}",
        )
    cost = avg_price * body.quantity
    realized = (body.sell_price - avg_price) * body.quantity - body.fees
    realized_pct = (realized / cost) if cost > 0 else None

    sell_txn_id = str(uuid.uuid4())
    stock_repo.add_portfolio_transaction({
        "transaction_id": sell_txn_id, "user_id": user.user_id,
        "ticker": tkr, "side": "SELL", "quantity": body.quantity,
        "price": body.sell_price, "currency": ccy, "market": mkt,
        "trade_date": date.fromisoformat(body.sell_date),
        "fees": body.fees, "notes": body.notes or "",
    })
    async with _close_session_scope() as session:
        closed = await portfolio_close_repo.add_closed_position(session, {
            "user_id": user.user_id, "ticker": tkr, "quantity": body.quantity,
            "buy_price": avg_price, "sell_price": body.sell_price,
            "sell_date": body.sell_date, "fees": body.fees,
            "realized_pnl": realized, "realized_pnl_pct": realized_pct,
            "currency": ccy, "market": mkt,
            "sell_transaction_id": sell_txn_id, "notes": body.notes,
        })
    _invalidate_portfolio_caches(user.user_id)   # extract the add-handler block
    return {
        "detail": "closed", "closed_id": closed["id"],
        "realized_pnl": realized, "realized_pnl_pct": realized_pct,
    }


@router.get("/portfolio/closed")
async def list_closed_positions(
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    from auth.repo import portfolio_close_repo
    async with _close_session_scope() as session:
        rows = await portfolio_close_repo.list_closed_positions(
            session, user.user_id,
        )
    total = sum(float(r["realized_pnl"]) for r in rows)
    return {"closed": rows, "totals": {"realized_pnl": total}}
```
Extract the existing add-handler cache block (`ticker_routes.py:932-947`) into `_invalidate_portfolio_caches(user_id)` and also invalidate `cache:portfolio:closed:{user_id}`; call it from both the add handler and the close handler (DRY).

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T backend python -m pytest backend/tests/test_portfolio_close_routes.py -v`
Expected: PASS. Add + run a partial-close test (quantity=20 → open 25 next read is out of scope for the unit test; assert the SELL qty=20 and realized uses 20) and error tests (`quantity=0` → 400; unknown ticker → 404) in the same file.

- [ ] **Step 5: Commit**

```bash
git add auth/endpoints/ticker_routes.py backend/tests/test_portfolio_close_routes.py
git commit -m "feat(portfolio): close + closed-list endpoints with realized P&L"
```

---

### Task 5: Frontend data layer — `closeHolding` + `useClosedPositions`

**Files:**
- Modify: `frontend/hooks/usePortfolio.ts` (add `closeHolding`)
- Create: `frontend/hooks/useClosedPositions.ts`
- Test: `frontend/hooks/__tests__/useClosedPositions.test.ts` (if a hooks test dir exists; else fold the render check into Task 6's vitest)

**Interfaces:**
- Produces:
  - `usePortfolio().closeHolding(ticker: string, body: {quantity:number; sell_price:number; sell_date:string; fees?:number; notes?:string})` → POSTs `/users/me/portfolio/{ticker}/close`, then `mutate()`.
  - `useClosedPositions()` → `{ closed: ClosedPosition[]; totals: {realized_pnl:number}; loading; error; refresh }`.

- [ ] **Step 1: Write `closeHolding`** (mirror `editHolding`, `usePortfolio.ts:73-101`)

```ts
const closeHolding = useCallback(
  async (
    ticker: string,
    body: { quantity: number; sell_price: number; sell_date: string;
            fees?: number; notes?: string },
  ) => {
    const r = await apiFetch(
      `${API_URL}/users/me/portfolio/${encodeURIComponent(ticker)}/close`,
      { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) },
    );
    if (!r.ok) {
      const b = await r.json().catch(() => ({}));
      throw new Error(b.detail || `HTTP ${r.status}`);
    }
    mutate();
    return r.json();
  },
  [mutate],
);
```
Add `closeHolding` to the returned object.

- [ ] **Step 2: Write `useClosedPositions`** (mirror `usePortfolio`'s SWR setup)

```ts
// frontend/hooks/useClosedPositions.ts
"use client";
import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface ClosedPosition {
  id: string; ticker: string; quantity: number; buy_price: number;
  sell_price: number; sell_date: string; fees: number;
  realized_pnl: number; realized_pnl_pct: number | null;
  currency: string; market: string;
}
interface ClosedResponse { closed: ClosedPosition[]; totals: { realized_pnl: number }; }

export function useClosedPositions() {
  const { data, error, isLoading, mutate } = useSWR<ClosedResponse>(
    `${API_URL}/users/me/portfolio/closed`,
    async (u: string) => {
      const r = await apiFetch(u);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    { revalidateOnFocus: false, dedupingInterval: 120_000 },
  );
  return {
    closed: data?.closed ?? [],
    totals: data?.totals ?? { realized_pnl: 0 },
    loading: isLoading,
    error: error ? "Failed to load" : null,
    refresh: () => mutate(),
  };
}
```

- [ ] **Step 3: Typecheck + commit**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head` (expect clean for these files).
```bash
git add frontend/hooks/usePortfolio.ts frontend/hooks/useClosedPositions.ts
git commit -m "feat(portfolio): closeHolding + useClosedPositions data layer"
```

---

### Task 6: `ClosePositionModal` + provider wiring

**Files:**
- Create: `frontend/components/widgets/ClosePositionModal.tsx`
- Modify: `frontend/providers/PortfolioActionsProvider.tsx` (add `openClose`)
- Test: `frontend/components/widgets/__tests__/ClosePositionModal.test.tsx`

**Interfaces:**
- Consumes: `usePortfolio().closeHolding` (Task 5), `PortfolioHolding` (ticker, quantity, avg_price, currency).
- Produces: `ClosePositionModal` props `{isOpen, holding: {ticker; quantity; avg_price; currency}, onClose, onConfirm}` where `onConfirm(body)` calls `closeHolding`. Provider gains `openClose(ticker: string)` on `PortfolioActionsCtx`.

- [ ] **Step 1: Write the failing vitest** (the realized-P&L preview is the logic worth testing)

```tsx
// __tests__/ClosePositionModal.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import ClosePositionModal from "../ClosePositionModal";

const holding = { ticker: "DLF.NS", quantity: 45, avg_price: 746.26, currency: "INR" };

test("previews realized P&L from qty, sell price, fees", () => {
  render(<ClosePositionModal isOpen holding={holding} onClose={() => {}}
         onConfirm={() => {}} />);
  fireEvent.change(screen.getByTestId("close-qty-input"), { target: { value: "20" } });
  fireEvent.change(screen.getByTestId("close-price-input"), { target: { value: "800" } });
  // (800 - 746.26) * 20 = 1074.80
  expect(screen.getByTestId("close-pnl-preview").textContent).toContain("1,074.80");
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run components/widgets/__tests__/ClosePositionModal.test.tsx`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the modal**

Model it on `EditStockModal.tsx` (same overlay/z-index/`data-testid` style; `z-[70]` per §5.6). Fields with testids: `close-qty-input` (default = `holding.quantity`, max = `holding.quantity`), `close-price-input`, `close-date-input` (default today), `close-fees-input` (default 0), `close-notes-input`. Compute `realized = (sellPrice - holding.avg_price) * qty - fees`; render in `data-testid="close-pnl-preview"` using `WatchlistWidget`'s `currencySymbol(holding.currency)` + `toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})`, colored green/red by sign. Confirm button `data-testid="close-confirm"` calls `onConfirm({quantity, sell_price, sell_date, fees, notes})`; validate `0 < qty <= holding.quantity` (disable confirm otherwise). Loading + error states like `EditStockModal`.

- [ ] **Step 4: Wire the provider**

In `PortfolioActionsProvider.tsx`: add `openClose: (ticker: string) => void` to `PortfolioActionsCtx`; add `const [closeTarget, setCloseTarget] = useState<string | null>(null);` + `openClose` callback; render `<ClosePositionModal isOpen={closeTarget !== null} holding={holdingFor(closeTarget)} onClose={() => setCloseTarget(null)} onConfirm={async (b) => { await closeHolding(closeTarget!, b); setCloseTarget(null); refreshClosed?.(); }} />` at the bottom (mirror the delete/edit blocks, lines 161-204). Source `holdingFor` from the `usePortfolio().holdings` already available in the provider.

- [ ] **Step 5: Run test to verify it passes + commit**

Run: `cd frontend && npx vitest run components/widgets/__tests__/ClosePositionModal.test.tsx`
Expected: PASS.
```bash
git add frontend/components/widgets/ClosePositionModal.tsx \
  frontend/providers/PortfolioActionsProvider.tsx \
  frontend/components/widgets/__tests__/ClosePositionModal.test.tsx
git commit -m "feat(portfolio): ClosePositionModal + provider openClose"
```

---

### Task 7: `WatchlistWidget` — Open/Closed tabs + close row-icon + Closed list

**Files:**
- Modify: `frontend/components/widgets/WatchlistWidget.tsx`
- Modify: `frontend/app/(authenticated)/dashboard/DashboardClient.tsx` (pass `onCloseStock`)
- Test: `e2e/` spec — add a close-icon presence check (testid) to the existing portfolio e2e (or a new `e2e/tests/portfolio-close.spec.ts` following the POM pattern)

**Interfaces:**
- Consumes: `usePortfolioActions().openClose` (Task 6), `useClosedPositions()` (Task 5).

- [ ] **Step 1: Split the Portfolio tab into two**

Extend `WidgetTab` (`WatchlistWidget.tsx:82`) to `"portfolio_open" | "portfolio_closed" | "watchlist" | "algo"`; default `"portfolio_open"`. In the tab bar (lines 240-290) render two pills labeled **"Portfolio (Open)"** and **"Portfolio (Closed)"** before Watchlist (each a `<button data-testid="tab-portfolio-open">` / `"tab-portfolio-closed"`), reusing the exact active/inactive pill classes. The existing open-holdings list renders under `portfolio_open`.

- [ ] **Step 2: Add the close row-icon** (Open list rows)

In the holding row action group (`WatchlistWidget.tsx:403`, alongside refresh/view/delete), add a button `data-testid={"portfolio-close-" + ticker}` `aria-label="Close position"` (an X-circle / exit inline SVG, styled like the delete button at lines 491-505) that calls a new prop `onCloseStock?.(ticker)`.

- [ ] **Step 3: Render the Closed list** (under `portfolio_closed`)

When `activeTab === "portfolio_closed"`, render a list from `useClosedPositions()`: a realized-P&L **total** header (colored by sign, `data-testid="closed-total"`) and one row per closed position — ticker, qty, `buy→sell` price, realized P&L (amount + %), sell date — each `data-testid={"closed-row-" + ticker}`. Currency via `currencySymbol`. Empty state: "No closed positions yet." If the row reaches ≥8 columns, apply §5.4 (column selector + CSV + sort) — for this compact row it does not, so a plain list is fine.

- [ ] **Step 4: Wire the prop** in `DashboardClient.tsx` (~line 401-415)

```tsx
onCloseStock={(ticker) => openClose(ticker)}
```
(destructure `openClose` from `usePortfolioActions()` where `openAdd`/`openDelete` are obtained).

- [ ] **Step 5: Verify + commit**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head` and `npx vitest run` (existing widget tests still green). Manually confirm in the app (or e2e) the close icon appears on Open rows and the Closed tab lists closed positions.
```bash
git add frontend/components/widgets/WatchlistWidget.tsx \
  frontend/app/(authenticated)/dashboard/DashboardClient.tsx e2e/
git commit -m "feat(portfolio): Open/Closed tabs + close row-icon + closed list"
```

---

## Self-Review

**Spec coverage:**
- §2a SELL-netting mechanism → Task 3 (holdings netting) + Task 4 (append SELL). ✓
- §4 PG table → Task 1 (model + migration). ✓
- §5 close + closed-list endpoints + realized P&L + cache invalidation → Task 4; repo → Task 2. ✓
- §6 two tabs + close icon + modal + closed list → Tasks 5/6/7. ✓
- §8 tests (full/partial close, 400/404, modal preview, e2e testid) → Tasks 3/4/6/7. ✓
- §9 rollout (migration + restart) → Task 1 step 6 + Global Constraints. ✓

**Placeholder scan:** backend tasks carry full code; frontend Tasks 6/7 give exact contracts (props, testids, formula, tab labels) + template references to real sibling components (EditStockModal/WatchlistWidget) — following existing patterns, not vague placeholders. ✓

**Type consistency:** `closeHolding(ticker, body)` (Task 5) matches the `POST /portfolio/{ticker}/close` shape (Task 4) and the provider's `onConfirm` (Task 6). `ClosePositionRequest` fields align across Task 4 ↔ Task 5 ↔ Task 6. `PortfolioClosedPosition` columns (Task 1) match the repo dict keys (Task 2) and `ClosedPosition` TS type (Task 5). ✓

**Verify-before-build flags for the implementer:** (a) `backend/db/base.py` `Base` export + whether `backend/db/models/__init__.py` re-exports models (register the new one); (b) an existing async `pg_session`/DB fixture in `backend/tests/conftest.py` to reuse (Task 2); (c) the exact fallback branch the Task 3 test mocks must hit; (d) how the provider currently obtains `usePortfolio().holdings`/`refreshClosed` for the modal (Task 6 step 4).
