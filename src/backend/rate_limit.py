"""A small in-memory limit on how often one person may do something.

Used for actions that reach other people (connection requests), so one
account can't flood everyone else. Counts are kept in memory only: they
reset when the server restarts, which is fine for a brake on bursts. Lasting
caps (how many requests may be waiting at once) live with the data, in the
graph services.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

MAX_TRACKED = 10_000  # people remembered at once


class RateLimiter:
    def __init__(
        self, limit: int, per_seconds: float, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.per_seconds = per_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._times: dict[str, deque[float]] = {}

    def allow(self, who: str) -> bool:
        """True, and counted, if `who` is still under the limit."""
        now = self._clock()
        with self._lock:
            times = self._times.get(who)
            if times is None:
                if len(self._times) >= MAX_TRACKED:
                    self._times.pop(next(iter(self._times)))
                times = self._times[who] = deque()
            while times and now - times[0] >= self.per_seconds:
                times.popleft()
            if len(times) >= self.limit:
                return False
            times.append(now)
            return True
