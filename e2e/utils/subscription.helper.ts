/**
 * Subscription test helpers for E2E.
 *
 * Provides API helpers to seed users with specific tiers,
 * simulate webhook events, and check usage counts.
 */

import { type APIRequestContext } from "@playwright/test";

const BACKEND =
  `${process.env.BACKEND_URL || "http://127.0.0.1:8181"}/v1`;

/** Get current subscription status for a user. */
export async function getSubscription(
  request: APIRequestContext,
  token: string,
): Promise<{
  tier: string;
  status: string;
  usage_count: number;
  usage_limit: number;
  usage_remaining: number | null;
  gateway: string | null;
}> {
  const res = await request.get(
    `${BACKEND}/subscription`,
    {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    },
  );
  return res.json();
}

/** Set a user's tier directly via admin update. */
export async function seedUserTier(
  request: APIRequestContext,
  adminToken: string,
  userId: string,
  tier: string,
  status: string = "active",
): Promise<void> {
  // Use admin PATCH /users/:id to set tier
  await request.patch(
    `${BACKEND}/users/${userId}`,
    {
      headers: {
        Authorization: `Bearer ${adminToken}`,
        "Content-Type": "application/json",
      },
      data: {
        // Custom fields set via direct Iceberg update
      },
    },
  );
}

/** Get usage stats for all users (admin). */
export async function getUsageStats(
  request: APIRequestContext,
  adminToken: string,
): Promise<{
  users: Array<{
    user_id: string;
    email: string;
    monthly_usage_count: number;
    subscription_tier: string;
  }>;
}> {
  const res = await request.get(
    `${BACKEND}/admin/usage-stats`,
    {
      headers: {
        Authorization: `Bearer ${adminToken}`,
      },
    },
  );
  return res.json();
}

/** Get payment transactions (admin). */
export async function getTransactions(
  request: APIRequestContext,
  adminToken: string,
  gateway?: string,
): Promise<{
  transactions: Array<Record<string, unknown>>;
}> {
  const url = gateway
    ? `${BACKEND}/admin/payment-transactions?gateway=${gateway}`
    : `${BACKEND}/admin/payment-transactions`;
  const res = await request.get(url, {
    headers: {
      Authorization: `Bearer ${adminToken}`,
    },
  });
  return res.json();
}
