"""Application logger for RealtimeTranslator."""

import logging
import os
from datetime import datetime


_logger = None


def setup_logger(logs_path: str = None, enabled: bool = False) -> logging.Logger:
    """Setup or reconfigure the app logger."""
    global _logger

    if _logger is None:
        _logger = logging.getLogger("RealtimeTranslator")
        _logger.setLevel(logging.DEBUG)

    # Remove existing handlers
    _logger.handlers.clear()

    if not enabled:
        _logger.addHandler(logging.NullHandler())
        return _logger

    logs_dir = logs_path or os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(logs_dir, exist_ok=True)

    ts = datetime.now().strftime("%Y-%m-%d")
    log_file = os.path.join(logs_dir, f"{ts}.log")

    # File handler
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)

    # Format
    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    fh.setFormatter(fmt)

    _logger.addHandler(fh)

    _logger.info("=== Logger initialized ===")
    return _logger


def get_logger() -> logging.Logger:
    """Get the app logger. Call setup_logger first."""
    global _logger
    if _logger is None:
        _logger = logging.getLogger("RealtimeTranslator")
        _logger.addHandler(logging.NullHandler())
    return _logger
