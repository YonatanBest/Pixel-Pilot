"""
Redis-backed rate limiter for generation requests.

Policy:
- 1000 successful generate requests per user per UTC day
- 60 successful generate requests per user per UTC minute
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis.asyncio as redis

DAILY_LIMIT = 1000
MINUTE_LIMIT = 60
OCR_REQUESTS_PER_DAY = max(
    1, int(os.getenv("OCR_REQUESTS_PER_DAY", str(DAILY_LIMIT)) or str(DAILY_LIMIT))
)
OCR_REQUESTS_PER_MINUTE = max(
    1, int(os.getenv("OCR_REQUESTS_PER_MINUTE", str(MINUTE_LIMIT)) or str(MINUTE_LIMIT))
)
LIVE_MAX_CONCURRENT_SESSIONS = max(
    1, int(os.getenv("LIVE_MAX_CONCURRENT_SESSIONS", "1") or "1")
)
LIVE_MAX_ACTIVE_SESSIONS_PER_USER = max(
    1, int(os.getenv("LIVE_MAX_ACTIVE_SESSIONS_PER_USER", "1") or "1")
)
LIVE_SESSION_STARTS_PER_MINUTE = max(
    1, int(os.getenv("LIVE_SESSION_STARTS_PER_MINUTE", "2") or "2")
)
LIVE_SESSION_STARTS_PER_DAY = max(
    1, int(os.getenv("LIVE_SESSION_STARTS_PER_DAY", "5") or "5")
)
LIVE_SESSION_LEASE_TTL_SECONDS = max(
    5, int(os.getenv("LIVE_SESSION_LEASE_TTL_SECONDS", "30") or "30")
)
LIVE_SESSION_HEARTBEAT_SECONDS = max(
    1, int(os.getenv("LIVE_SESSION_HEARTBEAT_SECONDS", "10") or "10")
)

WINDOW_DAY = "day"
WINDOW_MINUTE = "minute"
WINDOW_LIVE_CONCURRENT = "concurrent"

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

LIVE_RESERVE_SCRIPT = """
local day_key = KEYS[1]
local minute_key = KEYS[2]
local global_lease_key = KEYS[3]
local user_lease_key = KEYS[4]

local global_pattern = ARGV[1]
local user_pattern = ARGV[2]
local day_limit = tonumber(ARGV[3])
local minute_limit = tonumber(ARGV[4])
local global_limit = tonumber(ARGV[5])
local user_limit = tonumber(ARGV[6])
local day_ttl = tonumber(ARGV[7])
local minute_ttl = tonumber(ARGV[8])
local lease_ttl = tonumber(ARGV[9])

local global_keys = redis.call("KEYS", global_pattern)
local user_keys = redis.call("KEYS", user_pattern)
local global_active = #global_keys
local user_active = #user_keys

if global_active >= global_limit then
    return {0, "concurrent", "live_global", global_active, global_limit, 0, lease_ttl, 0, 0}
end

if user_active >= user_limit then
    return {0, "concurrent", "live_user", user_active, user_limit, 0, lease_ttl, 0, 0}
end

local day_count = tonumber(redis.call("GET", day_key) or "0")
local minute_count = tonumber(redis.call("GET", minute_key) or "0")

if day_count >= day_limit then
    return {0, "day", "live", day_count, day_limit, math.max(day_limit - day_count, 0), day_ttl, day_count, minute_count}
end

if minute_count >= minute_limit then
    return {0, "minute", "live", minute_count, minute_limit, math.max(minute_limit - minute_count, 0), minute_ttl, day_count, minute_count}
end

redis.call("SET", global_lease_key, "1", "EX", lease_ttl)
redis.call("SET", user_lease_key, "1", "EX", lease_ttl)

local new_day_count = redis.call("INCR", day_key)
redis.call("EXPIRE", day_key, day_ttl)

local new_minute_count = redis.call("INCR", minute_key)
redis.call("EXPIRE", minute_key, minute_ttl)

return {1, "", "live", 0, 0, 0, 0, new_day_count, new_minute_count}
"""

LIVE_REFUND_SCRIPT = """
local day_key = KEYS[1]
local minute_key = KEYS[2]
local global_lease_key = KEYS[3]
local user_lease_key = KEYS[4]

redis.call("DEL", global_lease_key)
redis.call("DEL", user_lease_key)

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

LIVE_RELEASE_SCRIPT = """
local global_lease_key = KEYS[1]
local user_lease_key = KEYS[2]

local removed = 0
removed = removed + redis.call("DEL", global_lease_key)
removed = removed + redis.call("DEL", user_lease_key)
return {removed}
"""

LIVE_REFRESH_SCRIPT = """
local global_lease_key = KEYS[1]
local user_lease_key = KEYS[2]
local lease_ttl = tonumber(ARGV[1])

local refreshed = 0
if redis.call("EXISTS", global_lease_key) == 1 then
    redis.call("EXPIRE", global_lease_key, lease_ttl)
    refreshed = refreshed + 1
end
if redis.call("EXISTS", user_lease_key) == 1 then
    redis.call("EXPIRE", user_lease_key, lease_ttl)
    refreshed = refreshed + 1
end

return {refreshed}
"""


