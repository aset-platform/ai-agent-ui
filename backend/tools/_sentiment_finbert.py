"""FinBERT batch sentiment scorer for financial headlines.

Scores headlines using the ``ProsusAI/finbert`` model from
HuggingFace Transformers. The model is lazy-loaded on first call
and cached as a module-level singleton. Inference runs on CPU only.

Public API
----------
- :func:`score_headlines_finbert` — score a list of raw headline strings
- :func:`compute_weighted_score` — weighted average of mapped scores
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

_PIPELINE: object | None = None
_PIPELINE_LOADED: bool = False

_MODEL_NAME = "ProsusAI/finbert"
_BATCH_SIZE = 16


# ------------------------------------------------------------------
# Lazy singleton
# ------------------------------------------------------------------


def _get_pipeline() -> object | None:
    """Return the cached FinBERT pipeline, loading it on first call.

    Returns ``None`` if the model cannot be loaded (e.g. ``transformers``
    or ``torch`` not installed, or network unavailable).
    """
    global _PIPELINE, _PIPELINE_LOADED  # noqa: PLW0603

    if _PIPELINE_LOADED:
        return _PIPELINE

    try:
        from transformers import pipeline  # type: ignore

        _PIPELINE = pipeline(
            "sentiment-analysis",
            model=_MODEL_NAME,
            device=-1,
            batch_size=_BATCH_SIZE,
        )
        _logger.info("FinBERT pipeline loaded: %s", _MODEL_NAME)
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "Failed to load FinBERT pipeline: %s", exc
        )
        _PIPELINE = None
    finally:
        _PIPELINE_LOADED = True

    return _PIPELINE


# ------------------------------------------------------------------
# Label mapping
# ------------------------------------------------------------------


def _map_score(label: str, confidence: float) -> float:
    """Map a FinBERT label + confidence to a [-1, +1] float."""
    label_lower = label.lower()
    if label_lower == "positive":
        return confidence
    if label_lower == "negative":
        return -confidence
    return 0.0


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def score_headlines_finbert(
    headlines: list[str],
) -> list[dict]:
    """Score financial headlines with FinBERT.

    Parameters
    ----------
    headlines:
        Raw headline strings to score.

    Returns
    -------
    list[dict]
        One dict per headline with keys:
        ``label`` (str), ``score`` (float confidence),
        ``mapped`` (float in [-1, +1]).
        Returns an empty list for empty input.
        Returns neutral dicts if the pipeline is unavailable.
    """
    if not headlines:
        return []

    pipe = _get_pipeline()

    if pipe is None:
        _logger.warning(
            "FinBERT pipeline unavailable — returning neutral scores"
        )
        return [
            {"label": "neutral", "score": 0.0, "mapped": 0.0}
            for _ in headlines
        ]

    try:
        raw_outputs = pipe(headlines)
    except Exception as exc:
        _logger.error("FinBERT inference failed: %s", exc)
        return [
            {"label": "neutral", "score": 0.0, "mapped": 0.0}
            for _ in headlines
        ]

    results: list[dict] = []
    for item_output in raw_outputs:
        # pipeline returns list[list[dict]] when return_all_scores=False
        # but also list[dict] — normalise to a single dict
        if isinstance(item_output, list):
            entry = item_output[0]
        else:
            entry = item_output

        label: str = entry["label"]
        confidence: float = float(entry["score"])
        mapped: float = _map_score(label, confidence)
        results.append(
            {"label": label, "score": confidence, "mapped": mapped}
        )

    return results


def compute_weighted_score(
    scored: list[dict],
    weights: list[float],
) -> float | None:
    """Compute a weighted average of FinBERT mapped scores.

    Parameters
    ----------
    scored:
        List of dicts as returned by :func:`score_headlines_finbert`.
    weights:
        Parallel list of non-negative floats (e.g. source reliability).

    Returns
    -------
    float | None
        Weighted average of ``mapped`` values, or ``None`` for empty input.
    """
    if not scored:
        return None

    total_weight = sum(weights)
    if total_weight == 0.0:
        return None

    weighted_sum = sum(
        item["mapped"] * w for item, w in zip(scored, weights)
    )
    return weighted_sum / total_weight
