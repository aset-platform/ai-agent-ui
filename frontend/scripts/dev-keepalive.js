#!/usr/bin/env node
/* eslint-disable @typescript-eslint/no-require-imports -- CommonJS Node preload */
/**
 * Bump Node's HTTP keep-alive timeout for `next dev`.
 *
 * Node's default `keepAliveTimeout` is 5s. Firefox aggressively
 * reuses pooled HTTP/1.1 connections to localhost without probing
 * for liveness, so a socket the dev server has already FIN'd
 * surfaces as `NetworkError when attempting to fetch resource` —
 * and on a `next/dynamic()` chunk that bubbles up as
 * `ChunkLoadError`. Chrome detects the dead socket and silently
 * reconnects, which is why the bug is Firefox-only.
 *
 * `next dev` doesn't expose `--keepAliveTimeout` (only `next start`
 * does), so we monkey-patch `http.createServer` here and load this
 * file via `node --require` before next's CLI runs. Turbopack +
 * HMR are unaffected; only the browser-facing TCP idle window grows.
 *
 * 65s comfortably exceeds typical browser idle on a dev session
 * and matches the de-facto LB standard. `headersTimeout` must be
 * strictly greater (Node enforces this since 18.x).
 */

const http = require("http");

const KEEP_ALIVE_MS = 65_000;
const HEADERS_TIMEOUT_MS = 70_000;

const origCreateServer = http.createServer;
http.createServer = function patchedCreateServer(...args) {
  const server = origCreateServer.apply(this, args);
  server.keepAliveTimeout = KEEP_ALIVE_MS;
  server.headersTimeout = HEADERS_TIMEOUT_MS;
  return server;
};
