"""
Redis-backed quota tracking for backend generation and live sessions.

Policy:
- 1000 successful generate requests per user per UTC day
- 60 successful generate requests per user per UTC minute
- Gemini Live usage is limited by total active session seconds per user per UTC day
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis.asyncio as redis

DAILY_LIMIT = 1000
MINUTE_LIMIT = 60
LIVE_MAX_CONCURRENT_SESSIONS = max(
    1, int(os.getenv("LIVE_MAX_CONCURRENT_SESSIONS", "2"))
)
LIVE_MAX_ACTIVE_SESSIONS_PER_USER = max(
    1, int(os.getenv("LIVE_MAX_ACTIVE_SESSIONS_PER_USER", "1"))
)
LIVE_SESSION_SECONDS_PER_DAY = max(
    60, int(os.getenv("LIVE_SESSION_SECONDS_PER_DAY", "600"))
)
LIVE_SESSION_LEASE_TTL_SECONDS = max(
    5, int(os.getenv("LIVE_SESSION_LEASE_TTL_SECONDS", "30"))
)
LIVE_SESSION_HEARTBEAT_SECONDS = max(
    1, int(os.getenv("LIVE_SESSION_HEARTBEAT_SECONDS", "10"))
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
local usage_key = KEYS[1]
local global_active_key = KEYS[2]
local user_active_key = KEYS[3]
local session_key = KEYS[4]
local token_key = KEYS[5]

local session_id = ARGV[1]
local user_id = ARGV[2]
local session_token = ARGV[3]
local time_budget = tonumber(ARGV[4])
local global_limit = tonumber(ARGV[5])
local user_limit = tonumber(ARGV[6])
local usage_ttl = tonumber(ARGV[7])
local lease_ttl = tonumber(ARGV[8])
local now_ts = tonumber(ARGV[9])
local expiry_ts = tonumber(ARGV[10])

redis.call("ZREMRANGEBYSCORE", global_active_key, "-inf", now_ts)
redis.call("ZREMRANGEBYSCORE", user_active_key, "-inf", now_ts)

local global_active = tonumber(redis.call("ZCARD", global_active_key) or "0")
local user_active = tonumber(redis.call("ZCARD", user_active_key) or "0")

if global_active >= global_limit then
    return {0, "concurrent", "live_global", global_active, global_limit, 0, lease_ttl, 0}
end

if user_active >= user_limit then
    return {0, "concurrent", "live_user", user_active, user_limit, 0, lease_ttl, 0}
end

local used_seconds = tonumber(redis.call("GET", usage_key) or "0")
if used_seconds >= time_budget then
    return {0, "day", "live_time", used_seconds, time_budget, math.max(time_budget - used_seconds, 0), usage_ttl, used_seconds}
end

redis.call("HSET", session_key, "session_id", session_id, "user_id", user_id, "token", session_token, "last_ts", now_ts, "expires_at", expiry_ts)
redis.call("EXPIRE", session_key, lease_ttl)
redis.call("SET", token_key, session_id, "EX", lease_ttl)
redis.call("ZADD", global_active_key, expiry_ts, session_id)
redis.call("ZADD", user_active_key, expiry_ts, session_id)

return {1, "", "live_time", used_seconds, time_budget, math.max(time_budget - used_seconds, 0), 0, used_seconds}
"""

LIVE_REFUND_SCRIPT = """
local global_active_key = KEYS[1]
local user_active_key = KEYS[2]
local session_key = KEYS[3]
local token_key = KEYS[4]
local usage_key = KEYS[5]
local session_id = ARGV[1]

redis.call("ZREM", global_active_key, session_id)
redis.call("ZREM", user_active_key, session_id)
redis.call("DEL", session_key)
redis.call("DEL", token_key)

local used_seconds = tonumber(redis.call("GET", usage_key) or "0")
return {used_seconds}
"""

LIVE_RELEASE_SCRIPT = """
local usage_key = KEYS[1]
local global_active_key = KEYS[2]
local user_active_key = KEYS[3]
local session_key = KEYS[4]
local token_key = KEYS[5]

local session_id = ARGV[1]
local usage_ttl = tonumber(ARGV[2])
local now_ts = tonumber(ARGV[3])

local used_seconds = tonumber(redis.call("GET", usage_key) or "0")
if redis.call("EXISTS", session_key) == 1 then
    local last_ts = tonumber(redis.call("HGET", session_key, "last_ts") or tostring(now_ts))
    local delta = math.max(0, now_ts - last_ts)
    if delta > 0 then
        used_seconds = tonumber(redis.call("INCRBY", usage_key, delta))
        redis.call("EXPIRE", usage_key, usage_ttl)
    end
end

redis.call("ZREM", global_active_key, session_id)
redis.call("ZREM", user_active_key, session_id)
redis.call("DEL", session_key)
redis.call("DEL", token_key)
return {1, used_seconds}
"""