@dataclass(frozen=True)
class RateLimitReservation:
    allowed: bool
    scope: str
    window: Optional[str]
    current: int
    limit: int
    remaining: int
    retry_after_seconds: int
    daily_remaining: int
    minute_remaining: int
    day_key: str
    minute_key: str


@dataclass(frozen=True)
class LiveSessionReservation:
    allowed: bool
    window: Optional[str]
    scope: str
    current: int
    limit: int
    remaining: int
    retry_after_seconds: int
    daily_remaining: int
    minute_remaining: int
    session_id: str
    user_id: str
    day_key: str
    minute_key: str
    global_lease_key: str
    user_lease_key: str


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
    scope: str = "generate",
    now: Optional[datetime] = None,
) -> tuple[str, str]:
    current_time = now or _utc_now()
    scope_key = str(scope or "generate").strip().lower() or "generate"
    day_key = current_time.strftime(
        f"ratelimit:{scope_key}:{WINDOW_DAY}:{user_id}:%Y-%m-%d"
    )
    minute_key = current_time.strftime(
        f"ratelimit:{scope_key}:{WINDOW_MINUTE}:{user_id}:%Y-%m-%dT%H:%M"
    )
    return day_key, minute_key


def _get_live_rate_limit_keys(
    user_id: str,
    session_id: str,
    *,
    now: Optional[datetime] = None,
) -> tuple[str, str, str, str, str, str]:
    current_time = now or _utc_now()
    day_key = current_time.strftime(f"ratelimit:live:start:{WINDOW_DAY}:{user_id}:%Y-%m-%d")
    minute_key = current_time.strftime(
        f"ratelimit:live:start:{WINDOW_MINUTE}:{user_id}:%Y-%m-%dT%H:%M"
    )
    global_lease_key = f"ratelimit:live:lease:global:{session_id}"
    user_lease_key = f"ratelimit:live:lease:user:{user_id}:{session_id}"
    global_pattern = "ratelimit:live:lease:global:*"
    user_pattern = f"ratelimit:live:lease:user:{user_id}:*"
    return (
        day_key,
        minute_key,
        global_lease_key,
        user_lease_key,
        global_pattern,
        user_pattern,
    )


def _to_int(value: object) -> int:
    return int(value or 0)


