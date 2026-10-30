# Algo Intraday Entry Window (Release 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the accidental 14:20 entry gate with an all-day intraday entry window (fire on yesterday-close OR today-forming RSI2≤5), guarded by a falling-knife veto, and shadow-log entry-strength context for Release 2 calibration.

**Architecture:** All changes are localised to the daily-realtime entry path in `backend/algo/live/runtime.py::_on_bar_close` (~L3916-4024) plus module-level config constants (~L141-159). Safety exits (stop-loss / time-stop / STOP_HIT / GTT / MIS square-off) are handled *earlier* in `_on_bar_close` (above L3860) and return before the entry block, so gating signal-BUYs/SELLs here cannot affect them. New behaviour sits behind env knobs so it is togglable without a redeploy.

**Tech Stack:** Python 3.12, pytest. Runtime tests run **inside the Docker backend container** (`docker compose exec backend python -m pytest ...`) — they self-skip without pyarrow + py≥3.10.

## Global Constants

- Line length ≤ 79 (black/isort/flake8). `X | None` not `Optional[X]`. No bare `except:`. Caught exceptions in long-running jobs log with `exc_info=True`.
- Events: append `event_row(session_id=self._session_id, user_id=self._user_id, strategy_id=self._strategy.id, mode="live", type_=..., payload={...})` to `self._events`; include `**({"dry_run": True} if self._dry_run else {})` in the payload (mirror existing `signal_rejected` emits at L3880-3894 / L3993-4013).
- **Safety invariant (must not regress):** GTT two-path accounting, budget-reservation release on GTT exits, STOP_HIT sacrosanct, caps freshness, no silent qty=0 drop. The entry-path edit touches none of these — a regression test asserts safety SELLs still fire pre-09:30.
- Config knobs (module-level, `os.environ`, `_parse_ist_time` for times): retire `ALGO_DAILY_MIN_EVAL_TIME_IST`; add `ALGO_MIN_SELL_TIME_IST` (default "09:30"), `ALGO_ENTRY_FALLING_KNIFE_3D_PCT` (default -10.0), `ALGO_ENTRY_FALLING_KNIFE_GAP_PCT` (default -4.0). `ALGO_MIN_BUY_TIME_IST` (09:30) unchanged.
- Tests mirror the harness in `backend/algo/live/tests/test_live_order_gate.py` + `conftest.py`: `_strategy_payload()` (schedule `interval:"1d"`, root `rsi_2 <= 5` → `set_target_weight`), Kite + budget mocked, freeze wall-clock via `patch` on `datetime` used by `_on_bar_close`.

---

### Task 1: OR-trigger all-day entry (remove Gate B)

**Files:**
- Modify: `backend/algo/live/runtime.py:141-149` (remove `_MIN_EVAL_TIME_IST`), `:3959-4024` (replace pre/post-gate branch), `:625` (remove the eval-time log line).
- Test: `backend/algo/live/tests/test_entry_window_or_trigger.py` (new)

**Interfaces:**
- Consumes (existing, unchanged signatures): `self._eval_entry_on_closed_bar(history, bar, last_price)` → `Signal | None`; `signal` (`_action_to_signal(...)` result, has `.side`); locals `now_ist`, `is_flat`, `daily_realtime`, `last_bar_is_today`, `history`, `bar`, `last_price`, `_MIN_BUY_TIME_IST`.
- Produces: the entry block now fires a BUY when EITHER trigger is BUY; no `_MIN_EVAL_TIME_IST` symbol remains.

- [ ] **Step 1: Write the failing test**

