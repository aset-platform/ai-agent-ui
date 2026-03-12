/**
 * Shared auth helpers for E2E tests.
 *
 * Reads cached JWTs from storageState JSON files produced
 * by the Playwright setup project — no extra API calls.
 */

import fs from "fs";
import path from "path";

const AUTH_DIR = path.join(__dirname, "..", ".auth");

/**
 * Read a cached JWT from a storageState JSON file.
 *
 * @param filename - Name of the file inside `.auth/`
 *   (e.g. `"general-user.json"`).
 * @returns The raw access token string.
 * @throws If the file is missing or has no token entry.
 */
export function readCachedToken(
  filename: string = "general-user.json",
): string {
  const filepath = path.join(AUTH_DIR, filename);
  const data = JSON.parse(
    fs.readFileSync(filepath, "utf8"),
  );
  const origin = data.origins?.[0];
  const entry = origin?.localStorage?.find(
    (e: { name: string; value: string }) =>
      e.name === "auth_access_token",
  );
  if (!entry?.value) {
    throw new Error(
      `No auth_access_token in ${filename}`,
    );
  }
  return entry.value;
}
