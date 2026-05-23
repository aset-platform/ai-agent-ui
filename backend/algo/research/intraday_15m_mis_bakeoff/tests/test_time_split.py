"""Tests for the strict-chronological train/val/test split."""

from datetime import date

import pandas as pd
import pytest

from backend.algo.research._shared.time_split import (
    chronological_split,
    assert_chronological,
)


def _make_frame(start: str, end: str) -> pd.DataFrame:
    """One row per business day with a bar_date column."""
    dates = pd.bdate_range(start, end)
    return pd.DataFrame({"bar_date": dates.date, "x": range(len(dates))})


def test_split_returns_three_disjoint_chronological_frames():
    df = _make_frame("2025-11-17", "2026-05-21")
    train_fit, train_val, test = chronological_split(
        df,
        date_col="bar_date",
        train_fit_end=date(2026, 2, 8),
        train_val_end=date(2026, 2, 28),
    )

    assert train_fit["bar_date"].max() <= date(2026, 2, 8)
    assert train_val["bar_date"].min() >  date(2026, 2, 8)
    assert train_val["bar_date"].max() <= date(2026, 2, 28)
    assert test["bar_date"].min()      >  date(2026, 2, 28)

    total = len(train_fit) + len(train_val) + len(test)
    assert total == len(df)


def test_split_raises_when_input_unsorted():
    df = _make_frame("2025-11-17", "2026-05-21").sample(frac=1, random_state=0)
    with pytest.raises(ValueError, match="must be sorted"):
        chronological_split(
            df,
            date_col="bar_date",
            train_fit_end=date(2026, 2, 8),
            train_val_end=date(2026, 2, 28),
        )


def test_assert_chronological_passes_on_disjoint_ordered_frames():
    df = _make_frame("2025-11-17", "2026-05-21")
    train_fit, train_val, test = chronological_split(
        df,
        date_col="bar_date",
        train_fit_end=date(2026, 2, 8),
        train_val_end=date(2026, 2, 28),
    )
    assert_chronological(train_fit, train_val, test, date_col="bar_date")


def test_assert_chronological_raises_on_overlap():
    df = _make_frame("2025-11-17", "2026-05-21")
    train_fit = df[df["bar_date"] <= date(2026, 2, 28)]
    train_val = df[(df["bar_date"] >= date(2026, 2, 1))
                   & (df["bar_date"] <= date(2026, 2, 28))]
    test = df[df["bar_date"] > date(2026, 2, 28)]
    with pytest.raises(AssertionError):
        assert_chronological(train_fit, train_val, test, date_col="bar_date")
