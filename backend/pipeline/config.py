"""Pipeline configuration constants."""

# Batch settings
DEFAULT_BATCH_SIZE = 50
MAX_CONCURRENCY = 10

# Rate limiting
REQUEST_DELAY_S = 0.5

# Retry settings
MAX_RETRIES = 3
RETRY_BACKOFF_BASE_S = 1.0  # 1s, 2s, 4s

# Rate limit escalation
RATE_LIMIT_BACKOFF_S = 60.0
MAX_CONSECUTIVE_429 = 3

# Default history period for new tickers
DEFAULT_HISTORY_YEARS = 10