```python
# test_entry_window_or_trigger.py — mirror test_live_order_gate.py setup.
# Build a LiveRuntime for _strategy_payload(), flat position, daily-realtime,
# last bar == today. Helpers feed a bar whose forming-bar RSI2 has bounced
# >5 but whose CLOSED (yesterday) bar was <=5, and vice-versa.

def test_intraday_forming_dip_enters_after_0930(runtime, feed_bar):
    # forming bar RSI2<=5 today, wall-clock 10:15 IST → BUY submitted
    with _clock("10:15"):
        n = feed_bar(rsi2_forming=3.0, rsi2_closed=60.0)
    assert n == 1  # entered on the intraday dip (was deferred to 14:20 before)

def test_yesterday_oversold_but_bounced_still_enters(runtime, feed_bar):
    # yesterday close RSI2<=5, today's forming bar bounced >5, 11:00 IST
    with _clock("11:00"):
        n = feed_bar(rsi2_forming=42.0, rsi2_closed=4.0)
    assert n == 1  # OR trigger: carried yesterday-close signal still fires

def test_buy_before_0930_deferred(runtime, feed_bar):
    with _clock("09:20"):
        n = feed_bar(rsi2_forming=3.0, rsi2_closed=3.0)
    assert n == 0  # observation window — no order
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec backend python -m pytest backend/algo/live/tests/test_entry_window_or_trigger.py -v`
Expected: FAIL — current code defers the intraday-only dip until 14:20 (test 1 returns 0).

- [ ] **Step 3: Remove Gate B constant and its log line**

Delete the `_MIN_EVAL_TIME_IST` block at `runtime.py:141-149` and the `_MIN_EVAL_TIME_IST.strftime(...)` log reference near `:625`.

- [ ] **Step 4: Replace the pre/post-gate branch (L3959-4024) with OR logic**

```python
            # Gate A (09:30 BUY floor) already applied above.
            # OR-trigger, all day: enter if EITHER today's forming-bar
            # signal is BUY OR yesterday's CLOSED bar was a BUY. Catches a
            # brief intraday dip the moment it prints <=5, and still honours
            # a gap-into-oversold carried from yesterday's close.
            closed_entry = self._eval_entry_on_closed_bar(
                history, bar, last_price,
            )
            forming_is_buy = signal is not None and signal.side == "BUY"
            closed_is_buy = (
                closed_entry is not None and closed_entry.side == "BUY"
            )
            if not forming_is_buy and closed_is_buy:
                # Yesterday oversold; today's forming bar no longer says BUY
                # (e.g. RSI2 bounced) — still enter on the carried signal.
                signal = closed_entry
            # else: forming_is_buy → `signal` already BUY, flows through;
            # neither → signal is hold/None, handled by the block below.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker compose exec backend python -m pytest backend/algo/live/tests/test_entry_window_or_trigger.py -v`
Expected: PASS (all three).

- [ ] **Step 6: Grep for stragglers + commit**

```bash
grep -rn "_MIN_EVAL_TIME_IST\|DAILY_MIN_EVAL_TIME" backend/   # expect: no hits
git add backend/algo/live/runtime.py backend/algo/live/tests/test_entry_window_or_trigger.py
git commit -m "feat(algo): all-day OR-trigger entry; remove 14:20 eval gate"
```

---

### Task 2: Signal-SELL 09:30 floor (safety exits stay ungated)

**Files:**
- Modify: `backend/algo/live/runtime.py:151-159` (add `_MIN_SELL_TIME_IST`), entry block after the Gate-A BUY check (~L3957).
- Test: `backend/algo/live/tests/test_sell_observation_floor.py` (new)

**Interfaces:**
- Consumes: `_MIN_SELL_TIME_IST` (new constant), `signal.side`, `now_ist`.
- Produces: signal-based SELLs deferred pre-09:30; safety exits (handled above L3860) unaffected.

- [ ] **Step 1: Write the failing test**

```python
def test_signal_sell_before_0930_deferred(runtime, feed_bar):
    # AST/rebalance SELL signal at 09:20 → deferred (observation window)
    with _clock("09:20"):
        n = feed_bar(open_qty=5, action="sell_signal")
    assert n == 0

def test_safety_sell_fires_before_0930(runtime, feed_stop_hit):
    # STOP_HIT / GTT-triggered exit at 09:10 → MUST fire (never gated)
    with _clock("09:10"):
        n = feed_stop_hit(open_qty=5)
    assert n == 1
```

- [ ] **Step 2: Run to verify fail**

Run: `docker compose exec backend python -m pytest backend/algo/live/tests/test_sell_observation_floor.py -v`
Expected: FAIL on test 1 (SELL currently not floored → fires at 09:20).

