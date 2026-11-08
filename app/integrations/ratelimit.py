"""Ограничение частоты: скользящее окно в памяти процесса.
Для нескольких воркеров/инстансов замените _hits на Redis (INCR + EXPIRE) — интерфейс тот же."""
from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque

from .errors import ApiError, error_response


class RateLimiter:
    def __init__(self):
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._last_gc = time.monotonic()

    def hit(self, key: str, limit: int, window_s: int) -> int:
        """0 — запрос разрешён и учтён; иначе — через сколько секунд повторить."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window_s:
                q.popleft()
            if len(q) >= limit:
                return max(1, math.ceil(q[0] + window_s - now))
            q.append(now)
            if now - self._last_gc > 300:           # чистим «мёртвые» ключи
                self._last_gc = now
                for k in [k for k, v in self._hits.items() if not v or v[-1] <= now - 86400]:
                    del self._hits[k]
            return 0

    def check(self, key: str, limit: int, window_s: int) -> None:
        wait = self.hit(key, limit, window_s)
        if wait:
            raise ApiError(429, "TOO_MANY_REQUESTS", "Слишком много запросов. Повторите позже",
                           headers={"Retry-After": str(wait)})


limiter = RateLimiter()


class GlobalRateLimitMiddleware:
    """Грубый лимит на IP для всего API (защита от перебора и случайных циклов клиента)."""

    def __init__(self, app, limit: int, window_s: int, skip_paths=("/health",)):
        self.app, self.limit, self.window, self.skip = app, limit, window_s, set(skip_paths)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] not in self.skip:
            ip = (scope.get("client") or ("-", 0))[0]
            wait = limiter.hit(f"ip:{ip}", self.limit, self.window)
            if wait:
                resp = error_response(429, "TOO_MANY_REQUESTS", "Слишком много запросов. Повторите позже",
                                      headers={"Retry-After": str(wait)})
                return await resp(scope, receive, send)
        await self.app(scope, receive, send)
