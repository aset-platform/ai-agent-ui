"""Create or update pintooabhay123@gmail.com as superuser."""

import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")
_logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "backend"),
)
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/.ai-agent-ui/backend.env"))

from auth.repo.repository import IcebergUserRepository
from auth.service import AuthService

repo = IcebergUserRepository()
svc = AuthService(
    secret_key=os.environ["JWT_SECRET_KEY"],
    access_expire_minutes=60,
    refresh_expire_days=7,
)

EMAIL = "pintooabhay123@gmail.com"
user = repo.get_by_email(EMAIL)

if user:
    _logger.info(
        f"Found user: id={user['user_id']} role={user['role']}"
    )
    if user["role"] != "superuser":
        repo.update(user["user_id"], {"role": "superuser"})
        _logger.info(f"Updated {EMAIL} role -> superuser")
    else:
        _logger.info("Already superuser, no change needed")
else:
    _logger.info(f"User {EMAIL} not found — creating as superuser")
    hashed = svc.hash_password("SuperAdmin123!")
    user_data = {
        "email": EMAIL,
        "hashed_password": hashed,
        "full_name": "Abhay Kumar Singh",
        "role": "superuser",
        "subscription_tier": "enterprise",
        "subscription_status": "active",
        "razorpay_customer_id": None,
        "razorpay_subscription_id": None,
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
        "monthly_usage_count": 0,
        "usage_month": None,
        "subscription_start_at": None,
        "subscription_end_at": None,
    }
    created = repo.create(user_data)
    _logger.info(f"Created superuser: id={created['user_id']}")

_logger.info("Done")