- [ ] **Step 3: Add the constant (L151-159 region)**

```python
# Earliest wall-clock at which a SIGNAL-based SELL (rebalance / AST /
# discretionary) may be placed. Safety exits (stop-loss, time-stop,
# STOP_HIT, GTT-triggered, MIS square-off) are handled earlier in
# _on_bar_close and are NEVER gated here.
_MIN_SELL_TIME_IST = _parse_ist_time(
    os.environ.get("ALGO_MIN_SELL_TIME_IST", "09:30"),
)
```

- [ ] **Step 4: Add the SELL floor in the entry block (just after the Gate-A BUY defer, ~L3957)**

```python
            if (
                signal is not None
                and signal.side == "SELL"
                and now_ist < _MIN_SELL_TIME_IST
            ):
                _logger.info(
                    "signal SELL deferred — before %s IST (ticker=%s "
                    "now=%s IST)",
                    _MIN_SELL_TIME_IST.strftime("%H:%M"),
                    bar.ticker,
                    now_ist.strftime("%H:%M:%S"),
                )
                return 0
```

- [ ] **Step 5: Run to verify pass**

Run: `docker compose exec backend python -m pytest backend/algo/live/tests/test_sell_observation_floor.py -v`
Expected: PASS. (Safety-SELL test passes because the stop/time-stop path returns above L3860, never reaching this floor.)

- [ ] **Step 6: Commit**

```bash
git add backend/algo/live/runtime.py backend/algo/live/tests/test_sell_observation_floor.py
git commit -m "feat(algo): 09:30 floor for signal SELLs; safety exits stay ungated"
```

---

### Task 3: Falling-knife veto

**Files:**
- Modify: `backend/algo/live/runtime.py` (constants L151-159 region; new `_in_free_fall` helper; call it in the entry block before a BUY fires).
- Test: `backend/algo/live/tests/test_falling_knife_veto.py` (new)

