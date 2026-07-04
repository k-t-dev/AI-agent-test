from __future__ import annotations

import re
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from threading import Lock

EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE = re.compile(r"\b0\d{1,4}-\d{1,4}-\d{3,4}\b")
CARD = re.compile(r"\b(?:\d[ -]*?){13,16}\b")


def mask_pii(value: str) -> str:
    value = EMAIL.sub("[EMAIL_MASKED]", value)
    value = PHONE.sub("[PHONE_MASKED]", value)
    return CARD.sub("[CARD_MASKED]", value)


class RateLimiter:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._events: dict[str, deque[datetime]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, subject: str) -> bool:
        cutoff = datetime.now(UTC) - timedelta(minutes=1)
        with self._lock:
            events = self._events[subject]
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(datetime.now(UTC))
            return True

