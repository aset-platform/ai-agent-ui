from backend.algo.research.entry_strength_feasibility.dimensions import (
    COMPOSITE,
    DIMENSIONS,
    KNOWN_COLUMNS,
)


def test_every_referenced_feature_is_known():
    referenced = {f for feats in DIMENSIONS.values() for f in feats}
    referenced |= {f for f, _ in COMPOSITE}
    missing = referenced - KNOWN_COLUMNS
    assert missing == set(), f"unknown feature columns: {missing}"


def test_composite_covers_four_dimensions_with_signs():
    assert len(COMPOSITE) == 4
    assert all(sign in (1.0, -1.0) for _, sign in COMPOSITE)
    assert len(DIMENSIONS) == 4
