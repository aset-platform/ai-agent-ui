# Paper Replay Fixture Builder — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A one-click builder that generates a replay JSONL fixture from the user's holdings ∪ watchlist, targeting dates where RSI(2) Connors v3's entry condition actually fires, so a paper replay run produces fills on the user's universe.

**Architecture:** A backend builder scans the SAME feature-assembly + AST evaluator the paper runtime uses (`assemble_per_bar_features` + `Evaluator.eval_node`) over a recent daily window to find trigger dates, emits synthetic `Tick`s on those dates, and writes `<AI_AGENT_UI_HOME>/fixtures/<user_id>.jsonl`. The replay loader is extended to allow that user dir. A WatchlistWidget overflow-menu item calls it.

**Tech Stack:** FastAPI, SQLAlchemy async, Iceberg/DuckDB, Next.js/React, SWR, pytest, vitest, Playwright.

Spec: `docs/superpowers/specs/2026-06-12-paper-replay-fixture-builder-design.md`
**Branch:** `feature/aa-add-to-watchlist` (PR #259; already checked out).

---

## File structure

- `backend/algo/paper/fixture_builder.py` (new) — universe resolve, trigger scan (reusing runtime eval), tick synthesis, JSONL write. One responsibility: build a fixture from a user's universe.
- `backend/algo/paper/supervisor.py` (modify) — add the user-fixtures root to `build_replay_source` + `list_replay_fixtures`.
- `backend/algo/routes/paper.py` (modify) — `POST /fixtures/build` + `BuildFixtureResponse` model.
- `tests/backend/test_paper_fixture_builder.py` (new) — builder + loader-allowlist + endpoint tests.
- `frontend/hooks/useBuildReplayFixture.ts` (new) + test.
- `frontend/components/widgets/WatchlistOverflowMenu.tsx` (modify) — menu item + result.
- `e2e/utils/selectors.ts`, watchlist POM, `e2e/tests/frontend/watchlist-build-fixture.spec.ts`.

Key existing pieces to reuse (READ before coding):
- `backend/algo/features/loader.py::load_intraday_features_window(tickers, interval_sec, period_start, period_end, *, feature_set_version=..., enable_on_demand_backfill=True) -> dict[str, dict[int, dict[str, Decimal|str]]]` (panel keyed `{ticker: {bar_open_ts_ns: feats}}`).
- `backend/algo/features/per_bar.py::assemble_per_bar_features(*, bar_feats, market_regime=None, market_trend=None, factor_row=None, regime_row=None, daily_overlay=None, ...) -> PerBarFeatures`; `lookup_daily_overlay(...)`.
- `backend/algo/backtest/evaluator.py::Evaluator().eval_node(node, ctx)` + `EvalContext`.
- `backend/algo/paper/runtime.py::_on_bar_close` (lines ~540-575) — canonical assembly: shows which inputs feed `assemble_per_bar_features` and how the runtime loads them (`_ensure_factor_cache`, `_ensure_regime_cache`, `_ensure_daily_overlay_cache`, `self._market_regime`, `self._market_trend`).
- `backend/algo/regime/repo.py::get_regime_history(start, end) -> list[RegimeRow]` (RegimeRow: `bar_date`, `regime_label`, `stress_prob`, `rule_inputs_json`).
- `backend/algo/stream/types.py::Tick` = `{ticker:str, ts_ns:int>=0, exchange_ts_ns:int|None, ltp:float>0, volume:int>=0}` (extra="forbid").
- `backend/algo/jobs/daily_features_daily_compute._utc_midnight_ns(bar_date)` — date→ts_ns convention used by the overlay.
- `backend/paths.py::AI_AGENT_UI_HOME`.
- Universe: `_get_stock_repo().get_portfolio_holdings(user_id)` (df: `ticker`,`quantity`) + the watchlist repo `get_user_tickers(user_id)` (see `auth/endpoints/ticker_routes.py` / `stocks/repository.py`).
- v3 entry AST = `strategy.ast_json["root"]["cond"]`: `and` of `rsi_2<=5`, `distance_from_sma200>0`, `stress_prob<0.5`, `nifty_above_sma200>=1`, `nifty_30d_return_pct>-5`.

---

## Task 1: Builder core — `_entry_fires` (drift-free via runtime evaluator)

**Files:** Create `backend/algo/paper/fixture_builder.py`; Test `tests/backend/test_paper_fixture_builder.py`

INTEGRATION task — the drift-free guarantee comes from reusing the runtime's `Evaluator`. Make the trigger decision a pure, testable function.

- [ ] **Step 1: Write the failing test**

```python
from decimal import Decimal
from backend.algo.paper.fixture_builder import _entry_fires

_COND = {"type": "and", "operands": [
    {"type": "compare", "op": "<=", "left": {"feature": "rsi_2"},
     "right": {"literal": 5}},
    {"type": "compare", "op": ">", "left": {"feature": "distance_from_sma200"},
     "right": {"literal": 0}},
    {"type": "compare", "op": "<", "left": {"feature": "stress_prob"},
     "right": {"literal": 0.5}},
    {"type": "compare", "op": ">=", "left": {"feature": "nifty_above_sma200"},
     "right": {"literal": 1}},
    {"type": "compare", "op": ">", "left": {"feature": "nifty_30d_return_pct"},
     "right": {"literal": -5}},
]}


def _f(rsi=3, dist=5, stress=0.2, nabove=1, n30=2.0):
    return {"rsi_2": Decimal(str(rsi)),
            "distance_from_sma200": Decimal(str(dist)),
            "stress_prob": Decimal(str(stress)),
            "nifty_above_sma200": Decimal(str(nabove)),
            "nifty_30d_return_pct": Decimal(str(n30))}


def test_entry_fires_all_gates_pass():
    assert _entry_fires(_COND, _f()) is True


def test_entry_fires_false_rsi_high():
    assert _entry_fires(_COND, _f(rsi=40)) is False


def test_entry_fires_false_missing_feature():
    assert _entry_fires(_COND, {}) is False  # KeyError swallowed
```

- [ ] **Step 2: Run — verify FAIL**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_paper_fixture_builder.py -q`
Expected: FAIL (module/`_entry_fires` missing).

- [ ] **Step 3: Implement**

Create `backend/algo/paper/fixture_builder.py`:
```python
"""Build a replay JSONL fixture from a user's holdings + watchlist,
targeting dates where the strategy entry AST fires — so a paper
*replay* run produces fills on the user's own universe.

Drift-free: the trigger scan evaluates the SAME strategy AST
(``Evaluator.eval_node``) against features assembled by the SAME
``assemble_per_bar_features`` the paper runtime uses at
``_on_bar_close``. A date this scan accepts reproduces in replay.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from backend.algo.backtest.evaluator import EvalContext, Evaluator

_logger = logging.getLogger(__name__)
_EVALUATOR = Evaluator()


def _entry_fires(cond: dict, features: dict) -> bool:
    """True iff the entry AST ``cond`` is truthy against ``features``.
    A missing feature or any eval error means 'does not fire'."""
    try:
        ctx = EvalContext(features=features)
        return bool(_EVALUATOR.eval_node(cond, ctx))
    except (KeyError, ValueError, TypeError):
        return False


@dataclass
class FixtureBuildResult:
    filename: str
    n_tickers: int
    n_trigger_dates: int
    n_ticks: int
    trigger_tickers: list[str]
```
(Confirm `EvalContext` is constructed `EvalContext(features=...)` — read `evaluator.py`; match the real field name if different.)

- [ ] **Step 4: Run — verify the 3 tests PASS**

Run: `docker compose exec -T backend python -m pytest tests/backend/test_paper_fixture_builder.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
flake8 backend/algo/paper/fixture_builder.py tests/backend/test_paper_fixture_builder.py
git add backend/algo/paper/fixture_builder.py tests/backend/test_paper_fixture_builder.py
git commit -m "feat(paper): fixture builder skeleton + drift-free _entry_fires"
```

---

## Task 2: Builder — per-(ticker,date) feature assembly + scan

**Files:** Modify `backend/algo/paper/fixture_builder.py`; Test same file.

- [ ] **Step 1: Write the failing test**

```python
from datetime import date
import backend.algo.paper.fixture_builder as fb


def test_scan_picks_only_firing_dates(monkeypatch):
    d1, d2 = date(2026, 6, 2), date(2026, 6, 3)
    feats = {
        d1: _f(rsi=3),    # fires
        d2: _f(rsi=40),   # no
    }
    monkeypatch.setattr(
        fb, "_assembled_features_by_date",
        lambda ticker, start, end: feats,
    )
    out = fb._scan_trigger_dates(
        "TCS.NS", _COND, date(2026, 6, 1), date(2026, 6, 4),
        max_dates=2,
    )
    assert out == [d1]
```
(`_COND`/`_f` from Task 1's test module — keep them at module scope so this test reuses them.)

- [ ] **Step 2: Run — verify FAIL** (`_scan_trigger_dates`/`_assembled_features_by_date` missing).

- [ ] **Step 3: Implement scan + assembly**

Add to `fixture_builder.py`:
```python
def _assembled_features_by_date(
    ticker: str, start: date, end: date,
) -> dict[date, dict]:
    """{bar_date: feature-dict} for *ticker* across [start, end],
    mirroring PaperRuntime._on_bar_close's assembly (read
    runtime.py:540-575): daily overlay (interval_sec=86400) +
    regime_row + market_regime/trend + factor_row ->
    assemble_per_bar_features -> its feature mapping."""
    from backend.algo.features.loader import (
        load_intraday_features_window,
    )
    from backend.algo.features.per_bar import (
        assemble_per_bar_features,
    )
    from backend.algo.jobs.daily_features_daily_compute import (
        _utc_midnight_ns,
    )
    from backend.algo.regime.repo import get_regime_history

    panel = load_intraday_features_window(
        [ticker], 86400, start, end,
    ).get(ticker, {})
    regime_by_date = {
        r.bar_date: r for r in get_regime_history(start, end)
    }
    out: dict[date, dict] = {}
    cur = start
    while cur <= end:
        bar_feats = panel.get(_utc_midnight_ns(cur))
        if bar_feats is not None:
            rr = regime_by_date.get(cur)
            pbf = assemble_per_bar_features(
                bar_feats=bar_feats,
                regime_row=_regime_row_to_dict(rr) if rr else None,
                daily_overlay=bar_feats,
            )
            out[cur] = _per_bar_features_to_dict(pbf)
        cur += timedelta(days=1)
    return out


def _scan_trigger_dates(
    ticker: str, entry_cond: dict, start: date, end: date,
    *, max_dates: int,
) -> list[date]:
    by_date = _assembled_features_by_date(ticker, start, end)
    hits = [
        dt for dt, feats in sorted(by_date.items())
        if _entry_fires(entry_cond, feats)
    ]
    return hits[-max_dates:]
```
Implement `_regime_row_to_dict(rr)` exposing `stress_prob` (RegimeRow field) + `nifty_above_sma200`, `nifty_30d_return_pct` (parsed from `rr.rule_inputs_json`; CONFIRM these key names against `regime/classifier_job.py::_compute_inputs`). Implement `_per_bar_features_to_dict(pbf)` using `PerBarFeatures`' real accessor (read `per_bar.py`). **CRITICAL: verify against runtime.py:540-575 which inputs carry nifty_*/stress_prob** — if the runtime injects them via `market_regime`/`market_trend`/`factor_row` rather than `regime_row`, replicate that exact wiring so the assembled dict contains all five gate features (else the scan would silently never fire). Load those extra inputs the same way the runtime's `_ensure_*` helpers do.

- [ ] **Step 4: Run — verify PASS.**

- [ ] **Step 5: Commit**

```bash
flake8 backend/algo/paper/fixture_builder.py tests/backend/test_paper_fixture_builder.py
git add -A && git commit -m "feat(paper): per-(ticker,date) trigger scan via runtime feature assembly"
```

---

## Task 3: Builder — tick synthesis + JSONL write + entrypoint

**Files:** Modify `backend/algo/paper/fixture_builder.py`; Test same file.

- [ ] **Step 1: Write the failing test**

```python
import json
from datetime import date
from backend.algo.stream.types import Tick


def test_synth_ticks_close_a_bar():
    ticks = fb._synth_ticks("TCS.NS", date(2026, 6, 2),
                            close=3500.0, volume=120)
    assert len(ticks) >= 2
    for t in ticks:
        Tick.model_validate(t)
    span = ticks[-1]["ts_ns"] - ticks[0]["ts_ns"]
    assert span >= 60 * 1_000_000_000
    assert all(t["ticker"] == "TCS.NS" and t["ltp"] == 3500.0
               for t in ticks)


def test_build_universe_fixture_writes_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "_user_fixtures_dir", lambda: tmp_path)
    monkeypatch.setattr(fb, "_resolve_universe", lambda uid: ["TCS.NS"])
    monkeypatch.setattr(fb, "_entry_cond_for_v3",
                        lambda: {"type": "compare", "op": "<=",
                        "left": {"feature": "rsi_2"},
                        "right": {"literal": 5}})
    monkeypatch.setattr(fb, "_scan_trigger_dates",
                        lambda t, c, s, e, *, max_dates: [date(2026, 6, 2)])
    monkeypatch.setattr(fb, "_close_for", lambda t, dt: (3500.0, 100))
    res = fb.build_universe_fixture("u1", lookback_days=30)
    assert res.n_tickers == 1 and res.n_trigger_dates == 1
    assert res.n_ticks >= 2
    out = tmp_path / "u1.jsonl"
    lines = [ln for ln in out.read_text().splitlines()
             if ln.strip() and not ln.startswith("#")]
    Tick.model_validate(json.loads(lines[0]))
```

- [ ] **Step 2: Run — verify FAIL.**

- [ ] **Step 3: Implement synth + write + entrypoint**

```python
from pathlib import Path
from fastapi import HTTPException
from backend.paths import AI_AGENT_UI_HOME

_NS = 1_000_000_000
_SESSION_OPEN_SECS = 3 * 3600 + 45 * 60   # 09:15 IST == 03:45 UTC


def _user_fixtures_dir() -> Path:
    d = Path(AI_AGENT_UI_HOME) / "fixtures"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _synth_ticks(ticker, dt, close, volume):
    from backend.algo.jobs.daily_features_daily_compute import (
        _utc_midnight_ns,
    )
    base = _utc_midnight_ns(dt) + _SESSION_OPEN_SECS * _NS
    offsets = (0, 30, 90)   # last-first = 90s > 60s -> 1-min bar closes
    vol = max(1, int(volume) // len(offsets))
    return [
        {"ticker": ticker, "ts_ns": base + o * _NS,
         "ltp": float(close), "volume": vol}
        for o in offsets
    ]


def build_universe_fixture(
    user_id: str, *, lookback_days: int = 60,
    max_dates_per_ticker: int = 2,
) -> FixtureBuildResult:
    end = date.today()
    start = end - timedelta(days=lookback_days)
    tickers = _resolve_universe(user_id)
    if not tickers:
        raise HTTPException(
            status_code=400,
            detail="Add tickers to your watchlist or holdings first.",
        )
    cond = _entry_cond_for_v3()
    all_ticks: list[dict] = []
    trigger_tickers: list[str] = []
    n_dates = 0
    for tk in tickers:
        dates = _scan_trigger_dates(
            tk, cond, start, end, max_dates=max_dates_per_ticker,
        )
        if not dates:
            continue
        trigger_tickers.append(tk)
        for dt in dates:
            close, volume = _close_for(tk, dt)
            if close is None or close <= 0:
                continue
            n_dates += 1
            all_ticks.extend(_synth_ticks(tk, dt, close, volume))
    all_ticks.sort(key=lambda t: t["ts_ns"])
    out = _user_fixtures_dir() / f"{user_id}.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        fh.write(
            f"# fixture_builder user={user_id} "
            f"tickers={len(trigger_tickers)} dates={n_dates}\n"
        )
        for t in all_ticks:
            fh.write(json.dumps(t) + "\n")
    return FixtureBuildResult(
        filename=out.name, n_tickers=len(tickers),
        n_trigger_dates=n_dates, n_ticks=len(all_ticks),
        trigger_tickers=trigger_tickers,
    )
```
Implement:
- `_resolve_universe(user_id)`: holdings (qty>0, `_get_stock_repo().get_portfolio_holdings`) ∪ watchlist (`get_user_tickers`), `.NS`/`.BO` only, deduped, sorted. Use the repos' SYNC access (this builder runs via `asyncio.to_thread`; use the same sync repo helpers other `stocks/repository.py` callers use — do NOT call async repo methods directly here).
- `_entry_cond_for_v3()`: load the v3 strategy's `ast_json["root"]["cond"]` (query `algo.strategies` for the canonical v3 — by the known id `0b267c76-2ae9-4057-ae33-aafe3f7a96f5` or by name match; read how other code loads a strategy AST).
- `_close_for(ticker, dt)`: `(close, volume)` from `stocks.ohlcv` for that date (via `query_iceberg_df`); return `(None, 0)` if absent.
Confirm the IST→UTC session offset yields a bar whose `bar_date == dt` under the runtime's bucketing.

- [ ] **Step 4: Run — verify PASS.**

- [ ] **Step 5: Commit**

```bash
flake8 backend/algo/paper/fixture_builder.py tests/backend/test_paper_fixture_builder.py
git add -A && git commit -m "feat(paper): synth ticks + JSONL write + build_universe_fixture"
```

---

## Task 4: Loader allowlist — user fixtures dir

**Files:** Modify `backend/algo/paper/supervisor.py` (`_FIXTURES_ROOT`, `build_replay_source` ~259-270, `list_replay_fixtures` ~273-315); Test `tests/backend/test_paper_fixture_builder.py`.

- [ ] **Step 1: Write the failing test**

```python
import pytest
import backend.algo.paper.supervisor as sup


def test_build_replay_source_allows_user_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sup, "_USER_FIXTURES_ROOT", tmp_path.resolve())
    (tmp_path / "u1.jsonl").write_text(
        '{"ticker":"X.NS","ts_ns":0,"ltp":1.0,"volume":1}\n')
    assert sup.build_replay_source("u1.jsonl") is not None


def test_build_replay_source_rejects_traversal():
    with pytest.raises((ValueError, FileNotFoundError)):
        sup.build_replay_source("../../../../etc/passwd")
```

- [ ] **Step 2: Run — verify FAIL.**

- [ ] **Step 3: Implement**

Add near `_FIXTURES_ROOT`:
```python
from backend.paths import AI_AGENT_UI_HOME
_USER_FIXTURES_ROOT = (Path(AI_AGENT_UI_HOME) / "fixtures").resolve()
```
Rewrite `build_replay_source` to accept under EITHER root:
```python
def build_replay_source(fixture_path: str) -> ReplayTickSource:
    for root in (_FIXTURES_ROOT, _USER_FIXTURES_ROOT):
        candidate = (root / fixture_path).resolve()
        if not str(candidate).startswith(str(root)):
            continue
        if candidate.exists():
            return ReplayTickSource(candidate, pace="fast")
    raise FileNotFoundError(
        f"fixture not found under allowed roots: {fixture_path}"
    )
```
In `list_replay_fixtures`, enumerate both roots (skip a missing user dir) and add `"source": "ci"` / `"user"` per entry.

- [ ] **Step 4: Run — verify PASS.**

- [ ] **Step 5: Commit**

```bash
flake8 backend/algo/paper/supervisor.py tests/backend/test_paper_fixture_builder.py
git add -A && git commit -m "feat(paper): allow user fixtures dir in replay loader/list"
```

---

## Task 5: Endpoint — `POST /v1/algo/paper/fixtures/build`

**Files:** Modify `backend/algo/routes/paper.py`; Test `tests/backend/test_paper_fixture_builder.py`.

- [ ] **Step 1: Write the failing test**

Read an existing paper-route test (`backend/algo/tests/test_paper_*`) for the TestClient + `pro_or_superuser` override pattern, then:
```python
def test_build_fixture_endpoint_happy(monkeypatch):
    # mount the algo paper router; override pro_or_superuser ->
    # stub user; monkeypatch build_universe_fixture to return a
    # FixtureBuildResult; POST /v1/algo/paper/fixtures/build {} ->
    # 200 with filename/n_tickers/n_trigger_dates.
    ...

def test_build_fixture_endpoint_empty_universe(monkeypatch):
    # monkeypatch build_universe_fixture to raise
    # HTTPException(400, ...) -> assert 400.
    ...
```
Fill these in concretely using the real client/auth helper from the existing paper tests.

- [ ] **Step 2: Run — verify FAIL (404 / missing route).**

- [ ] **Step 3: Implement model + route**

Near the other models in `paper.py`:
```python
class BuildFixtureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lookback_days: int = Field(60, ge=5, le=250)


class BuildFixtureResponse(BaseModel):
    filename: str
    n_tickers: int
    n_trigger_dates: int
    n_ticks: int
    trigger_tickers: list[str]
```
Route (builder does sync I/O → `asyncio.to_thread`):
```python
@router.post("/fixtures/build", response_model=BuildFixtureResponse)
async def build_fixture(
    body: BuildFixtureRequest,
    user: UserContext = Depends(pro_or_superuser),
) -> BuildFixtureResponse:
    from backend.algo.paper.fixture_builder import (
        build_universe_fixture,
    )
    res = await asyncio.to_thread(
        build_universe_fixture, user.user_id,
        lookback_days=body.lookback_days,
    )
    return BuildFixtureResponse(
        filename=res.filename, n_tickers=res.n_tickers,
        n_trigger_dates=res.n_trigger_dates, n_ticks=res.n_ticks,
        trigger_tickers=res.trigger_tickers,
    )
```
Confirm `asyncio`, `Field`, `ConfigDict`, `BaseModel`, `pro_or_superuser`, `UserContext` are imported in `paper.py` (add if missing).

- [ ] **Step 4: Run — verify PASS, then restart backend (new route, §6.2)**

```bash
docker compose exec -T backend python -m pytest tests/backend/test_paper_fixture_builder.py -q
./run.sh restart backend && sleep 8
```

- [ ] **Step 5: Commit**

```bash
flake8 backend/algo/routes/paper.py tests/backend/test_paper_fixture_builder.py
git add -A && git commit -m "feat(paper): POST /algo/paper/fixtures/build endpoint"
```

---

## Task 6: Frontend hook `useBuildReplayFixture`

**Files:** Create `frontend/hooks/useBuildReplayFixture.ts` + `frontend/hooks/__tests__/useBuildReplayFixture.test.ts`.

- [ ] **Step 1: Write the failing test**

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
vi.mock("@/lib/apiFetch", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/apiFetch";
import { useBuildReplayFixture } from "../useBuildReplayFixture";

beforeEach(() => vi.clearAllMocks());

it("posts and returns the build result", async () => {
  (apiFetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
    ok: true,
    json: async () => ({
      filename: "u1.jsonl", n_tickers: 8, n_trigger_dates: 14,
      n_ticks: 84, trigger_tickers: ["TCS.NS"],
    }),
  });
  const { result } = renderHook(() => useBuildReplayFixture());
  let res: { filename: string } | undefined;
  await act(async () => { res = await result.current.submit(); });
  expect(apiFetch).toHaveBeenCalledWith(
    expect.stringContaining("/algo/paper/fixtures/build"),
    expect.objectContaining({ method: "POST" }),
  );
  expect(res?.filename).toBe("u1.jsonl");
});
```

- [ ] **Step 2: Run — verify FAIL** (`cd frontend && npx vitest run hooks/__tests__/useBuildReplayFixture.test.ts`).

- [ ] **Step 3: Implement**

```ts
"use client";
import { useCallback, useState } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface BuildFixtureResult {
  filename: string;
  n_tickers: number;
  n_trigger_dates: number;
  n_ticks: number;
  trigger_tickers: string[];
}

export function useBuildReplayFixture() {
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<BuildFixtureResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = useCallback(
    async (lookbackDays = 60): Promise<BuildFixtureResult> => {
      setSubmitting(true);
      setError(null);
      try {
        const r = await apiFetch(
          `${API_URL}/algo/paper/fixtures/build`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lookback_days: lookbackDays }),
          },
        );
        if (!r.ok) {
          const b = await r.text();
          const m = `Build failed: ${r.status} ${b}`;
          setError(m);
          throw new Error(m);
        }
        const data = (await r.json()) as BuildFixtureResult;
        setResult(data);
        return data;
      } finally {
        setSubmitting(false);
      }
    },
    [],
  );

  return { submit, submitting, result, error };
}
```

- [ ] **Step 4: Run — verify PASS.**

- [ ] **Step 5: Commit**

```bash
cd frontend && npx eslint hooks/useBuildReplayFixture.ts hooks/__tests__/useBuildReplayFixture.test.ts && cd ..
git add frontend/hooks/useBuildReplayFixture.ts frontend/hooks/__tests__/useBuildReplayFixture.test.ts
git commit -m "feat(fe): useBuildReplayFixture hook"
```

---

## Task 7: WatchlistOverflowMenu item + result

**Files:** Modify `frontend/components/widgets/WatchlistOverflowMenu.tsx`.

- [ ] **Step 1: Read the component** fully to learn its menu-item + result-message idiom and prop wiring. Match it; do NOT restructure.

- [ ] **Step 2: Add the item + handler**

Add a menu item **"Build RSI(2) replay fixture"** (`data-testid="watchlist-build-fixture"`) that calls `useBuildReplayFixture().submit()`. Disable while `submitting`. On success show inline text via the menu's existing result affordance:
`Built {filename} · {n_tickers} tickers · {n_trigger_dates} trigger dates`, or when `n_trigger_dates === 0`: `No oversold (RSI2≤5) setups in the lookback window`. On error show the hook's `error`.

- [ ] **Step 3: Lint + typecheck changed file**

```bash
cd frontend
npx eslint components/widgets/WatchlistOverflowMenu.tsx hooks/useBuildReplayFixture.ts
npx tsc --noEmit 2>&1 | grep -E "WatchlistOverflowMenu|useBuildReplayFixture" || echo "clean for changed files"
cd ..
```
Expected: eslint clean; "clean for changed files".

- [ ] **Step 4: Commit**

```bash
git add frontend/components/widgets/WatchlistOverflowMenu.tsx
git commit -m "feat(fe): Build RSI(2) replay fixture in watchlist overflow menu"
```

---

## Task 8: E2E + verify + PROGRESS + push

**Files:** Modify `e2e/utils/selectors.ts`; watchlist POM; create `e2e/tests/frontend/watchlist-build-fixture.spec.ts`.

- [ ] **Step 1: Register testid** — `FE.watchlistBuildFixture = "watchlist-build-fixture"` in `e2e/utils/selectors.ts`.

- [ ] **Step 2: POM + spec** — read an existing watchlist E2E + POM for the open-overflow-menu pattern; add a method to open the ⋮ menu + locate the build item; create the spec (superuser storage state) that opens the dashboard, opens the menu, clicks the item, asserts a result message (`/Built|No oversold/`). Element waits only (no `networkidle`). Name the spec so no `testIgnore` pattern excludes it.

- [ ] **Step 3: Run E2E**

`cd e2e && npx playwright test watchlist-build-fixture --project=frontend-chromium`
If it fails for an environment reason (auth fixture, stack), commit the artifacts and report DONE_WITH_CONCERNS.

- [ ] **Step 4: Manual smoke (the real goal)**

```bash
docker compose exec -T backend python -c "
from backend.algo.paper.fixture_builder import build_universe_fixture
print(build_universe_fixture('60d30496-acb9-4530-8a4c-cf774c4934f4', lookback_days=90))
"
```
Expect `n_trigger_dates > 0` (try larger lookback if 0). Then a paper **replay** run with this fixture should produce fills.

- [ ] **Step 5: PROGRESS + push**

Dated `PROGRESS.md` entry (builder + why: replay default ignored the watchlist; this targets v3 trigger dates from holdings+watchlist). Commit + push:
```bash
git add PROGRESS.md e2e/
git commit -m "test(e2e)+docs: paper replay fixture builder"
git push
```

---

## Self-review notes

- **Spec coverage:** §4.1 builder → Tasks 1-3; §4.2 endpoint → Task 5; §4.3 loader allowlist → Task 4; §5 frontend → Tasks 6-7; §8 testing → Tasks 1-8. Covered.
- **Type consistency:** `FixtureBuildResult`(filename/n_tickers/n_trigger_dates/n_ticks/trigger_tickers) ≡ endpoint `BuildFixtureResponse` ≡ FE `BuildFixtureResult`. Signatures `_entry_fires(cond, features)`, `_scan_trigger_dates(ticker, cond, start, end, *, max_dates)`, `_synth_ticks(ticker, dt, close, volume)`, `build_universe_fixture(user_id, *, lookback_days, max_dates_per_ticker)` consistent across tasks.
- **Integration caveat (Task 2):** the exact inputs to `assemble_per_bar_features` (whether nifty_*/stress_prob arrive via `regime_row` vs `market_regime`/`market_trend`/`factor_row`) MUST be verified against `runtime._on_bar_close` (540-575) and mirrored — that's the drift-free guarantee. Also confirm `PerBarFeatures`' feature-dict accessor + `EvalContext`'s field name. These are reads against existing code, not placeholders.
- **Restart:** the new route (Task 5) needs `./run.sh restart backend` before manual/E2E testing (§6.2).
