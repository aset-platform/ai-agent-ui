/**
 * Auth helper — Playwright login via form fill.
 * Returns the authenticated page with cookies + localStorage.
 */

const { TIMEOUTS } = require("./config");

/**
 * Log in via the frontend login form.
 * @param {import('playwright').Page} page
 * @param {string} baseUrl
 * @param {string} email
 * @param {string} password
 * @returns {Promise<boolean>} true if login succeeded
 */
async function login(page, baseUrl, email, password) {
  await page.goto(`${baseUrl}/login`, {
    waitUntil: "domcontentloaded",
    timeout: TIMEOUTS.pageLoad,
  });

  // Clear and type (triggers React onChange for validation)
  const emailInput = page.locator(
    'input[type="email"], input[name="email"]',
  ).first();
  const pwdInput = page.locator(
    'input[type="password"], input[name="password"]',
  ).first();

  // React onChange needs real keystrokes — `.fill()`
  // silently no-ops on prod builds (login submit stays
  // disabled because the form never marks itself dirty).
  await emailInput.click();
  await emailInput.pressSequentially(email, { delay: 15 });
  await pwdInput.click();
  await pwdInput.pressSequentially(password, { delay: 15 });

  // Wait for submit button to become enabled
  await page.waitForSelector(
    'button[type="submit"]:not([disabled])',
    { timeout: 5000 },
  ).catch(() => {});

  await page.click('button[type="submit"]', {
    force: true,
  });

  try {
    await page.waitForURL("**/dashboard**", {
      timeout: 15000,
    });
    return true;
  } catch {
    const url = page.url();
    if (!url.includes("/login")) return true;
    return false;
  }
}

/**
 * Check if the page redirected to /login and re-login.
 * @param {import('playwright').Page} page
 * @param {string} baseUrl
 * @param {string} email
 * @param {string} password
 * @returns {Promise<boolean>}
 */
async function ensureAuth(page, baseUrl, email, password) {
  if (page.url().includes("/login")) {
    return login(page, baseUrl, email, password);
  }
  return true;
}

module.exports = { login, ensureAuth };
