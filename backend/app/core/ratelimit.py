"""Rate limiting — Doc 09 §9.6.

A fixed-window counter held in process memory. That is the right trade for the MVP and the
wrong one for a horizontally scaled deployment, so it says so out loud: with more than one
worker each holds its own counter and the effective limit multiplies. TRD §2.3.4 lists
Redis, and moving to it is replacing ``_HITS`` with a shared store — the call sites do not
change.

What it defends: the credential endpoints. Doc 09 §9.6 sets five sign-in attempts per
minute per IP, which stops a distributed guessing run spread across many accounts — the
per-account lockout alone would not, because each account would only see one failure.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from app.core.errors import AppError


class RateLimitExceeded(AppError):
    status_code = 429
    code = "RATE_LIMIT_EXCEEDED"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            "Too many requests. Wait before trying again.",
            details={"retry_after_seconds": retry_after_seconds},
        )
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class Limit:
    max_requests: int
    window_seconds: int


#: Doc 09 §9.6 sets the sign-in limit explicitly.
LOGIN_LIMIT = Limit(max_requests=5, window_seconds=60)

#: Requesting a reset is unauthenticated and takes an arbitrary address, so it is the
#: enumeration surface and is held tight.
RESET_REQUEST_LIMIT = Limit(max_requests=3, window_seconds=300)

#: Redeeming is looser: the token itself is a 256-bit single-use credential, so this is
#: not a guessing surface. The limit exists to bound abuse, not to gate the legitimate
#: flow — a user who mistypes a new password three times must not be locked out of their
#: own reset link.
RESET_REDEEM_LIMIT = Limit(max_requests=20, window_seconds=300)

_LOCK = threading.Lock()
_HITS: dict[tuple[str, str], list[float]] = {}


def check(bucket: str, identity: str, limit: Limit, *, now: float | None = None) -> None:
    """Record a hit and raise once the window is full.

    ``now`` is injectable so the window can be tested without sleeping through it.
    """
    moment = now if now is not None else time.monotonic()
    key = (bucket, identity)

    with _LOCK:
        hits = [t for t in _HITS.get(key, []) if moment - t < limit.window_seconds]
        if len(hits) >= limit.max_requests:
            oldest = min(hits)
            retry_after = int(limit.window_seconds - (moment - oldest)) + 1
            _HITS[key] = hits
            raise RateLimitExceeded(retry_after)
        hits.append(moment)
        _HITS[key] = hits


def reset(bucket: str | None = None) -> None:
    """Clear counters. Used by tests, and by an operator unblocking a shared office IP."""
    with _LOCK:
        if bucket is None:
            _HITS.clear()
        else:
            for key in [k for k in _HITS if k[0] == bucket]:
                del _HITS[key]


def remaining(bucket: str, identity: str, limit: Limit, *, now: float | None = None) -> int:
    moment = now if now is not None else time.monotonic()
    with _LOCK:
        hits = [t for t in _HITS.get((bucket, identity), []) if moment - t < limit.window_seconds]
    return max(limit.max_requests - len(hits), 0)
