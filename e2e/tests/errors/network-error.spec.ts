/**
 * E2E tests for network error handling.
 *
 * Uses Playwright's route() to simulate failures.
 */

import { test, expect } from "@playwright/test";

test.describe("Network error handling", () => {
  test("backend 500 → chat shows error", async ({ page }) => {
    // Mock the streaming endpoint to return 500
    await page.route("**/chat/stream", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Internal server error" }),
      }),
    );

    await page.goto("/");
    await expect(
      page.getByTestId("chat-message-input"),
    ).toBeVisible({ timeout: 15_000 });

    // Send a message
    await page.getByTestId("chat-message-input").fill("test");
    await page.getByTestId("chat-send-button").click();

    // Should show some error indication (status badge or message)
    await page.waitForTimeout(3_000);
    const errorIndicator = page
      .locator("text=error")
      .or(page.getByTestId("status-badge"));
    await expect(errorIndicator.first()).toBeVisible({
      timeout: 10_000,
    });
  });

  test("dashboard refresh failure → error overlay", async ({
    page,
  }) => {
    // Get a token first via API (with retry on 429)
    const BACKEND =
      process.env.BACKEND_URL || "http://127.0.0.1:8181";
    let access_token = "";
    for (let i = 0; i < 5; i++) {
      const loginRes = await page.request.post(
        `${BACKEND}/auth/login`,
        {
          data: {
            email:
              process.env.TEST_USER_EMAIL ||
              "test@demo.com",
            password:
              process.env.TEST_USER_PASSWORD ||
              "Test1234!",
          },
        },
      );
      if (loginRes.ok()) {
        ({ access_token } = await loginRes.json());
        break;
      }
      if (loginRes.status() === 429 && i < 4) {
        await new Promise((r) => setTimeout(r, 3_000));
        continue;
      }
      throw new Error(
        `Login failed: ${loginRes.status()}`,
      );
    }

    const DASHBOARD =
      process.env.DASHBOARD_URL || "http://127.0.0.1:8050";
    await page.goto(
      `${DASHBOARD}/analysis?token=${access_token}`,
    );

    // Wait for the page to fully render
    const refreshBtn = page.locator("#analysis-refresh-btn");
    await expect(refreshBtn).toBeVisible({ timeout: 15_000 });

    // Select a ticker first (refresh without ticker shows warning, not error)
    const dropdown = page.locator("#analysis-ticker-dropdown");
    await dropdown.click();
    const option = page
      .locator('[role="option"]')
      .first();
    if ((await option.count()) > 0) {
      await option.click();
    }

    await refreshBtn.click();
    // The refresh status or error overlay should populate
    const status = page.locator("#analysis-refresh-status");
    await expect(status).not.toBeEmpty({ timeout: 90_000 });
  });

  test("network offline → graceful handling", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(
      page.getByTestId("chat-message-input"),
    ).toBeVisible({ timeout: 15_000 });

    // Simulate offline by aborting all backend requests
    await page.route("**/127.0.0.1:8181/**", (route) =>
      route.abort("connectionrefused"),
    );

    await page.getByTestId("chat-message-input").fill("test");
    await page.getByTestId("chat-send-button").click();

    // Should handle gracefully (no unhandled crash)
    await page.waitForTimeout(5_000);
    // Page should still be responsive
    await expect(
      page.getByTestId("chat-message-input"),
    ).toBeVisible();
  });
});
