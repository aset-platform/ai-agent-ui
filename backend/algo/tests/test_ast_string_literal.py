"""Verify ``Literal_`` accepts string values (REGIME-3).

Used by ``regime_label == "bull"`` style compares without
introducing a sugar AST node.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.algo.strategy.ast import Literal_


def test_string_literal_parses() -> None:
    lit = Literal_.model_validate({"literal": "bull"})
    assert lit.literal == "bull"


def test_int_literal_still_works() -> None:
    lit = Literal_.model_validate({"literal": 42})
    assert lit.literal == 42


def test_float_literal_still_works() -> None:
    lit = Literal_.model_validate({"literal": 3.14})
    assert lit.literal == 3.14


def test_literal_rejects_dict() -> None:
    with pytest.raises(ValidationError):
        Literal_.model_validate({"literal": {"x": 1}})


def test_literal_rejects_list() -> None:
    with pytest.raises(ValidationError):
        Literal_.model_validate({"literal": [1, 2]})
