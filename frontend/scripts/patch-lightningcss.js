#!/usr/bin/env node
/* eslint-disable @typescript-eslint/no-require-imports -- CommonJS Node build script */
/**
 * Patch lightningcss/node/index.js to use absolute require
 * paths for the native binary. Turbopack's PostCSS worker
 * evaluates the code in a sandboxed context where relative
 * require paths and optional dependency resolution fail.
 *
 * This replaces the try/catch require block with a direct
 * absolute path require that works in both normal Node and
 * Turbopack's bundled evaluation.
 */

const fs = require("fs");
const path = require("path");

const indexPath = path.join(
  __dirname,
  "..",
  "node_modules",
  "lightningcss",
  "node",
  "index.js",
);

if (!fs.existsSync(indexPath)) {
  console.log("lightningcss not found, skipping patch");
  process.exit(0);
}

const original = fs.readFileSync(indexPath, "utf8");

// Already patched?
if (original.includes("PATCHED_BY_POSTINSTALL")) {
  console.log("lightningcss: already patched");
  process.exit(0);
}

// Detect platform slug
const parts = [process.platform, process.arch];
try {
  const { familySync } = require("detect-libc");
  const family = familySync();
  if (family === "musl") parts.push("musl");
  else if (process.arch === "arm") parts.push("gnueabihf");
  else parts.push("gnu");
} catch {
  // Not Linux — no suffix needed
}

const slug = parts.join("-");
const pkgDir = path.join(
  __dirname,
  "..",
  "node_modules",
  `lightningcss-${slug}`,
);
const nativeFile = path.join(
  pkgDir,
  `lightningcss.${slug}.node`,
);

if (!fs.existsSync(nativeFile)) {
  console.log(
    `lightningcss: native binary not found for ${slug}, skipping patch`,
  );
  process.exit(0);
}

// Replace the require block with absolute path
const patched = `// PATCHED_BY_POSTINSTALL — absolute require for Turbopack compat
if (process.env.CSS_TRANSFORMER_WASM) {
  module.exports = require(\`../pkg\`);
} else {
  module.exports = require(${JSON.stringify(nativeFile)});
}

module.exports.browserslistToTargets = require('./browserslistToTargets');
module.exports.composeVisitors = require('./composeVisitors');
module.exports.Features = require('./flags').Features;
`;

fs.writeFileSync(indexPath, patched);
console.log(`lightningcss: patched for ${slug} (Turbopack compat)`);
