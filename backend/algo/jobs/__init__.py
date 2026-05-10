"""Algo Trading scheduled jobs."""

# REGIME-1 — daily regime classifier orchestrator (the
# ``@register_job("regime_classifier_daily")`` wrapper itself
# lives in ``backend/jobs/executor.py``; importing the
# orchestrator here keeps the algo job inventory cohesive even
# though the side-effect registration is centralised).
from backend.algo.regime import classifier_job  # noqa: F401
