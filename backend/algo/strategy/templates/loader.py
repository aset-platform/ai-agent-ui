"""Strategy template loader (REGIME-7).

Loads bundled JSON strategy templates by stem name and parses
them through the AST validator. Used by the templates gallery
(future v4 UI) and by tests to verify ship-templates stay
parseable as the grammar evolves.
"""
from __future__ import annotations

import json
from pathlib import Path

from backend.algo.strategy.ast import Strategy, parse_strategy

_TEMPLATE_DIR = Path(__file__).parent


def list_templates() -> list[str]:
    """Return sorted stem names of all bundled JSON templates."""
    return sorted(p.stem for p in _TEMPLATE_DIR.glob("*.json"))


def load_template(name: str) -> Strategy:
    """Load a JSON template by stem name.

    Raises ``FileNotFoundError`` if the file is missing and
    ``pydantic.ValidationError`` if the AST is malformed for the
    current grammar.
    """
    path = _TEMPLATE_DIR / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Template not found: {name}")
    with path.open() as fh:
        payload = json.load(fh)
    return parse_strategy(payload)
