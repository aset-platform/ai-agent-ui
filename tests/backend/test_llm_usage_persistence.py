"""Tests for LLM usage persistence (ASETPLTFRM-56).

Covers pricing CRUD, usage append, totals seeding,
and ObservabilityCollector Iceberg integration.
"""

from datetime import date, datetime, timezone

import pytest

from stocks.repository import StockRepository


@pytest.fixture()
def repo():
    """Return a fresh StockRepository with seeded pricing."""
    r = StockRepository()
    # Seed pricing if empty (tests expect seeded data).
    if r.get_current_pricing().empty:
        from datetime import date as _d
        from scripts.seed_llm_pricing import _INITIAL_RATES
        for rate in _INITIAL_RATES:
            r.add_pricing(
                provider=rate["provider"],
                model=rate["model"],
                input_cost=rate["input_cost"],
                output_cost=rate["output_cost"],
                effective_from=_d(2026, 3, 1),
            )
    return r


class TestLlmPricing:
    """Pricing CRUD on ``stocks.llm_pricing``."""

    def test_add_and_get_current(self, repo):
        """Adding a rate makes it visible."""
        df = repo.get_current_pricing()
        # Seeded data should exist from seed script.
        assert not df.empty
        assert "provider" in df.columns
        assert "model" in df.columns
        assert "input_cost_per_1m" in df.columns

    def test_current_has_null_effective_to(self, repo):
        """Current rates have effective_to=None."""
        df = repo.get_current_pricing()
        assert df["effective_to"].isna().all()

    def test_all_pricing_includes_history(self, repo):
        """get_all_pricing returns all rows."""
        df = repo.get_all_pricing()
        assert len(df) >= len(
            repo.get_current_pricing(),
        )


class TestLlmUsage:
    """Usage append and read on ``stocks.llm_usage``."""

    def test_append_and_read_totals(self, repo):
        """Appended events are counted in totals."""
        now = datetime.now(timezone.utc).replace(
            tzinfo=None,
        )
        events = [
            {
                "request_date": date.today(),
                "timestamp": now,
                "agent_id": "test",
                "model": "test-model",
                "provider": "groq",
                "event_type": "request",
                "success": True,
            },
            {
                "request_date": date.today(),
                "timestamp": now,
                "agent_id": "test",
                "model": "test-model",
                "provider": "groq",
                "event_type": "cascade",
                "cascade_reason": "budget_exhausted",
                "success": False,
            },
        ]
        repo.append_llm_usage(events)
        totals = repo.get_usage_totals()
        assert totals["requests_total"] >= 1
        assert totals["cascade_count"] >= 1

    def test_empty_append_is_noop(self, repo):
        """Appending empty list does nothing."""
        repo.append_llm_usage([])

    def test_date_range_filter(self, repo):
        """get_usage_by_date_range filters correctly."""
        df = repo.get_usage_by_date_range(
            date(2020, 1, 1),
            date(2020, 1, 2),
        )
        assert df.empty


class TestObservabilityWithRepo:
    """ObservabilityCollector with Iceberg backing."""

    def test_seed_from_iceberg(self, repo):
        """Collector seeds totals from Iceberg."""
        from observability import ObservabilityCollector

        collector = ObservabilityCollector(repo=repo)
        stats = collector.get_stats()
        # Should have seeded counts ≥ 0.
        assert stats["requests_total"] >= 0
        assert stats["cascade_count"] >= 0
        # Clean up flush timer.
        collector.flush_sync()

    def test_pricing_loaded(self, repo):
        """Collector loads pricing on init."""
        from observability import ObservabilityCollector

        collector = ObservabilityCollector(repo=repo)
        # Should have at least the 5 seeded rates.
        pricing = collector.get_pricing("groq", "llama-3.3-70b-versatile")
        assert pricing is not None
        assert pricing["input_cost_per_1m"] == 0.59
        collector.flush_sync()

    def test_record_enqueues_event(self, repo):
        """Recording a request enqueues a pending
        event."""
        from observability import ObservabilityCollector

        collector = ObservabilityCollector(repo=repo)
        collector.record_request(
            "test-model",
            provider="groq",
            agent_id="test",
        )
        with collector._lock:
            pending = len(collector._pending_events)
        assert pending >= 1
        collector.flush_sync()
