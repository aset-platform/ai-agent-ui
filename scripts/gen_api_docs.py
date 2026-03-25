"""Auto-generate API reference docs from FastAPI routes.

Called by ``mkdocs-gen-files`` during ``mkdocs build`` / ``mkdocs serve``.
Outputs ``docs/backend/api-reference.md`` (never committed).

Usage (standalone test)::

    python scripts/gen_api_docs.py
"""

import sys
from pathlib import Path

_PROJECT = Path(__file__).resolve().parent.parent
_BACKEND = _PROJECT / "backend"
for _p in (str(_BACKEND), str(_PROJECT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Silence startup logs during doc generation.
import os  # noqa: E402

os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault(
    "JWT_SECRET_KEY",
    "docgen-placeholder-key-not-real-xxx",
)


def _auth_label(
    router_deps: list,
    route,
) -> str:
    """Detect auth level from both router and endpoint deps.

    Checks router-level dependencies AND endpoint-level
    dependencies via ``route.dependant.dependencies``
    (the correct FastAPI introspection path).
    """
    names: list[str] = []

    # Router-level deps
    for d in router_deps or []:
        names.append(getattr(d, "__name__", str(d)))

    # Endpoint-level deps (FastAPI stores these in
    # route.dependant.dependencies[].call)
    dependant = getattr(route, "dependant", None)
    if dependant:
        for dep in getattr(dependant, "dependencies", []):
            call = getattr(dep, "call", None)
            if call:
                names.append(getattr(call, "__name__", str(call)))

    if any("superuser" in n for n in names):
        return "superuser"
    if any("current_user" in n for n in names):
        return "authenticated"
    return "public"


def _build_app_lightweight():
    """Build the FastAPI app with routes only.

    Avoids full bootstrap (no Iceberg, no LLM init,
    no ThreadPool, no Redis). Only registers routers
    so route metadata can be introspected.
    """
    from fastapi import FastAPI

    app = FastAPI(title="AI Agent API")

    # Auth + user routes
    try:
        from auth.api import (
            create_auth_router,
            get_ticker_router,
        )

        app.include_router(
            create_auth_router(),
            prefix="/v1",
        )
        app.include_router(
            get_ticker_router(),
            prefix="/v1",
        )
    except Exception:
        pass

    # Dashboard + insights + audit routes
    try:
        from dashboard_routes import (
            create_dashboard_router,
        )

        app.include_router(
            create_dashboard_router(),
            prefix="/v1",
        )
    except Exception:
        pass

    try:
        from insights_routes import (
            create_insights_router,
        )

        app.include_router(
            create_insights_router(),
            prefix="/v1",
        )
    except Exception:
        pass

    try:
        from audit_routes import (
            create_audit_router,
        )

        app.include_router(
            create_audit_router(),
            prefix="/v1",
        )
    except Exception:
        pass

    return app


def _generate() -> str:
    """Introspect FastAPI app and build markdown."""
    app = _build_app_lightweight()

    lines = [
        "# API Reference (Auto-Generated)",
        "",
        "!!! info",
        "    This page is auto-generated from FastAPI "
        "route definitions on every `mkdocs build`.",
        "    Do not edit manually.",
        "",
    ]

    # Group routes by prefix.
    groups: dict[str, list[dict]] = {}
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        path = getattr(route, "path", "")
        if path in (
            "/openapi.json",
            "/docs",
            "/redoc",
        ):
            continue

        # Determine group.
        if "/admin/" in path:
            group = "Admin"
        elif "/auth/" in path:
            group = "Auth"
        elif "/users/" in path:
            group = "Users"
        elif "/dashboard/" in path:
            group = "Dashboard"
        elif "/insights/" in path:
            group = "Insights"
        elif "/bulk" in path:
            group = "Bulk Data"
        elif "/ws/" in path:
            group = "WebSocket"
        else:
            group = "Core"

        deps = getattr(route, "dependencies", [])
        dep_callables = [
            d.dependency for d in deps if hasattr(d, "dependency")
        ]
        auth = _auth_label(dep_callables, route)

        for method in sorted(methods):
            if method == "HEAD":
                continue
            groups.setdefault(group, []).append(
                {
                    "method": method,
                    "path": path,
                    "name": getattr(route, "name", ""),
                    "summary": getattr(route, "summary", "") or "",
                    "auth": auth,
                }
            )

    # Render tables.
    order = [
        "Core",
        "Auth",
        "Users",
        "Dashboard",
        "Insights",
        "Admin",
        "Bulk Data",
        "WebSocket",
    ]
    for grp in order:
        routes = groups.get(grp)
        if not routes:
            continue
        lines.append(f"## {grp}\n")
        lines.append("| Method | Path | Auth | Description |")
        lines.append("|--------|------|------|-------------|")
        for r in sorted(routes, key=lambda x: x["path"]):
            desc = r["summary"] or r["name"]
            lines.append(
                f"| `{r['method']}` "
                f"| `{r['path']}` "
                f"| {r['auth']} "
                f"| {desc} |"
            )
        lines.append("")

    # Count.
    total = sum(len(v) for v in groups.values())
    lines.append(f"---\n\n*{total} endpoints total.*\n")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.write(_generate())
else:
    # Called by mkdocs-gen-files.
    try:
        import mkdocs_gen_files  # noqa: F401

        content = _generate()
        with mkdocs_gen_files.open("backend/api-reference.md", "w") as f:
            f.write(content)
    except ImportError:
        pass
