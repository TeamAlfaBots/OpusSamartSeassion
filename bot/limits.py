import time
from collections import deque


class RateLimiter:
    """Tiny in-memory sliding-window limiter (per user id)."""

    def __init__(self, max_attempts, window):
        self.max_attempts = max_attempts
        self.window = window
        self._hits = {}

    def check(self, user_id):
        """Return 0 if allowed, otherwise the seconds left to wait."""
        q = self._hits.get(user_id)
        if not q:
            return 0
        now = time.monotonic()
        while q and now - q[0] > self.window:
            q.popleft()
        if not q:
            self._hits.pop(user_id, None)
            return 0
        if len(q) >= self.max_attempts:
            return int(self.window - (now - q[0])) + 1
        return 0

    def hit(self, user_id):
        self._hits.setdefault(user_id, deque()).append(time.monotonic())
