# Research: Next.js 16 + Turbopack + LightningCSS in Docker

**Date:** 2026-04-08
**Confidence:** High (multiple sources, official examples, confirmed fixes)

---

## Executive Summary

The `lightningcss.linux-arm64-musl.node` error is a **solved problem**.
The fix is straightforward: **switch from Alpine to Debian slim base
images**. This is exactly what the official Next.js Docker example does
(uses `node:24-slim`, not Alpine). The current project workaround
(manual .node binary copying + patch script) is unnecessary complexity.

For `next dev` (Turbopack dev server): Docker hot-reload is still
broken upstream (Docker Compose watch + Turbopack don't work together).
Running dev natively on the host remains the correct approach.

For production (`next build` + `node server.js`): Fully containerized
with Debian slim images works perfectly.

---

## Root Cause Analysis

### Why Alpine Fails

1. **Alpine uses musl libc**, not glibc
2. lightningcss ships platform-specific `.node` binaries as npm
   `optionalDependencies` (e.g., `lightningcss-linux-arm64-musl`)
3. **Two bugs compound:**
   - npm lockfile bug ([npm/cli#4828](https://github.com/npm/cli/issues/4828)):
     platform-specific optional deps not always installed correctly
   - detect-libc misidentification (fixed in lightningcss 1.29.2 via
     [PR #923](https://github.com/parcel-bundler/lightningcss/pull/923)):
     musl detected as glibc, wrong binary loaded
4. **Turbopack's PostCSS sandbox** can't resolve dynamic `require()`
   for native addons — the standard lightningcss platform detection
   code doesn't work inside the sandbox

### Why Debian Slim Fixes It

- Uses glibc (standard Linux C library)
- `lightningcss-linux-arm64-gnu` (the default variant) works without
  any special handling
- No detect-libc confusion, no musl edge cases
- Image size difference: ~40MB larger than Alpine (acceptable)

---

## Solution: Switch to Debian Slim

### Recommended Dockerfile Changes

| Current | Recommended |
|---------|-------------|
| `node:22-alpine` (all 3 stages) | `node:22-slim` (all 3 stages) |
| Manual lightningcss binary copy | Remove entirely |
| `patch-lightningcss.js` postinstall | Remove entirely |
| No non-root user | Add `USER node` in runner |
| Standard npm ci | Add BuildKit cache mounts |

### What the Official Next.js Example Does

Source: [github.com/vercel/next.js/tree/canary/examples/with-docker](https://github.com/vercel/next.js/tree/canary/examples/with-docker)

- Uses `node:24.13.0-slim` (Debian bookworm slim)
- 3-stage build: deps -> builder -> runner
- Standalone output mode
- BuildKit cache: `RUN --mount=type=cache,target=/root/.npm`
- Non-root user: `USER node`
- No lightningcss workarounds needed

### What Gets Removed

1. `Dockerfile.frontend` lines 13-21 (lightningcss binary copy hack)
2. `frontend/scripts/patch-lightningcss.js` (postinstall patch script)
3. `frontend/package.json` postinstall script reference
4. CLAUDE.md gotcha about "Frontend Docker + Turbopack"

---

## Dev Server in Docker: Still Not Recommended

Even with the lightningcss fix, running `next dev --turbopack` inside
Docker has separate issues:

| Issue | Source |
|-------|--------|
| Docker Compose watch doesn't trigger Turbopack hot reload | [docker/compose#12827](https://github.com/docker/compose/issues/12827) |
| File watching via Docker volumes is broken on Mac/Windows | [next.js#71622](https://github.com/vercel/next.js/issues/71622) |
| Significant perf degradation on Mac due to FS layer | Next.js docs |

**Official recommendation**: Run dev server natively on host.

This means the current `docker-compose.override.yml` approach (frontend
under `profiles: ["native-frontend"]`) is correct for dev mode.

The production Dockerfile (`next build` + `node server.js`) works
perfectly in Docker with slim images.

---

## Impact Assessment

### What Changes
- `Dockerfile.frontend`: Switch base image, remove lightningcss hacks
- `frontend/scripts/patch-lightningcss.js`: Delete
- `frontend/package.json`: Remove postinstall script
- Docker image: ~40MB larger (slim vs alpine), but simpler

### What Stays the Same
- `docker-compose.yml`: No changes needed
- `docker-compose.override.yml`: Frontend still uses
  `profiles: ["native-frontend"]` for dev
- `run.sh`: Frontend still launched natively for dev
- `next.config.ts`: `output: "standalone"` already configured

---

## Sources

| Source | URL |
|--------|-----|
| Official Next.js Docker example | github.com/vercel/next.js/tree/canary/examples/with-docker |
| lightningcss musl fix PR | github.com/parcel-bundler/lightningcss/pull/923 |
| lightningcss Alpine issue | github.com/parcel-bundler/lightningcss/issues/913 |
| Tailwind CSS musl.node issue | github.com/tailwindlabs/tailwindcss/issues/17958 |
| Turbopack lightningcss discussion | github.com/vercel/next.js/discussions/78584 |
| Docker Compose watch + Turbopack | github.com/docker/compose/issues/12827 |
| npm optional deps lockfile bug | github.com/npm/cli/issues/4828 |
| Next.js hot reload in Docker | github.com/vercel/next.js/issues/71622 |
