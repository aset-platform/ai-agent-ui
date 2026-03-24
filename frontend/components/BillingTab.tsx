"use client";
/**
 * Billing tab — pricing cards, usage meter, upgrade/cancel.
 *
 * Fetches subscription status from GET /v1/subscription and
 * integrates with Razorpay checkout.js for INR payments.
 */

import { useState, useEffect, useCallback } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { refreshAccessToken } from "@/lib/auth";
import { API_URL } from "@/lib/config";
import { ConfirmDialog } from "@/components/ConfirmDialog";

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */

interface SubscriptionInfo {
  tier: string;
  status: string;
  usage_count: number;
  usage_limit: number;
  usage_remaining: number | null;
  gateway: string | null;
}

declare global {
  interface Window {
    Razorpay?: new (opts: Record<string, unknown>) => {
      open: () => void;
      on: (event: string, cb: () => void) => void;
    };
  }
}

/* ------------------------------------------------------------------ */
/* Tier definitions                                                    */
/* ------------------------------------------------------------------ */

const TIERS = [
  {
    id: "free",
    name: "Free",
    priceINR: "Free forever",
    priceUSD: "Free forever",
    features: [
      "3 analyses / month",
      "10 chat messages / day",
      "3-month forecast horizon",
      "Basic stock data",
    ],
  },
  {
    id: "pro",
    name: "Pro",
    priceINR: "\u20B9499/mo",
    priceUSD: "$6/mo",
    features: [
      "30 analyses / month",
      "100 chat messages / day",
      "All forecast horizons",
      "Market news search",
      "Priority support",
    ],
  },
  {
    id: "premium",
    name: "Premium",
    priceINR: "\u20B91,499/mo",
    priceUSD: "$18/mo",
    features: [
      "Unlimited analyses",
      "Unlimited messages",
      "All features unlocked",
      "Early access to new tools",
      "Dedicated support",
    ],
  },
];

/* ------------------------------------------------------------------ */
/* Component                                                           */
/* ------------------------------------------------------------------ */

