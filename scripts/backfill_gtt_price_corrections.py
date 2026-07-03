"""One-off: correct today's GTT-triggered order_filled_live events
for SKYGOLD, SOUTHBANK, ZENTEC with Kite's true post-trigger
execution price, replacing the GTT's configured-order-price
estimate. See backend/algo/jobs/gtt_price_correction.py for the
implementation and docs/superpowers/specs/2026-07-02-closed-trade-
pairing-and-gtt-price-fix-design.md for why HSCL is out of scope
(Kite's order history is today-scoped; HSCL triggered 2026-07-01).

Run dry-run first (default), inspect the output, then re-run with
--apply.

Usage::

    docker compose exec backend python \
        scripts/backfill_gtt_price_corrections.py --user-id <uuid>
    docker compose exec backend python \
        scripts/backfill_gtt_price_corrections.py --user-id <uuid> --apply
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from uuid import UUID

from backend.algo.jobs.gtt_price_correction import (
    correct_gtt_event_prices,
)

_logger = logging.getLogger(__name__)

_SYMBOLS = ["SKYGOLD", "SOUTHBANK", "ZENTEC"]


async def _run(user_id: UUID, apply: bool) -> None:
    from backend.algo.routes.live import (
        _build_kite_client_for_user,
    )

    kite = await _build_kite_client_for_user(user_id)
    result = correct_gtt_event_prices(
        kite, _SYMBOLS, dry_run=not apply,
    )
    _logger.info("gtt price correction result: %s", result)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write the correction (default: dry-run).",
    )
    parser.add_argument(
        "--user-id", required=True, type=UUID,
        help="User id whose Kite session to use.",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.user_id, args.apply))


if __name__ == "__main__":
    main()
