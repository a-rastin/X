"""Shared provider HTTP deadline: saved revision, environment, then default."""

import math
import os

TIMEOUT_ENV = "X_INSIGHT_PROVIDER_TIMEOUT_SECONDS"
DEFAULT_TIMEOUT_SECONDS = 300.0
MIN_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 900.0
TIMEOUT_ERROR = "timeout_seconds must be a finite number within [10, 900]"


def validate_timeout(value: object) -> float:
    try:
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError
        timeout = float(value)
        if not math.isfinite(timeout) or not MIN_TIMEOUT_SECONDS <= timeout <= MAX_TIMEOUT_SECONDS:
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise ValueError(TIMEOUT_ERROR) from None
    return timeout


def effective_timeout(saved: float | None = None) -> float:
    return validate_timeout(
        saved if saved is not None else os.environ.get(TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS)
    )
