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
from typing import Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from pydantic import BaseModel

import auth.endpoints.helpers as _helpers
from auth.dependencies import (
    get_current_user,
    superuser_only,
)
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
        gateway: Payment gateway
            (``"razorpay"`` or ``"stripe"``).
    """

    tier: Literal["pro", "premium"]
    gateway: Literal["razorpay", "stripe"] = "razorpay"


class CheckoutResponse(BaseModel):
    """Checkout response (supports both gateways).

    Attributes:
        gateway: ``"razorpay"`` or ``"stripe"``.
        upgraded: True if existing sub was PATCHed.
        subscription_id: Razorpay subscription ID.
        key_id: Razorpay public key.
        plan_id: Razorpay plan ID.
        checkout_url: Stripe hosted checkout URL.
    """

    gateway: str = "razorpay"
    upgraded: bool = False
    # Razorpay fields
    subscription_id: str | None = None
    key_id: str | None = None
    plan_id: str | None = None
    # Stripe fields
    checkout_url: str | None = None


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
    gateway: str | None = None


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
            detail=(f"Plan not configured for tier:" f" {tier}"),
        )
    return plan_id


def _fetch_rz_sub_status(
    client: Any,
    sub_id: str,
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
# Stripe helpers
# ---------------------------------------------------------------


def _get_stripe_client():
    """Return configured Stripe module.

    Raises:
        HTTPException: 503 if keys not configured.
    """
    from config import get_settings

    settings = get_settings()
    if not settings.stripe_secret_key:
        raise HTTPException(
            status_code=503,
            detail="Stripe not configured",
        )
    import stripe

    stripe.api_key = settings.stripe_secret_key
    return stripe


def _get_stripe_price_id(tier: str) -> str:
    """Resolve Stripe price ID for *tier*."""
    import os

    key = f"STRIPE_PRICE_{tier.upper()}"
    price_id = os.environ.get(key, "")
    if not price_id:
        raise HTTPException(
            status_code=400,
            detail=(f"Stripe price not configured" f" for tier: {tier}"),
        )
    return price_id


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
# Transaction ledger
# ---------------------------------------------------------------


def _log_transaction(
    user_id: str,
    gateway: str,
    event_type: str,
    gateway_event_id: str | None = None,
    subscription_id: str | None = None,
    customer_id: str | None = None,
    amount: float | None = None,
    currency: str | None = None,
    tier_before: str | None = None,
    tier_after: str | None = None,
    status: str = "success",
    raw_payload: str | None = None,
) -> None:
    """Append a payment event to the ledger.

    Fire-and-forget — errors logged, never raised.
    """
    try:
        import uuid
        from datetime import datetime, timezone

        import pyarrow as pa

        from auth.repo.schemas import (
            _PAYMENT_TXN_PA_SCHEMA,
            _PAYMENT_TXN_TABLE,
        )

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        repo = _helpers._get_repo()
        cat = repo._get_catalog()
        tbl = cat.load_table(_PAYMENT_TXN_TABLE)
        row = pa.table(
            {
                "transaction_id": [str(uuid.uuid4())],
                "user_id": [user_id],
                "gateway": [gateway],
                "event_type": [event_type],
                "gateway_event_id": [gateway_event_id],
                "subscription_id": [subscription_id],
                "customer_id": [customer_id],
                "amount": [amount],
                "currency": [currency],
                "tier_before": [tier_before],
                "tier_after": [tier_after],
                "status": [status],
                "raw_payload": [raw_payload],
                "created_at": [now],
            },
            schema=_PAYMENT_TXN_PA_SCHEMA,
        )
        tbl.append(row)
        _logger.info(
            "Transaction logged: user=%s gw=%s" " event=%s status=%s",
            user_id,
            gateway,
            event_type,
            status,
        )
    except Exception:
        _logger.exception(
            "Failed to log transaction for user=%s",
            user_id,
        )


# ---------------------------------------------------------------
# Gateway-specific checkout helpers
# ---------------------------------------------------------------


def _checkout_razorpay(
    repo: Any,
    db_user: dict | None,
    user: UserContext,
    tier: str,
) -> CheckoutResponse:
    """Razorpay checkout — modal-based."""
    client = _get_razorpay_client()

    rz_cust_id = db_user.get("razorpay_customer_id") if db_user else None
    if not rz_cust_id:
        cust = client.customer.create(
            {
                "name": (db_user.get("full_name", "") if db_user else ""),
                "email": user.email,
            }
        )
        rz_cust_id = cust["id"]
        repo.update(
            user.user_id,
            {"razorpay_customer_id": rz_cust_id},
        )

    plan_id = _get_razorpay_plan_id(tier)

    rz_sub_id = db_user.get("razorpay_subscription_id") if db_user else None
    upgraded = False

    if rz_sub_id:
        rz_status = _fetch_rz_sub_status(
            client,
            rz_sub_id,
        )
        if rz_status in _ACTIVE_STATUSES:
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
                old_tier = (
                    db_user.get("subscription_tier") if db_user else None
                ) or "free"
                _safe_update(
                    repo,
                    user.user_id,
                    {
                        "subscription_tier": tier,
                        "subscription_status": "active",
                    },
                )
                from subscription_config import (
                    TIER_PRICE_INR,
                )

                _log_transaction(
                    user_id=user.user_id,
                    gateway="razorpay",
                    event_type="upgrade",
                    subscription_id=rz_sub_id,
                    amount=float(TIER_PRICE_INR.get(tier, 0)),
                    tier_before=old_tier,
                    tier_after=tier,
                    currency="INR",
                )
            except Exception:
                rz_sub_id = None

    if not upgraded:
        if rz_sub_id:
            try:
                client.subscription.cancel(rz_sub_id)
            except Exception:
                pass
        sub = client.subscription.create(
            {
                "plan_id": plan_id,
                "customer_id": rz_cust_id,
                "total_count": 12,
                "quantity": 1,
            }
        )
        rz_sub_id = sub["id"]
        repo.update(
            user.user_id,
            {"razorpay_subscription_id": rz_sub_id},
        )

    from config import get_settings

    settings = get_settings()

    _logger.info(
        "Razorpay checkout: user=%s tier=%s" " sub=%s upgraded=%s",
        user.user_id,
        tier,
        rz_sub_id,
        upgraded,
    )
    return CheckoutResponse(
        gateway="razorpay",
        subscription_id=rz_sub_id,
        key_id=settings.razorpay_key_id,
        plan_id=plan_id,
        upgraded=upgraded,
    )


def _checkout_stripe(
    repo: Any,
    db_user: dict | None,
    user: UserContext,
    tier: str,
) -> CheckoutResponse:
    """Stripe checkout — redirect to hosted page."""
    stripe = _get_stripe_client()
    from config import get_settings

    settings = get_settings()

    # Create or reuse Stripe customer
    st_cust_id = db_user.get("stripe_customer_id") if db_user else None
    if not st_cust_id:
        cust = stripe.Customer.create(
            email=user.email,
            name=(db_user.get("full_name", "") if db_user else ""),
        )
        st_cust_id = cust.id
        repo.update(
            user.user_id,
            {"stripe_customer_id": st_cust_id},
        )

    price_id = _get_stripe_price_id(tier)

    # Try to upgrade existing Stripe subscription
    st_sub_id = db_user.get("stripe_subscription_id") if db_user else None
    if st_sub_id:
        try:
            existing = stripe.Subscription.retrieve(
                st_sub_id,
            )
            if existing.status in ("active", "trialing"):
                # Modify subscription — Stripe handles
                # pro-rata automatically
                stripe.Subscription.modify(
                    st_sub_id,
                    items=[
                        {
                            "id": existing["items"]["data"][0]["id"],
                            "price": price_id,
                        }
                    ],
                    proration_behavior="create_prorations",
                )
                _safe_update(
                    repo,
                    user.user_id,
                    {
                        "subscription_tier": tier,
                        "subscription_status": "active",
                    },
                )
                old_tier = (
                    db_user.get("subscription_tier") if db_user else None
                ) or "free"
                from subscription_config import (
                    TIER_PRICE_USD,
                )

                _log_transaction(
                    user_id=user.user_id,
                    gateway="stripe",
                    event_type="upgrade",
                    subscription_id=st_sub_id,
                    customer_id=st_cust_id,
                    amount=float(
                        TIER_PRICE_USD.get(tier, 0),
                    ),
                    tier_before=old_tier,
                    tier_after=tier,
                    currency="USD",
                )
                _logger.info(
                    "Stripe upgrade: user=%s" " tier=%s sub=%s",
                    user.user_id,
                    tier,
                    st_sub_id,
                )
                return CheckoutResponse(
                    gateway="stripe",
                    upgraded=True,
                )
        except Exception:
            _logger.warning(
                "Stripe modify failed, creating" " new session: sub=%s",
                st_sub_id,
            )

    # Create Stripe Checkout Session (new sub)
    session = stripe.checkout.Session.create(
        customer=st_cust_id,
        mode="subscription",
        line_items=[
            {"price": price_id, "quantity": 1},
        ],
        success_url=(
            f"{settings.oauth_redirect_uri}" "/../dashboard?billing=success"
        ).replace("/auth/oauth/callback/../", "/"),
        cancel_url=(
            f"{settings.oauth_redirect_uri}" "/../dashboard?billing=cancelled"
        ).replace("/auth/oauth/callback/../", "/"),
        metadata={
            "user_id": user.user_id,
            "tier": tier,
        },
    )

    _logger.info(
        "Stripe checkout: user=%s tier=%s" " session=%s",
        user.user_id,
        tier,
        session.id,
    )
    return CheckoutResponse(
        gateway="stripe",
        checkout_url=session.url,
    )


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

        repo = _helpers._get_repo()
        db_user = repo.get_by_id(user.user_id)

        db_tier = (
            db_user.get("subscription_tier") if db_user else None
        ) or "free"
        db_status = (
            db_user.get("subscription_status") if db_user else None
        ) or "active"
        current_level = TIER_ORDER.get(db_tier, 0)
        target_level = TIER_ORDER.get(body.tier, 0)

        # Block same-or-lower tier only if active
        if current_level >= target_level and db_status in _ACTIVE_STATUSES:
            raise HTTPException(
                status_code=400,
                detail="Already on this tier or higher",
            )

        # ── Stripe path ──────────────────────
        if body.gateway == "stripe":
            return _checkout_stripe(
                repo,
                db_user,
                user,
                body.tier,
            )

        # ── Razorpay path (default) ─────────
        return _checkout_razorpay(
            repo,
            db_user,
            user,
            body.tier,
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
            (db_user.get("subscription_tier") or DEFAULT_TIER)
            if db_user
            else DEFAULT_TIER
        )
        status = (
            (db_user.get("subscription_status") or DEFAULT_STATUS)
            if db_user
            else DEFAULT_STATUS
        )
        count = (db_user.get("monthly_usage_count", 0) if db_user else 0) or 0

        # Cancelled = effectively free
        effective_tier = "free" if status == "cancelled" else tier
        quota = USAGE_QUOTAS.get(effective_tier, 3)
        remaining = None if quota == 0 else max(0, quota - count)

        # Detect which gateway is active
        active_gw = None
        if db_user:
            if db_user.get("stripe_subscription_id"):
                active_gw = "stripe"
            elif db_user.get(
                "razorpay_subscription_id",
            ):
                active_gw = "razorpay"

        return SubscriptionStatus(
            tier=effective_tier,
            status=status,
            usage_count=count,
            usage_limit=quota,
            usage_remaining=remaining,
            gateway=active_gw,
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
    ) -> dict[str, str]:
        """Cancel the user's active subscription."""
        repo = _helpers._get_repo()
        db_user = repo.get_by_id(user.user_id)
        db_tier = (
            db_user.get("subscription_tier") if db_user else None
        ) or "free"
        db_status = (
            db_user.get("subscription_status") if db_user else None
        ) or "active"

        if db_tier == "free" or db_status == "cancelled":
            raise HTTPException(
                status_code=400,
                detail="No active subscription",
            )

        # Cancel in Razorpay if active
        rz_sub_id = (
            db_user.get("razorpay_subscription_id") if db_user else None
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

        # Cancel in Stripe if active
        st_sub_id = db_user.get("stripe_subscription_id") if db_user else None
        if st_sub_id:
            try:
                stripe = _get_stripe_client()
                stripe.Subscription.cancel(st_sub_id)
            except Exception:
                _logger.exception(
                    "Stripe cancel failed: sub=%s",
                    st_sub_id,
                )

        # Reset to free + clear both sub IDs
        _safe_update(
            repo,
            user.user_id,
            {
                "subscription_tier": "free",
                "subscription_status": "cancelled",
                "razorpay_subscription_id": None,
                "stripe_subscription_id": None,
            },
        )

        gw = "stripe" if st_sub_id else "razorpay" if rz_sub_id else "unknown"
        _log_transaction(
            user_id=user.user_id,
            gateway=gw,
            event_type="user_cancelled",
            subscription_id=st_sub_id or rz_sub_id,
            tier_before=db_tier,
            tier_after="free",
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
    ) -> dict[str, Any]:
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
        sub_to_user: dict[str, str] = {}
        cust_to_user: dict[str, str] = {}
        for u in all_users:
            sid = u.get("razorpay_subscription_id")
            cid = u.get("razorpay_customer_id")
            uid = u.get("user_id", "")
            if sid:
                sub_to_user[sid] = uid
            if cid:
                cust_to_user[cid] = uid

        # Fetch all Razorpay subscriptions
        triage: list[dict[str, str]] = []
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
                triage.append(
                    {
                        "sub_id": sid,
                        "customer_id": cid,
                        "status": status,
                        "classification": "matched",
                        "action": "keep",
                    }
                )
            elif cid in cust_to_user:
                triage.append(
                    {
                        "sub_id": sid,
                        "customer_id": cid,
                        "status": status,
                        "classification": "orphaned",
                        "action": ("will_cancel" if dry_run else "cancelled"),
                    }
                )
                if not dry_run:
                    try:
                        client.subscription.cancel(
                            sid,
                        )
                        cleaned += 1
                        _logger.info(
                            "Cancelled orphan" " sub=%s",
                            sid,
                        )
                    except Exception:
                        triage[-1]["action"] = "cancel_failed"
            else:
                triage.append(
                    {
                        "sub_id": sid,
                        "customer_id": cid,
                        "status": status,
                        "classification": "unlinked",
                        "action": "manual_review",
                    }
                )

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
    ) -> dict[str, str]:
        """Handle Razorpay webhook events."""
        from config import get_settings

        settings = get_settings()
        body = await request.body()
        signature = request.headers.get(
            "X-Razorpay-Signature",
            "",
        )

        secret = settings.razorpay_webhook_secret
        if not secret:
            _logger.error(
                "RAZORPAY_WEBHOOK_SECRET not set" " — rejecting webhook",
            )
            raise HTTPException(
                status_code=503,
                detail="Webhook not configured",
            )
        if not verify_razorpay_signature(
            body,
            signature,
            secret,
        ):
            _logger.warning(
                "Invalid Razorpay signature",
            )
            raise HTTPException(
                status_code=400,
                detail="Invalid webhook signature",
            )

        payload: dict[str, Any] = json.loads(body)
        event = payload.get("event", "")
        entity = (
            payload.get("payload", {})
            .get("subscription", {})
            .get("entity", {})
        )
        payment_entity = (
            payload.get("payload", {}).get("payment", {}).get("entity", {})
        )

        _logger.info(
            "Razorpay webhook: event=%s",
            event,
        )

        if event == "subscription.charged":
            _handle_charged(entity)
        elif event == "subscription.cancelled":
            _handle_cancelled(entity)
        elif event == "payment.failed":
            _handle_payment_failed(
                payment_entity,
                entity,
            )

        return {"status": "ok"}

    # -----------------------------------------------------------
    # Stripe webhook handler
    # -----------------------------------------------------------

    @router.post(
        "/subscription/webhooks/stripe",
        tags=["webhooks"],
    )
    async def stripe_webhook(
        request: Request,
    ) -> dict[str, str]:
        """Handle Stripe webhook events."""
        stripe = _get_stripe_client()
        from config import get_settings

        settings = get_settings()
        body = await request.body()
        sig = request.headers.get(
            "Stripe-Signature",
            "",
        )

        # Verify signature
        secret = settings.stripe_webhook_secret
        if not secret:
            _logger.error(
                "STRIPE_WEBHOOK_SECRET not set" " — rejecting webhook",
            )
            raise HTTPException(
                status_code=503,
                detail="Webhook not configured",
            )
        try:
            event = stripe.Webhook.construct_event(
                body,
                sig,
                secret,
            )
        except Exception as exc:
            _logger.warning(
                "Stripe signature failed: %s",
                exc,
            )
            raise HTTPException(
                status_code=400,
                detail="Invalid Stripe signature",
            )

        event_type = event.get(
            "type",
            event.get("event", ""),
        )
        data = event.get("data", {}).get("object", {})

        _logger.info(
            "Stripe webhook: type=%s",
            event_type,
        )

        if event_type == "checkout.session.completed":
            _handle_stripe_checkout(data)
        elif event_type in (
            "customer.subscription.deleted",
            "customer.subscription.updated",
        ):
            _handle_stripe_sub_change(data)
        elif event_type == "invoice.payment_failed":
            _handle_stripe_payment_failed(data)

        return {"status": "ok"}


