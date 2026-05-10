"""Trade reason log builder (REGIME-6).

Joins one closed trade with its entry + exit ``signal_generated``
events from ``algo.events`` and produces a ``TradeReason`` row
that the UI renders as a single line.

The entry payload (REGIME-6+) carries the regime label, stress
posterior, and the strategy's factor exposures at decision time.
The exit payload typically carries an ``exit_reason`` string from
the strategy AST. Pre-REGIME-6 events lack the attribution
fields — we fall back to ``None`` / empty defaults rather than
raising, so legacy events still render.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class TradeReason:
    """One row of the trade-reason log."""

    ticker: str
    opened_at: date
    closed_at: date
    qty: int
    entry_price: float
    exit_price: float
    pnl_inr: float
    pnl_pct: float
    entry_regime: str | None
    stress_prob: float | None
    entry_factor_exposures: dict[str, float] = field(
        default_factory=dict,
    )
    exit_reason: str | None = None
    reason_text: str = ""


def _parse_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Decode the ``payload_json`` string from an algo.events row.

    Tolerates an already-parsed dict (some test fixtures hand in
    a dict directly), and an empty / malformed payload returns
    ``{}`` rather than raising.
    """
    raw = event.get("payload_json") if event else None
    if raw is None:
        raw = event.get("payload") if event else None
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
        return decoded if isinstance(decoded, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _format_reason_text(
    *,
    entry_price: float,
    exit_price: float,
    pnl_pct: float,
    regime: str | None,
    exposures: dict[str, float],
    exit_reason: str | None,
) -> str:
    """Compose the human-readable reason string."""
    parts: list[str] = [
        f"BUY @ {entry_price:.2f}",
    ]
    if regime:
        parts.append(f" because regime={regime}")
    if exposures:
        top = sorted(
            exposures.items(),
            key=lambda kv: abs(kv[1]),
            reverse=True,
        )[:3]
        parts.append(
            "; factors: "
            + ", ".join(f"{k}={v:.2f}" for k, v in top),
        )
    parts.append(
        f". Exited @ {exit_price:.2f} ({pnl_pct:+.1f}%)",
    )
    if exit_reason:
        parts.append(f" via {exit_reason}")
    return "".join(parts)


def build_trade_reason(
    trade: dict[str, Any],
    entry_event: dict[str, Any] | None,
    exit_event: dict[str, Any] | None,
) -> TradeReason:
    """Compose one ``TradeReason`` row.

    ``trade`` carries the closed-position projection
    (ticker, opened_at, closed_at, qty, avg_entry_price,
    avg_exit_price, realised_pnl_inr).

    ``entry_event`` / ``exit_event`` are the two
    ``signal_generated`` rows from algo.events; either may be
    None (e.g. pre-REGIME-6 history without the joined event).
    """
    entry_payload = _parse_payload(entry_event or {})
    exit_payload = _parse_payload(exit_event or {})

    entry_price = float(trade["avg_entry_price"])
    exit_price = float(trade["avg_exit_price"])
    pnl_pct = (
        (exit_price - entry_price) / entry_price * 100.0
        if entry_price > 0
        else 0.0
    )

    regime = entry_payload.get("regime_label")
    stress = entry_payload.get("stress_prob")
    exposures_raw = entry_payload.get("factor_exposures") or {}
    exposures: dict[str, float] = {}
    if isinstance(exposures_raw, dict):
        for k, v in exposures_raw.items():
            try:
                exposures[k] = float(v)
            except (TypeError, ValueError):
                continue
    exit_reason = exit_payload.get("reason") or exit_payload.get(
        "exit_reason",
    )

    reason_text = _format_reason_text(
        entry_price=entry_price,
        exit_price=exit_price,
        pnl_pct=pnl_pct,
        regime=regime,
        exposures=exposures,
        exit_reason=exit_reason,
    )

    return TradeReason(
        ticker=trade["ticker"],
        opened_at=trade["opened_at"],
        closed_at=trade["closed_at"],
        qty=int(trade["qty"]),
        entry_price=entry_price,
        exit_price=exit_price,
        pnl_inr=float(trade["realised_pnl_inr"]),
        pnl_pct=pnl_pct,
        entry_regime=regime,
        stress_prob=(
            float(stress) if stress is not None else None
        ),
        entry_factor_exposures=exposures,
        exit_reason=exit_reason,
        reason_text=reason_text,
    )
