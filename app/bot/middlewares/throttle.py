import logging

from aiogram import BaseMiddleware

log = logging.getLogger(__name__)


class ThrottleMiddleware(BaseMiddleware):
    """Per-user rate limit (anti-spam / anti-flood). Counts updates in a short Redis window and silently drops the
    excess. Registered first so flooding users cost almost nothing (no DB work). Admins are exempt; if Redis is
    down the limiter fails open instead of blocking everyone."""

    def __init__(
        self, redis, limit: int = 20, window: int = 5, exempt: set[int] | None = None
    ):
        self.redis, self.limit, self.window, self.exempt = (
            redis,
            limit,
            window,
            exempt or set(),
        )

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is None or user.id in self.exempt:
            return await handler(event, data)
        try:
            key = f"thr:{user.id}"
            n = await self.redis.incr(key)
            if n == 1:
                await self.redis.expire(key, self.window)
        except Exception:
            log.warning("throttle: redis unavailable, not limiting", exc_info=True)
            return await handler(event, data)
        if n > self.limit:
            cq = getattr(event, "callback_query", None)
            if n == self.limit + 1 and cq is not None and data.get("bot") is not None:
                try:  # stop the button's loading spinner once, politely
                    await data["bot"].answer_callback_query(cq.id, text="⏳")
                except Exception:
                    pass
            return None
        return await handler(event, data)
