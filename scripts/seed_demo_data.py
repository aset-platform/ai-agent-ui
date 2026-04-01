"""Seed script — populate Iceberg with demo data and users.

Loads JSON fixtures from ``fixtures/seed/`` and inserts them
into the 9 stock tables plus creates two demo user accounts
(admin + test) with all 5 tickers linked.

Safe to re-run: skips tickers/users that already exist.

Usage::

    python scripts/seed_demo_data.py

Set ``SKIP_SEED=1`` to skip entirely (used by setup.sh).
"""

import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
for p in (str(_PROJECT_ROOT), str(_PROJECT_ROOT / "backend")):
    if p not in sys.path:
        sys.path.insert(0, p)


def _load_dotenv(dotenv_path: Path) -> None:
    """Parse key=value pairs from *dotenv_path* into environ.

    Existing variables are never overwritten.

    Args:
        dotenv_path: Path to the ``.env`` file.
    """
    if not dotenv_path.exists():
        return
    with open(dotenv_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv(_PROJECT_ROOT / ".env")
_load_dotenv(_PROJECT_ROOT / "backend" / ".env")

# Set PyIceberg env vars before any pyiceberg import.
from paths import ICEBERG_CATALOG_URI, ICEBERG_WAREHOUSE_URI

os.environ.setdefault(
    "PYICEBERG_CATALOG__LOCAL__URI",
    ICEBERG_CATALOG_URI,
)
os.environ.setdefault(
    "PYICEBERG_CATALOG__LOCAL__WAREHOUSE",
    ICEBERG_WAREHOUSE_URI,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
_logger = logging.getLogger(__name__)

FIXTURES_DIR = _PROJECT_ROOT / "fixtures" / "seed"
SEED_TICKERS = [
    "AAPL",
    "MSFT",
    "RELIANCE.NS",
    "TCS.NS",
    "TSLA",
]

# Demo users: email, password, full_name, role
DEMO_USERS = [
    {
        "email": "admin@demo.com",
        "password": "Admin123!",
        "full_name": "Admin User",
        "role": "superuser",
    },
    {
        "email": "test@demo.com",
        "password": "Test1234!",
        "full_name": "Test User",
        "role": "general",
    },
]


def _load_fixture(name: str) -> list:
    """Load a JSON fixture file.

    Args:
        name: Fixture filename without extension.

    Returns:
        List of dicts from the JSON file, or empty list
        if the file does not exist.
    """
    path = FIXTURES_DIR / f"{name}.json"
    if not path.exists():
        _logger.warning("Fixture not found: %s", path)
        return []
    with open(path, "r") as f:
        return json.load(f)


def _parse_date(val):
    """Parse ISO date string to date object.

    Args:
        val: Date string or None.

    Returns:
        ``date`` object or ``None``.
    """
    if not val:
        return None
    if isinstance(val, date):
        return val
    return date.fromisoformat(str(val)[:10])


def _seed_stocks() -> None:
    """Seed all 9 stock Iceberg tables from fixtures."""
    import pandas as pd

    from stocks.repository import StockRepository

    os.chdir(str(_PROJECT_ROOT))
    repo = StockRepository()
    existing_reg = repo.get_all_registry()

    # ── Registry ────────────────────────────────────
    for entry in _load_fixture("registry"):
        t = entry["ticker"]
        if t in existing_reg:
            _logger.info(
                "Registry: %s already exists, skipping",
                t,
            )
            continue
        repo.upsert_registry(
            ticker=t,
            last_fetch_date=_parse_date(entry["last_fetch_date"]),
            total_rows=entry["total_rows"],
            date_range_start=_parse_date(entry["date_range_start"]),
            date_range_end=_parse_date(entry["date_range_end"]),
            market=entry["market"],
        )
        _logger.info("Registry: seeded %s", t)

    # ── Company Info ────────────────────────────────
    for ci in _load_fixture("company_info"):
        t = ci["ticker"]
        existing = repo.get_latest_company_info(t)
        if existing:
            _logger.info(
                "CompanyInfo: %s exists, skipping",
                t,
            )
            continue
        repo.insert_company_info(t, ci)
        _logger.info("CompanyInfo: seeded %s", t)

    # ── OHLCV ───────────────────────────────────────
    ohlcv_records = _load_fixture("ohlcv")
    for t in SEED_TICKERS:
        existing = repo.get_ohlcv(t)
        if not existing.empty:
            _logger.info(
                "OHLCV: %s has %d rows, skipping",
                t,
                len(existing),
            )
            continue
        rows = [r for r in ohlcv_records if r["ticker"] == t]
        if not rows:
            continue
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        for col in ("open", "high", "low", "close", "adj_close"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = (
            pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype(int)
        )
        if "fetched_at" in df.columns:
            df = df.drop(columns=["fetched_at"])
        # Rename to yfinance-style columns expected by
        # repository.insert_ohlcv.
        df = df.rename(
            columns={
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "adj_close": "Adj Close",
                "volume": "Volume",
            }
        )
        n = repo.insert_ohlcv(t, df)
        _logger.info("OHLCV: seeded %s (%d rows)", t, n)

    # ── Dividends ───────────────────────────────────
    div_records = _load_fixture("dividends")
    for t in SEED_TICKERS:
        existing = repo.get_dividends(t)
        if not existing.empty:
            _logger.info(
                "Dividends: %s has %d rows, skipping",
                t,
                len(existing),
            )
            continue
        rows = [r for r in div_records if r["ticker"] == t]
        if not rows:
            continue
        df = pd.DataFrame(rows)
        df["ex_date"] = pd.to_datetime(df["ex_date"]).dt.date
        df["dividend_amount"] = pd.to_numeric(
            df["dividend_amount"], errors="coerce"
        )
        if "fetched_at" in df.columns:
            df = df.drop(columns=["fetched_at"])
        n = repo.insert_dividends(t, df)
        _logger.info("Dividends: seeded %s (%d rows)", t, n)

    # ── Technical Indicators ────────────────────────
    ti_records = _load_fixture("technical_indicators")
    for t in SEED_TICKERS:
        existing = repo.get_technical_indicators(t)
        if not existing.empty:
            _logger.info(
                "TechIndicators: %s has %d rows, skipping",
                t,
                len(existing),
            )
            continue
        rows = [r for r in ti_records if r["ticker"] == t]
        if not rows:
            continue
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        if "computed_at" in df.columns:
            df = df.drop(columns=["computed_at"])
        repo.upsert_technical_indicators(t, df)
        _logger.info(
            "TechIndicators: seeded %s (%d rows)",
            t,
            len(df),
        )

    # ── Analysis Summary ────────────────────────────
    for entry in _load_fixture("analysis_summary"):
        t = entry["ticker"]
        existing = repo.get_latest_analysis_summary(t)
        if existing:
            _logger.info(
                "AnalysisSummary: %s exists, skipping",
                t,
            )
            continue
        repo.insert_analysis_summary(t, entry)
        _logger.info("AnalysisSummary: seeded %s", t)

    # ── Forecast Runs ───────────────────────────────
    for entry in _load_fixture("forecast_runs"):
        t = entry["ticker"]
        h = entry.get("horizon_months", 9)
        existing = repo.get_latest_forecast_run(t, h)
        if existing:
            _logger.info(
                "ForecastRun: %s (h=%d) exists, skipping",
                t,
                h,
            )
            continue
        repo.insert_forecast_run(t, h, entry)
        _logger.info("ForecastRun: seeded %s (h=%d)", t, h)

    # ── Forecasts (series) ──────────────────────────
    fc_records = _load_fixture("forecasts")
    for t in SEED_TICKERS:
        h = 9
        existing = repo.get_latest_forecast_series(t, h)
        if existing is not None and not existing.empty:
            _logger.info(
                "Forecasts: %s (h=%d) has %d rows, skip",
                t,
                h,
                len(existing),
            )
            continue
        rows = [
            r
            for r in fc_records
            if r["ticker"] == t and r.get("horizon_months", 9) == h
        ]
        if not rows:
            continue
        df = pd.DataFrame(rows)
        df["run_date"] = pd.to_datetime(df["run_date"]).dt.date
        df["forecast_date"] = pd.to_datetime(df["forecast_date"]).dt.date
        for col in (
            "predicted_price",
            "lower_bound",
            "upper_bound",
        ):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        run_date = df["run_date"].iloc[0]
        # Rename to Prophet-style columns expected by
        # repository.insert_forecast_series.
        df = df.rename(
            columns={
                "forecast_date": "ds",
                "predicted_price": "yhat",
                "lower_bound": "yhat_lower",
                "upper_bound": "yhat_upper",
            }
        )
        repo.insert_forecast_series(t, h, run_date, df)
        _logger.info(
            "Forecasts: seeded %s (h=%d, %d rows)",
            t,
            h,
            len(df),
        )

    # ── Quarterly Results ───────────────────────────
    qr_records = _load_fixture("quarterly_results")
    for t in SEED_TICKERS:
        existing = repo.get_quarterly_results(t)
        if not existing.empty:
            _logger.info(
                "Quarterly: %s has %d rows, skipping",
                t,
                len(existing),
            )
            continue
        rows = [r for r in qr_records if r["ticker"] == t]
        if not rows:
            continue
        df = pd.DataFrame(rows)
        df["quarter_end"] = pd.to_datetime(df["quarter_end"]).dt.date
        if "updated_at" in df.columns:
            df = df.drop(columns=["updated_at"])
        repo.insert_quarterly_results(t, df)
        _logger.info(
            "Quarterly: seeded %s (%d rows)",
            t,
            len(rows),
        )

    _logger.info(
        "Stock data seeding complete for %d tickers",
        len(SEED_TICKERS),
    )


def _seed_users() -> None:
    """Create demo user accounts and link tickers."""
    jwt_secret = os.environ.get("JWT_SECRET_KEY", "")
    if not jwt_secret or len(jwt_secret) < 32:
        _logger.warning(
            "JWT_SECRET_KEY not set or too short; "
            "skipping user seeding. Set it in backend/.env"
        )
        return

    os.chdir(str(_PROJECT_ROOT))

    import asyncio

    try:
        from auth.repo.repository import UserRepository
        from auth.service import AuthService
        from db.engine import get_session_factory
    except ImportError as exc:
        _logger.warning(
            "Cannot import auth modules: %s  "
            "Skipping user seeding.",
            exc,
        )
        return

    service = AuthService(
        secret_key=jwt_secret,
        access_expire_minutes=int(
            os.environ.get(
                "ACCESS_TOKEN_EXPIRE_MINUTES", "60",
            )
        ),
        refresh_expire_days=int(
            os.environ.get(
                "REFRESH_TOKEN_EXPIRE_DAYS", "7",
            )
        ),
    )

    repo = UserRepository(
        session_factory=get_session_factory(),
    )

    async def _seed():
        for u in DEMO_USERS:
            existing = await repo.get_by_email(
                u["email"],
            )
            if existing:
                _logger.info(
                    "User '%s' already exists "
                    "(id=%s), skip",
                    u["email"],
                    existing["user_id"],
                )
                user_id = existing["user_id"]
            else:
                hashed = service.hash_password(
                    u["password"],
                )
                user = await repo.create(
                    {
                        "email": u["email"],
                        "hashed_password": hashed,
                        "full_name": u["full_name"],
                        "role": u["role"],
                    }
                )
                user_id = user["user_id"]
                _logger.info(
                    "Created user '%s' (role=%s,"
                    " id=%s)",
                    u["email"],
                    u["role"],
                    user_id,
                )

            # Link all seed tickers to this user
            for t in SEED_TICKERS:
                try:
                    linked = await repo.link_ticker(
                        user_id, t, "seed",
                    )
                    if linked:
                        _logger.info(
                            "  Linked %s to %s",
                            t,
                            u["email"],
                        )
                except Exception as exc:
                    _logger.warning(
                        "  Failed to link %s to"
                        " %s: %s",
                        t,
                        u["email"],
                        exc,
                    )

    asyncio.run(_seed())
    _logger.info("User seeding complete")


def main() -> None:
    """Seed demo stock data and user accounts.

    Checks ``SKIP_SEED`` env var — exits early if set.
    Otherwise seeds stock fixtures, then creates demo
    users with tickers linked.
    """
    if os.environ.get("SKIP_SEED") == "1":
        _logger.info("SKIP_SEED=1 — skipping seed")
        return

    if not FIXTURES_DIR.exists():
        _logger.error(
            "Fixtures directory not found: %s",
            FIXTURES_DIR,
        )
        sys.exit(1)

    _logger.info(
        "Seeding demo data from %s",
        FIXTURES_DIR,
    )
    _seed_stocks()
    _seed_users()
    _logger.info(
        "Demo data ready. Login: admin@demo.com" " or test@demo.com",
    )


if __name__ == "__main__":
    main()
