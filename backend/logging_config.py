"""Centralised logging configuration for the AI Agent backend.

This module provides a single entry point, :func:`setup_logging`, that
configures the root :mod:`logging` logger with two handlers:

- **Console** (``stdout``) — always active; useful during development and in
  containerised environments where stdout is captured by a log aggregator.
- **File** (optional) — a :class:`~logging.handlers.TimedRotatingFileHandler`
  that rotates at midnight and retains seven days of history under
  ``<log_dir>/agent.log``.

All child loggers (``tools.registry``, ``agent.general``, etc.) inherit the
root configuration, so calling :func:`setup_logging` once at process start is
sufficient.

Typical usage::

    from logging_config import setup_logging

    setup_logging(level="INFO", log_to_file=True, log_dir="logs")
"""

import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler


def setup_logging(
    level: str = "DEBUG",
    log_to_file: bool = True,
    log_dir: str = "logs",
) -> logging.Logger:
    """Configure the root logger with a console handler and an optional rotating file handler.

    Should be called **once** at application startup before any other module
    imports or uses :mod:`logging`.  Calling it more than once (e.g. due to
    uvicorn hot-reload) is safe because all existing handlers are cleared
    before new ones are attached.

    The shared log format is::

        YYYY-MM-DD HH:MM:SS,mmm | LEVEL    | logger.name | message

    Args:
        level: Minimum severity to emit.  Accepts any
            :mod:`logging` level name (``"DEBUG"``, ``"INFO"``,
            ``"WARNING"``, ``"ERROR"``, ``"CRITICAL"``).  Defaults to
            ``"DEBUG"``.  An unrecognised string silently falls back to
            ``logging.DEBUG``.
        log_to_file: When ``True``, a rotating file handler is added in
            addition to the console handler.  Defaults to ``True``.
        log_dir: Directory in which the log file is created.  Created
            automatically if it does not exist.  Defaults to ``"logs"``
            (relative to the process working directory).

    Returns:
        The configured root :class:`logging.Logger` instance.

    Example:
        >>> import logging
        >>> from logging_config import setup_logging
        >>> root = setup_logging(level="INFO", log_to_file=False)
        >>> root.info("Server starting")
    """
    log_level = getattr(logging, level.upper(), logging.DEBUG)
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    formatter = logging.Formatter(fmt)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove stale handlers so hot-reload does not duplicate log lines.
    root_logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    if log_to_file:
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "agent.log")

        # Rotate at midnight; suffix makes archived files sortable by date.
        file_handler = TimedRotatingFileHandler(
            log_path,
            when="midnight",
            interval=1,
            backupCount=7,
            encoding="utf-8",
        )
        file_handler.suffix = "%Y-%m-%d"
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    return root_logger
