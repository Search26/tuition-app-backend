"""Fixed-window counters in Redis for the login rate limit.

Redis being down must not lock everyone out, so every call here fails open and logs a warning.
"""

import hashlib
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.errors import RateLimitedError

logger = logging.getLogger(__name__)


def _ip_key(ip: str) -> str:
    return f"ratelimit:login:ip:{ip}"


def _email_key(email: str) -> str:
    # Hashed so raw addresses never sit in Redis.
    return f"ratelimit:login:email:{hashlib.sha256(email.encode()).hexdigest()}"


class LoginRateLimiter:
    def __init__(self, redis: Redis, settings: Settings):
        self._redis = redis
        self._settings = settings

    async def check(self, ip: str, email: str) -> None:
        """Count this attempt against the IP and raise if either limit is already hit."""
        s = self._settings
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(_ip_key(ip))
                pipe.expire(_ip_key(ip), s.login_ip_window_seconds, nx=True)
                pipe.ttl(_ip_key(ip))
                pipe.get(_email_key(email))
                pipe.ttl(_email_key(email))
                ip_count, _, ip_ttl, email_failures, email_ttl = await pipe.execute()
        except RedisError:
            logger.warning("Login rate limit skipped: Redis unavailable")
            return

        if ip_count > s.login_ip_limit:
            raise RateLimitedError(_retry_after(ip_ttl, s.login_ip_window_seconds))
        if email_failures is not None and int(email_failures) >= s.login_email_failure_limit:
            raise RateLimitedError(_retry_after(email_ttl, s.login_email_window_seconds))

    async def record_failure(self, email: str) -> None:
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(_email_key(email))
                pipe.expire(_email_key(email), self._settings.login_email_window_seconds, nx=True)
                await pipe.execute()
        except RedisError:
            logger.warning("Login failure not counted: Redis unavailable")

    async def reset(self, email: str) -> None:
        try:
            await self._redis.delete(_email_key(email))
        except RedisError:
            logger.warning("Login failure count not reset: Redis unavailable")


def _retry_after(ttl: int, window: int) -> int:
    # TTL is -1/-2 if the key has no expiry or vanished between commands.
    return ttl if ttl > 0 else window
