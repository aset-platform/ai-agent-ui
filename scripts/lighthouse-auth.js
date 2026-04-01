/**
 * Puppeteer auth script for Lighthouse CI.
 *
 * Logs in via the backend API, then sets the access token
 * as BOTH a cookie and localStorage on the target origin.
 *
 * Lighthouse clears localStorage but preserves cookies.
 * The frontend reads from localStorage, so we also inject
 * a small script via cookie that restores the token to
 * localStorage on page load.
 *
 * Env vars:
 *   PERF_TEST_EMAIL    — test user email
 *   PERF_TEST_PASSWORD — test user password
 */

const http = require("http");

/** POST to backend login, return access_token. */
function loginViaApi(email, password) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ email, password });
    const opts = {
      hostname: "localhost",
      port: 8181,
      path: "/v1/auth/login",
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
      },
    };
    const req = http.request(opts, (res) => {
      let data = "";
      res.on("data", (c) => { data += c; });
      res.on("end", () => {
        try {
          const j = JSON.parse(data);
          if (j.access_token) resolve(j.access_token);
          else reject(new Error(
            "No access_token: " + data.slice(0, 200),
          ));
        } catch (e) {
          reject(new Error("Parse error: " + e.message));
        }
      });
    });
    req.on("error", (e) =>
      reject(new Error("Backend unreachable: " + e.message)),
    );
    req.setTimeout(10000, () => {
      req.destroy();
      reject(new Error("Login timeout"));
    });
    req.write(body);
    req.end();
  });
}

module.exports = async (browser, context) => {
  const email =
    process.env.PERF_TEST_EMAIL || "demo@example.com";
  const password =
    process.env.PERF_TEST_PASSWORD || "demo1234";

  try {
    const token = await loginViaApi(email, password);

    // Set access_token as a cookie on the target origin.
    // Lighthouse preserves cookies across navigations.
    await browser.defaultBrowserContext().overridePermissions(
      "http://localhost:3030",
      [],
    );

    const page = await browser.newPage();

    // Set cookie that persists across Lighthouse navigations
    await page.setCookie({
      name: "lhci_access_token",
      value: token,
      domain: "localhost",
      path: "/",
      httpOnly: false,
      secure: false,
      sameSite: "Lax",
    });

    // Navigate to the login page and inject a script that
    // copies the cookie value to localStorage on every load
    await page.goto("http://localhost:3030/login", {
      waitUntil: "domcontentloaded",
      timeout: 30000,
    });

    // Set localStorage AND register a script to restore it
    await page.evaluate((t) => {
      localStorage.setItem("access_token", t);
    }, token);

    // Use CDP to add a script that runs on every page load
    // before any other JS — copies cookie → localStorage
    const client = await page.target().createCDPSession();
    await client.send("Page.addScriptToEvaluateOnNewDocument", {
      source: `
        (function() {
          var match = document.cookie.match(
            /lhci_access_token=([^;]+)/
          );
          if (match) {
            localStorage.setItem("access_token", match[1]);
          }
        })();
      `,
    });

    console.log(
      "Lighthouse auth: token set via cookie + CDP script",
    );
    await page.close();
  } catch (err) {
    console.error("Lighthouse auth failed:", err.message);
  }
};