def _handle_stripe_checkout(
    session: dict[str, Any],
) -> None:
    """Process checkout.session.completed."""
    cust_id = session.get("customer", "")
    sub_id = session.get("subscription", "")
    meta = session.get("metadata", {})
    user_id = meta.get("user_id", "")
    tier = meta.get("tier", "")

    if not user_id:
        _logger.warning(
            "Stripe checkout: no user_id in metadata",
        )
        return

    if tier not in ("pro", "premium"):
        _logger.error(
            "Stripe: invalid tier in metadata: %s",
            tier,
        )
        return

    repo = _helpers._get_repo()
    old_user = repo.get_by_id(user_id)
    old_tier = (
        old_user.get("subscription_tier") if old_user else None
    ) or "free"
    _safe_update(
        repo,
        user_id,
        {
            "subscription_tier": tier,
            "subscription_status": "active",
            "stripe_customer_id": cust_id,
            "stripe_subscription_id": sub_id,
        },
    )
    # amount_total is in cents
    amt_cents = session.get("amount_total")
    amt = float(amt_cents) / 100 if amt_cents else None

    _log_transaction(
        user_id=user_id,
        gateway="stripe",
        event_type="checkout_completed",
        subscription_id=sub_id,
        customer_id=cust_id,
        amount=amt,
        tier_before=old_tier,
        tier_after=tier,
        currency=(session.get("currency", "usd").upper()),
        raw_payload=json.dumps(session),
    )
    _logger.info(
        "Stripe tier activated: user=%s tier=%s",
        user_id,
        tier,
    )


