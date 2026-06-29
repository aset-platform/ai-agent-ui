# Regime pipeline degraded mode: stress_prob=None data patch

When the regime classifier pipeline runs in degraded mode
(`pct_above_50sma=nan`, `degraded=True` in the row), it writes
`regime_label` to `stocks.regime_history` but leaves `stress_prob=None`.

## Downstream impact

`_ensure_regime_cache` (`backend/algo/live/runtime.py`) only inserts
`stress_prob` into `_regime_by_date[date]` when the value is not None.
For any oversold ticker that passes conditions 1–3 of a buy rule and
reaches `stress_prob < 0.5` (condition 4), the evaluator raises:

```
KeyError: Feature not in context: stress_prob
```

Before the eval gate (e.g. `< 14:20 IST`), most tickers short-circuit
on RSI > 5 so the gap is invisible. After the gate, genuinely oversold
tickers hit the KeyError and are dropped — no event, no UI activity.

## Code-level defence (already in place)

`_ensure_regime_cache` forward-fills `stress_prob` after loading the
window, carrying the last known non-None value across any degraded days.
This prevents future recurrence for newly starting sessions. Existing
degraded rows in Iceberg still need a manual data patch.

## Data patch procedure

```python
# Run inside the container:
# docker compose exec backend python -c "..."
from datetime import date
from backend.algo.regime.repo import RegimeRow, upsert_regime_history, get_regime_history

# 1. Locate the degraded day and the last good day
rows = get_regime_history(date(YYYY, MM, DD - 5), date(YYYY, MM, DD + 1))
bad  = next(r for r in rows if r.bar_date == date(YYYY, MM, DD))
prev = next(r for r in rows if r.stress_prob is not None
            and r.bar_date < bad.bar_date)  # most-recent good row

# 2. Patch: forward-carry stress_prob; preserve regime_label + rule_inputs
patch = RegimeRow(
    bar_date=bad.bar_date,
    regime_label=bad.regime_label,   # keep the classifier's label
    stress_prob=prev.stress_prob,    # carry forward from last good day
    rule_inputs=bad.rule_inputs,
    classifier_version=bad.classifier_version,
)
n = upsert_regime_history([patch])
print(f"Patched {n} row(s) — stress_prob={prev.stress_prob}")
```

3. **Restart backend** — flushes the in-process DuckDB metadata cache so
   `_ensure_regime_cache` reloads the patched row on the next bar.

   > ⚠️ Restart also tears down the live Kite WS session.
   > User must reconnect from the Algo Trading UI after restart.

## Why regime_label is preserved

The classifier writes `regime_label` correctly even in degraded mode
(HMM state is valid; only the breadth input is missing). Forward-filling
`stress_prob` is safe because market stress changes slowly; a one-day
carry introduces negligible error and unblocks signal evaluation.

## Detection

Check `stocks.regime_history` for any row where `stress_prob IS NULL`:

```python
rows = get_regime_history(start, end)
bad_days = [r.bar_date for r in rows if r.stress_prob is None]
print(bad_days)  # [] = clean
```

Also look in the pipeline run log for:
```
degraded=True  pct_above_50sma=nan
```

See also: `mem:shared/debugging/regime-classifier-assertion-rule-inputs-missing`
