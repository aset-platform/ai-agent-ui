"""Subscription tier constants and quota definitions.

Centralises all subscription-related configuration so that
guards, billing endpoints, and frontend can reference a single
source of truth.

Constants
---------
- :data:`TIER_ORDER` — numeric ordering for tier comparison.
- :data:`USAGE_QUOTAS` — monthly analysis quota per tier.
- :data:`DAILY_MSG_LIMITS` — daily chat message limits.
- :data:`TIER_NAMES` — human-readable tier labels.
"""

from __future__ import annotations

# -----------------------------------------------------------------
# Tier ordering (higher = more permissive)
# -----------------------------------------------------------------
TIER_ORDER: dict[str, int] = {
    "free": 0,
    "pro": 1,
    "premium": 2,
}

# -----------------------------------------------------------------
# Monthly analysis quota (0 = unlimited)
# -----------------------------------------------------------------
USAGE_QUOTAS: dict[str, int] = {
    "free": 3,
    "pro": 30,
    "premium": 0,
}

# -----------------------------------------------------------------
# Daily chat message limits (0 = unlimited)
# -----------------------------------------------------------------
DAILY_MSG_LIMITS: dict[str, int] = {
    "free": 10,
    "pro": 100,
    "premium": 0,
}

# -----------------------------------------------------------------
# Human-readable tier names
# -----------------------------------------------------------------
TIER_NAMES: dict[str, str] = {
    "free": "Free",
    "pro": "Pro",
    "premium": "Premium",
}

# -----------------------------------------------------------------
# Pricing (INR) — mirrors Razorpay plans
# -----------------------------------------------------------------
TIER_PRICE_INR: dict[str, int] = {
    "free": 0,
    "pro": 499,
    "premium": 1499,
}

# -----------------------------------------------------------------
# Pricing (USD) — mirrors Stripe prices
# -----------------------------------------------------------------
TIER_PRICE_USD: dict[str, int] = {
    "free": 0,
    "pro": 6,
    "premium": 18,
}

# -----------------------------------------------------------------
# Valid subscription statuses
# -----------------------------------------------------------------
VALID_STATUSES = (
    "active",
    "past_due",
    "cancelled",
    "expired",
)

# Default tier for new users
DEFAULT_TIER = "free"
DEFAULT_STATUS = "active"
