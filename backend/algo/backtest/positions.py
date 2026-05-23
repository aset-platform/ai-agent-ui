"""PositionTracker — long-only v1 with simple weighted-avg cost
basis. Realised P&L computed on every closing leg; unrealised
P&L computed on demand against a mark-price dict (typically
the most recent bar close at snapshot time).

v2 will add short positions, partial-fill grouping, and
options-style margin accounting.
"""
from __future__ import annotations

from decimal import Decimal

from datetime import date as date_type

from backend.algo.backtest.types import Fill, Position


class PositionTracker:
    def __init__(self) -> None:
        self._open: dict[str, Position] = {}
        self._closed: list[Position] = []
        self._realised_total: Decimal = Decimal("0")

    def apply_fill(self, fill: Fill) -> None:
        if fill.side == "BUY":
            self._apply_buy(fill)
        else:
            self._apply_sell(fill)

    def _apply_buy(self, fill: Fill) -> None:
        existing = self._open.get(fill.ticker)
        if existing is None:
            self._open[fill.ticker] = Position(
                ticker=fill.ticker,
                qty=fill.qty,
                avg_price=fill.fill_price,
                opened_at=fill.fill_date,
                opened_at_ts_ns=fill.fill_ts_ns,
            )
            return
        # Weighted average cost basis. Preserve the original
        # opened_at + ts so the eventual close row shows the
        # earliest entry, not the last add-on.
        total_qty = existing.qty + fill.qty
        new_avg = (
            (existing.avg_price * existing.qty)
            + (fill.fill_price * fill.qty)
        ) / total_qty
        self._open[fill.ticker] = existing.model_copy(update={
            "qty": total_qty,
            "avg_price": new_avg,
        })

    def _apply_sell(self, fill: Fill) -> None:
        existing = self._open.get(fill.ticker)
        if existing is None or existing.qty <= 0:
            # v1 long-only — bare sells are no-ops.
            return
        sell_qty = min(fill.qty, existing.qty)
        realised = (fill.fill_price - existing.avg_price) * sell_qty
        self._realised_total += realised
        if sell_qty == existing.qty:
            closed = existing.model_copy(update={
                "closed_at": fill.fill_date,
                "closed_at_ts_ns": fill.fill_ts_ns,
                "realised_pnl_inr": realised,
                "exit_reason": fill.exit_reason,
            })
            self._closed.append(closed)
            del self._open[fill.ticker]
        else:
            # Partial close: retain remainder open, archive the
            # closed slice as its own row.
            self._closed.append(Position(
                ticker=existing.ticker,
                qty=sell_qty,
                avg_price=existing.avg_price,
                opened_at=existing.opened_at,
                opened_at_ts_ns=existing.opened_at_ts_ns,
                closed_at=fill.fill_date,
                closed_at_ts_ns=fill.fill_ts_ns,
                realised_pnl_inr=realised,
                exit_reason=fill.exit_reason,
            ))
            self._open[fill.ticker] = existing.model_copy(update={
                "qty": existing.qty - sell_qty,
            })

    def force_close_all(
        self,
        *,
        marks: dict[str, Decimal],
        fill_date: date_type,
        exit_reason: str,
        fill_ts_ns: int | None = None,
    ) -> list[Position]:
        """Synthetically close every open position at the supplied
        mark price for that ticker.

        Used by the backtest runner at period end (so trade_list
        accounts for 100% of total_pnl) and at MIS day-end square-
        off (so MIS simulations match Zerodha's auto-close
        contract). Realised PnL is computed at the mark — no fees
        are charged because there is no real fill.

        Returns the list of positions that were closed. Tickers
        with no mark in ``marks`` are skipped (no way to value the
        exit fairly) — those callers should treat them as stranded.
        """
        out: list[Position] = []
        for ticker in list(self._open.keys()):
            mark = marks.get(ticker)
            if mark is None:
                continue
            pos = self._open[ticker]
            # Defensive: skip a position whose ``opened_at`` is in
            # the future relative to this force-close. Used to
            # happen when a late-day BUY at the last 15m bar of
            # day N filled on day N+1's opening bar — the position
            # ended up "opened" in day N's tracker iteration via
            # the same-loop fill, then day N's MIS square-off
            # tried to close it. Now we let day N+1's square-off
            # catch it cleanly.
            if pos.opened_at > fill_date:
                continue
            realised = (mark - pos.avg_price) * pos.qty
            self._realised_total += realised
            closed = pos.model_copy(
                update={
                    "closed_at": fill_date,
                    "closed_at_ts_ns": fill_ts_ns,
                    "realised_pnl_inr": realised,
                    "exit_reason": exit_reason,
                },
            )
            self._closed.append(closed)
            del self._open[ticker]
            out.append(closed)
        return out

    def open_positions(self) -> dict[str, Position]:
        return dict(self._open)

    def closed_positions(self) -> list[Position]:
        return list(self._closed)

    def total_realised_pnl_inr(self) -> Decimal:
        return self._realised_total

    def unrealised_pnl_inr(
        self, marks: dict[str, Decimal],
    ) -> Decimal:
        total = Decimal("0")
        for ticker, pos in self._open.items():
            mark = marks.get(ticker)
            if mark is None:
                continue
            total += (mark - pos.avg_price) * pos.qty
        return total