**Interfaces:**
- Consumes: `history` (ascending `BarData` with `.close`, `.open`), `bar` (today's forming bar, `.open`, `.close`), thresholds `_KNIFE_3D_PCT`, `_KNIFE_GAP_PCT`.
- Produces: `self._in_free_fall(history, bar) -> tuple[bool, dict]` — `(veto, {"ret_3d_pct", "gap_pct"})`. When veto, entry block emits `signal_rejected reason="falling_knife_veto"` and returns 0.

- [ ] **Step 1: Write the failing test**

```python
def test_veto_rejects_free_fall(runtime, feed_bar, events):
    # prior-3d return -14% (knife) → rejected + event, no order
    with _clock("10:30"):
        n = feed_bar(rsi2_forming=3.0, closes=[100, 96, 90, 86])  # -14% 3d
    assert n == 0
    assert any(e["type"] == "signal_rejected"
               and json.loads(e["payload_json"])["reason"] == "falling_knife_veto"
               for e in events())

def test_veto_rejects_gap_down(runtime, feed_bar, events):
    with _clock("10:30"):
        n = feed_bar(rsi2_forming=3.0, closes=[100, 100, 100, 100],
                     today_open=95.5)  # -4.5% gap
    assert n == 0

def test_normal_dip_passes_veto(runtime, feed_bar):
    with _clock("10:30"):
        n = feed_bar(rsi2_forming=3.0, closes=[100, 99, 98, 98])  # -2% 3d, no gap
    assert n == 1
```

- [ ] **Step 2: Run to verify fail**

Run: `docker compose exec backend python -m pytest backend/algo/live/tests/test_falling_knife_veto.py -v`
Expected: FAIL — no veto yet (knife test returns 1).

- [ ] **Step 3: Add thresholds (L151-159 region)**

```python
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default

# Falling-knife veto — hard NO-ENTRY when a name is in free-fall.
# Validated against 32 July-2026 live trades: blocks both knife losers,
# zero winners. Env-overridable for tuning from shadow data.
_KNIFE_3D_PCT = _env_float("ALGO_ENTRY_FALLING_KNIFE_3D_PCT", -10.0)
_KNIFE_GAP_PCT = _env_float("ALGO_ENTRY_FALLING_KNIFE_GAP_PCT", -4.0)
```

- [ ] **Step 4: Add the `_in_free_fall` helper (method on LiveRuntime)**

```python
    def _in_free_fall(self, history: list, bar) -> tuple[bool, dict]:
        """True if the name is in free-fall (knife). Uses the last three
        CLOSED daily bars for the 3-day return and today's forming-bar
        open vs yesterday's close for the gap. Missing data → no veto."""
        metrics: dict[str, float | None] = {
            "ret_3d_pct": None, "gap_pct": None,
        }
        closed = history[:-1] if (
            history and history[-1].date == bar.date
        ) else history
        if len(closed) >= 4:
            c_now = float(closed[-1].close)
            c_4 = float(closed[-4].close)
            if c_4 > 0:
                metrics["ret_3d_pct"] = (c_now / c_4 - 1.0) * 100.0
        if closed:
            c_prev = float(closed[-1].close)
            if c_prev > 0:
                metrics["gap_pct"] = (float(bar.open) / c_prev - 1.0) * 100.0
        veto = (
            (metrics["ret_3d_pct"] is not None
             and metrics["ret_3d_pct"] <= _KNIFE_3D_PCT)
            or (metrics["gap_pct"] is not None
                and metrics["gap_pct"] <= _KNIFE_GAP_PCT)
        )
        return veto, metrics
```

- [ ] **Step 5: Wire the veto into the entry block (immediately before the OR-trigger fires a BUY, after Task 1's block resolves `signal` to BUY)**

```python
            if signal is not None and signal.side == "BUY":
                _veto, _knife = self._in_free_fall(history, bar)
                if _veto:
                    _logger.info(
                        "falling-knife veto — ticker=%s ret_3d=%s gap=%s",
                        bar.ticker, _knife["ret_3d_pct"], _knife["gap_pct"],
                    )
                    self._events.append(
                        event_row(
                            session_id=self._session_id,
                            user_id=self._user_id,
                            strategy_id=self._strategy.id,
                            mode="live",
                            type_="signal_rejected",
                            payload={
                                **({"dry_run": True} if self._dry_run else {}),
                                "reason": "falling_knife_veto",
                                "ticker": bar.ticker,
                                "side": "BUY",
                                **_knife,
                            },
                        )
                    )
                    return 0
```

- [ ] **Step 6: Run to verify pass**

Run: `docker compose exec backend python -m pytest backend/algo/live/tests/test_falling_knife_veto.py -v`
Expected: PASS (all three).

- [ ] **Step 7: Commit**

```bash
git add backend/algo/live/runtime.py backend/algo/live/tests/test_falling_knife_veto.py
git commit -m "feat(algo): falling-knife veto on BUY entry (3d/gap thresholds)"
```

---

### Task 4: RSI2 cache + universe-oversold breadth helper

**Files:**
- Modify: `backend/algo/live/runtime.py` (init `self._last_rsi2`; update it after each eval near L3897; add `_universe_oversold_breadth`).
- Test: `backend/algo/live/tests/test_universe_breadth.py` (new)

**Interfaces:**
- Produces: `self._last_rsi2: dict[str, float]` (ticker → most-recent computed RSI2, `.NS`-keyed); `self._universe_oversold_breadth() -> tuple[int, int]` → `(n_oversold, n_total)` counting `self._last_rsi2` entries ≤ 5. Consumed by Task 5.

- [ ] **Step 1: Write the failing test**

```python
def test_breadth_counts_oversold(runtime):
    runtime._last_rsi2 = {"A.NS": 3.0, "B.NS": 4.9, "C.NS": 55.0, "D.NS": 5.0}
    assert runtime._universe_oversold_breadth() == (3, 4)  # <=5 : A,B,D
```

- [ ] **Step 2: Run to verify fail**

Run: `docker compose exec backend python -m pytest backend/algo/live/tests/test_universe_breadth.py -v`
Expected: FAIL — `_universe_oversold_breadth` / `_last_rsi2` missing.

- [ ] **Step 3: Init the cache (in `__init__`, near the other per-ticker state ~L410)**

```python
        # Most-recent computed RSI2 per ticker (.NS-keyed), refreshed each
        # _on_bar_close eval — powers the shadow breadth snapshot (Task 5).
        self._last_rsi2: dict[str, float] = {}
```

- [ ] **Step 4: Refresh the cache after each eval (right after the `eval ...` log, ~L3907)**

```python
        _rsi2_val = (features or {}).get("rsi_2")
        if _rsi2_val is not None:
            try:
                self._last_rsi2[bar.ticker] = float(_rsi2_val)
            except (TypeError, ValueError):
                pass
```

- [ ] **Step 5: Add the breadth helper**

```python
    def _universe_oversold_breadth(self, threshold: float = 5.0) -> tuple[int, int]:
        """(count of tracked tickers with RSI2<=threshold, total tracked)."""
        vals = list(self._last_rsi2.values())
        n_os = sum(1 for v in vals if v <= threshold)
        return n_os, len(vals)
```

- [ ] **Step 6: Run to verify pass**

Run: `docker compose exec backend python -m pytest backend/algo/live/tests/test_universe_breadth.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/algo/live/runtime.py backend/algo/live/tests/test_universe_breadth.py
git commit -m "feat(algo): per-ticker RSI2 cache + universe-oversold breadth helper"
```

---

### Task 5: Shadow `entry_strength_snapshot` event

**Files:**
- Modify: `backend/algo/live/runtime.py` (emit the snapshot when a BUY is about to fire, after the veto passes).
- Test: `backend/algo/live/tests/test_entry_strength_snapshot.py` (new)

**Interfaces:**
- Consumes: `features` (`rsi_2`, `distance_from_sma50`, `distance_from_sma200`), `self._in_free_fall` metrics, `self._universe_oversold_breadth()`, `closed_is_buy`/`forming_is_buy` (Task 1 locals).
- Produces: a non-gating `entry_strength_snapshot` event; the order path is unchanged whether or not the emit succeeds.

- [ ] **Step 1: Write the failing test**

```python
def test_snapshot_emitted_and_nonblocking(runtime, feed_bar, events):
    with _clock("10:30"):
        n = feed_bar(rsi2_forming=3.0, rsi2_closed=60.0)  # intraday trigger
    assert n == 1  # order still placed (snapshot never blocks)
    snaps = [e for e in events() if e["type"] == "entry_strength_snapshot"]
    assert len(snaps) == 1
    p = json.loads(snaps[0]["payload_json"])
    assert p["trigger"] == "intraday_forming"
    assert p["rsi2_forming"] == 3.0
    assert "breadth_oversold" in p and "breadth_total" in p
    assert "ret_3d_pct" in p and "gap_pct" in p
```

- [ ] **Step 2: Run to verify fail**

Run: `docker compose exec backend python -m pytest backend/algo/live/tests/test_entry_strength_snapshot.py -v`
Expected: FAIL — no `entry_strength_snapshot` event yet.

- [ ] **Step 3: Emit the snapshot (in the entry block, after the veto passes, before `_submit_order`)**

```python
                _os, _tot = self._universe_oversold_breadth()
                _trigger = (
                    "both" if (forming_is_buy and closed_is_buy)
                    else "intraday_forming" if forming_is_buy
                    else "yesterday_close"
                )
                try:
                    self._events.append(
                        event_row(
                            session_id=self._session_id,
                            user_id=self._user_id,
                            strategy_id=self._strategy.id,
                            mode="live",
                            type_="entry_strength_snapshot",
                            payload={
                                **({"dry_run": True} if self._dry_run else {}),
                                "ticker": bar.ticker,
                                "trigger": _trigger,
                                "rsi2_forming": (features or {}).get("rsi_2"),
                                "dist_sma50": (features or {}).get(
                                    "distance_from_sma50"),
                                "dist_sma200": (features or {}).get(
                                    "distance_from_sma200"),
                                "ret_3d_pct": _knife["ret_3d_pct"],
                                "gap_pct": _knife["gap_pct"],
                                "breadth_oversold": _os,
                                "breadth_total": _tot,
                            },
                        )
                    )
                except Exception:  # noqa: BLE001
                    _logger.warning(
                        "entry_strength_snapshot emit failed ticker=%s",
                        bar.ticker, exc_info=True,
                    )  # never block the order path
```

Note: `_knife` is in scope from Task 3's veto check (compute it once and reuse — if the veto block returned early on a knife, this snapshot is only reached for non-knife BUYs, which is intended: we log the entries we actually take).

- [ ] **Step 4: Run to verify pass**

Run: `docker compose exec backend python -m pytest backend/algo/live/tests/test_entry_strength_snapshot.py -v`
Expected: PASS.

- [ ] **Step 5: Full entry-path suite + commit**

```bash
docker compose exec backend python -m pytest backend/algo/live/tests/ -k "entry_window or sell_observation or falling_knife or breadth or entry_strength" -v
git add backend/algo/live/runtime.py backend/algo/live/tests/test_entry_strength_snapshot.py
git commit -m "feat(algo): shadow entry_strength_snapshot event (non-gating)"
```

---

### Task 6: Safety-invariant regression + lint

**Files:**
- Test: `backend/algo/live/tests/test_entry_change_safety_regression.py` (new)

- [ ] **Step 1: Write the regression test**

```python
def test_stop_hit_fires_pre_0930_after_entry_changes(runtime, feed_stop_hit):
    with _clock("09:05"):
        assert feed_stop_hit(open_qty=5) == 1  # STOP_HIT never gated

def test_gtt_exit_releases_budget_reservation(runtime, ...):
    # Mirror the assertion in test_gtt_trailing_integration.py: a
    # GTT-triggered exit still calls _release_budget_reservation_for_gtt_exit.
    ...  # copy the existing assertion; entry-path change must not affect it
```

- [ ] **Step 2: Run the broader safety suite to confirm no regression**

Run: `docker compose exec backend python -m pytest backend/algo/live/tests/test_safety_fail_closed.py backend/algo/live/tests/test_stop_hit_tracked.py backend/algo/live/tests/test_gtt_trailing_integration.py backend/algo/live/tests/test_live_order_gate.py -v`
Expected: PASS (unchanged).

- [ ] **Step 3: Lint**

Run: `black backend/algo/live/runtime.py && isort backend/algo/live/runtime.py --profile black && flake8 backend/algo/live/runtime.py`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add backend/algo/live/tests/test_entry_change_safety_regression.py
git commit -m "test(algo): safety-invariant regression for entry-window change"
```

---

## Self-Review

**Spec coverage:** §4.1 OR-trigger → Task 1. §4.2 sell floor + safety ungated → Task 2 (+ Task 6 regression). §4.3 falling-knife veto → Task 3. §4.4 shadow snapshot + breadth → Tasks 4-5. §4.5 config knobs → folded into Tasks 1-3 Global Constants. §5 safety invariants → Task 6. §6 test matrix → Tasks 1-6 cover all 8 cases. §7 rollout (env-togglable + Kite-WS restart) → operational, not a code task; noted in Global Constants + spec §7.

**Placeholder scan:** Task 6 Step 1's second test says "copy the existing assertion" — intentional (reuse the verbatim assertion from `test_gtt_trailing_integration.py` rather than restate broker/budget mock plumbing); the deliverable is a regression guard, not new logic.

**Type consistency:** `_in_free_fall` returns `(bool, dict)` with keys `ret_3d_pct`/`gap_pct` used identically in Tasks 3 & 5; `_universe_oversold_breadth` returns `(int, int)` consumed as `_os, _tot` in Task 5; `_last_rsi2` `.NS`-keyed dict defined in Task 4, read in Task 4's helper. `forming_is_buy`/`closed_is_buy` defined in Task 1, reused in Task 5. Consistent.

## Deferred / follow-ups (not Release 1)

- Release 2 composite gate calibration (own spec).
- Optional product backfill of `stocks.entry_quality_daily` for Jun 24–Jul 12.
- `algo.live_caps` edit-history audit table.
