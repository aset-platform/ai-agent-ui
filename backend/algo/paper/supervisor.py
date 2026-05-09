"""PaperSupervisor — per-process registry of running PaperRuntime
asyncio tasks keyed by (user_id, strategy_id).

v1 design (Slice 8c):
- One Tick source per runtime (replay or live). Multi-strategy
  fan-out from a SINGLE Kite WebSocket lands in v2 — see spec
  § 13 risk #6 ("connection storm"). The single-source approach
  is acceptable for v1 because:
    1. Replay-fixture mode (CI) doesn't share resources anyway.
    2. Real Kite WS in v1 is one-user-one-strategy in practice
       — multi-strategy paper-trading is an edge case for v1.
    3. The supervisor abstraction lets us swap in a multiplexer
       transparently in v2 without changing the run lifecycle
       endpoints.

Lifecycle:
  start_run(...) → spawn an asyncio.Task, register in the dict.
  stop_run(...)  → cancel the task; await its completion.
  list_active(user_id) → return the running runs for the user.

Process-local state — supervisor is reset on backend restart.
On restart, the runs simply don't auto-resume; the user must
re-arm them via the API. The replay rebuilder (Slice 8b)
handles the risk_state side of recovery.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from backend.algo.paper.runtime import PaperRuntime
from backend.algo.stream.sources import ReplayTickSource, TickSource
from backend.algo.strategy.ast import Strategy

_logger = logging.getLogger(__name__)


class PaperSupervisor:
    """Process-local supervisor. Created via ``get_supervisor()``."""

    def __init__(self) -> None:
        self._runs: dict[tuple[UUID, UUID], dict[str, Any]] = {}

    async def start_run(
        self,
        *,
        user_id: UUID,
        strategy: Strategy,
        source: TickSource,
        initial_capital_inr: Decimal,
        kill_switch_active: bool = False,
    ) -> dict[str, Any]:
        """Spawn a PaperRuntime task. Idempotent — if the same
        (user, strategy) is already running, raises RuntimeError.
        """
        key = (user_id, strategy.id)
        if key in self._runs:
            raise RuntimeError(
                f"Run already active for strategy {strategy.id}",
            )

        runtime = PaperRuntime(
            strategy=strategy,
            user_id=user_id,
            initial_capital_inr=initial_capital_inr,
            fee_as_of=date.today(),
            kill_switch_active=kill_switch_active,
        )
        task = asyncio.create_task(runtime.run(source))
        self._runs[key] = {
            "user_id": user_id,
            "strategy_id": strategy.id,
            "strategy_name": strategy.name,
            "started_at": datetime.now(timezone.utc),
            "task": task,
            "runtime": runtime,
        }
        _logger.info(
            "PaperSupervisor: started run user=%s strat=%s",
            user_id, strategy.id,
        )
        return self._public_row(self._runs[key])

    async def stop_run(
        self, *, user_id: UUID, strategy_id: UUID,
    ) -> bool:
        """Cancel the task. Returns True if a run was stopped."""
        key = (user_id, strategy_id)
        entry = self._runs.pop(key, None)
        if entry is None:
            return False
        task: asyncio.Task = entry["task"]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            _logger.exception(
                "PaperSupervisor: task raised on cancel",
            )
        _logger.info(
            "PaperSupervisor: stopped run user=%s strat=%s",
            user_id, strategy_id,
        )
        return True

    def list_active(
        self, *, user_id: UUID,
    ) -> list[dict[str, Any]]:
        """Return active runs for *user_id* as plain dicts."""
        # Reap any task that completed since last call.
        for key, entry in list(self._runs.items()):
            task: asyncio.Task = entry["task"]
            if task.done():
                self._runs.pop(key, None)
        return [
            self._public_row(entry)
            for (uid, _sid), entry in self._runs.items()
            if uid == user_id
        ]

    @staticmethod
    def _public_row(entry: dict[str, Any]) -> dict[str, Any]:
        task: asyncio.Task = entry["task"]
        return {
            "user_id": str(entry["user_id"]),
            "strategy_id": str(entry["strategy_id"]),
            "strategy_name": entry["strategy_name"],
            "started_at": entry["started_at"].isoformat(),
            "status": "running" if not task.done() else "completed",
        }


_singleton: PaperSupervisor | None = None


def get_supervisor() -> PaperSupervisor:
    """Module-level singleton — one supervisor per backend process."""
    global _singleton
    if _singleton is None:
        _singleton = PaperSupervisor()
    return _singleton


_FIXTURES_ROOT = Path(
    "/app/backend/algo/tests/fixtures"
).resolve()


def build_replay_source(fixture_path: str) -> ReplayTickSource:
    """Helper for the routes layer — validates the path lives
    inside the algo tests fixtures dir (so users can't read
    arbitrary files via the API)."""
    candidate = (_FIXTURES_ROOT / fixture_path).resolve()
    if not str(candidate).startswith(str(_FIXTURES_ROOT)):
        raise ValueError(
            f"fixture_path must live under {_FIXTURES_ROOT}",
        )
    if not candidate.exists():
        raise FileNotFoundError(str(candidate))
    return ReplayTickSource(candidate, pace="fast")


def list_replay_fixtures() -> list[dict[str, Any]]:
    """Enumerate ``*.jsonl`` files in the fixtures dir with
    summary stats (tick count, distinct tickers). Powers the
    start-run form's fixture dropdown — same validation as
    build_replay_source so the dropdown can't show a path the
    POST /runs endpoint would reject.
    """
    import json

    if not _FIXTURES_ROOT.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(_FIXTURES_ROOT.glob("*.jsonl")):
        n_ticks = 0
        tickers: set[str] = set()
        try:
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    n_ticks += 1
                    try:
                        obj = json.loads(s)
                    except Exception:  # noqa: BLE001
                        continue
                    t = obj.get("ticker")
                    if isinstance(t, str):
                        tickers.add(t)
        except Exception:  # noqa: BLE001
            _logger.exception(
                "list_replay_fixtures: failed to read %s", path,
            )
            continue
        out.append({
            "path": path.name,
            "n_ticks": n_ticks,
            "distinct_tickers": len(tickers),
            "sample_tickers": sorted(tickers)[:5],
            "size_bytes": path.stat().st_size,
        })
    return out
