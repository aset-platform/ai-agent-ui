"""Unit tests for backend/paths.py — centralised path config.

Tests cover:
- Default path resolution to ``~/.ai-agent-ui``
- ``AI_AGENT_UI_HOME`` env-var override
- ``ensure_dirs()`` creates the full directory tree
- All exported paths are under ``APP_HOME``
"""

import importlib
import os
import sys
from pathlib import Path
from unittest import mock

# Ensure backend/ is on sys.path (same as conftest.py)
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_BACKEND_DIR = _PROJECT_ROOT / "backend"
for _p in (str(_BACKEND_DIR), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class TestDefaultPaths:
    """Verify default path values point to ~/.ai-agent-ui."""

    def test_app_home_default(self):
        """APP_HOME defaults to ~/.ai-agent-ui."""
        import paths

        expected = Path.home() / ".ai-agent-ui"
        assert paths.APP_HOME == expected

    def test_data_dir_under_app_home(self):
        """DATA_DIR is APP_HOME / 'data'."""
        import paths

        assert paths.DATA_DIR == paths.APP_HOME / "data"

    def test_iceberg_catalog_under_data(self):
        """ICEBERG_CATALOG is under DATA_DIR/iceberg."""
        import paths

        assert paths.ICEBERG_CATALOG == (
            paths.DATA_DIR / "iceberg" / "catalog.db"
        )

    def test_iceberg_warehouse_under_data(self):
        """ICEBERG_WAREHOUSE is under DATA_DIR/iceberg."""
        import paths

        assert paths.ICEBERG_WAREHOUSE == (
            paths.DATA_DIR / "iceberg" / "warehouse"
        )

    def test_cache_dir(self):
        """CACHE_DIR is under DATA_DIR."""
        import paths

        assert paths.CACHE_DIR == paths.DATA_DIR / "cache"

    def test_logs_dir(self):
        """LOGS_DIR is under APP_HOME."""
        import paths

        assert paths.LOGS_DIR == paths.APP_HOME / "logs"

    def test_charts_dirs(self):
        """Chart dirs are under APP_HOME/charts."""
        import paths

        assert paths.CHARTS_ANALYSIS_DIR == (
            paths.APP_HOME / "charts" / "analysis"
        )
        assert paths.CHARTS_FORECASTS_DIR == (
            paths.APP_HOME / "charts" / "forecasts"
        )

    def test_avatars_dir(self):
        """AVATARS_DIR is under DATA_DIR."""
        import paths

        assert paths.AVATARS_DIR == (paths.DATA_DIR / "avatars")

    def test_project_root_is_repo(self):
        """PROJECT_ROOT points to the repository checkout."""
        import paths

        assert (paths.PROJECT_ROOT / "backend").is_dir()
        assert (paths.PROJECT_ROOT / "frontend").is_dir()

    def test_iceberg_catalog_uri_format(self):
        """ICEBERG_CATALOG_URI is a sqlite:/// URI."""
        import paths

        assert paths.ICEBERG_CATALOG_URI.startswith("sqlite:///")
        assert "catalog.db" in paths.ICEBERG_CATALOG_URI

    def test_iceberg_warehouse_uri_format(self):
        """ICEBERG_WAREHOUSE_URI is a file:/// URI."""
        import paths

        assert paths.ICEBERG_WAREHOUSE_URI.startswith("file:///")
        assert "warehouse" in paths.ICEBERG_WAREHOUSE_URI


class TestEnvVarOverride:
    """Verify AI_AGENT_UI_HOME env var overrides APP_HOME."""

    def test_override_changes_all_paths(self, tmp_path):
        """Setting AI_AGENT_UI_HOME changes APP_HOME and all children."""
        custom = str(tmp_path / "custom-home")
        with mock.patch.dict(
            os.environ,
            {"AI_AGENT_UI_HOME": custom},
        ):
            # Force re-import to pick up new env var
            import paths

            reloaded = importlib.reload(paths)

        assert reloaded.APP_HOME == Path(custom)
        assert reloaded.DATA_DIR == Path(custom) / "data"
        assert reloaded.LOGS_DIR == Path(custom) / "logs"
        assert reloaded.CACHE_DIR == (Path(custom) / "data" / "cache")
        assert reloaded.ICEBERG_CATALOG == (
            Path(custom) / "data" / "iceberg" / "catalog.db"
        )

        # Restore original module state
        importlib.reload(paths)


class TestEnsureDirs:
    """Verify ensure_dirs() creates the full directory tree."""

    def test_creates_all_dirs(self, tmp_path):
        """ensure_dirs() creates every listed directory."""
        custom = str(tmp_path / "test-home")
        with mock.patch.dict(
            os.environ,
            {"AI_AGENT_UI_HOME": custom},
        ):
            import paths

            reloaded = importlib.reload(paths)
            reloaded.ensure_dirs()

        expected = [
            "data/iceberg",
            "data/iceberg/warehouse",
            "data/cache",
            "data/raw",
            "data/forecasts",
            "data/processed",
            "data/metadata",
            "data/avatars",
            "charts/analysis",
            "charts/forecasts",
            "logs",
        ]
        for rel in expected:
            assert (Path(custom) / rel).is_dir(), f"Missing: {rel}"

        # Restore original module state
        import paths as _p

        importlib.reload(_p)

    def test_idempotent(self, tmp_path):
        """Calling ensure_dirs() twice does not raise."""
        custom = str(tmp_path / "idem-home")
        with mock.patch.dict(
            os.environ,
            {"AI_AGENT_UI_HOME": custom},
        ):
            import paths

            reloaded = importlib.reload(paths)
            reloaded.ensure_dirs()
            reloaded.ensure_dirs()  # no error

        importlib.reload(paths)
