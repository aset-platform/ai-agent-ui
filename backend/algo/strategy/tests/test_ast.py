"""Tests for backend.algo.strategy.ast — UniverseFilter."""


def test_universe_filter_defaults_is_fno_to_false():
    from backend.algo.strategy.ast import UniverseFilter

    uf = UniverseFilter(ticker_type=["stock"])
    assert uf.is_fno is False


def test_universe_filter_accepts_is_fno_true():
    from backend.algo.strategy.ast import UniverseFilter

    uf = UniverseFilter(ticker_type=["stock"], is_fno=True)
    assert uf.is_fno is True


def test_universe_filter_rejects_extra_fields():
    import pytest
    from pydantic import ValidationError

    from backend.algo.strategy.ast import UniverseFilter

    with pytest.raises(ValidationError):
        UniverseFilter(ticker_type=["stock"], unknown_field=42)
