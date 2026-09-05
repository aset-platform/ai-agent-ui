from backend.algo.research.entry_strength_feasibility.stats import (
    mannwhitney_p,
    median_split_win_rate,
    separation_auc,
)

_F = [1.0, 2.0, 3.0, 4.0]
_PERFECT = [False, False, True, True]   # winners have higher values
_REVERSED = [True, True, False, False]
_NOSEP = [True, False, False, True]


def test_auc_perfect_reversed_and_none():
    assert separation_auc(_F, _PERFECT) == 1.0
    assert separation_auc(_F, _REVERSED) == 0.0
    assert separation_auc(_F, _NOSEP) == 0.5
    # single class -> undefined -> 0.5
    assert separation_auc(_F, [True, True, True, True]) == 0.5


def test_auc_drops_nan_pairs():
    f = [1.0, None, 3.0, 4.0]
    # None row dropped; remaining [1,3,4] vs [F,T,T] still perfectly separates
    assert separation_auc(f, _PERFECT) == 1.0


def test_median_split_win_rate():
    top, bottom = median_split_win_rate(_F, _PERFECT)
    assert top == 1.0 and bottom == 0.0


def test_mannwhitney_p_range_and_empty_class():
    p = mannwhitney_p(_F, _PERFECT)
    assert p is not None and 0.0 <= p <= 1.0
    assert mannwhitney_p(_F, [True, True, True, True]) is None


def test_mannwhitney_p_none_when_all_values_identical():
    assert mannwhitney_p([2.0, 2.0, 2.0, 2.0], _PERFECT) is None
