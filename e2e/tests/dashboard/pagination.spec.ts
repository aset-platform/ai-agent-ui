/**
 * E2E tests for pagination across all dashboard pages.
 *
 * Validates that:
 * - Clicking a page number shows correct page data.
 * - The active page indicator stays on the clicked page
 *   (does not reset to page 1).
 * - Page-size changes reset pagination to page 1.
 * - Sort-header clicks reset pagination to page 1.
 * - Filter changes reset pagination to page 1.
 */

import { type Page } from "@playwright/test";

import { test, expect } from "../../fixtures/auth.fixture";
import { waitForDashLoading } from "../../utils/wait.helper";

// ── Helpers ────────────────────────────────────────────────

/** Get the active page number from a dbc.Pagination component. */
async function getActivePage(
  page: Page,
  paginationId: string,
): Promise<number> {
  const active = page.locator(
    `#${paginationId} .page-item.active .page-link`,
  );
  const text = await active.innerText({ timeout: 5_000 });
  return parseInt(text, 10);
}

/** Click a specific page number in a dbc.Pagination. */
async function clickPage(
  page: Page,
  paginationId: string,
  pageNum: number,
): Promise<void> {
  // dbc.Pagination renders page-link buttons with text
  const pageLink = page
    .locator(`#${paginationId} .page-link`)
    .filter({ hasText: new RegExp(`^${pageNum}$`) });
  await pageLink.click({ force: true });
  await waitForDashLoading(page);
  // Allow Dash callback chain to settle
  await page.waitForTimeout(1_500);
}

/** Get the max page number visible in the pagination. */
async function getMaxPage(
  page: Page,
  paginationId: string,
): Promise<number> {
  const links = page.locator(
    `#${paginationId} .page-link`,
  );
  const count = await links.count();
  let max = 1;
  for (let i = 0; i < count; i++) {
    const text = await links.nth(i).innerText();
    const num = parseInt(text, 10);
    if (!isNaN(num) && num > max) max = num;
  }
  return max;
}

/** Navigate to a Dash page with JWT token. */
async function navigateTo(
  page: Page,
  path: string,
  token: string,
): Promise<void> {
  await page.goto(`${path}?token=${token}`);
  await waitForDashLoading(page);
  // Retry once if Dash restarted mid-load
  const err = page.locator("text=Callback error");
  if ((await err.count()) > 0) {
    await page.waitForTimeout(3_000);
    await page.reload();
    await waitForDashLoading(page);
  }
}

// ── Tests: Marketplace pagination ──────────────────────────

test.describe("Marketplace pagination", () => {
  test.beforeEach(async ({ page, userToken }) => {
    await navigateTo(page, "/marketplace", userToken);
  });

  test("active page indicator stays on clicked page", async ({
    page,
  }) => {
    const pid = "marketplace-pagination";
    const pagination = page.locator(`#${pid}`);
    await expect(pagination).toBeVisible({
      timeout: 30_000,
    });

    const maxPage = await getMaxPage(page, pid);
    if (maxPage < 2) {
      test.skip();
      return;
    }

    await clickPage(page, pid, 2);
    const active = await getActivePage(page, pid);
    expect(active).toBe(2);
  });

  test("page-size change resets to page 1", async ({
    page,
  }) => {
    const pid = "marketplace-pagination";
    const pagination = page.locator(`#${pid}`);
    await expect(pagination).toBeVisible({
      timeout: 30_000,
    });

    const maxPage = await getMaxPage(page, pid);
    if (maxPage < 2) {
      test.skip();
      return;
    }

    // Go to page 2
    await clickPage(page, pid, 2);
    expect(await getActivePage(page, pid)).toBe(2);

    // Change page size — should reset to page 1
    const pageSize = page.locator("#marketplace-page-size");
    await pageSize.selectOption("25");
    await waitForDashLoading(page);
    await page.waitForTimeout(1_500);

    expect(await getActivePage(page, pid)).toBe(1);
  });

  test("sort header click resets to page 1", async ({
    page,
  }) => {
    const pid = "marketplace-pagination";
    const pagination = page.locator(`#${pid}`);
    await expect(pagination).toBeVisible({
      timeout: 30_000,
    });

    const maxPage = await getMaxPage(page, pid);
    if (maxPage < 2) {
      test.skip();
      return;
    }

    await clickPage(page, pid, 2);
    expect(await getActivePage(page, pid)).toBe(2);

    // Click a sort header
    const sortBtn = page
      .locator(".sort-header-btn")
      .first();
    if ((await sortBtn.count()) > 0) {
      await sortBtn.click();
      await waitForDashLoading(page);
      await page.waitForTimeout(1_500);
      expect(await getActivePage(page, pid)).toBe(1);
    }
  });
});

