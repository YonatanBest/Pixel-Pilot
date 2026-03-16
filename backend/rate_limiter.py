"""
Redis-backed rate limiter for generation requests.

Policy:
- 1000 successful generate requests per user per UTC day
- 60 successful generate requests per user per UTC minute
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis.asyncio as redis

DAILY_LIMIT = 1000
MINUTE_LIMIT = 60

WINDOW_DAY = "day"
WINDOW_MINUTE = "minute"

RESERVE_SCRIPT = """
local day_key = KEYS[1]
local minute_key = KEYS[2]

local day_limit = tonumber(ARGV[1])
local minute_limit = tonumber(ARGV[2])
local day_ttl = tonumber(ARGV[3])
local minute_ttl = tonumber(ARGV[4])

local day_count = tonumber(redis.call("GET", day_key) or "0")
local minute_count = tonumber(redis.call("GET", minute_key) or "0")

if day_count >= day_limit then
    return {0, "day", day_count, day_limit, math.max(day_limit - day_count, 0), day_ttl, day_count, minute_count}
end

if minute_count >= minute_limit then
    return {0, "minute", minute_count, minute_limit, math.max(minute_limit - minute_count, 0), minute_ttl, day_count, minute_count}
end

local new_day_count = redis.call("INCR", day_key)
redis.call("EXPIRE", day_key, day_ttl)

local new_minute_count = redis.call("INCR", minute_key)
redis.call("EXPIRE", minute_key, minute_ttl)

return {
    1,
    "",
    0,
    0,
    0,
    0,
    new_day_count,
    new_minute_count
}
"""

REFUND_SCRIPT = """
local day_key = KEYS[1]
local minute_key = KEYS[2]

local day_count = tonumber(redis.call("GET", day_key) or "0")
local minute_count = tonumber(redis.call("GET", minute_key) or "0")

if day_count > 0 then
    day_count = tonumber(redis.call("DECR", day_key))
    if day_count <= 0 then
        redis.call("DEL", day_key)
        day_count = 0
    end
end

if minute_count > 0 then
    minute_count = tonumber(redis.call("DECR", minute_key))
    if minute_count <= 0 then
        redis.call("DEL", minute_key)
        minute_count = 0
    end
end

return {day_count, minute_count}
"""


@dataclass(frozen=True)
class RateLimitReservation:
    allowed: bool
    window: Optional[str]
    current: int
    limit: int
    remaining: int
    retry_after_seconds: int
    daily_remaining: int
    minute_remaining: int
    day_key: str
    minute_key: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _seconds_until_next_utc_minute(now: datetime) -> int:
    next_minute = (now.replace(second=0, microsecond=0) + timedelta(minutes=1))
    return max(1, int((next_minute - now).total_seconds()))


def _seconds_until_next_utc_midnight(now: datetime) -> int:
    next_midnight = (now + timedelta(days=1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return max(1, int((next_midnight - now).total_seconds()))


def _get_rate_limit_keys(
    user_id: str,
    *,
    now: Optional[datetime] = None,
) -> tuple[str, str]:
    current_time = now or _utc_now()
    day_key = current_time.strftime(f"ratelimit:generate:{WINDOW_DAY}:{user_id}:%Y-%m-%d")
    minute_key = current_time.strftime(
        f"ratelimit:generate:{WINDOW_MINUTE}:{user_id}:%Y-%m-%dT%H:%M"
    )
    return day_key, minute_key


def _to_int(value: object) -> int:
    return int(value or 0)


def _normalize_reservation(
    raw_result: list[object],
    *,
    day_key: str,
    minute_key: str,
) -> RateLimitReservation:
    allowed = bool(_to_int(raw_result[0]))
    window = str(raw_result[1] or "") or None
    current = _to_int(raw_result[2])
    limit = _to_int(raw_result[3])
    remaining = _to_int(raw_result[4])
    retry_after_seconds = _to_int(raw_result[5])
    day_count = _to_int(raw_result[6])
    minute_count = _to_int(raw_result[7])

    daily_remaining = max(0, DAILY_LIMIT - day_count)
    minute_remaining = max(0, MINUTE_LIMIT - minute_count)

    if allowed:
        current = 0
        limit = 0
        remaining = daily_remaining
        retry_after_seconds = 0
        window = None

    return RateLimitReservation(
        allowed=allowed,
        window=window,
        current=current,
        limit=limit,
        remaining=remaining,
        retry_after_seconds=retry_after_seconds,
        daily_remaining=daily_remaining,
        minute_remaining=minute_remaining,
        day_key=day_key,
        minute_key=minute_key,
    )


async def reserve_generate_request(
    user_id: str,
    redis_client: redis.Redis,
) -> RateLimitReservation:
    """
    Atomically reserve quota for a generate request.

    A successful reservation must be refunded if the downstream generation fails.
    """
    now = _utc_now()
    day_key, minute_key = _get_rate_limit_keys(user_id, now=now)
    day_ttl = _seconds_until_next_utc_midnight(now)
    minute_ttl = _seconds_until_next_utc_minute(now)

    raw_result = await redis_client.eval(
        RESERVE_SCRIPT,
        2,
        day_key,
        minute_key,
        DAILY_LIMIT,
        MINUTE_LIMIT,
        day_ttl,
        minute_ttl,
    )
    return _normalize_reservation(
        raw_result,
        day_key=day_key,
        minute_key=minute_key,
    )


async def refund_generate_request(
    reservation: RateLimitReservation,
    redis_client: redis.Redis,
) -> RateLimitReservation:
    """
    Refund a previously reserved generate request after downstream failure.
    """
    raw_result = await redis_client.eval(
        REFUND_SCRIPT,
        2,
        reservation.day_key,
        reservation.minute_key,
    )
    day_count = _to_int(raw_result[0])
    minute_count = _to_int(raw_result[1])

    return RateLimitReservation(
        allowed=True,
        window=None,
        current=0,
        limit=0,
        remaining=max(0, DAILY_LIMIT - day_count),
        retry_after_seconds=0,
        daily_remaining=max(0, DAILY_LIMIT - day_count),
        minute_remaining=max(0, MINUTE_LIMIT - minute_count),
        day_key=reservation.day_key,
        minute_key=reservation.minute_key,
    )


async def get_remaining_requests(
    user_id: str,
    redis_client: redis.Redis,
) -> int:
    """Get the remaining daily requests for the current UTC day."""
    day_key, _ = _get_rate_limit_keys(user_id)
    current = await redis_client.get(day_key)
    current_count = _to_int(current)
    return max(0, DAILY_LIMIT - current_count)
