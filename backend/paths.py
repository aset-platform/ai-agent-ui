"""Centralised filesystem paths for the AI Agent UI platform.

All services (backend, dashboard, auth, scripts) import paths from
this module instead of computing them locally.  This ensures every
process agrees on where data, logs, and charts live.

The root directory defaults to ``~/.ai-agent-ui`` and can be
overridden with the ``AI_AGENT_UI_HOME`` environment variable.

Attributes
----------
APP_HOME : pathlib.Path
    Root of all persistent application data
    (default ``~/.ai-agent-ui``).
DATA_DIR : pathlib.Path
    ``APP_HOME / "data"``.
ICEBERG_CATALOG : pathlib.Path
    ``DATA_DIR / "iceberg" / "catalog.db"``.
ICEBERG_WAREHOUSE : pathlib.Path
    ``DATA_DIR / "iceberg" / "warehouse"``.
CACHE_DIR : pathlib.Path
    ``DATA_DIR / "cache"`` — same-day tool result cache.
RAW_DIR : pathlib.Path
    ``DATA_DIR / "raw"`` — legacy flat-file backup.
FORECASTS_DIR : pathlib.Path
    ``DATA_DIR / "forecasts"`` — legacy forecast backup.
PROCESSED_DIR : pathlib.Path
    ``DATA_DIR / "processed"`` — placeholder.
AVATARS_DIR : pathlib.Path
    ``DATA_DIR / "avatars"`` — user profile pictures.
CHARTS_ANALYSIS_DIR : pathlib.Path
    ``APP_HOME / "charts" / "analysis"``.
CHARTS_FORECASTS_DIR : pathlib.Path
    ``APP_HOME / "charts" / "forecasts"``.
LOGS_DIR : pathlib.Path
    ``APP_HOME / "logs"``.
PROJECT_ROOT : pathlib.Path
    Absolute path to the repository checkout (read-only
    reference for code, templates, and static assets).
"""

import os
from pathlib import Path

# ── repository root (code checkout) ────────────────────────────
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# ── application home (persistent data) ─────────────────────────
APP_HOME: Path = Path(
    os.environ.get("AI_AGENT_UI_HOME", Path.home() / ".ai-agent-ui")
)

# ── data directories ───────────────────────────────────────────
DATA_DIR: Path = APP_HOME / "data"
ICEBERG_DIR: Path = DATA_DIR / "iceberg"
ICEBERG_CATALOG: Path = ICEBERG_DIR / "catalog.db"
ICEBERG_WAREHOUSE: Path = ICEBERG_DIR / "warehouse"
CACHE_DIR: Path = DATA_DIR / "cache"
RAW_DIR: Path = DATA_DIR / "raw"
FORECASTS_DIR: Path = DATA_DIR / "forecasts"
PROCESSED_DIR: Path = DATA_DIR / "processed"
METADATA_DIR: Path = DATA_DIR / "metadata"
AVATARS_DIR: Path = DATA_DIR / "avatars"

# ── chart output ───────────────────────────────────────────────
CHARTS_DIR: Path = APP_HOME / "charts"
CHARTS_ANALYSIS_DIR: Path = CHARTS_DIR / "analysis"
CHARTS_FORECASTS_DIR: Path = CHARTS_DIR / "forecasts"

# ── logs ───────────────────────────────────────────────────────
LOGS_DIR: Path = APP_HOME / "logs"

# ── PyIceberg catalog URIs ─────────────────────────────────────
ICEBERG_CATALOG_URI: str = f"sqlite:///{ICEBERG_CATALOG.resolve()}"
ICEBERG_WAREHOUSE_URI: str = f"file:///{ICEBERG_WAREHOUSE.resolve()}"

# ── all directories that must exist ────────────────────────────
_ALL_DIRS: tuple[Path, ...] = (
    ICEBERG_DIR,
    ICEBERG_WAREHOUSE,
    CACHE_DIR,
    RAW_DIR,
    FORECASTS_DIR,
    PROCESSED_DIR,
    METADATA_DIR,
    AVATARS_DIR,
    CHARTS_ANALYSIS_DIR,
    CHARTS_FORECASTS_DIR,
    LOGS_DIR,
)


def ensure_dirs() -> None:
    """Create every application directory if it does not exist.

    Safe to call multiple times (idempotent).  Typically invoked
    once at process startup by ``backend/main.py`` or
    ``dashboard/app.py``.
    """
    for d in _ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)
