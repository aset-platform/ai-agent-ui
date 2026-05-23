"""Strict-chronological train/val/test splitter for time-series data.

No shuffling, no group-K-fold across tickers. Time generalization
only — see spec §4.5.
"""

from __future__ import annotations

from datetime import date

import pandas as pd


def chronological_split(
    df: pd.DataFrame,
    *,
    date_col: str,
    train_fit_end: date,
    train_val_end: date,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split *df* into (train_fit, train_val, test) by *date_col*.

    Args:
        df: Frame to split. MUST be sorted ascending on
            ``date_col`` — we enforce this rather than re-sort
            so the caller stays honest about input ordering.
        date_col: Name of the date / timestamp column to split on.
        train_fit_end: Last date (inclusive) for the training fold.
        train_val_end: Last date (inclusive) for the early-stopping
            validation fold; everything strictly after lands in test.

    Returns:
        ``(train_fit, train_val, test)`` — strictly disjoint and
        chronologically ordered.
    """
    if not df[date_col].is_monotonic_increasing:
        raise ValueError(f"{date_col} must be sorted ascending")

    train_fit = df[df[date_col] <= train_fit_end].copy()
    train_val = df[
        (df[date_col] > train_fit_end) & (df[date_col] <= train_val_end)
    ].copy()
    test = df[df[date_col] > train_val_end].copy()
    return train_fit, train_val, test


def assert_chronological(
    train_fit: pd.DataFrame,
    train_val: pd.DataFrame,
    test: pd.DataFrame,
    *,
    date_col: str,
) -> None:
    """Hard-fail Gate 1 — see spec §7.1.

    Raises:
        AssertionError: if any split's max date is not strictly
            less than the next split's min date.
    """
    assert train_fit[date_col].max() < train_val[date_col].min(), (
        f"train_fit max {train_fit[date_col].max()} >= "
        f"train_val min {train_val[date_col].min()}"
    )
    assert train_val[date_col].max() < test[date_col].min(), (
        f"train_val max {train_val[date_col].max()} >= "
        f"test min {test[date_col].min()}"
    )
