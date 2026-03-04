"""Seed script — create the initial superuser account.

Run once after ``python auth/create_tables.py`` to bootstrap the first
superuser.  Safe to re-run: if a user with ``ADMIN_EMAIL`` already exists
the script exits with a success message and makes no changes.

Usage::

    # from the project root, with demoenv activated:
    python scripts/seed_admin.py

Required environment variables (or set in .env at the project root or
backend/.env)::

    ADMIN_EMAIL    admin@example.com
    ADMIN_PASSWORD <strong-password>  (min 8 chars, at least one digit)
    JWT_SECRET_KEY <min-32-char-secret>

Optional::

    ADMIN_FULL_NAME   Admin User   (default: "Admin User")
    ACCESS_TOKEN_EXPIRE_MINUTES  60  (default)
    REFRESH_TOKEN_EXPIRE_DAYS    7   (default)
"""

import logging
import os
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Path setup — allow running from project root or scripts/ directory
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Load .env from project root and backend/.env (best-effort; no extra deps)
# ---------------------------------------------------------------------------


def _load_dotenv(dotenv_path: Path) -> None:
    """Parse key=value pairs from *dotenv_path* into ``os.environ``.

    Existing environment variables are never overwritten.  Lines starting
    with ``#`` and blank lines are skipped.  Values may optionally be
    wrapped in single or double quotes.

    Args:
        dotenv_path: Absolute path to the ``.env`` file to load.
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

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Create the initial superuser account if it does not already exist.

    Reads ``ADMIN_EMAIL``, ``ADMIN_PASSWORD``, and ``JWT_SECRET_KEY`` from
    the environment, validates them, checks whether the account already
    exists in the Iceberg users table, and creates it if absent.

    Raises:
        SystemExit: Exit code 1 on any error (missing env vars, weak
            password, catalog unavailable, unexpected repository error).
            Exit code 0 on success or when the account already exists.
    """
    admin_email: str = os.environ.get("ADMIN_EMAIL", "").strip()
    admin_password: str = os.environ.get("ADMIN_PASSWORD", "").strip()
    admin_full_name: str = os.environ.get(
        "ADMIN_FULL_NAME", "Admin User"
    ).strip()
    jwt_secret: str = os.environ.get("JWT_SECRET_KEY", "").strip()
    access_minutes: int = int(
        os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "60")
    )
    refresh_days: int = int(os.environ.get("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

    # ── Validate required env vars ─────────────────────────────────────────
    missing = [
        k
        for k, v in [
            ("ADMIN_EMAIL", admin_email),
            ("ADMIN_PASSWORD", admin_password),
            ("JWT_SECRET_KEY", jwt_secret),
        ]
        if not v
    ]

    if missing:
        logger.error(
            "Missing required environment variables: %s", ", ".join(missing)
        )
        logger.error(
            "Set them in .env at the project root (or backend/.env) "
            "or export them before running this script."
        )
        sys.exit(1)

    # ── Validate password strength ─────────────────────────────────────────
    if len(admin_password) < 8:
        logger.error("ADMIN_PASSWORD must be at least 8 characters.")
        sys.exit(1)
    if not any(c.isdigit() for c in admin_password):
        logger.error("ADMIN_PASSWORD must contain at least one digit.")
        sys.exit(1)

    # ── Validate JWT secret length ─────────────────────────────────────────
    if len(jwt_secret) < 32:
        logger.error(
            "JWT_SECRET_KEY must be at least 32 characters. "
            'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
        )
        sys.exit(1)

    # Change to project root so PyIceberg can find .pyiceberg.yaml
    os.chdir(str(_PROJECT_ROOT))

    # ── Import auth modules ────────────────────────────────────────────────
    try:
        from auth.repository import IcebergUserRepository  # type: ignore[import]
        from auth.service import AuthService  # type: ignore[import]
    except ImportError as exc:
        logger.error(
            "Could not import auth modules: %s  "
            "Have you run 'python auth/create_tables.py' first?",
            exc,
        )
        sys.exit(1)

    # ── Open Iceberg catalog ───────────────────────────────────────────────
    try:
        repo = IcebergUserRepository()
    except Exception as exc:
        logger.error(
            "Could not open Iceberg catalog: %s  "
            "Run 'python auth/create_tables.py' to initialise it.",
            exc,
        )
        sys.exit(1)

    # ── Check for existing user ────────────────────────────────────────────
    existing: Optional[dict] = repo.get_by_email(admin_email)
    if existing is not None:
        if existing.get("role") == "superuser":
            logger.info(
                "Superuser '%s' already exists (user_id=%s). Nothing to do.",
                admin_email,
                existing["user_id"],
            )
        else:
            logger.warning(
                "A user with email '%s' exists but has role '%s' (not superuser). "
                "No changes made.",
                admin_email,
                existing.get("role"),
            )
        sys.exit(0)

    # ── Create superuser ───────────────────────────────────────────────────
    service = AuthService(
        secret_key=jwt_secret,
        access_expire_minutes=access_minutes,
        refresh_expire_days=refresh_days,
    )
    hashed = service.hash_password(admin_password)

    try:
        user = repo.create(
            {
                "email": admin_email,
                "hashed_password": hashed,
                "full_name": admin_full_name,
                "role": "superuser",
            }
        )
        repo.append_audit_event(
            "USER_CREATED",
            actor_user_id=user["user_id"],
            target_user_id=user["user_id"],
            metadata={"source": "seed_admin.py", "email": admin_email},
        )
    except Exception as exc:
        logger.error("Failed to create superuser: %s", exc)
        sys.exit(1)

    logger.info(
        "Superuser created successfully.  email=%s  user_id=%s",
        admin_email,
        user["user_id"],
    )
    logger.info("You can now log in at http://localhost:3000/login")


if __name__ == "__main__":
    main()