def _normalize_reservation(
    raw_result: list[object],
    *,
    scope: str,
    daily_limit: int,
    minute_limit: int,
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

    daily_remaining = max(0, daily_limit - day_count)
    minute_remaining = max(0, minute_limit - minute_count)

    if allowed:
        current = 0
        limit = 0
        remaining = daily_remaining
        retry_after_seconds = 0
        window = None

    return RateLimitReservation(
        allowed=allowed,
        scope=str(scope or "generate"),
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


def _normalize_live_reservation(
    raw_result: list[object],
    *,
    user_id: str,
    session_id: str,
    day_key: str,
    minute_key: str,
    global_lease_key: str,
    user_lease_key: str,
) -> LiveSessionReservation:
    allowed = bool(_to_int(raw_result[0]))
    window = str(raw_result[1] or "") or None
    scope = str(raw_result[2] or "live")
    current = _to_int(raw_result[3])
    limit = _to_int(raw_result[4])
    remaining = _to_int(raw_result[5])
    retry_after_seconds = _to_int(raw_result[6])
    day_count = _to_int(raw_result[7])
    minute_count = _to_int(raw_result[8])

    daily_remaining = max(0, LIVE_SESSION_STARTS_PER_DAY - day_count)
    minute_remaining = max(0, LIVE_SESSION_STARTS_PER_MINUTE - minute_count)

    if allowed:
        current = 0
        limit = 0
        remaining = daily_remaining
        retry_after_seconds = 0
        window = None
        scope = "live"

    return LiveSessionReservation(
        allowed=allowed,
        window=window,
        scope=scope,
        current=current,
        limit=limit,
        remaining=remaining,
        retry_after_seconds=retry_after_seconds,
        daily_remaining=daily_remaining,
        minute_remaining=minute_remaining,
        session_id=session_id,
        user_id=user_id,
        day_key=day_key,
        minute_key=minute_key,
        global_lease_key=global_lease_key,
        user_lease_key=user_lease_key,
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
    day_key, minute_key = _get_rate_limit_keys(user_id, scope="generate", now=now)
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
        scope="generate",
        daily_limit=DAILY_LIMIT,
        minute_limit=MINUTE_LIMIT,
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
        scope="generate",
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


async def reserve_ocr_request(
    user_id: str,
    redis_client: redis.Redis,
) -> RateLimitReservation:
    now = _utc_now()
    day_key, minute_key = _get_rate_limit_keys(user_id, scope="ocr", now=now)
    day_ttl = _seconds_until_next_utc_midnight(now)
    minute_ttl = _seconds_until_next_utc_minute(now)

    raw_result = await redis_client.eval(
        RESERVE_SCRIPT,
        2,
        day_key,
        minute_key,
        OCR_REQUESTS_PER_DAY,
        OCR_REQUESTS_PER_MINUTE,
        day_ttl,
        minute_ttl,
    )
    return _normalize_reservation(
        raw_result,
        scope="ocr",
        daily_limit=OCR_REQUESTS_PER_DAY,
        minute_limit=OCR_REQUESTS_PER_MINUTE,
        day_key=day_key,
        minute_key=minute_key,
    )


async def refund_ocr_request(
    reservation: RateLimitReservation,
    redis_client: redis.Redis,
) -> RateLimitReservation:
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
        scope="ocr",
        window=None,
        current=0,
        limit=0,
        remaining=max(0, OCR_REQUESTS_PER_DAY - day_count),
        retry_after_seconds=0,
        daily_remaining=max(0, OCR_REQUESTS_PER_DAY - day_count),
        minute_remaining=max(0, OCR_REQUESTS_PER_MINUTE - minute_count),
        day_key=reservation.day_key,
        minute_key=reservation.minute_key,
    )


async def reserve_live_session_start(
    user_id: str,
    session_id: str,
    redis_client: redis.Redis,
) -> LiveSessionReservation:
    now = _utc_now()
    (
        day_key,
        minute_key,
        global_lease_key,
        user_lease_key,
        global_pattern,
        user_pattern,
    ) = _get_live_rate_limit_keys(user_id, session_id, now=now)
    day_ttl = _seconds_until_next_utc_midnight(now)
    minute_ttl = _seconds_until_next_utc_minute(now)

    raw_result = await redis_client.eval(
        LIVE_RESERVE_SCRIPT,
        4,
        day_key,
        minute_key,
        global_lease_key,
        user_lease_key,
        global_pattern,
        user_pattern,
        LIVE_SESSION_STARTS_PER_DAY,
        LIVE_SESSION_STARTS_PER_MINUTE,
        LIVE_MAX_CONCURRENT_SESSIONS,
        LIVE_MAX_ACTIVE_SESSIONS_PER_USER,
        day_ttl,
        minute_ttl,
        LIVE_SESSION_LEASE_TTL_SECONDS,
    )
    return _normalize_live_reservation(
        raw_result,
        user_id=user_id,
        session_id=session_id,
        day_key=day_key,
        minute_key=minute_key,
        global_lease_key=global_lease_key,
        user_lease_key=user_lease_key,
    )


async def refund_live_session_start(
    reservation: LiveSessionReservation,
    redis_client: redis.Redis,
) -> LiveSessionReservation:
    raw_result = await redis_client.eval(
        LIVE_REFUND_SCRIPT,
        4,
        reservation.day_key,
        reservation.minute_key,
        reservation.global_lease_key,
        reservation.user_lease_key,
    )
    day_count = _to_int(raw_result[0])
    minute_count = _to_int(raw_result[1])
    return LiveSessionReservation(
        allowed=True,
        window=None,
        scope="live",
        current=0,
        limit=0,
        remaining=max(0, LIVE_SESSION_STARTS_PER_DAY - day_count),
        retry_after_seconds=0,
        daily_remaining=max(0, LIVE_SESSION_STARTS_PER_DAY - day_count),
        minute_remaining=max(0, LIVE_SESSION_STARTS_PER_MINUTE - minute_count),
        session_id=reservation.session_id,
        user_id=reservation.user_id,
        day_key=reservation.day_key,
        minute_key=reservation.minute_key,
        global_lease_key=reservation.global_lease_key,
        user_lease_key=reservation.user_lease_key,
    )


async def release_live_session(
    reservation: LiveSessionReservation,
    redis_client: redis.Redis,
) -> bool:
    raw_result = await redis_client.eval(
        LIVE_RELEASE_SCRIPT,
        2,
        reservation.global_lease_key,
        reservation.user_lease_key,
    )
    return bool(_to_int(raw_result[0]))


async def refresh_live_session_lease(
    reservation: LiveSessionReservation,
    redis_client: redis.Redis,
) -> bool:
    raw_result = await redis_client.eval(
        LIVE_REFRESH_SCRIPT,
        2,
        reservation.global_lease_key,
        reservation.user_lease_key,
        LIVE_SESSION_LEASE_TTL_SECONDS,
    )
    return _to_int(raw_result[0]) == 2


async def get_remaining_requests(
    user_id: str,
    redis_client: redis.Redis,
) -> int:
    """Get the remaining daily requests for the current UTC day."""
    day_key, _ = _get_rate_limit_keys(user_id)
    current = await redis_client.get(day_key)
    current_count = _to_int(current)
    return max(0, DAILY_LIMIT - current_count)