def _handle_stripe_sub_change(
    sub: dict[str, Any],
) -> None:
    """Process subscription.deleted/updated."""
    status = sub.get("status", "")
    cust_id = sub.get("customer", "")
    sub_id = sub.get("id", "")

    repo = _helpers._get_repo()
    user = _find_user_by_stripe(repo, sub_id, cust_id)
    if user is None:
        return

    if status in ("canceled", "unpaid"):
        _safe_update(
            repo,
            user["user_id"],
            {
                "subscription_tier": "free",
                "subscription_status": "cancelled",
                "stripe_subscription_id": None,
            },
        )
        _log_transaction(
            user_id=user["user_id"],
            gateway="stripe",
            event_type="cancelled",
            subscription_id=sub.get("id"),
            customer_id=cust_id,
            tier_before=(user.get("subscription_tier") or "free"),
            tier_after="free",
            raw_payload=json.dumps(sub),
        )
        _logger.info(
            "Stripe sub cancelled: user=%s",
            user["user_id"],
        )


def _handle_stripe_payment_failed(
    invoice: dict[str, Any],
) -> None:
    """Process invoice.payment_failed."""
    cust_id = invoice.get("customer", "")
    sub_id = invoice.get("subscription", "")

    repo = _helpers._get_repo()
    user = _find_user_by_stripe(repo, sub_id, cust_id)
    if user is None:
        return

    _safe_update(
        repo,
        user["user_id"],
        {"subscription_status": "past_due"},
    )
    _log_transaction(
        user_id=user["user_id"],
        gateway="stripe",
        event_type="payment_failed",
        subscription_id=sub_id,
        customer_id=cust_id,
        status="failed",
    )
    _logger.info(
        "Stripe payment failed: user=%s",
        user["user_id"],
    )


