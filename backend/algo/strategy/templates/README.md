# Strategy Templates

JSON AST templates for built-in strategies. Each file is loaded by
`loader.py` and surfaced in the UI strategy picker.

---

## F&O 200 universe convention for MIS strategies

For MIS strategies that need to be restricted to liquid F&O underlyings:

- **Backtest**: set `universe.filter.is_fno = true` in the AST. The
  backtest universe resolver intersects with
  `backend/algo/research/intraday_15m_mis_bakeoff/fno_200.csv` automatically.
- **Paper and live**: the AST `is_fno` field is NOT honoured by the paper or
  live runtimes. Operators MUST pre-populate the strategy's
  `caps.allowed_tickers` row (PG, `backend/algo/live/caps_repo.py`) with the
  same F&O list at promotion time. The operator can copy the list out of the
  same CSV:

  ```python
  from backend.algo.research.intraday_15m_mis_bakeoff.universe import (
      load_fno_universe,
  )
  tickers = load_fno_universe()
  # Then pass `allowed_tickers=tickers` to the caps upsert.
  ```

  Failure to do this means the live runtime will accept signals on any
  ticker the strategy emits, including illiquid names — for MIS this
  causes square-off slippage and risks broker rejections.

The F&O list is a static quarterly snapshot. Refresh it when NSE rebalances
the F&O list — see `backend/algo/research/intraday_15m_mis_bakeoff/README.md`.
