"""Subscription management endpoints — checkout, status, cancel.

Supports Razorpay (INR / UPI AutoPay) as primary gateway.
Stripe support is added by ASETPLTFRM-79.

Endpoints
---------
- ``POST /subscription/checkout`` — create or upgrade sub
- ``GET  /subscription`` — current tier, usage, billing info
- ``POST /subscription/cancel`` — cancel active subscription
- ``POST /subscription/webhooks/razorpay`` — webhook
- ``POST /subscription/cleanup`` — admin: cancel orphans
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any, Dict

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from pydantic import BaseModel

import auth.endpoints.helpers as _helpers
from auth.dependencies import get_current_user, superuser_only
from auth.models import UserContext

_logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = ("active", "authenticated")


# ---------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------


class CheckoutRequest(BaseModel):
    """Checkout request body.

    Attributes:
        tier: Target tier (``"pro"`` or ``"premium"``).
        gateway: Payment gateway (``"razorpay"``).
    """

    tier: str  # "pro" or "premium"
    gateway: str = "razorpay"


class CheckoutResponse(BaseModel):
    """Razorpay checkout response.

    Attributes:
        subscription_id: Razorpay subscription ID.
        key_id: Razorpay public key for the checkout modal.
        plan_id: Razorpay plan ID.
        gateway: Always ``"razorpay"``.
        upgraded: True if existing sub was updated.
    """

    subscription_id: str
    key_id: str
    plan_id: str
    gateway: str = "razorpay"
    upgraded: bool = False


class SubscriptionStatus(BaseModel):
    """Current subscription status.

    Attributes:
        tier: Current tier name.
        status: Subscription status.
        usage_count: Analyses used this month.
        usage_limit: Monthly quota (0 = unlimited).
        usage_remaining: Analyses left (None = unlimited).
    """

    tier: str
    status: str
    usage_count: int
    usage_limit: int
    usage_remaining: int | None


# ---------------------------------------------------------------
# Razorpay client helper
# ---------------------------------------------------------------


def _get_razorpay_client():
    """Return a configured Razorpay client.

    Raises:
        HTTPException: 503 if keys are not configured.
    """
    from config import get_settings

    settings = get_settings()
    if not settings.razorpay_key_id:
        raise HTTPException(
            status_code=503,
            detail="Razorpay not configured",
        )
    import razorpay

    return razorpay.Client(
        auth=(
            settings.razorpay_key_id,
            settings.razorpay_key_secret,
        ),
    )


def _get_razorpay_plan_id(tier: str) -> str:
    """Resolve Razorpay plan ID for *tier*.

    Args:
        tier: ``"pro"`` or ``"premium"``.

    Returns:
        The Razorpay plan ID string.

    Raises:
        HTTPException: 400 if plan ID not configured.
    """
    import os

    key = f"RAZORPAY_PLAN_{tier.upper()}"
    plan_id = os.environ.get(key, "")
    if not plan_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Plan not configured for tier:"
                f" {tier}"
            ),
        )
    return plan_id


def _fetch_rz_sub_status(
    client: Any, sub_id: str,
) -> str | None:
    """Fetch Razorpay subscription status.

    Returns status string or None if not found.
    """
    try:
        sub = client.subscription.fetch(sub_id)
        return sub.get("status")
    except Exception:
        return None


# ---------------------------------------------------------------
# Webhook signature verification
# ---------------------------------------------------------------


def verify_razorpay_signature(
    body: bytes,
    signature: str,
    secret: str,
) -> bool:
    """Verify Razorpay webhook HMAC-SHA256 signature."""
    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------


def register(router: APIRouter) -> None:
    """Register subscription management routes.

    Args:
        router: The :class:`~fastapi.APIRouter` to attach
            routes to.
    """

    # -----------------------------------------------------------
    # POST /subscription/checkout
    # -----------------------------------------------------------

    @router.post(
        "/subscription/checkout",
        response_model=CheckoutResponse,
        tags=["subscription"],
    )
    def checkout(
        body: CheckoutRequest,
        user: UserContext = Depends(get_current_user),
    ) -> CheckoutResponse:
        """Create or upgrade a Razorpay subscription.

        If the user has an active subscription, updates
        it via PATCH (pro-rata). Otherwise creates a new
        one.
        """
        from subscription_config import TIER_ORDER

        if body.tier not in ("pro", "premium"):
            raise HTTPException(
                status_code=400,
                detail="Invalid tier. Use 'pro' or"
                " 'premium'.",
            )

        repo = _helpers._get_repo()
        db_user = repo.get_by_id(user.user_id)

        db_tier = (
            db_user.get("subscription_tier")
            if db_user
            else None
        ) or "free"
        db_status = (
            db_user.get("subscription_status")
            if db_user
            else None
        ) or "active"
        current_level = TIER_ORDER.get(db_tier, 0)
        target_level = TIER_ORDER.get(body.tier, 0)

        # Block same-or-lower tier only if active
        if (
            current_level >= target_level
            and db_status in _ACTIVE_STATUSES
        ):
            raise HTTPException(
                status_code=400,
                detail="Already on this tier or higher",
            )

        client = _get_razorpay_client()

        # Create or reuse Razorpay customer
        rz_cust_id = (
            db_user.get("razorpay_customer_id")
            if db_user
            else None
        )
        if not rz_cust_id:
            cust = client.customer.create({
                "name": (
                    db_user.get("full_name", "")
                    if db_user
                    else ""
                ),
                "email": user.email,
            })
            rz_cust_id = cust["id"]
            repo.update(
                user.user_id,
                {"razorpay_customer_id": rz_cust_id},
            )

        plan_id = _get_razorpay_plan_id(body.tier)

        # Try to upgrade existing subscription
        rz_sub_id = (
            db_user.get("razorpay_subscription_id")
            if db_user
            else None
        )
        upgraded = False

        if rz_sub_id:
            rz_status = _fetch_rz_sub_status(
                client, rz_sub_id,
            )
            if rz_status in _ACTIVE_STATUSES:
                # PATCH existing subscription
                try:
                    client.subscription.edit(
                        rz_sub_id,
                        {
                            "plan_id": plan_id,
                            "schedule_change_at": "now",
                            "customer_notify": 1,
                        },
                    )
                    upgraded = True
                    _logger.info(
                        "Subscription upgraded:"
                        " user_id=%s sub=%s"
                        " new_plan=%s",
                        user.user_id,
                        rz_sub_id,
                        plan_id,
                    )
                    # Update tier immediately
                    _safe_update(
                        repo,
                        user.user_id,
                        {
                            "subscription_tier": body.tier,
                            "subscription_status": "active",
                        },
                    )
                except Exception:
                    _logger.warning(
                        "PATCH sub failed, creating"
                        " new: sub=%s",
                        rz_sub_id,
                    )
                    rz_sub_id = None

        if not upgraded:
            # Cancel any stale subscription first
            if rz_sub_id:
                try:
                    client.subscription.cancel(
                        rz_sub_id,
                    )
                except Exception:
                    pass

            # Create fresh subscription
            sub = client.subscription.create({
                "plan_id": plan_id,
                "customer_id": rz_cust_id,
                "total_count": 12,
                "quantity": 1,
            })
            rz_sub_id = sub["id"]
            repo.update(
                user.user_id,
                {
                    "razorpay_subscription_id": (
                        rz_sub_id
                    ),
                },
            )

        from config import get_settings
        settings = get_settings()

        _logger.info(
            "Checkout: user=%s tier=%s sub=%s"
            " upgraded=%s",
            user.user_id,
            body.tier,
            rz_sub_id,
            upgraded,
        )
        return CheckoutResponse(
            subscription_id=rz_sub_id,
            key_id=settings.razorpay_key_id,
            plan_id=plan_id,
            upgraded=upgraded,
        )

    # -----------------------------------------------------------
    # GET /subscription
    # -----------------------------------------------------------

    @router.get(
        "/subscription",
        response_model=SubscriptionStatus,
        tags=["subscription"],
    )
    def get_subscription(
        user: UserContext = Depends(get_current_user),
    ) -> SubscriptionStatus:
        """Return current subscription tier and usage."""
        from subscription_config import (
            DEFAULT_STATUS,
            DEFAULT_TIER,
            USAGE_QUOTAS,
        )

        repo = _helpers._get_repo()
        db_user = repo.get_by_id(user.user_id)

        tier = (
            db_user.get("subscription_tier")
            or DEFAULT_TIER
        ) if db_user else DEFAULT_TIER
        status = (
            db_user.get("subscription_status")
            or DEFAULT_STATUS
        ) if db_user else DEFAULT_STATUS
        count = (
            db_user.get("monthly_usage_count", 0)
            if db_user
            else 0
        ) or 0

        # Cancelled = effectively free
        effective_tier = (
            "free" if status == "cancelled" else tier
        )
        quota = USAGE_QUOTAS.get(effective_tier, 3)
        remaining = (
            None if quota == 0
            else max(0, quota - count)
        )

        return SubscriptionStatus(
            tier=effective_tier,
            status=status,
            usage_count=count,
            usage_limit=quota,
            usage_remaining=remaining,
        )

    # -----------------------------------------------------------
    # POST /subscription/cancel
    # -----------------------------------------------------------

    @router.post(
        "/subscription/cancel",
        tags=["subscription"],
    )
    def cancel_subscription(
        user: UserContext = Depends(get_current_user),
    ) -> Dict[str, str]:
        """Cancel the user's active subscription."""
        repo = _helpers._get_repo()
        db_user = repo.get_by_id(user.user_id)
        db_tier = (
            db_user.get("subscription_tier")
            if db_user
            else None
        ) or "free"
        db_status = (
            db_user.get("subscription_status")
            if db_user
            else None
        ) or "active"

        if db_tier == "free" or db_status == "cancelled":
            raise HTTPException(
                status_code=400,
                detail="No active subscription",
            )

        rz_sub_id = (
            db_user.get("razorpay_subscription_id")
            if db_user
            else None
        )

        if rz_sub_id:
            try:
                client = _get_razorpay_client()
                client.subscription.cancel(rz_sub_id)
            except Exception:
                _logger.exception(
                    "Razorpay cancel failed: sub=%s",
                    rz_sub_id,
                )

        # Reset to free + clear sub ID
        _safe_update(
            repo,
            user.user_id,
            {
                "subscription_tier": "free",
                "subscription_status": "cancelled",
                "razorpay_subscription_id": None,
            },
        )

        _logger.info(
            "Subscription cancelled: user_id=%s",
            user.user_id,
        )
        return {"detail": "Subscription cancelled"}

    # -----------------------------------------------------------
    # POST /subscription/cleanup (admin only)
    # -----------------------------------------------------------

    @router.post(
        "/subscription/cleanup",
        tags=["subscription"],
    )
    def cleanup_subscriptions(
        dry_run: bool = True,
        user: UserContext = Depends(superuser_only),
    ) -> Dict[str, Any]:
        """Triage and clean orphaned Razorpay subs.

        Classifications:
        - **matched**: sub_id matches a user's current
        - **orphaned**: same customer but wrong sub_id
        - **unlinked**: no user found for customer

        Only orphaned subs are cancelled on execute.
        """
        repo = _helpers._get_repo()
        all_users = repo.list_all()
        client = _get_razorpay_client()

        # Build lookup maps
        sub_to_user: Dict[str, str] = {}
        cust_to_user: Dict[str, str] = {}
        for u in all_users:
            sid = u.get("razorpay_subscription_id")
            cid = u.get("razorpay_customer_id")
            uid = u.get("user_id", "")
            if sid:
                sub_to_user[sid] = uid
            if cid:
                cust_to_user[cid] = uid

        # Fetch all Razorpay subscriptions
        triage: list[Dict[str, str]] = []
        cleaned = 0
        try:
            subs = client.subscription.all(
                {"count": 100},
            )
        except Exception:
            _logger.exception(
                "Failed to fetch Razorpay subs",
            )
            return {
                "triage": [],
                "cleaned": 0,
                "dry_run": dry_run,
                "error": "Failed to fetch subs",
            }

        for s in subs.get("items", []):
            sid = s.get("id", "")
            cid = s.get("customer_id", "")
            status = s.get("status", "")

            if status not in _ACTIVE_STATUSES:
                continue

            if sid in sub_to_user:
                triage.append({
                    "sub_id": sid,
                    "customer_id": cid,
                    "status": status,
                    "classification": "matched",
                    "action": "keep",
                })
            elif cid in cust_to_user:
                triage.append({
                    "sub_id": sid,
                    "customer_id": cid,
                    "status": status,
                    "classification": "orphaned",
                    "action": (
                        "will_cancel"
                        if dry_run
                        else "cancelled"
                    ),
                })
                if not dry_run:
                    try:
                        client.subscription.cancel(
                            sid,
                        )
                        cleaned += 1
                        _logger.info(
                            "Cancelled orphan"
                            " sub=%s",
                            sid,
                        )
                    except Exception:
                        triage[-1]["action"] = (
                            "cancel_failed"
                        )
            else:
                triage.append({
                    "sub_id": sid,
                    "customer_id": cid,
                    "status": status,
                    "classification": "unlinked",
                    "action": "manual_review",
                })

        return {
            "triage": triage,
            "cleaned": cleaned,
            "dry_run": dry_run,
        }

    # -----------------------------------------------------------
    # Razorpay webhook handler
    # -----------------------------------------------------------

    @router.post(
        "/subscription/webhooks/razorpay",
        tags=["webhooks"],
    )
    @router.post(
        "/webhooks/razorpay",
        tags=["webhooks"],
        include_in_schema=False,
    )
    async def razorpay_webhook(
        request: Request,
    ) -> Dict[str, str]:
        """Handle Razorpay webhook events."""
        from config import get_settings

        settings = get_settings()
        body = await request.body()
        signature = request.headers.get(
            "X-Razorpay-Signature", "",
        )

        secret = settings.razorpay_webhook_secret
        if secret:
            if not verify_razorpay_signature(
                body, signature, secret,
            ):
                _logger.warning(
                    "Invalid Razorpay signature",
                )
                raise HTTPException(
                    status_code=400,
                    detail="Invalid webhook signature",
                )
        else:
            _logger.warning(
                "Webhook secret not configured"
                " — skipping signature check",
            )

        payload: Dict[str, Any] = json.loads(body)
        event = payload.get("event", "")
        entity = (
            payload.get("payload", {})
            .get("subscription", {})
            .get("entity", {})
        )
        payment_entity = (
            payload.get("payload", {})
            .get("payment", {})
            .get("entity", {})
        )

        _logger.info(
            "Razorpay webhook: event=%s", event,
        )

        if event == "subscription.charged":
            _handle_charged(entity)
        elif event == "subscription.cancelled":
            _handle_cancelled(entity)
        elif event == "payment.failed":
            _handle_payment_failed(
                payment_entity, entity,
            )

        return {"status": "ok"}


