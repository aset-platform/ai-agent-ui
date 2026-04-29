"""Pydantic v2 response models for recommendation endpoints.

Used by :mod:`recommendation_routes` to serialise
portfolio health recommendations for the dashboard.
"""

from pydantic import BaseModel


class RecommendationItem(BaseModel):
    """Single actionable recommendation."""

    id: str
    tier: str
    category: str
    ticker: str | None = None
    company_name: str | None = None
    action: str
    severity: str
    rationale: str
    expected_impact: str | None = None
    data_signals: dict = {}
    price_at_rec: float | None = None
    target_price: float | None = None
    expected_return_pct: float | None = None
    index_tags: list[str] = []
    status: str = "active"
    acted_on_date: str | None = None


class RecommendationResponse(BaseModel):
    """Full recommendation run with items."""

    run_id: str
    run_date: str
    run_type: str
    health_score: float
    health_label: str
    health_assessment: str | None = None
    recommendations: list[RecommendationItem] = []
    generated_at: str | None = None
    cached: bool = False
    reset_at: str | None = None
    scope: str | None = None


class HistoryRunItem(BaseModel):
    """Summary row for a past recommendation run."""

    run_id: str
    run_date: str
    scope: str = "all"
    run_type: str = "scheduled"
    health_score: float
    health_label: str
    total_recommendations: int = 0
    acted_on_count: int = 0


class AggregateStats(BaseModel):
    """Roll-up performance metrics across runs."""

    total_runs: int = 0
    total_recommendations: int = 0
    overall_hit_rate_30d: float | None = None
    overall_hit_rate_60d: float | None = None
    overall_hit_rate_90d: float | None = None
    overall_avg_return_pct: float | None = None
    overall_avg_excess_pct: float | None = None
    adoption_rate_pct: float = 0.0


class RecommendationHistoryResponse(BaseModel):
    """Past runs with aggregate statistics."""

    runs: list[HistoryRunItem] = []
    aggregate_stats: AggregateStats = AggregateStats()


class RecommendationStatsResponse(BaseModel):
    """Aggregate performance stats for all recs."""

    total_recommendations: int = 0
    total_acted_on: int = 0
    adoption_rate_pct: float = 0.0
    hit_rate_30d: float | None = None
    hit_rate_60d: float | None = None
    hit_rate_90d: float | None = None
    avg_return_30d: float | None = None
    avg_return_60d: float | None = None
    avg_return_90d: float | None = None
    avg_excess_return_30d: float | None = None
    avg_excess_return_60d: float | None = None
    avg_excess_return_90d: float | None = None
    category_breakdown: dict[str, int] = {}


class PerfBucket(BaseModel):
    """One time-bucket of cohort performance.

    A *cohort* bucket groups recommendations by when
    they were issued (``recommendations.created_at``
    truncated to week / month / quarter, IST). Outcome
    metrics attached to the bucket are computed from
    the 30 / 60 / 90-day post-issuance checks already
    persisted in ``recommendation_outcomes``.
    """

    bucket_start: str  # ISO date (Monday for week, 1st for month/quarter)
    bucket_label: str  # human label, e.g. "2026-W17", "Apr 2026", "Q2 2026"
    total_recs: int = 0
    acted_on_count: int = 0
    pending_count: int = 0  # recs <30d old, no outcomes yet
    hit_rate_30d: float | None = None
    hit_rate_60d: float | None = None
    hit_rate_90d: float | None = None
    avg_return_30d: float | None = None
    avg_return_60d: float | None = None
    avg_return_90d: float | None = None
    avg_excess_30d: float | None = None
    avg_excess_60d: float | None = None
    avg_excess_90d: float | None = None


class PerfSummary(BaseModel):
    """Roll-up across all returned buckets."""

    total_recs: int = 0
    acted_on_count: int = 0
    pending_count: int = 0
    hit_rate_30d: float | None = None
    hit_rate_60d: float | None = None
    hit_rate_90d: float | None = None
    avg_return_90d: float | None = None
    avg_excess_90d: float | None = None


class RecommendationPerformanceResponse(BaseModel):
    """Cohort-bucketed performance view for the
    Performance sub-tab. Buckets are ordered oldest
    → newest; missing periods are omitted (frontend
    can fill gaps if it needs a continuous axis).
    """

    granularity: str  # "week" | "month" | "quarter"
    scope: str  # "all" | "india" | "us"
    acted_on_only: bool
    months_back: int
    buckets: list[PerfBucket] = []
    summary: PerfSummary = PerfSummary()
