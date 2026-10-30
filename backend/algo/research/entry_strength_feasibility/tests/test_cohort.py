import math

from backend.algo.research.entry_strength_feasibility.cohort import (
    filter_cohort,
    to_frame,
)

_ROWS = [
    {"mode": "live", "filled": True, "outcome_settled": True,
     "label_win": True, "dry_run": False, "breadth_oversold": 5,
     "breadth_total": 100, "rsi2_at_entry": 3.0},
    {"mode": "live", "filled": True, "outcome_settled": False,
     "label_win": None, "dry_run": False, "breadth_oversold": 1,
     "breadth_total": 100, "rsi2_at_entry": 4.0},
    {"mode": "live", "filled": False, "outcome_settled": True,
     "label_win": False, "dry_run": False, "breadth_oversold": 2,
     "breadth_total": 100, "rsi2_at_entry": 4.0},
    {"mode": "live", "filled": True, "outcome_settled": True,
     "label_win": True, "dry_run": True, "breadth_oversold": 2,
     "breadth_total": 100, "rsi2_at_entry": 4.0},
    {"mode": "paper", "filled": True, "outcome_settled": True,
     "label_win": False, "dry_run": False, "breadth_oversold": 2,
     "breadth_total": 100, "rsi2_at_entry": 4.0},
]


def test_filter_cohort_live_keeps_only_settled_filled_labeled():
    kept = filter_cohort(_ROWS, "live")
    assert len(kept) == 1
    assert kept[0]["rsi2_at_entry"] == 3.0


def test_to_frame_derives_breadth_pct():
    df = to_frame(filter_cohort(_ROWS, "live"))
    assert math.isclose(df["breadth_oversold_pct"].iloc[0], 0.05)
    assert df["label_win"].iloc[0] is True or bool(
        df["label_win"].iloc[0]
    )
