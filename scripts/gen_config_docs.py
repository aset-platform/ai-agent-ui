"""Auto-generate config/env-var reference from Settings class.

Called by ``mkdocs-gen-files`` during ``mkdocs build`` / ``mkdocs serve``.
Outputs ``docs/backend/config-reference.md`` (never committed).

Usage (standalone test)::

    python scripts/gen_config_docs.py
"""

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
    "JWT_SECRET_KEY", "docgen-placeholder-key-not-real"
)


def _type_name(annotation) -> str:
    """Human-readable type name."""
    name = getattr(
        annotation, "__name__", str(annotation)
    )
    return (
        name.replace("typing.", "")
        .replace("<class '", "")
        .replace("'>", "")
    )


def _categorise(name: str) -> str:
    """Group a field name into a category."""
    if "groq" in name or "anthropic" in name or "serpapi" in name:
        return "API Keys"
    if "model_tiers" in name or "agent_ui_env" in name:
        return "LLM Cascade"
    if "jwt" in name or "token_expire" in name:
        return "JWT & Auth"
    if "google" in name or "facebook" in name or "oauth" in name:
        return "OAuth / SSO"
    if "ws_" in name:
        return "WebSocket"
    if "rate_limit" in name:
        return "Rate Limiting"
    if "redis" in name:
        return "Redis"
    if "retention" in name:
        return "Data Retention"
    if "log" in name:
        return "Logging"
    if "max_history" in name or "max_tool" in name:
        return "Message Compression"
    if "agent_timeout" in name:
        return "Agent Execution"
    if "jwks" in name:
        return "OAuth / SSO"
    return "Other"


def _generate() -> str:
    """Introspect Settings class and build markdown."""
    from config import Settings

    lines = [
        "# Configuration Reference (Auto-Generated)",
        "",
        "!!! info",
        "    This page is auto-generated from the "
        "`Settings` class in `backend/config.py` on "
        "every `mkdocs build`. Do not edit manually.",
        "",
        "All settings are read from environment variables "
        "(or `backend/.env`). The env var name is the "
        "field name in **UPPER_CASE**.",
        "",
    ]

    # Extract fields from Settings.
    fields = Settings.model_fields
    categorised: dict[str, list[dict]] = {}

    for name, info in fields.items():
        env_var = name.upper()
        default = info.default
        ftype = _type_name(info.annotation)

        # Mask secrets in defaults.
        if default == "" and (
            "key" in name or "secret" in name
        ):
            default_str = "*(empty — required)*"
        elif isinstance(default, str) and len(default) > 50:
            default_str = f"`{default[:40]}…`"
        else:
            default_str = f"`{default}`"

        cat = _categorise(name)
        categorised.setdefault(cat, []).append({
            "env_var": env_var,
            "type": ftype,
            "default": default_str,
        })

    # Render tables by category.
    order = [
        "API Keys",
        "LLM Cascade",
        "JWT & Auth",
        "OAuth / SSO",
        "WebSocket",
        "Rate Limiting",
        "Redis",
        "Logging",
        "Agent Execution",
        "Message Compression",
        "Data Retention",
        "Other",
    ]
    total = 0
    for cat in order:
        items = categorised.get(cat)
        if not items:
            continue
        lines.append(f"## {cat}\n")
        lines.append(
            "| Variable | Type | Default |"
        )
        lines.append(
            "|----------|------|---------|"
        )
        for item in items:
            lines.append(
                f"| `{item['env_var']}` "
                f"| {item['type']} "
                f"| {item['default']} |"
            )
            total += 1
        lines.append("")

    lines.append(
        f"---\n\n*{total} configuration fields total.*\n"
    )
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.write(_generate())
else:
    try:
        import mkdocs_gen_files  # noqa: F401

        content = _generate()
        with mkdocs_gen_files.open(
            "backend/config-reference.md", "w"
        ) as f:
            f.write(content)
    except ImportError:
        pass
