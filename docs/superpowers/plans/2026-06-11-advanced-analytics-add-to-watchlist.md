# Advanced Analytics → Add to Watchlist — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reusable "Add to Watchlist" button on every Advanced Analytics result tab that appends all filter-matching tickers to the user's watchlist (server-side dedupe), growing the paper live-WS pool.

**Architecture:** A `GET /advanced-analytics/{report}/tickers` JSON endpoint (reuses the export's full-set filter pipeline, capped) feeds the button; the button POSTs the list to a generic `POST /users/me/tickers/bulk-add` JSON endpoint that reuses the existing bulk dedupe core. One shared button in `AdvancedAnalyticsTable` surfaces on all tabs.

**Tech Stack:** FastAPI (Python 3.12, SQLAlchemy async), Next.js 16 / React 19, SWR, pytest, vitest, Playwright.

Spec: `docs/superpowers/specs/2026-06-11-advanced-analytics-add-to-watchlist-design.md`

**Branch:** `feature/aa-add-to-watchlist` (already created off `dev`).

---

## File structure

- `auth/endpoints/ticker_routes.py` — extract `_bulk_link_tickers` core; add `BulkAddRequest` + `POST /tickers/bulk-add`; hoist.
- `backend/advanced_analytics_routes.py` — `ReportTickersResponse` + `_export_tickers` + `GET /{report}/tickers`.
- `tests/backend/test_ticker_routes.py` — bulk-add tests (create if absent).
- `tests/backend/test_advanced_analytics_routes.py` — `/tickers` test (extend nearest existing).
- `frontend/hooks/useAddToWatchlist.ts` — POST hook.
- `frontend/hooks/__tests__/useAddToWatchlist.test.ts` — hook test.
- `frontend/components/advanced-analytics/AddFilteredToWatchlistButton.tsx` — button.
- `frontend/components/advanced-analytics/AdvancedAnalyticsTable.tsx` — mount + handler.
- `frontend/lib/types/advancedAnalytics.ts` — `ReportTickersResponse`.
- `e2e/utils/selectors.ts` + `e2e/pages/frontend/AdvancedAnalyticsPage.ts` + `e2e/tests/advanced-analytics-add-to-watchlist.spec.ts`.

---

## Task 1: Backend — extract `_bulk_link_tickers` core (CSV behaviour unchanged)

**Files:**
- Modify: `auth/endpoints/ticker_routes.py` (`_bulk_link_impl` ~340-439)
- Test: `tests/backend/test_ticker_routes.py`

- [ ] **Step 1: Write failing test**

Create `tests/backend/test_ticker_routes.py`:
```python
import pytest
from unittest.mock import AsyncMock, patch

from auth.endpoints import ticker_routes as tr


@pytest.mark.asyncio
async def test_bulk_link_tickers_dedupes_and_validates():
    repo = AsyncMock()
    repo.bulk_link_tickers = AsyncMock(
        return_value=(["TCS.NS", "INFY.NS"], ["ITC.NS"]),
    )
    with patch.object(tr._helpers, "_get_repo", return_value=repo), \
         patch.object(tr, "_invalidate_watchlist_cache") as inval:
        rows = [
            (1, "tcs.ns"),    # normalised -> TCS.NS
            (2, "INFY.NS"),
            (3, "ITC.NS"),    # repo reports already-linked
            (4, "tcs.ns"),    # in-batch dup -> error
            (5, ""),          # empty -> error
        ]
        resp = await tr._bulk_link_tickers(
            user_id="u1", rows=rows, source="bulk_json", total_rows=5,
        )
    sent = repo.bulk_link_tickers.await_args.args[1]
    assert sent == ["TCS.NS", "INFY.NS", "ITC.NS"]
    assert resp.added == ["TCS.NS", "INFY.NS"]
    assert resp.skipped_already_linked == ["ITC.NS"]
    assert "duplicate in batch" in {e.reason for e in resp.errors}
    assert resp.total_rows == 5
    inval.assert_called_once_with("u1")
```

- [ ] **Step 2: Run test — verify it fails**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_ticker_routes.py -q`
Expected: FAIL — `module 'auth.endpoints.ticker_routes' has no attribute '_bulk_link_tickers'`.

- [ ] **Step 3: Implement the extraction**

In `auth/endpoints/ticker_routes.py`, add the core ABOVE `_bulk_link_impl`:
```python
async def _bulk_link_tickers(
    *,
    user_id: str,
    rows: list[tuple[int, str]],
    source: str,
    total_rows: int,
) -> BulkTickerResponse:
    """Validate + dedupe (row_number, raw_ticker) pairs, link via
    repo, invalidate the watchlist cache, build the per-row report.
    Shared by the CSV (``_bulk_link_impl``) and JSON
    (``bulk_add_tickers``) entry points."""
    valid: list[str] = []
    errors: list[BulkTickerErrorRow] = []
    seen_in_batch: set[str] = set()
    for row_num, raw_val in rows:
        raw = (raw_val or "").strip()
        if not raw:
            errors.append(BulkTickerErrorRow(
                row=row_num, ticker="", reason="empty ticker",
            ))
            continue
        norm = raw.upper()
        err = validate_ticker(norm)
        if err is not None:
            errors.append(BulkTickerErrorRow(
                row=row_num, ticker=raw, reason=err,
            ))
            continue
        if norm in seen_in_batch:
            errors.append(BulkTickerErrorRow(
                row=row_num, ticker=raw,
                reason="duplicate in batch",
            ))
            continue
        seen_in_batch.add(norm)
        valid.append(norm)

    repo = _helpers._get_repo()
    added, already_linked = await repo.bulk_link_tickers(
        user_id, valid, source=source,
    )
    _invalidate_watchlist_cache(user_id)
    _logger.info(
        "bulk_link user=%s source=%s added=%d skipped=%d errors=%d",
        user_id, source,
        len(added), len(already_linked), len(errors),
    )
    return BulkTickerResponse(
        added=added,
        skipped_already_linked=already_linked,
        errors=errors,
        total_rows=total_rows,
    )
```

Then change `_bulk_link_impl` to delegate. Replace its body from
`valid: list[str] = []` (~line 389) through the final `return
BulkTickerResponse(...)` with:
```python
    rows = [
        (
            i,
            row[ticker_col]
            if (row and ticker_col < len(row))
            else "",
        )
        for i, row in enumerate(rows_raw, start=2)
    ]
    return await _bulk_link_tickers(
        user_id=user_id,
        rows=rows,
        source="bulk_csv",
        total_rows=len(rows_raw),
    )
```
(Keep all CSV parsing/header/cap logic above unchanged. Blank CSV
cells now report `reason="empty ticker"` instead of the old
`"empty row"` — acceptable; both are row-level errors.)

- [ ] **Step 4: Run test — verify it passes**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_ticker_routes.py -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
flake8 auth/endpoints/ticker_routes.py tests/backend/test_ticker_routes.py
git add auth/endpoints/ticker_routes.py tests/backend/test_ticker_routes.py
git commit -m "refactor(watchlist): extract _bulk_link_tickers core from CSV bulk-add"
```

---

## Task 2: Backend — `POST /tickers/bulk-add` JSON route + hoist

**Files:**
- Modify: `auth/endpoints/ticker_routes.py` (models ~294-317; routes ~464-483; `_hoist_bulk_routes` ~1086-1099)
- Test: `tests/backend/test_ticker_routes.py`

- [ ] **Step 1: Write failing test**

Append to `tests/backend/test_ticker_routes.py`:
```python
from unittest.mock import AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client_with_user():
    from auth.dependencies import get_current_user
    from auth.models import UserContext
    app = FastAPI()
    app.include_router(tr.router, prefix="/v1/users/me")
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        user_id="u1", email="a@b.c", role="general", tier="free",
    )
    return TestClient(app)


def test_bulk_add_tickers_json_happy(monkeypatch):
    repo = AsyncMock()
    repo.bulk_link_tickers = AsyncMock(return_value=(["TCS.NS"], []))
    monkeypatch.setattr(tr._helpers, "_get_repo", lambda: repo)
    monkeypatch.setattr(tr, "_invalidate_watchlist_cache", lambda u: None)
    c = _client_with_user()
    r = c.post("/v1/users/me/tickers/bulk-add", json={"tickers": ["TCS.NS"]})
    assert r.status_code == 200
    assert r.json()["added"] == ["TCS.NS"]


def test_bulk_add_tickers_empty_400():
    c = _client_with_user()
    r = c.post("/v1/users/me/tickers/bulk-add", json={"tickers": []})
    assert r.status_code == 400
```
(Adjust `UserContext(...)` kwargs to the real model in `auth/models.py`.)

- [ ] **Step 2: Run — verify it fails**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_ticker_routes.py -q`
Expected: FAIL — 404 on the new route.

- [ ] **Step 3: Implement model + route + hoist**

Add the request model near `BulkTickerResponse` (~307):
```python
class BulkAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tickers: list[str]
```
Add the route immediately after the existing CSV `bulk_link_tickers`
route (~483):
```python
@router.post(
    "/tickers/bulk-add",
    response_model=BulkTickerResponse,
)
async def bulk_add_tickers(
    body: BulkAddRequest,
    user: UserContext = Depends(get_current_user),
) -> BulkTickerResponse:
    """Bulk-link tickers from a JSON list (e.g. an Advanced
    Analytics filtered set). Server-side dedupe; per-row report."""
    if not body.tickers:
        raise HTTPException(status_code=400, detail="tickers list is empty")
    if len(body.tickers) > _BULK_ROW_CAP:
        raise HTTPException(
            status_code=413,
            detail=f"exceeds {_BULK_ROW_CAP}-row limit",
        )
    rows = list(enumerate(body.tickers, start=1))
    return await _bulk_link_tickers(
        user_id=user.user_id,
        rows=rows,
        source="bulk_json",
        total_rows=len(body.tickers),
    )
```
Update the hoist tuple (~1089):
```python
    bulk_suffixes = ("/tickers/bulk", "/tickers/bulk-add", "/tickers/all")
```

- [ ] **Step 4: Run — verify it passes**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_ticker_routes.py -q`
Expected: PASS (happy + empty-400 + Task 1 test).

- [ ] **Step 5: Lint + commit**

```bash
flake8 auth/endpoints/ticker_routes.py tests/backend/test_ticker_routes.py
git add auth/endpoints/ticker_routes.py tests/backend/test_ticker_routes.py
git commit -m "feat(watchlist): POST /tickers/bulk-add JSON bulk-add (deduped)"
```

---

## Task 3: Backend — `GET /advanced-analytics/{report}/tickers`

**Files:**
- Modify: `backend/advanced_analytics_routes.py` (`_stream_export`; `_make_export_endpoint` ~1661; router loop ~1718-1726)
- Test: `tests/backend/test_advanced_analytics_routes.py`

- [ ] **Step 1: Write failing test**

Add to `tests/backend/test_advanced_analytics_routes.py` (create if
absent):
```python
import pytest
from unittest.mock import AsyncMock
import backend.advanced_analytics_routes as aa


@pytest.mark.asyncio
async def test_export_tickers_returns_filtered_list(monkeypatch):
    from auth.models import UserContext
    rows = [aa.AdvancedRow(ticker=t) for t in ["TCS.NS", "INFY.NS"]]
    monkeypatch.setattr(aa, "_cached_full_rows", AsyncMock(return_value=rows))
    monkeypatch.setattr(aa, "_filter_tickers", lambda ts, m, tt: ts)
    monkeypatch.setattr(aa, "_passes_filter", lambda r, rep: True)
    user = UserContext(user_id="u1", email="a@b.c", role="pro", tier="pro")
    resp = await aa._export_tickers(
        user, "current-day-upmove", None, "desc",
        "all", "all", "", "", "",
    )
    assert set(resp.tickers) == {"TCS.NS", "INFY.NS"}
    assert resp.total == 2
    assert resp.capped is False
```
(Align `AdvancedRow(...)` required fields with the real model; set
only `ticker` if the rest default.)

- [ ] **Step 2: Run — verify it fails**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_advanced_analytics_routes.py -q`
Expected: FAIL — `_export_tickers` / `ReportTickersResponse` undefined.

- [ ] **Step 3: Implement response model + `_export_tickers` + routes**

Add near the other Pydantic models:
```python
class ReportTickersResponse(BaseModel):
    tickers: list[str]
    total: int
    capped: bool
```
Add `_export_tickers` next to `_stream_export` (reuse the SAME
filter pipeline, no pagination, no CSV):
```python
async def _export_tickers(
    user: UserContext,
    report: ReportName,
    sort_key: str | None,
    sort_dir: str,
    market: MarketFilter,
    ticker_type: TickerTypeFilter,
    search: str,
    tech: str,
    fund: str,
) -> ReportTickersResponse:
    """Full filtered ticker list for the report (JSON), capped at
    FILTER_EXPORT_ROW_CAP. Mirrors _stream_export's filter pipeline
    minus sort/CSV — order is not significant for a watchlist add."""
    needle = search.strip().upper()
    tech_keys = parse_filter_csv(tech, TECH_KEYS, "tech")
    fund_keys = parse_filter_csv(fund, FUND_KEYS, "fund")
    as_of = _effective_trading_date()
    full_rows = await _cached_full_rows(user, as_of)
    keep = set(_filter_tickers(
        [r.ticker for r in full_rows], market, ticker_type,
    ))
    rows = [r for r in full_rows if r.ticker in keep]
    if needle:
        rows = [r for r in rows if needle in r.ticker.upper()]
    if tech_keys or fund_keys:
        rows = [
            r for r in rows
            if passes_bundle_filters(r, tech_keys, fund_keys)
        ]
    rows = [r for r in rows if _passes_filter(r, report)]
    total = len(rows)
    tickers = [r.ticker for r in rows][:FILTER_EXPORT_ROW_CAP]
    return ReportTickersResponse(
        tickers=tickers,
        total=total,
        capped=total > FILTER_EXPORT_ROW_CAP,
    )
```
Add the route factory + registration after the export loop (~1726):
```python
    def _make_tickers_endpoint(report: ReportName):
        async def _handler(
            user: UserContext = Depends(pro_or_superuser),
            sort_key: str | None = Query(None),
            sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
            market: str = Query("all", pattern="^(all|india|us)$"),
            ticker_type: str = Query("all", pattern="^(all|stock|etf)$"),
            search: str = Query("", max_length=20),
            tech: str = Query("", max_length=200, pattern="^[a-z0-9_,]*$"),
            fund: str = Query("", max_length=200, pattern="^[a-z0-9_,]*$"),
        ) -> ReportTickersResponse:
            try:
                return await _export_tickers(
                    user, report, sort_key, sort_dir,
                    market, ticker_type, search, tech, fund,  # type: ignore[arg-type]
                )
            except HTTPException:
                raise
            except Exception as exc:
                _logger.exception(
                    "advanced_analytics %s tickers failed: %s", report, exc,
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"advanced_analytics {report} tickers failed",
                )
        _handler.__name__ = f"tickers_{report.replace('-', '_')}"
        return _handler

    for report in REPORTS:
        router.add_api_route(
            path=f"/{report}/tickers",
            endpoint=_make_tickers_endpoint(report),
            methods=["GET"],
            response_model=ReportTickersResponse,
            name=f"advanced_analytics_{report.replace('-', '_')}_tickers",
        )
```

- [ ] **Step 4: Run — verify it passes**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_advanced_analytics_routes.py -q`
Expected: PASS.

- [ ] **Step 5: Restart backend (new routes + response_model need restart — §6.2), lint, commit**

```bash
./run.sh restart backend && sleep 8
flake8 backend/advanced_analytics_routes.py tests/backend/test_advanced_analytics_routes.py
git add backend/advanced_analytics_routes.py tests/backend/test_advanced_analytics_routes.py
git commit -m "feat(advanced-analytics): GET /{report}/tickers filtered list (capped)"
```

---

## Task 4: Frontend — `useAddToWatchlist` hook

**Files:**
- Create: `frontend/hooks/useAddToWatchlist.ts`
- Create: `frontend/hooks/__tests__/useAddToWatchlist.test.ts`

- [ ] **Step 1: Write failing test**

`frontend/hooks/__tests__/useAddToWatchlist.test.ts`:
```ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

vi.mock("@/lib/apiFetch", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/apiFetch";
import { useAddToWatchlist } from "../useAddToWatchlist";

beforeEach(() => vi.clearAllMocks());

describe("useAddToWatchlist", () => {
  it("posts tickers and returns the bulk response", async () => {
    (apiFetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: async () => ({
        added: ["TCS.NS"], skipped_already_linked: [],
        errors: [], total_rows: 1,
      }),
    });
    const { result } = renderHook(() => useAddToWatchlist());
    let resp: { added: string[] } | undefined;
    await act(async () => {
      resp = await result.current.submit(["TCS.NS"]);
    });
    expect(apiFetch).toHaveBeenCalledWith(
      expect.stringContaining("/users/me/tickers/bulk-add"),
      expect.objectContaining({ method: "POST" }),
    );
    expect(resp?.added).toEqual(["TCS.NS"]);
  });

  it("sets error on non-ok response", async () => {
    (apiFetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false, status: 400, text: async () => "tickers list is empty",
    });
    const { result } = renderHook(() => useAddToWatchlist());
    await act(async () => {
      await result.current.submit([]).catch(() => {});
    });
    expect(result.current.error).toContain("400");
  });
});
```

- [ ] **Step 2: Run — verify it fails**

Run: `cd frontend && npx vitest run hooks/__tests__/useAddToWatchlist.test.ts`
Expected: FAIL — cannot resolve `../useAddToWatchlist`.

- [ ] **Step 3: Implement the hook**

`frontend/hooks/useAddToWatchlist.ts`:
```ts
"use client";

import { useCallback, useState } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type { BulkTickerResponse } from "@/lib/types/bulkTickers";

export function useAddToWatchlist() {
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<BulkTickerResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reset = useCallback(() => {
    setResult(null);
    setError(null);
  }, []);

  const submit = useCallback(
    async (tickers: string[]): Promise<BulkTickerResponse> => {
      setSubmitting(true);
      setError(null);
      try {
        const r = await apiFetch(
          `${API_URL}/users/me/tickers/bulk-add`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tickers }),
          },
        );
        if (!r.ok) {
          const body = await r.text();
          const msg = `Add failed: ${r.status} ${body}`;
          setError(msg);
          throw new Error(msg);
        }
        const data = (await r.json()) as BulkTickerResponse;
        setResult(data);
        return data;
      } finally {
        setSubmitting(false);
      }
    },
    [],
  );

  return { submit, submitting, result, error, reset };
}
```

- [ ] **Step 4: Run — verify it passes**

Run: `cd frontend && npx vitest run hooks/__tests__/useAddToWatchlist.test.ts`
Expected: PASS (both cases).

- [ ] **Step 5: Lint + commit**

```bash
cd frontend && npx eslint hooks/useAddToWatchlist.ts hooks/__tests__/useAddToWatchlist.test.ts && cd ..
git add frontend/hooks/useAddToWatchlist.ts frontend/hooks/__tests__/useAddToWatchlist.test.ts
git commit -m "feat(fe): useAddToWatchlist hook (POST /tickers/bulk-add)"
```

---

## Task 5: Frontend — button component + wire into the shared table

**Files:**
- Create: `frontend/components/advanced-analytics/AddFilteredToWatchlistButton.tsx`
- Modify: `frontend/lib/types/advancedAnalytics.ts` (add `ReportTickersResponse`)
- Modify: `frontend/components/advanced-analytics/AdvancedAnalyticsTable.tsx` (handler ~230-259; controls row)

- [ ] **Step 1: Add the response type**

In `frontend/lib/types/advancedAnalytics.ts` add:
```ts
export interface ReportTickersResponse {
  tickers: string[];
  total: number;
  capped: boolean;
}
```

- [ ] **Step 2: Implement the button component**

`frontend/components/advanced-analytics/AddFilteredToWatchlistButton.tsx`:
```tsx
"use client";

import { useState } from "react";
import { useAddToWatchlist } from "@/hooks/useAddToWatchlist";

interface Props {
  disabled: boolean;
  tooltip?: string;
  /** Fetch the full filtered ticker list (capped) for the current
   *  report + filters. Provided by AdvancedAnalyticsTable. */
  fetchTickers: () => Promise<string[]>;
  /** Called after a successful add so the parent can revalidate. */
  onAdded?: () => void;
}

export function AddFilteredToWatchlistButton(
  { disabled, tooltip, fetchTickers, onAdded }: Props,
) {
  const { submit, submitting } = useAddToWatchlist();
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleClick() {
    setBusy(true);
    setMsg(null);
    try {
      const tickers = await fetchTickers();
      if (tickers.length === 0) {
        setMsg("No tickers match the current filter");
        return;
      }
      const res = await submit(tickers);
      const errs = res.errors.length
        ? ` · ${res.errors.length} error(s)`
        : "";
      setMsg(
        `Added ${res.added.length} · `
        + `${res.skipped_already_linked.length} already in watchlist`
        + errs,
      );
      onAdded?.();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Add failed");
    } finally {
      setBusy(false);
      window.setTimeout(() => setMsg(null), 6000);
    }
  }

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={handleClick}
        disabled={disabled || busy || submitting}
        title={tooltip}
        data-testid="aa-add-to-watchlist"
        className="inline-flex items-center gap-1 rounded-md border border-indigo-200 dark:border-indigo-800 bg-indigo-50 dark:bg-indigo-950/40 px-2.5 py-1 text-xs font-medium text-indigo-700 dark:text-indigo-300 disabled:opacity-50"
      >
        {busy || submitting ? "Adding…" : "Add to Watchlist"}
      </button>
      {msg && (
        <span
          data-testid="aa-add-to-watchlist-result"
          className="text-[11px] text-slate-500 dark:text-slate-400"
        >
          {msg}
        </span>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Wire into `AdvancedAnalyticsTable`**

Add imports near the top:
```tsx
import { AddFilteredToWatchlistButton } from "./AddFilteredToWatchlistButton";
import { apiFetch } from "@/lib/apiFetch";
import type { ReportTickersResponse } from "@/lib/types/advancedAnalytics";
```
Add a `fetchFilteredTickers` callback next to `handleCsv` (reuse the
SAME filter params `handleCsv` builds):
```tsx
  const fetchFilteredTickers = useCallback(async (): Promise<string[]> => {
    const params = new URLSearchParams({
      sort_dir: sortDir, market, ticker_type: tickerType,
    });
    if (sortKey) params.set("sort_key", sortKey);
    if (search) params.set("search", search);
    if (tech.length > 0) params.set("tech", [...tech].sort().join(","));
    if (fund.length > 0) params.set("fund", [...fund].sort().join(","));
    const url =
      `${API_URL}/advanced-analytics/${report}/tickers?${params.toString()}`;
    const r = await apiFetch(url);
    if (!r.ok) throw new Error(`Failed to load tickers: ${r.status}`);
    const data = (await r.json()) as ReportTickersResponse;
    return data.tickers;
  }, [sortDir, sortKey, market, tickerType, search, tech, fund, report]);
```
Render the button beside the existing `<DownloadCsvButton .../>`
(locate it in the controls row and add immediately after):
```tsx
        <AddFilteredToWatchlistButton
          disabled={csvDisabled}
          tooltip={csvTooltip}
          fetchTickers={fetchFilteredTickers}
        />
```
(`csvDisabled` / `csvTooltip` already exist at ~221-228. `API_URL`
is already imported for `handleCsv`. If `useCallback` isn't yet
imported from "react", add it.)

- [ ] **Step 4: Lint + typecheck changed files**

```bash
cd frontend
npx eslint components/advanced-analytics/AddFilteredToWatchlistButton.tsx components/advanced-analytics/AdvancedAnalyticsTable.tsx lib/types/advancedAnalytics.ts
npx tsc --noEmit 2>&1 | grep -E "AddFilteredToWatchlist|AdvancedAnalyticsTable|advancedAnalytics" || echo "clean for changed files"
cd ..
```
Expected: eslint clean; no tsc errors in the changed files.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/advanced-analytics/AddFilteredToWatchlistButton.tsx frontend/components/advanced-analytics/AdvancedAnalyticsTable.tsx frontend/lib/types/advancedAnalytics.ts
git commit -m "feat(fe): Add to Watchlist button on Advanced Analytics tabs"
```

---

## Task 6: E2E (Playwright, POM) + selector registry

**Files:**
- Modify: `e2e/utils/selectors.ts` (add to `FE`)
- Create: `e2e/pages/frontend/AdvancedAnalyticsPage.ts`
- Create: `e2e/tests/advanced-analytics-add-to-watchlist.spec.ts`

- [ ] **Step 1: Register testids**

In `e2e/utils/selectors.ts`, add to the `FE` object:
```ts
  aaAddToWatchlist: "aa-add-to-watchlist",
  aaAddToWatchlistResult: "aa-add-to-watchlist-result",
```

- [ ] **Step 2: Page Object**

`e2e/pages/frontend/AdvancedAnalyticsPage.ts`:
```ts
import { BasePage } from "./BasePage";
import { FE } from "../../utils/selectors";

export class AdvancedAnalyticsPage extends BasePage {
  async goto() {
    await this.page.goto("/advanced-analytics");
  }
  addToWatchlistBtn() {
    return this.tid(FE.aaAddToWatchlist);
  }
  result() {
    return this.tid(FE.aaAddToWatchlistResult);
  }
}
```
(Confirm `BasePage` import path + `tid` helper match the repo POM base.)

- [ ] **Step 3: Spec (superuser fixture — advanced-analytics is pro/superuser-gated)**

`e2e/tests/advanced-analytics-add-to-watchlist.spec.ts`:
```ts
import { test, expect } from "@playwright/test";
import { AdvancedAnalyticsPage } from "../pages/frontend/AdvancedAnalyticsPage";

test.use({ storageState: "e2e/.auth/superuser.json" });

test("add filtered tickers to watchlist", async ({ page }) => {
  const aa = new AdvancedAnalyticsPage(page);
  await aa.goto();
  const btn = aa.addToWatchlistBtn();
  await btn.waitFor({ state: "attached" });
  await btn.scrollIntoViewIfNeeded();
  await expect(btn).toBeVisible();
  await btn.click();
  await expect(aa.result()).toContainText(/Added \d+|No tickers/);
});
```

- [ ] **Step 4: Run the E2E**

Run: `cd e2e && npx playwright test advanced-analytics-add-to-watchlist --project=frontend-chromium`
Expected: PASS (1 worker). If the default tab has 0 rows today, the
assertion also accepts the "No tickers" state.

- [ ] **Step 5: Commit**

```bash
git add e2e/utils/selectors.ts e2e/pages/frontend/AdvancedAnalyticsPage.ts e2e/tests/advanced-analytics-add-to-watchlist.spec.ts
git commit -m "test(e2e): Add to Watchlist on Advanced Analytics"
```

---

## Task 7: Full verification + PROGRESS + PR

- [ ] **Step 1: Suites**

```bash
docker compose exec -T backend python -m pytest tests/backend/test_ticker_routes.py tests/backend/test_advanced_analytics_routes.py -q
cd frontend && npx vitest run hooks/__tests__/useAddToWatchlist.test.ts && cd ..
```
Expected: all PASS.

- [ ] **Step 2: Lint sweep**

```bash
black auth/ backend/ && isort auth/ backend/ --profile black && flake8 auth/ backend/
cd frontend && npx eslint . && cd ..
```

- [ ] **Step 3: Update `PROGRESS.md`** — dated entry: the paper-pool diagnosis (RSI(2) Connors v3, watchlist∪holdings vs discovery) + the Add-to-Watchlist feature.

- [ ] **Step 4: Commit + push + PR to `dev`**

```bash
git add PROGRESS.md && git commit -m "docs(progress): Advanced Analytics Add to Watchlist"
git push -u origin feature/aa-add-to-watchlist
gh pr create --base dev --title "feat: Add to Watchlist from Advanced Analytics filters" --body "Grows the paper live-WS pool (watchlist ∪ holdings) so selective strategies like RSI(2) Connors v3 have candidates to trigger on. Spec: docs/superpowers/specs/2026-06-11-advanced-analytics-add-to-watchlist-design.md"
```

---

## Manual verification (closes the loop on the original goal)

After merge + deploy: Advanced Analytics → a tab (e.g.
current-day-upmove) → set filters → **Add to Watchlist** → confirm
the inline "Added N" message and that the dashboard watchlist grew →
(re)start the RSI(2) Connors v3 paper run → with a larger watched
pool, RSI(2)≤5 entries become possible.

---

## Self-review notes

- **Spec coverage:** §4.1 → Tasks 1-2; §4.2 → Task 3; §5 → Tasks 4-5;
  §8 → Tasks 1-6; §6 data flow → Tasks 3+5. All covered.
- **Type consistency:** `BulkTickerResponse` (added /
  skipped_already_linked / errors / total_rows) used identically in
  backend + `@/lib/types/bulkTickers`; `ReportTickersResponse`
  (tickers/total/capped) identical backend + frontend; hook
  `submit(tickers) → BulkTickerResponse` matches button usage;
  `_bulk_link_tickers(rows, source, total_rows)` signature identical
  across Tasks 1-2.
- **Placeholders:** code is concrete. Items to confirm against the
  repo at implementation time (do not change the design):
  `UserContext(...)` kwargs, `AdvancedRow` required fields,
  `BasePage` import path/`tid`, the exact `<DownloadCsvButton>`
  insertion point, and that `useCallback` is imported in the table.
- **Restart:** backend route/response_model additions (Tasks 2-3)
  require `./run.sh restart backend` before manual/API testing
  (§6.2).
