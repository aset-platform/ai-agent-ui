"""Quick fix: re-seed demo users after Iceberg table rebuild."""

import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")
_logger = logging.getLogger(__name__)

_root = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_root, "backend"))
sys.path.insert(0, _root)  # so 'auth' is importable
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/.ai-agent-ui/backend.env"))

from auth.repo.repository import IcebergUserRepository
from auth.service import AuthService

svc = AuthService(
    secret_key=os.environ["JWT_SECRET_KEY"],
    access_expire_minutes=60,
    refresh_expire_days=7,
)
repo = IcebergUserRepository()

USERS = [
    {
        "email": "demo@asetplatform.com",
        "password": "DemoPass123!",
        "full_name": "Demo User",
        "role": "user",
    },
    {
        "email": "admin@asetplatform.com",
        "password": "AdminPass123!",
        "full_name": "Admin User",
        "role": "admin",
    },
]

for u in USERS:
    existing = repo.get_by_email(u["email"])
    if existing:
        _logger.info(f"User {u['email']} already exists")
        continue
    hashed = svc.hash_password(u["password"])
    user_data = {
        "email": u["email"],
        "hashed_password": hashed,
        "full_name": u["full_name"],
        "role": u["role"],
        "subscription_tier": "free",
        "subscription_status": "active",
        "razorpay_customer_id": "",
        "razorpay_subscription_id": "",
        "stripe_customer_id": "",
        "stripe_subscription_id": "",
        "monthly_usage_count": 0,
        "usage_month": "",
        "subscription_start_at": None,
        "subscription_end_at": None,
    }
    user = repo.create(user_data)
    _logger.info(
        f"Created user {u['email']} id={user['user_id']}"
    )

_logger.info("Done")