# ---------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------


def _safe_update(
    repo: Any,
    user_id: str,
    updates: Dict[str, Any],
) -> None:
    """Update with retry on Iceberg commit conflict."""
    for attempt in range(3):
        try:
            repo.update(user_id, updates)
            return
        except Exception as exc:
            if (
                "CommitFailed" in type(exc).__name__
                and attempt < 2
            ):
                _logger.warning(
                    "Iceberg conflict, retry %d/3"
                    " user=%s",
                    attempt + 1,
                    user_id,
                )
                continue
            raise


# ---------------------------------------------------------------
# Webhook handlers
# ---------------------------------------------------------------


def _handle_charged(entity: Dict[str, Any]) -> None:
    """Process subscription.charged — activate tier.

    Only updates if the webhook sub_id matches the
    user's stored subscription or customer ID.
    """
    sub_id = entity.get("id", "")
    plan_id = entity.get("plan_id", "")
    cust_id = entity.get("customer_id", "")

    if not sub_id:
        return

    repo = _helpers._get_repo()
    user = _find_user_by_razorpay(repo, sub_id, cust_id)
    if user is None:
        _logger.warning(
            "Webhook charged: no user for sub=%s",
            sub_id,
        )
        return

    # Guard: ignore events for old/orphaned subs
    stored_sub = user.get("razorpay_subscription_id")
    if stored_sub and stored_sub != sub_id:
        _logger.info(
            "Ignoring charged for old sub=%s"
            " (current=%s)",
            sub_id,
            stored_sub,
        )
        return

    tier = _plan_id_to_tier(plan_id)
    _safe_update(
        repo,
        user["user_id"],
        {
            "subscription_tier": tier,
            "subscription_status": "active",
            "razorpay_subscription_id": sub_id,
        },
    )
    _logger.info(
        "Tier activated: user_id=%s tier=%s",
        user["user_id"],
        tier,
    )