LIVE_REFRESH_SCRIPT = """
local usage_key = KEYS[1]
local global_active_key = KEYS[2]
local user_active_key = KEYS[3]
local session_key = KEYS[4]
local token_key = KEYS[5]

local session_id = ARGV[1]
local time_budget = tonumber(ARGV[2])
local lease_ttl = tonumber(ARGV[3])
local usage_ttl = tonumber(ARGV[4])
local now_ts = tonumber(ARGV[5])
local expiry_ts = tonumber(ARGV[6])

local used_seconds = tonumber(redis.call("GET", usage_key) or "0")
if redis.call("EXISTS", session_key) ~= 1 or redis.call("EXISTS", token_key) ~= 1 then
    redis.call("ZREM", global_active_key, session_id)
    redis.call("ZREM", user_active_key, session_id)
    return {0, used_seconds}
end

local last_ts = tonumber(redis.call("HGET", session_key, "last_ts") or tostring(now_ts))
local delta = math.max(0, now_ts - last_ts)
if delta > 0 then
    used_seconds = tonumber(redis.call("INCRBY", usage_key, delta))
    redis.call("EXPIRE", usage_key, usage_ttl)
end

if used_seconds >= time_budget then
    redis.call("ZREM", global_active_key, session_id)
    redis.call("ZREM", user_active_key, session_id)
    redis.call("DEL", session_key)
    redis.call("DEL", token_key)
    return {1, used_seconds}
end

redis.call("HSET", session_key, "last_ts", now_ts, "expires_at", expiry_ts)
redis.call("EXPIRE", session_key, lease_ttl)
redis.call("SET", token_key, session_id, "EX", lease_ttl)
redis.call("ZADD", global_active_key, expiry_ts, session_id)
redis.call("ZADD", user_active_key, expiry_ts, session_id)
return {2, used_seconds}
"""