export function BillingTab() {
  const [sub, setSub] = useState<SubscriptionInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [upgradingTier, setUpgradingTier] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);
  const [gateway, setGateway] = useState<"razorpay" | "stripe">("razorpay");

  /* Fetch current subscription */
  const fetchSubscription = useCallback(async () => {
    try {
      const res = await apiFetch(`${API_URL}/subscription`);
      if (res.ok) {
        const data = await res.json();
        setSub(data);
        if (data.gateway) {
          setGateway(
            data.gateway === "stripe"
              ? "stripe"
              : "razorpay",
          );
        }
      }
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSubscription();
  }, [fetchSubscription]);

  /* Load Razorpay checkout.js dynamically */
  useEffect(() => {
    if (document.getElementById("razorpay-script")) return;
    const s = document.createElement("script");
    s.id = "razorpay-script";
    s.src = "https://checkout.razorpay.com/v1/checkout.js";
    s.async = true;
    document.head.appendChild(s);
  }, []);

  /* Upgrade handler */
  const handleUpgrade = async (tier: string) => {
    setError("");
    setSuccess("");
    setUpgradingTier(tier);
    try {
      const res = await apiFetch(`${API_URL}/subscription/checkout`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tier, gateway }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail ?? "Checkout failed");
        return;
      }
      const data = await res.json();

      // Server-side upgrade via PATCH — no modal needed
      if (data.upgraded) {
        setSuccess("Plan upgraded! Pro-rata billing applied.");
        await refreshAccessToken();
        fetchSubscription();
        return;
      }

      // Stripe → redirect to hosted checkout
      if (data.checkout_url) {
        window.location.href = data.checkout_url;
        return;
      }

      // Razorpay → open checkout modal
      if (!window.Razorpay) {
        setError("Razorpay SDK not loaded. Please refresh.");
        return;
      }

      const rzp = new window.Razorpay({
        key: data.key_id,
        subscription_id: data.subscription_id,
        name: "ASET Platform",
        description: `${tier.charAt(0).toUpperCase() + tier.slice(1)} Plan`,
        handler: async () => {
          setSuccess("Payment successful! Updating your plan...");
          await refreshAccessToken();
          setTimeout(() => fetchSubscription(), 3000);
        },
        modal: {
          ondismiss: () => setError(""),
        },
        theme: { color: "#4f46e5" },
      });
      rzp.open();
    } catch {
      setError("Network error — please try again.");
    } finally {
      setUpgradingTier(null);
    }
  };

  /* Cancel handler */
  const handleCancel = async () => {
    setCancelling(true);
    setError("");
    try {
      const res = await apiFetch(`${API_URL}/subscription/cancel`, {
        method: "POST",
      });
      if (res.ok) {
        setSuccess("Subscription cancelled.");
        await refreshAccessToken();
        fetchSubscription();
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data.detail ?? "Cancel failed");
      }
    } catch {
      setError("Network error.");
    } finally {
      setCancelling(false);
    }
  };

  /* ---------------------------------------------------------------- */
  /* Render                                                            */
  /* ---------------------------------------------------------------- */

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-indigo-600 border-t-transparent" />
      </div>
    );
  }

  const currentTier = sub?.tier ?? "free";
  const usageCount = sub?.usage_count ?? 0;
  const usageLimit = sub?.usage_limit ?? 3;
  const isUnlimited = usageLimit === 0;
  const usagePct = isUnlimited ? 0 : Math.min(100, (usageCount / usageLimit) * 100);
  const usageColor =
    usagePct < 50 ? "bg-emerald-500" : usagePct < 80 ? "bg-amber-500" : "bg-red-500";

  return (
    <div className="space-y-5">
      <ConfirmDialog
        open={showCancelConfirm}
        title="Cancel Subscription"
        message="Are you sure you want to cancel your subscription? Your plan will revert to Free immediately."
        confirmLabel="Cancel Subscription"
        cancelLabel="Keep Plan"
        variant="danger"
        onConfirm={() => {
          setShowCancelConfirm(false);
          handleCancel();
        }}
        onCancel={() => setShowCancelConfirm(false)}
      />

      {/* Status bar */}
      {error && (
        <div className="rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 px-3 py-2 text-sm text-red-700 dark:text-red-300">
          {error}
        </div>
      )}
      {success && (
        <div className="rounded-lg bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300">
          {success}
        </div>
      )}

      {/* Current plan + usage */}
      <div className="rounded-xl border border-gray-200 dark:border-gray-700 p-4">
        <div className="flex items-center justify-between mb-3">
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400">
              Current Plan
            </p>
            <p className="text-lg font-semibold text-gray-900 dark:text-gray-100 capitalize">
              {currentTier}
              {sub?.status === "cancelled" && (
                <span className="ml-2 text-xs font-normal text-amber-600 dark:text-amber-400">
                  (cancelled)
                </span>
              )}
              {sub?.status === "past_due" && (
                <span className="ml-2 text-xs font-normal text-red-600 dark:text-red-400">
                  (payment due)
                </span>
              )}
            </p>
          </div>
          {currentTier !== "free" && sub?.status !== "cancelled" && (
            <button
              onClick={() => setShowCancelConfirm(true)}
              disabled={cancelling}
              className="text-xs text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300 disabled:opacity-50"
            >
              {cancelling ? "Cancelling\u2026" : "Cancel plan"}
            </button>
          )}
        </div>

        {/* Usage meter */}
        <div>
          <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400 mb-1">
            <span>Monthly analyses</span>
            <span>
              {isUnlimited
                ? `${usageCount} used (unlimited)`
                : `${usageCount} / ${usageLimit} used`}
            </span>
          </div>
          {!isUnlimited && (
            <div className="h-2 rounded-full bg-gray-200 dark:bg-gray-700 overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${usageColor}`}
                style={{ width: `${usagePct}%` }}
              />
            </div>
          )}
          {isUnlimited && (
            <div className="h-2 rounded-full bg-emerald-200 dark:bg-emerald-800 overflow-hidden">
              <div className="h-full w-full rounded-full bg-emerald-500" />
            </div>
          )}
        </div>
      </div>

      {/* Pricing cards */}
      <div className="grid grid-cols-3 gap-3">
        {TIERS.map((t) => {
          const isCurrent = t.id === currentTier;
          const canSubscribe = t.id !== "free" && !isCurrent;

          return (
            <div
              key={t.id}
              className={`rounded-xl border p-3 flex flex-col transition-colors ${
                isCurrent
                  ? "border-indigo-500 bg-indigo-50/50 dark:bg-indigo-900/20 dark:border-indigo-500"
                  : "border-gray-200 dark:border-gray-700"
              }`}
            >
              <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                {t.name}
              </h3>
              <p className="text-lg font-bold text-gray-900 dark:text-gray-100 mt-1">
                {gateway === "stripe" ? t.priceUSD : t.priceINR}
              </p>
              <ul className="mt-2 space-y-1 flex-1">
                {t.features.map((f) => (
                  <li
                    key={f}
                    className="flex items-start gap-1.5 text-xs text-gray-600 dark:text-gray-400"
                  >
                    <svg
                      className="h-3.5 w-3.5 mt-0.5 shrink-0 text-emerald-500"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth={2.5}
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                    {f}
                  </li>
                ))}
              </ul>

              {isCurrent ? (
                <div className="mt-3 text-center text-xs font-medium text-indigo-600 dark:text-indigo-400 py-1.5 rounded-lg bg-indigo-100 dark:bg-indigo-900/40">
                  Current plan
                </div>
              ) : canSubscribe ? (
                <button
                  onClick={() => handleUpgrade(t.id)}
                  disabled={upgradingTier !== null}
                  className="mt-3 w-full text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 py-1.5 rounded-lg transition-colors"
                >
                  {upgradingTier === t.id ? "Processing\u2026" : currentTier === "free" ? "Subscribe" : "Upgrade"}
                </button>
              ) : (
                <div className="mt-3 h-8" />
              )}
            </div>
          );
        })}
      </div>

      {/* Gateway selector */}
      <div className="flex items-center justify-center gap-2">
        <span className="text-xs text-gray-500 dark:text-gray-400">Pay with:</span>
        <button
          onClick={() => setGateway("razorpay")}
          className={`text-xs px-3 py-1 rounded-full border transition-colors ${
            gateway === "razorpay"
              ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-400 font-medium"
              : "border-gray-200 dark:border-gray-700 text-gray-500 dark:text-gray-400 hover:border-gray-400"
          }`}
        >
          UPI / Card (INR)
        </button>
        <button
          onClick={() => setGateway("stripe")}
          className={`text-xs px-3 py-1 rounded-full border transition-colors ${
            gateway === "stripe"
              ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-400 font-medium"
              : "border-gray-200 dark:border-gray-700 text-gray-500 dark:text-gray-400 hover:border-gray-400"
          }`}
        >
          International Card (USD)
        </button>
      </div>

      {/* Payment info */}
      <p className="text-xs text-gray-400 dark:text-gray-500 text-center">
        {gateway === "stripe"
          ? "Payments processed securely via Stripe. Sandbox/test mode."
          : "Payments processed securely via Razorpay. Sandbox/test mode."}
      </p>
    </div>
  );
}