def _find_user_by_stripe(
    repo: Any,
    sub_id: str,
    cust_id: str,
) -> dict | None:
    """Find user by Stripe IDs (sub_id first)."""
    users = repo.list_all()
    if sub_id:
        match = next(
            (u for u in users if u.get("stripe_subscription_id") == sub_id),
            None,
        )
        if match:
            return match
    if cust_id:
        return next(
            (u for u in users if u.get("stripe_customer_id") == cust_id),
            None,
        )
    return None


# ---------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------


def _safe_update(
    repo: Any,
    user_id: str,
    updates: dict[str, Any],
) -> None:
    """Update with retry on Iceberg commit conflict."""
    for attempt in range(3):
        try:
            repo.update(user_id, updates)
            return
        except Exception as exc:
            if "CommitFailed" in type(exc).__name__ and attempt < 2:
                _logger.warning(
                    "Iceberg conflict, retry %d/3" " user=%s",
                    attempt + 1,
                    user_id,
                )
                continue
            raise


# ---------------------------------------------------------------
# Webhook handlers
# ---------------------------------------------------------------


def _handle_charged(entity: dict[str, Any]) -> None:
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
            "Ignoring charged for old sub=%s" " (current=%s)",
            sub_id,
            stored_sub,
        )
        return

    tier = _plan_id_to_tier(plan_id)
    if tier is None:
        return
    _safe_update(
        repo,
        user["user_id"],
        {
            "subscription_tier": tier,
            "subscription_status": "active",
            "razorpay_subscription_id": sub_id,
        },
    )
    from subscription_config import TIER_PRICE_INR

    _log_transaction(
        user_id=user["user_id"],
        gateway="razorpay",
        event_type="charged",
        subscription_id=sub_id,
        customer_id=cust_id,
        amount=float(TIER_PRICE_INR.get(tier, 0)),
        tier_before=(user.get("subscription_tier") or "free"),
        tier_after=tier,
        currency="INR",
        raw_payload=json.dumps(entity),
    )
    _logger.info(
        "Tier activated: user_id=%s tier=%s",
        user["user_id"],
        tier,
    )


