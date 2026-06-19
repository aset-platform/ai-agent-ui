# Regime classifier assertions always fire: vix_close/pct_above_50sma is None

## Symptom
`india_regime_daily` pipeline → `regime_classifier_daily` step emits 2 error
violations every night:
```
value-is-not-nan:vix_close     vix_close is None
value-is-not-nan:pct_above_50sma  pct_above_50sma is None
```
The actual `stocks.regime_history` rows are written correctly — only the
post-step assertion reporting is broken.

## Root cause
`run_classifier_job` in `classifier_job.py` returned only:
```python
{"as_of": ..., "regime_label": ..., "stress_prob": ...}
```
`rule_inputs` was never included. `pipeline_steps.py` does:
```python
rule_inputs = out.get("rule_inputs") or {}   # → always {}
ctx = {"vix_close": rule_inputs.get("vix_close"), ...}  # → always None
```
The assertion framework (`value_is_not_nan`) checks `v is None` → fires every time.
The data itself is fine; the return dict omission was the entire bug.

## Fix
One line added to `run_classifier_job` return dict (`classifier_job.py`):
```python
"rule_inputs": row.rule_inputs,
```

## Verification
```bash
docker compose exec backend python -c "
from backend.algo.regime.classifier_job import run_classifier_job
out = run_classifier_job({})
print(out.get('rule_inputs'))  # {'vix_close': 13.42, 'pct_above_50sma': 0.651, ...}
"
```

## Note
`run_classifier` (the underlying fn) always stored `rule_inputs` correctly on
`RegimeRow` and in Iceberg. Only the scheduler-facing `run_classifier_job` wrapper
was missing the field.
