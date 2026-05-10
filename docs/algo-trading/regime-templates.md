# Regime-Tailored Strategy Templates

Three reference strategies, one per regime, that exercise the v3 feature
set (regime classifier, factor library, vol-target sizing). Each is a
JSON file under `backend/algo/strategy/templates/` and loads via
`backend.algo.strategy.templates.loader.load_template(name)`.

These are starting points — copy + tune to your own preferences.

| Regime | Template | Style |
|---|---|---|
| BULL | `regime_bull_momentum` | Trend-following + momentum confirmation |
| SIDEWAYS | `regime_sideways_meanrev_quality` | Mean reversion of high-quality low-vol names |
| BEAR | `regime_bear_defensive_lowvol` | Defensive low-vol + outperforming-during-stress |

## Feature selection rationale

Per spec §3.3 and the research synthesis §3 + §8, different factor
families dominate in different regimes. Heuristics applied:

### BULL — momentum dominates

Trend-following is the single most-cited NSE strategy that works in
sustained bull markets (research §8). Factors and rationale:

| Factor | Threshold | Why |
|---|---|---|
| `regime_label == BULL` | hard gate | Don't take trend-bets outside trend regime |
| `mom_12_1 > 0.10` | momentum filter | Skip-month convention proven in research §3 (best in trending bull) |
| `adx_14 > 25` | trend strength | ADX is directionless; 25+ = trending market |
| `distance_from_sma200 > 0` | confirmation | Above long-term trend |
| `volume_x_avg_20 > 1.0` | conviction | Volume confirms the move (research §3 volume) |
| `f_score >= 6` | quality floor | Avoid junk-rallying garbage |

**Sizing:** aggressive (vol-target 1.5%). Caps: 12% per position, 80% gross exposure, 10 names.

### SIDEWAYS — mean reversion + quality

Sideways markets reward picking up quality on dips (research §8). Pure
momentum gets chopped up. Factors:

| Factor | Threshold | Why |
|---|---|---|
| `regime_label == SIDEWAYS` | hard gate | Don't mean-revert into a falling market |
| `f_score >= 7` | quality essential | Higher bar — junk doesn't recover in sideways |
| `realized_vol_60d < 0.30` | low-vol bias | Sideways punishes vol exposure |
| `rsi 30-50` (Between) | oversold but recovering | Catch the bounce, not the falling knife |
| `distance_from_sma200 -5%..+10%` | near trend | Not extended in either direction |

**Sizing:** moderate (vol-target 1.0%). Caps: 10% per position, 60% gross
exposure, 8 names.

### BEAR — defensive low-vol quality

Most quants go to cash in BEAR. This template takes the alternative —
high-conviction defensive entries when the HMM hints the regime is
calming (`stress_prob < 0.5`). Factors:

| Factor | Threshold | Why |
|---|---|---|
| `regime_label == BEAR` | hard gate | This is the "in BEAR but selectively long" template |
| `stress_prob < 0.5` | HMM agreement | Don't enter when HMM still flags high stress |
| `f_score >= 8` | top decile quality | Survivorship matters most in BEAR |
| `beta_to_nifty < 0.7` | low beta | Defensive names hold up |
| `realized_vol_60d < 0.20` | low absolute vol | Avoid further-falling stocks |
| `rs_vs_nifty_3m > 1.0` | outperforming | Names beating the index even in BEAR are the survivors |

**Sizing:** conservative (vol-target 0.75%). Caps: 10% per position, 40%
gross exposure, 5 names. The cash drag IS the alpha here.

## Loading

```python
from backend.algo.strategy.templates.loader import load_template

bull = load_template("regime_bull_momentum")
side = load_template("regime_sideways_meanrev_quality")
bear = load_template("regime_bear_defensive_lowvol")
```

`list_templates()` returns all four (the existing
`sector_rotation_monthly` plus these three).

## Picking the right template for your backtest

The Backtest tab now shows a regime distribution chip when you select a
period. If `BULL >= 50%` of the period, the BULL template is recommended
— but the user always picks. For mixed periods, run all three and
compare returns.

## Out of scope

- **Auto-switching between templates mid-backtest** — each template is
  one strategy. A meta-strategy that switches with regime is a v3.1 / v4
  topic.
- **Sector rotation overlay** — see `sector_rotation_monthly` template
  for the sector-tilt pattern; that's separate from these three.
- **Walk-forward gate tuning per template** — use REGIME-5 walk-forward
  with the existing 5-gate strip to validate.