def _handle_cancelled(entity: dict[str, Any]) -> None:
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
    _log_transaction(
        user_id=user["user_id"],
        gateway="razorpay",
        event_type="cancelled",
        subscription_id=sub_id,
        customer_id=cust_id,
        tier_before=(user.get("subscription_tier") or "free"),
        tier_after="free",
        raw_payload=json.dumps(entity),
    )
    _logger.info(
        "Subscription cancelled via webhook:" " user_id=%s",
        user["user_id"],
    )


def _handle_payment_failed(
    payment: dict[str, Any],
    sub_entity: dict[str, Any],
) -> None:
    """Process payment.failed — mark past_due."""
    sub_id = sub_entity.get("id", "")
    cust_id = payment.get("customer_id", "")

    repo = _helpers._get_repo()
    user = _find_user_by_razorpay(
        repo,
        sub_id,
        cust_id,
    )
    if user is None:
        return

    _safe_update(
        repo,
        user["user_id"],
        {"subscription_status": "past_due"},
    )
    _log_transaction(
        user_id=user["user_id"],
        gateway="razorpay",
        event_type="payment_failed",
        subscription_id=sub_id,
        customer_id=cust_id,
        status="failed",
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
    if sub_id:
        match = next(
            (u for u in users if u.get("razorpay_subscription_id") == sub_id),
            None,
        )
        if match:
            return match
    if cust_id:
        return next(
            (u for u in users if u.get("razorpay_customer_id") == cust_id),
            None,
        )
    return None


def _plan_id_to_tier(plan_id: str) -> str | None:
    """Map Razorpay plan ID to tier name.

    Returns:
        ``"pro"``, ``"premium"``, or ``None`` if unknown.
    """
    import os

    if plan_id == os.environ.get(
        "RAZORPAY_PLAN_PREMIUM",
    ):
        return "premium"
    if plan_id == os.environ.get(
        "RAZORPAY_PLAN_PRO",
    ):
        return "pro"
    _logger.error(
        "Unknown Razorpay plan_id: %s",
        plan_id,
    )
    return None
