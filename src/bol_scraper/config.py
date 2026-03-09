from __future__ import annotations

import os


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# OCR / extraction quality thresholds
OCR_CONF_THRESHOLD = _float("BOL_SCRAPER_OCR_CONF_THRESHOLD", 40.0)

# Routing sanity thresholds (in miles)
MAX_LOCAL_MILES = _float("BOL_SCRAPER_MAX_LOCAL_MILES", 3000.0)

# When to consider a load \"high value\" for paid APIs
MIN_GOOGLE_RATE_USD = _float("BOL_SCRAPER_MIN_GOOGLE_RATE_USD", 3000.0)

# Feature flags
ENABLE_LLM = _bool("BOL_SCRAPER_ENABLE_LLM", True)
ENABLE_GOOGLE = _bool("BOL_SCRAPER_ENABLE_GOOGLE", True)