// ── Tests: Insights screener pagination ────────────────────

test.describe("Insights screener pagination", () => {
  test.beforeEach(async ({ page, adminToken }) => {
    await navigateTo(page, "/insights", adminToken);
    // Wait for screener table to load
    await expect(
      page.locator("#screener-table-container table"),
    ).toBeVisible({ timeout: 30_000 });
  });

  test("active page indicator stays on clicked page", async ({
    page,
  }) => {
    const pid = "screener-pagination";
    const pagination = page.locator(`#${pid}`);
    await expect(pagination).toBeVisible({
      timeout: 15_000,
    });

    const maxPage = await getMaxPage(page, pid);
    if (maxPage < 2) {
      test.skip();
      return;
    }

    await clickPage(page, pid, 2);
    const active = await getActivePage(page, pid);
    expect(active).toBe(2);
  });

  test("sort header click resets to page 1", async ({
    page,
  }) => {
    const pid = "screener-pagination";
    const pagination = page.locator(`#${pid}`);
    await expect(pagination).toBeVisible({
      timeout: 15_000,
    });

    const maxPage = await getMaxPage(page, pid);
    if (maxPage < 2) {
      test.skip();
      return;
    }

    await clickPage(page, pid, 2);
    expect(await getActivePage(page, pid)).toBe(2);

    const sortBtn = page
      .locator(".sort-header-btn")
      .first();
    if ((await sortBtn.count()) > 0) {
      await sortBtn.click();
      await waitForDashLoading(page);
      await page.waitForTimeout(1_500);
      expect(await getActivePage(page, pid)).toBe(1);
    }
  });
});

// ── Tests: Admin users pagination ──────────────────────────

test.describe("Admin users pagination", () => {
  test.beforeEach(async ({ page, adminToken }) => {
    await navigateTo(page, "/admin/users", adminToken);
    await expect(
      page.getByTestId("admin-user-table"),
    ).toBeVisible({ timeout: 30_000 });
  });

  test("active page indicator stays on clicked page", async ({
    page,
  }) => {
    const pid = "users-pagination";
    const pagination = page.locator(`#${pid}`);
    await expect(pagination).toBeVisible({
      timeout: 15_000,
    });

    const maxPage = await getMaxPage(page, pid);
    if (maxPage < 2) {
      test.skip();
      return;
    }

    await clickPage(page, pid, 2);
    const active = await getActivePage(page, pid);
    expect(active).toBe(2);
  });

  test("search resets to page 1", async ({ page }) => {
    const pid = "users-pagination";
    const pagination = page.locator(`#${pid}`);
    await expect(pagination).toBeVisible({
      timeout: 15_000,
    });

    const maxPage = await getMaxPage(page, pid);
    if (maxPage < 2) {
      test.skip();
      return;
    }

    await clickPage(page, pid, 2);
    expect(await getActivePage(page, pid)).toBe(2);

    // Type in search — should reset to page 1
    const search = page.locator("#users-search");
    await search.fill("test");
    await waitForDashLoading(page);
    await page.waitForTimeout(1_500);

    expect(await getActivePage(page, pid)).toBe(1);
  });
});

// ── Tests: Home pagination ─────────────────────────────────

test.describe("Home pagination", () => {
  test.beforeEach(async ({ page, userToken }) => {
    await navigateTo(page, "/", userToken);
    await expect(
      page.locator(".stock-card").first(),
    ).toBeVisible({ timeout: 30_000 });
  });

  test("active page indicator stays on clicked page", async ({
    page,
  }) => {
    const pid = "home-pagination";
    const pagination = page.locator(`#${pid}`);
    await expect(pagination).toBeVisible({
      timeout: 15_000,
    });

    const maxPage = await getMaxPage(page, pid);
    if (maxPage < 2) {
      test.skip();
      return;
    }

    await clickPage(page, pid, 2);
    const active = await getActivePage(page, pid);
    expect(active).toBe(2);
  });

  test("market filter resets to page 1", async ({
    page,
  }) => {
    const pid = "home-pagination";
    const pagination = page.locator(`#${pid}`);
    await expect(pagination).toBeVisible({
      timeout: 15_000,
    });

    const maxPage = await getMaxPage(page, pid);
    if (maxPage < 2) {
      test.skip();
      return;
    }

    await clickPage(page, pid, 2);
    expect(await getActivePage(page, pid)).toBe(2);

    // Click US filter — should reset to page 1
    await page.locator("#filter-us-btn").click();
    await waitForDashLoading(page);
    await page.waitForTimeout(1_500);

    expect(await getActivePage(page, pid)).toBe(1);
  });
});