LIVE_VALIDATE_TOKEN_SCRIPT = """
local token_key = KEYS[1]
local session_key = KEYS[2]
local global_active_key = KEYS[3]
local user_active_key = KEYS[4]

local expected_session_id = ARGV[1]
local expected_user_id = ARGV[2]
local now_ts = tonumber(ARGV[3])

local actual_session_id = redis.call("GET", token_key)
if not actual_session_id or actual_session_id ~= expected_session_id then
    return 0
end

if redis.call("EXISTS", session_key) ~= 1 then
    return 0
end

if redis.call("HGET", session_key, "user_id") ~= expected_user_id then
    return 0
end

redis.call("ZREMRANGEBYSCORE", global_active_key, "-inf", now_ts)
redis.call("ZREMRANGEBYSCORE", user_active_key, "-inf", now_ts)

if not redis.call("ZSCORE", global_active_key, expected_session_id) then
    return 0
end
if not redis.call("ZSCORE", user_active_key, expected_session_id) then
    return 0
end

return 1
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
    session_token: str
    user_id: str
    usage_key: str
    global_active_key: str
    user_active_key: str
    session_key: str
    token_key: str


@dataclass(frozen=True)
class LiveSessionLeaseRefreshResult:
    status: str
    used_seconds: int
    remaining_seconds: int

    @property
    def ok(self) -> bool:
        return self.status == "ok"


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


def _hash_live_session_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _get_live_rate_limit_keys(
    user_id: str,
    session_id: str,
    session_token: str,
    *,
    now: Optional[datetime] = None,
) -> tuple[str, str, str, str, str]:
    current_time = now or _utc_now()
    usage_key = current_time.strftime(
        f"ratelimit:live:usage:{WINDOW_DAY}:{user_id}:%Y-%m-%d"
    )
    global_active_key = "ratelimit:live:active:global"
    user_active_key = f"ratelimit:live:active:user:{user_id}"
    session_key = f"ratelimit:live:session:{session_id}"
    token_key = f"ratelimit:live:token:{_hash_live_session_token(session_token)}"
    return (
        usage_key,
        global_active_key,
        user_active_key,
        session_key,
        token_key,
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
    session_token: str,
    usage_key: str,
    global_active_key: str,
    user_active_key: str,
    session_key: str,
    token_key: str,
) -> LiveSessionReservation:
    allowed = bool(_to_int(raw_result[0]))
    window = str(raw_result[1] or "") or None
    scope = str(raw_result[2] or "live")
    current = _to_int(raw_result[3])
    limit = _to_int(raw_result[4])
    remaining = _to_int(raw_result[5])
    retry_after_seconds = _to_int(raw_result[6])
    used_seconds = _to_int(raw_result[7])

    daily_remaining = max(0, LIVE_SESSION_SECONDS_PER_DAY - used_seconds)
    minute_remaining = 0

    if allowed:
        current = used_seconds
        limit = LIVE_SESSION_SECONDS_PER_DAY
        remaining = daily_remaining
        retry_after_seconds = 0
        window = None
        scope = "live_time"

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
        session_token=session_token,
        user_id=user_id,
        usage_key=usage_key,
        global_active_key=global_active_key,
        user_active_key=user_active_key,
        session_key=session_key,
        token_key=token_key,
    )


async def reserve_generate_request(
    user_id: str,
    redis_client: redis.Redis,
) -> RateLimitReservation:
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


async def reserve_live_session_start(
    user_id: str,
    session_id: str,
    session_token: str,
    redis_client: redis.Redis,
) -> LiveSessionReservation:
    now = _utc_now()
    (
        usage_key,
        global_active_key,
        user_active_key,
        session_key,
        token_key,
    ) = _get_live_rate_limit_keys(user_id, session_id, session_token, now=now)
    usage_ttl = _seconds_until_next_utc_midnight(now)
    now_ts = int(now.timestamp())
    expiry_ts = now_ts + LIVE_SESSION_LEASE_TTL_SECONDS

    raw_result = await redis_client.eval(
        LIVE_RESERVE_SCRIPT,
        5,
        usage_key,
        global_active_key,
        user_active_key,
        session_key,
        token_key,
        session_id,
        user_id,
        session_token,
        LIVE_SESSION_SECONDS_PER_DAY,
        LIVE_MAX_CONCURRENT_SESSIONS,
        LIVE_MAX_ACTIVE_SESSIONS_PER_USER,
        usage_ttl,
        LIVE_SESSION_LEASE_TTL_SECONDS,
        now_ts,
        expiry_ts,
    )
    return _normalize_live_reservation(
        raw_result,
        user_id=user_id,
        session_id=session_id,
        session_token=session_token,
        usage_key=usage_key,
        global_active_key=global_active_key,
        user_active_key=user_active_key,
        session_key=session_key,
        token_key=token_key,
    )


async def refund_live_session_start(
    reservation: LiveSessionReservation,
    redis_client: redis.Redis,
) -> LiveSessionReservation:
    raw_result = await redis_client.eval(
        LIVE_REFUND_SCRIPT,
        5,
        reservation.global_active_key,
        reservation.user_active_key,
        reservation.session_key,
        reservation.token_key,
        reservation.usage_key,
        reservation.session_id,
    )
    used_seconds = _to_int(raw_result[0])
    return LiveSessionReservation(
        allowed=True,
        window=None,
        scope="live_time",
        current=0,
        limit=LIVE_SESSION_SECONDS_PER_DAY,
        remaining=max(0, LIVE_SESSION_SECONDS_PER_DAY - used_seconds),
        retry_after_seconds=0,
        daily_remaining=max(0, LIVE_SESSION_SECONDS_PER_DAY - used_seconds),
        minute_remaining=0,
        session_id=reservation.session_id,
        session_token=reservation.session_token,
        user_id=reservation.user_id,
        usage_key=reservation.usage_key,
        global_active_key=reservation.global_active_key,
        user_active_key=reservation.user_active_key,
        session_key=reservation.session_key,
        token_key=reservation.token_key,
    )


async def release_live_session(
    reservation: LiveSessionReservation,
    redis_client: redis.Redis,
) -> bool:
    now = _utc_now()
    raw_result = await redis_client.eval(
        LIVE_RELEASE_SCRIPT,
        5,
        reservation.usage_key,
        reservation.global_active_key,
        reservation.user_active_key,
        reservation.session_key,
        reservation.token_key,
        reservation.session_id,
        _seconds_until_next_utc_midnight(now),
        int(now.timestamp()),
    )
    return bool(_to_int(raw_result[0]))


async def refresh_live_session_lease(
    reservation: LiveSessionReservation,
    redis_client: redis.Redis,
) -> LiveSessionLeaseRefreshResult:
    now = _utc_now()
    now_ts = int(now.timestamp())
    raw_result = await redis_client.eval(
        LIVE_REFRESH_SCRIPT,
        5,
        reservation.usage_key,
        reservation.global_active_key,
        reservation.user_active_key,
        reservation.session_key,
        reservation.token_key,
        reservation.session_id,
        LIVE_SESSION_SECONDS_PER_DAY,
        LIVE_SESSION_LEASE_TTL_SECONDS,
        _seconds_until_next_utc_midnight(now),
        now_ts,
        now_ts + LIVE_SESSION_LEASE_TTL_SECONDS,
    )
    status_code = _to_int(raw_result[0])
    used_seconds = _to_int(raw_result[1])
    status = {
        0: "missing",
        1: "expired",
        2: "ok",
    }.get(status_code, "missing")
    return LiveSessionLeaseRefreshResult(
        status=status,
        used_seconds=used_seconds,
        remaining_seconds=max(0, LIVE_SESSION_SECONDS_PER_DAY - used_seconds),
    )


async def validate_live_session_token(
    user_id: str,
    session_token: str,
    redis_client: redis.Redis,
) -> bool:
    clean_token = str(session_token or "").strip()
    if not clean_token:
        return False

    token_key = f"ratelimit:live:token:{_hash_live_session_token(clean_token)}"
    session_id = await redis_client.get(token_key)
    if not session_id:
        return False

    (
        _usage_key,
        global_active_key,
        user_active_key,
        session_key,
        token_key,
    ) = _get_live_rate_limit_keys(user_id, str(session_id), clean_token)
    return bool(
        _to_int(
            await redis_client.eval(
                LIVE_VALIDATE_TOKEN_SCRIPT,
                4,
                token_key,
                session_key,
                global_active_key,
                user_active_key,
                str(session_id),
                str(user_id),
                int(_utc_now().timestamp()),
            )
        )
    )