def _handle_cancelled(entity: Dict[str, Any]) -> None:
    """Process subscription.cancelled — reset to free."""
    sub_id = entity.get("id", "")
    cust_id = entity.get("customer_id", "")

    if not sub_id:
        return

    repo = _helpers._get_repo()
    user = _find_user_by_razorpay(repo, sub_id, cust_id)
    if user is None:
        return

    # Only process if this is the current subscription
    stored_sub = user.get("razorpay_subscription_id")
    if stored_sub and stored_sub != sub_id:
        _logger.info(
            "Ignoring cancelled for old sub=%s",
            sub_id,
        )
        return

    _safe_update(
        repo,
        user["user_id"],
        {
            "subscription_tier": "free",
            "subscription_status": "cancelled",
            "razorpay_subscription_id": None,
        },
    )
    _logger.info(
        "Subscription cancelled via webhook:"
        " user_id=%s",
        user["user_id"],
    )


def _handle_payment_failed(
    payment: Dict[str, Any],
    sub_entity: Dict[str, Any],
) -> None:
    """Process payment.failed — mark past_due."""
    sub_id = sub_entity.get("id", "")
    cust_id = payment.get("customer_id", "")

    repo = _helpers._get_repo()
    user = _find_user_by_razorpay(
        repo, sub_id, cust_id,
    )
    if user is None:
        return

    _safe_update(
        repo,
        user["user_id"],
        {"subscription_status": "past_due"},
    )
    _logger.info(
        "Payment failed: user_id=%s marked past_due",
        user["user_id"],
    )


# ---------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------


def _find_user_by_razorpay(
    repo: Any,
    sub_id: str,
    cust_id: str,
) -> dict | None:
    """Find user by Razorpay IDs.

    Prioritises subscription_id match over customer_id
    to avoid confusion with orphaned subscriptions.
    """
    users = repo.list_all()

    # Pass 1: exact subscription_id match
    if sub_id:
        for u in users:
            if (
                u.get("razorpay_subscription_id")
                == sub_id
            ):
                return u

    # Pass 2: fallback to customer_id
    if cust_id:
        for u in users:
            if (
                u.get("razorpay_customer_id")
                == cust_id
            ):
                return u

    return None


def _plan_id_to_tier(plan_id: str) -> str:
    """Map Razorpay plan ID to tier name."""
    import os

    if plan_id == os.environ.get(
        "RAZORPAY_PLAN_PREMIUM",
    ):
        return "premium"
    return "pro"
