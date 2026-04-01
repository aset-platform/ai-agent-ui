"""Detect documentation drift between code and docs.

Compares FastAPI routes and Settings fields against the
hand-written docs to find undocumented or stale entries.

Usage::

    python scripts/docs_drift_check.py
    # or via run.sh:
    ./run.sh docs-check

Exit code 0 if clean, 1 if drift detected.
"""

import re
import sys
from pathlib import Path

_PROJECT = Path(__file__).resolve().parent.parent
_BACKEND = _PROJECT / "backend"
for _p in (str(_BACKEND), str(_PROJECT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import os  # noqa: E402

os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault(
    "JWT_SECRET_KEY",
    "driftcheck-placeholder-not-real-xx",
)

# ANSI colours.
G = "\033[0;32m"
R = "\033[0;31m"
Y = "\033[1;33m"
N = "\033[0m"

# CLI output helper (not logging — intentional stdout).
_out = sys.stdout.write


def _extract_code_routes() -> set[str]:
    """Extract all route paths from FastAPI app."""
    from main import app

    routes = set()
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        path = getattr(route, "path", "")
        if path in ("/openapi.json", "/docs", "/redoc"):
            continue
        for method in methods:
            if method == "HEAD":
                continue
            routes.add(f"{method} {path}")
    return routes


def _extract_code_config() -> set[str]:
    """Extract all Settings field names (uppercased)."""
    from config import Settings

    return {name.upper() for name in Settings.model_fields}


def _extract_doc_routes(doc_path: Path) -> set[str]:
    """Extract documented routes from api.md."""
    if not doc_path.exists():
        return set()
    text = doc_path.read_text()
    # Match patterns like: POST /v1/chat, GET /auth/me
    # Also match backtick-wrapped: `POST` | `/v1/chat`
    routes = set()
    for m in re.finditer(
        r"(GET|POST|PUT|PATCH|DELETE)\s*[|`\s]*" r"(/[^\s|`]+)",
        text,
    ):
        method = m.group(1)
        path = m.group(2).rstrip("|`")
        routes.add(f"{method} {path}")
    return routes


def _extract_doc_config(doc_path: Path) -> set[str]:
    """Extract documented env var names from config.md."""
    if not doc_path.exists():
        return set()
    text = doc_path.read_text()
    # Match backtick-wrapped uppercase names.
    # Filter out log level names and HTTP codes.
    _skip = {
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
        "TRUE",
        "FALSE",
        "NONE",
        "HTTP",
        "HTTPS",
        "POST",
        "GET",
        "PUT",
        "PATCH",
        "DELETE",
        "HEAD",
        "OPTIONS",
    }
    return {
        m.group(1)
        for m in re.finditer(r"`([A-Z][A-Z0-9_]+)`", text)
        if m.group(1) not in _skip
        and "_" in m.group(1)
        and len(m.group(1)) > 5
    }


def main() -> int:
    """Run drift checks and print report."""
    _out(f"\n{Y}Documentation Drift Check{N}\n" f"{'─' * 56}\n")
    issues = 0

    # ── API drift ──────────────────────────────
    _out(f"{Y}[1/2] API Route Drift{N}\n" + "\n")
    code_routes = _extract_code_routes()
    api_md = _PROJECT / "docs" / "backend" / "api.md"
    doc_routes = _extract_doc_routes(api_md)

    undoc_routes = code_routes - doc_routes
    stale_routes = doc_routes - code_routes

    if undoc_routes:
        for r in sorted(undoc_routes):
            _out(f"  {R}[MISSING]{N} {r}\n")
        issues += len(undoc_routes)
    if stale_routes:
        for r in sorted(stale_routes):
            _out(f"  {Y}[STALE]{N}   {r} (in docs but not in code)\n")
        issues += len(stale_routes)
    if not undoc_routes and not stale_routes:
        _out(f"  {G}[OK]{N} All routes documented\n")

    documented = code_routes & doc_routes
    _out(
        f"\n  {len(documented)} documented, "
        f"{len(undoc_routes)} missing, "
        f"{len(stale_routes)} stale\n"
    )

    # ── Config drift ───────────────────────────
    _out(f"{Y}[2/2] Config/Env Var Drift{N}\n" + "\n")
    code_config = _extract_code_config()
    config_md = _PROJECT / "docs" / "backend" / "config.md"
    doc_config = _extract_doc_config(config_md)

    undoc_config = code_config - doc_config
    stale_config = doc_config - code_config

    if undoc_config:
        for c in sorted(undoc_config):
            _out(f"  {R}[MISSING]{N} {c}\n")
        issues += len(undoc_config)
    if stale_config:
        for c in sorted(stale_config):
            _out(f"  {Y}[STALE]{N}   {c} (in docs but not in code)\n")
        issues += len(stale_config)
    if not undoc_config and not stale_config:
        _out(f"  {G}[OK]{N} All config fields documented\n")

    documented_cfg = code_config & doc_config
    _out(
        f"\n  {len(documented_cfg)} documented, "
        f"{len(undoc_config)} missing, "
        f"{len(stale_config)} stale\n"
    )

    # ── Summary ────────────────────────────────
    _out(f"{'─' * 56}" + "\n")
    if issues == 0:
        _out(f"{G}No drift detected.{N}\n\n")
        return 0
    else:
        _out(f"{R}{issues} issue(s) detected.{N} " f"Update docs or code.\n\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
