# Containerized Lighthouse Runner — Gotchas

Surfaced 2026-04-24 while shipping ASETPLTFRM-330 (`scripts/perf-lighthouse-all-routes.js` + `Dockerfile.perf` + compose `perf` profile). All five bit the first-try run.

## 1. Lighthouse 12 is ESM-only

`require("lighthouse")` throws `ERR_REQUIRE_ESM` from a CJS script. Symptom: runner says "Could not resolve any of: … lighthouse" — misleading, the module exists, `require()` just can't load it.

**Fix**: dynamic `import()` (async) inside `main()`, or rename runner to `.mjs`. Playwright remains CJS so mixed loading works:

```js
const pwModule = await tryImport(playwrightCandidates);
const chromium = pwModule.chromium ?? pwModule.default?.chromium;
const lhModule = await tryImport(lighthouseCandidates);
const lighthouse = lhModule.default ?? lhModule;
```

## 2. `crypto.randomUUID` is secure-context-gated

Chromium only exposes `crypto.randomUUID()` on HTTPS, `localhost`, or `127.0.0.1`. Docker-network hostnames (`http://frontend-perf:3000`) are neither — the API is `undefined` and app JS that calls it throws silently. Lighthouse reports **identical FCP == LCP** across every authenticated route because the render stalls right after the shell paints.

**Fix**: polyfill via Playwright `context.addInitScript` — runs before any page script:

```js
await context.addInitScript(() => {
  if (typeof window !== "undefined" && window.crypto &&
      typeof window.crypto.randomUUID !== "function") {
    window.crypto.randomUUID = function() {
      const b = new Uint8Array(16);
      window.crypto.getRandomValues(b);
      b[6] = (b[6] & 0x0f) | 0x40;
      b[8] = (b[8] & 0x3f) | 0x80;
      const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
      return h.slice(0,8) + "-" + h.slice(8,12) + "-" +
             h.slice(12,16) + "-" + h.slice(16,20) + "-" + h.slice(20);
    };
  }
});
```

`--unsafely-treat-insecure-origin-as-secure=URL` Chromium flag does NOT cover `crypto.randomUUID` — polyfill is required.

## 3. Lighthouse detached promises crash Node

Lighthouse's CDP session occasionally rejects promises AFTER the `await lighthouse(url)` parent has settled. Node's default `unhandledRejection` handler terminates the process at next tick — kills the loop mid-run. Consistently trips on `/admin?tab=my_account` after ~30 audits, stack shows `ExecutionContext._getOrCreateIsolatedContextId` → `checkForQuiet`.

**Fix**: process-level swallow + retry-on-crash.

```js
process.on("unhandledRejection", (reason) => {
  console.warn(`[unhandled] ${String(reason).slice(0, 120)}`);
});
process.on("uncaughtException", (err) => {
  console.warn(`[uncaught] ${err.message.slice(0, 120)}`);
});
```

Wrap each `audit(url)` in try/catch; on error, close the page, open a new one, re-navigate to `/dashboard` to warm cookies, retry once.

## 4. CDP session exhaustion after ~30 audits

Even with the handler above, the same Chromium tab accumulates CDP state and `Page.enable` errors out with `Session closed`. Mitigate BEFORE it crashes.

**Fix**: rotate the target tab every N audits (12 is safe for 34-route suite).

```js
const ROUTES_PER_PAGE = 12;
for (let i = 0; i < ROUTES.length; i++) {
  if (i > 0 && i % ROUTES_PER_PAGE === 0) {
    try { await currentPage.close(); } catch {}
    currentPage = await context.newPage();
    await currentPage.goto(`${BASE}/dashboard`, { waitUntil: "domcontentloaded" });
  }
  // ... audit
}
```

## 5. Next.js `rewrites()` destinations bake at build time

`next.config.ts` pattern:

```ts
async rewrites() {
  const backend = process.env.BACKEND_URL || "http://localhost:8181";
  return [{ source: "/v1/:path*", destination: `${backend}/v1/:path*` }];
}
```

`process.env.BACKEND_URL` is read ONCE during `next build` and serialized into `routes-manifest.json`. Setting `BACKEND_URL` as a runtime env var does **nothing** — the built image uses whatever was set when `next build` ran.

**Fix**: expose as a Dockerfile ARG so the build-time value flows through.

```dockerfile
ARG BACKEND_URL=http://localhost:8181
ENV BACKEND_URL=$BACKEND_URL
RUN npm run build
```

And pass through `docker-compose.override.yml`:

```yaml
frontend-perf:
  build:
    args:
      BACKEND_URL: http://backend:8181
```

## 6. Backend first-request async-loop race

FastAPI backend occasionally returns 500 on the first `/v1/auth/login` after a container recreate — stack shows asyncpg `got Future attached to a different loop`. Transient, second request works.

**Fix**: warm the backend with a curl before kicking the audit, or add an initial login retry in the perf script.

```bash
curl -fsS -X POST http://localhost:8181/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@demo.com","password":"Admin123!"}' > /dev/null
docker compose --profile perf run --rm perf
```

## Playwright + Lighthouse install strategy

`npm install -g` in the Playwright Docker image doesn't land modules where `require()` walks. Install locally instead:

```dockerfile
RUN echo '{"name":"perf-runner","private":true}' > package.json \
    && npm install --no-audit --no-fund lighthouse@12 playwright@1.48.0
```

Then `NODE_PATH=/app/node_modules` (belt-and-suspenders) + CWD at `/app` so `require.resolve("lighthouse")` finds it from the mounted script at `/app/scripts/*`.

## Why: these five bugs aren't in Lighthouse docs

Lighthouse assumes a dev-machine workflow (host Chrome, localhost origin, one-off audits). Containerized + 34-route + docker-network-origin + ESM module + Playwright-launched Chromium compounds five separate footguns. Future perf runs should start from this memory before troubleshooting.
