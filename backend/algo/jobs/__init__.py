"""Algo Trading scheduled jobs."""

# REGIME-1 — daily regime classifier orchestrator (the
# ``@register_job("regime_classifier_daily")`` wrapper itself
# lives in ``backend/jobs/executor.py``; importing the
# orchestrator here keeps the algo job inventory cohesive even
# though the side-effect registration is centralised).
from backend.algo.regime import classifier_job  # noqa: F401

# REGIME-2a — daily factor compute orchestrator (the
# ``@register_job("compute_daily_factors")`` wrapper itself
# lives in ``backend/jobs/executor.py``).
from backend.algo.factors import compute_job  # noqa: F401

# REGIME-3 — daily regime-change notifier (22:35 IST).  The
# ``@register_job("regime_change_notifier")`` wrapper lives in
# ``backend/jobs/executor.py``; importing here keeps the algo
# job inventory cohesive.
from backend.algo.jobs import regime_change_notifier  # noqa: F401

# REGIME-6 — daily Brinson + monthly factor regression
# orchestrators.  The ``@register_job(...)`` wrappers themselves
# live in ``backend/jobs/executor.py``; importing here keeps the
# algo job inventory cohesive.
from backend.algo.attribution import job as attribution_job  # noqa: F401, E501

# REGIME-7 — monthly universe snapshot (1st Sunday 03:00 IST). The
# ``@register_job("universe_snapshot_monthly")`` wrapper itself
# lives in ``backend/jobs/executor.py``; importing here keeps the
# algo job inventory cohesive.
from backend.algo.universe import snapshot_job as universe_snapshot_job  # noqa: F401, E501
