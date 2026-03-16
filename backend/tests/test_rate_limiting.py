from __future__ import annotations

import asyncio
import os
import sys
import types
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("GEMINI_API_KEY", "test-key")

motor_stub = types.ModuleType("motor")
motor_asyncio_stub = types.ModuleType("motor.motor_asyncio")


class AsyncIOMotorClient:
    def __init__(self, *args, **kwargs):
        self.admin = types.SimpleNamespace(command=self._command)

    async def _command(self, *args, **kwargs):
        return {"ok": 1}

    def close(self):
        return None


class AsyncIOMotorDatabase:
    pass


motor_asyncio_stub.AsyncIOMotorClient = AsyncIOMotorClient
motor_asyncio_stub.AsyncIOMotorDatabase = AsyncIOMotorDatabase
motor_stub.motor_asyncio = motor_asyncio_stub
sys.modules["motor"] = motor_stub
sys.modules["motor.motor_asyncio"] = motor_asyncio_stub

redis_package_stub = types.ModuleType("redis")
redis_asyncio_stub = types.ModuleType("redis.asyncio")


class Redis:
    pass


def from_url(*args, **kwargs):
    return None


redis_asyncio_stub.Redis = Redis
redis_asyncio_stub.from_url = from_url
redis_package_stub.asyncio = redis_asyncio_stub
sys.modules["redis"] = redis_package_stub
sys.modules["redis.asyncio"] = redis_asyncio_stub

service_stub = types.ModuleType("service")


class ServiceGenerationRequest(BaseModel):
    model: str
    contents: list[dict]
    config: dict | None = None


async def default_generate_content(request):
    return {"text": f"ok:{request.model}"}


service_stub.GenerationRequest = ServiceGenerationRequest
service_stub.generate_content = default_generate_content
sys.modules["service"] = service_stub

live_service_stub = types.ModuleType("live_service")


class LiveSessionError(RuntimeError):
    def __init__(self, code: int, detail):
        super().__init__(str(detail))
        self.code = int(code)
        self.detail = detail


class BackendLiveSession:
    def __init__(self, *, websocket, user_id: str, redis_client):
        self.websocket = websocket
        self.user_id = user_id
        self.redis_client = redis_client
        self.started = False
        self._reservation = None

    async def start(self, config):
        if self.redis_client is None:
            raise LiveSessionError(503, "Service temporarily unavailable")
        reservation = await rate_limiter.reserve_live_session_start(
            self.user_id,
            f"stub-session-{self.user_id}",
            self.redis_client,
        )
        if not reservation.allowed:
            raise LiveSessionError(
                429,
                {
                    "message": "Live session blocked by rate limit",
                    "window": reservation.window,
                    "limit": reservation.limit,
                    "remaining": reservation.remaining,
                    "retry_after_seconds": reservation.retry_after_seconds,
                    "scope": reservation.scope,
                },
            )
        self._reservation = reservation
        self.started = True
        await self.websocket.send_json({"type": "live_started", "model": "stub-live"})

    async def send_text(self, text: str):
        await self.websocket.send_json(
            {
                "type": "live_event",
                "event": {
                    "server_content": {
                        "output_transcription": {"text": f"echo:{text}"},
                        "turn_complete": True,
                    }
                },
            }
        )

    async def send_audio(self, data: bytes, mime_type: str):
        await self.websocket.send_json(
            {
                "type": "live_event",
                "event": {
                    "server_content": {
                        "model_turn": {
                            "parts": [
                                {
                                    "inline_data": {
                                        "data": "",
                                        "mime_type": mime_type,
                                    }
                                }
                            ]
                        }
                    }
                },
            }
        )

    async def send_video(self, data: bytes, mime_type: str):
        await self.websocket.send_json(
            {
                "type": "live_event",
                "event": {
                    "server_content": {
                        "input_transcription": {"text": mime_type},
                    }
                },
            }
        )

    async def send_tool_responses(self, responses):
        await self.websocket.send_json(
            {
                "type": "live_event",
                "event": {
                    "tool_call": {
                        "function_calls": responses,
                    }
                },
            }
        )

    async def stop(self, *, notify_client: bool = False, close_client: bool = False):
        self.started = False
        if self._reservation is not None and self.redis_client is not None:
            await rate_limiter.release_live_session(self._reservation, self.redis_client)
            self._reservation = None
        if notify_client:
            await self.websocket.send_json({"type": "live_closed"})

    async def shutdown(self):
        self.started = False
        if self._reservation is not None and self.redis_client is not None:
            await rate_limiter.release_live_session(self._reservation, self.redis_client)
            self._reservation = None


live_service_stub.LIVE_PROVIDER_ERROR_MESSAGE = "Gemini Live session failed"
live_service_stub.LiveSessionError = LiveSessionError
live_service_stub.BackendLiveSession = BackendLiveSession
sys.modules["live_service"] = live_service_stub

auth_stub = types.ModuleType("auth")


class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str


class UserInfo(BaseModel):
    user_id: str
    email: str


async def register_user(email: str, password: str, db):
    return {"user_id": "new-user", "email": email}


async def authenticate_user(email: str, password: str, db):
    return {"user_id": "existing-user", "email": email}


def create_access_token(user_id: str, email: str) -> str:
    return "token"


def verify_access_token(token: str):
    return None


auth_stub.RegisterRequest = RegisterRequest
auth_stub.LoginRequest = LoginRequest
auth_stub.TokenResponse = TokenResponse
auth_stub.UserInfo = UserInfo
auth_stub.register_user = register_user
auth_stub.authenticate_user = authenticate_user
auth_stub.create_access_token = create_access_token
auth_stub.verify_access_token = verify_access_token
sys.modules["auth"] = auth_stub

database_stub = types.ModuleType("database")


@asynccontextmanager
async def stub_lifespan(app):
    yield


async def get_db():
    return None


async def get_redis():
    return None


database_stub.lifespan = stub_lifespan
database_stub.get_db = get_db
database_stub.get_redis = get_redis
sys.modules["database"] = database_stub

import main as backend_main  # noqa: E402
import rate_limiter  # noqa: E402


class FakeRedis:
    def __init__(self, now_func):
        self._now_func = now_func
        self._store: dict[str, int] = {}
        self._expires_at: dict[str, datetime] = {}

    def _cleanup(self) -> None:
        now = self._now_func()
        expired = [
            key for key, expires_at in self._expires_at.items() if expires_at <= now
        ]
        for key in expired:
            self._store.pop(key, None)
            self._expires_at.pop(key, None)

    def get_count(self, key: str) -> int:
        self._cleanup()
        return int(self._store.get(key, 0))

    def seed(self, key: str, value: int, ttl_seconds: int) -> None:
        self._store[key] = int(value)
        self._expires_at[key] = self._now_func() + timedelta(seconds=ttl_seconds)

    async def get(self, key: str):
        self._cleanup()
        if key not in self._store:
            return None
        return str(self._store[key])

    async def eval(self, script: str, numkeys: int, *args):
        self._cleanup()
        keys = list(args[:numkeys])
        values = list(args[numkeys:])
        if script == rate_limiter.RESERVE_SCRIPT:
            return self._eval_reserve(keys, values)
        if script == rate_limiter.REFUND_SCRIPT:
            return self._eval_refund(keys)
        if script == rate_limiter.LIVE_RESERVE_SCRIPT:
            return self._eval_live_reserve(keys, values)
        if script == rate_limiter.LIVE_REFUND_SCRIPT:
            return self._eval_live_refund(keys)
        if script == rate_limiter.LIVE_RELEASE_SCRIPT:
            return self._eval_live_release(keys)
        if script == rate_limiter.LIVE_REFRESH_SCRIPT:
            return self._eval_live_refresh(keys, values)
        raise AssertionError("Unexpected Redis script")

    async def ping(self):
        return True

    async def close(self):
        return None

    def _eval_reserve(self, keys: list[str], values: list[object]) -> list[object]:
        day_key, minute_key = keys
        day_limit = int(values[0])
        minute_limit = int(values[1])
        day_ttl = int(values[2])
        minute_ttl = int(values[3])

        day_count = self.get_count(day_key)
        minute_count = self.get_count(minute_key)

        if day_count >= day_limit:
            return [
                0,
                rate_limiter.WINDOW_DAY,
                day_count,
                day_limit,
                max(day_limit - day_count, 0),
                day_ttl,
                day_count,
                minute_count,
            ]

        if minute_count >= minute_limit:
            return [
                0,
                rate_limiter.WINDOW_MINUTE,
                minute_count,
                minute_limit,
                max(minute_limit - minute_count, 0),
                minute_ttl,
                day_count,
                minute_count,
            ]

        now = self._now_func()
        day_count += 1
        minute_count += 1
        self._store[day_key] = day_count
        self._store[minute_key] = minute_count
        self._expires_at[day_key] = now + timedelta(seconds=day_ttl)
        self._expires_at[minute_key] = now + timedelta(seconds=minute_ttl)

        return [1, "", 0, 0, 0, 0, day_count, minute_count]

    def _eval_refund(self, keys: list[str]) -> list[int]:
        day_key, minute_key = keys
        day_count = self.get_count(day_key)
        minute_count = self.get_count(minute_key)

        if day_count > 0:
            day_count -= 1
            if day_count <= 0:
                self._store.pop(day_key, None)
                self._expires_at.pop(day_key, None)
                day_count = 0
            else:
                self._store[day_key] = day_count

        if minute_count > 0:
            minute_count -= 1
            if minute_count <= 0:
                self._store.pop(minute_key, None)
                self._expires_at.pop(minute_key, None)
                minute_count = 0
            else:
                self._store[minute_key] = minute_count

        return [day_count, minute_count]

    def _matching_keys(self, pattern: str) -> list[str]:
        self._cleanup()
        return [key for key in self._store if fnmatch(key, pattern)]

    def _eval_live_reserve(self, keys: list[str], values: list[object]) -> list[object]:
        day_key, minute_key, global_lease_key, user_lease_key = keys
        global_pattern = str(values[0])
        user_pattern = str(values[1])
        day_limit = int(values[2])
        minute_limit = int(values[3])
        global_limit = int(values[4])
        user_limit = int(values[5])
        day_ttl = int(values[6])
        minute_ttl = int(values[7])
        lease_ttl = int(values[8])

        global_active = len(self._matching_keys(global_pattern))
        user_active = len(self._matching_keys(user_pattern))
        if global_active >= global_limit:
            return [0, "concurrent", "live_global", global_active, global_limit, 0, lease_ttl, 0, 0]
        if user_active >= user_limit:
            return [0, "concurrent", "live_user", user_active, user_limit, 0, lease_ttl, 0, 0]

        day_count = self.get_count(day_key)
        minute_count = self.get_count(minute_key)
        if day_count >= day_limit:
            return [0, "day", "live", day_count, day_limit, max(day_limit - day_count, 0), day_ttl, day_count, minute_count]
        if minute_count >= minute_limit:
            return [0, "minute", "live", minute_count, minute_limit, max(minute_limit - minute_count, 0), minute_ttl, day_count, minute_count]

        now = self._now_func()
        self._store[global_lease_key] = 1
        self._store[user_lease_key] = 1
        self._expires_at[global_lease_key] = now + timedelta(seconds=lease_ttl)
        self._expires_at[user_lease_key] = now + timedelta(seconds=lease_ttl)

        day_count += 1
        minute_count += 1
        self._store[day_key] = day_count
        self._store[minute_key] = minute_count
        self._expires_at[day_key] = now + timedelta(seconds=day_ttl)
        self._expires_at[minute_key] = now + timedelta(seconds=minute_ttl)
        return [1, "", "live", 0, 0, 0, 0, day_count, minute_count]

    def _eval_live_refund(self, keys: list[str]) -> list[int]:
        day_key, minute_key, global_lease_key, user_lease_key = keys
        self._store.pop(global_lease_key, None)
        self._store.pop(user_lease_key, None)
        self._expires_at.pop(global_lease_key, None)
        self._expires_at.pop(user_lease_key, None)
        return self._eval_refund([day_key, minute_key])

    def _eval_live_release(self, keys: list[str]) -> list[int]:
        removed = 0
        for key in keys:
            if key in self._store:
                removed += 1
                self._store.pop(key, None)
                self._expires_at.pop(key, None)
        return [removed]

    def _eval_live_refresh(self, keys: list[str], values: list[object]) -> list[int]:
        lease_ttl = int(values[0])
        refreshed = 0
        now = self._now_func()
        for key in keys:
            if key in self._store:
                self._expires_at[key] = now + timedelta(seconds=lease_ttl)
                refreshed += 1
        return [refreshed]


@pytest.fixture
def fixed_time(monkeypatch):
    state = {"value": datetime(2026, 3, 16, 12, 0, 15, tzinfo=timezone.utc)}
    monkeypatch.setattr(rate_limiter, "_utc_now", lambda: state["value"])
    return state


@pytest.fixture
def test_client(monkeypatch, fixed_time):
    fake_redis = FakeRedis(rate_limiter._utc_now)

    @asynccontextmanager
    async def noop_lifespan(app):
        yield

    async def fake_get_redis():
        return fake_redis

    async def fake_generate_content(request):
        return {"text": f"ok:{request.model}"}

    user = {"user_id": "user-123", "email": "user@example.com"}
    original_get_redis = backend_main.get_redis
    original_get_current_user = backend_main.get_current_user

    backend_main.app.dependency_overrides[original_get_redis] = fake_get_redis
    backend_main.app.dependency_overrides[original_get_current_user] = lambda: user

    monkeypatch.setattr(backend_main, "get_redis", fake_get_redis)
    monkeypatch.setattr(backend_main.auth, "verify_access_token", lambda token: user if token == "valid-token" else None)
    monkeypatch.setattr(backend_main.service, "generate_content", fake_generate_content)
    monkeypatch.setattr(backend_main.app.router, "lifespan_context", noop_lifespan)

    with TestClient(backend_main.app) as client:
        yield client, fake_redis, fixed_time

    backend_main.app.dependency_overrides.clear()


def _request_payload(model: str = "gemini-3.1") -> dict:
    return {
        "model": model,
        "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
        "config": None,
    }


def test_registration_is_disabled_by_default(test_client):
    client, _, _ = test_client

    response = client.post(
        "/auth/register",
        json={"email": "tester@example.com", "password": "secret123"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == backend_main.REGISTRATION_DISABLED_MESSAGE


def test_login_still_works_when_registration_is_disabled(test_client):
    client, _, _ = test_client

    response = client.post(
        "/auth/login",
        json={"email": "tester@example.com", "password": "secret123"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "access_token": "token",
        "token_type": "bearer",
        "user_id": "existing-user",
        "email": "tester@example.com",
    }


def test_rest_success_increments_daily_and_minute_counts(test_client):
    client, fake_redis, fixed_time = test_client

    response = client.post("/v1/generate", json=_request_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "ok:gemini-3.1"
    assert body["remaining_requests"] == rate_limiter.DAILY_LIMIT - 1

    day_key, minute_key = rate_limiter._get_rate_limit_keys(
        "user-123",
        now=fixed_time["value"],
    )
    assert fake_redis.get_count(day_key) == 1
    assert fake_redis.get_count(minute_key) == 1


def test_websocket_success_shares_counter_with_rest(test_client):
    client, fake_redis, fixed_time = test_client

    rest_response = client.post("/v1/generate", json=_request_payload())
    assert rest_response.status_code == 200

    with client.websocket_connect("/ws/generate") as websocket:
        websocket.send_json({"type": "auth", "token": "valid-token"})
        assert websocket.receive_json() == {"type": "auth_ok", "user_id": "user-123"}

        websocket.send_json({"type": "generate", "request": _request_payload("gemini-live")})
        result = websocket.receive_json()

    assert result["type"] == "generate_result"
    assert result["data"]["text"] == "ok:gemini-live"
    assert result["data"]["remaining_requests"] == rate_limiter.DAILY_LIMIT - 2

    day_key, minute_key = rate_limiter._get_rate_limit_keys(
        "user-123",
        now=fixed_time["value"],
    )
    assert fake_redis.get_count(day_key) == 2
    assert fake_redis.get_count(minute_key) == 2


def test_websocket_free_messages_do_not_consume_quota(test_client):
    client, fake_redis, fixed_time = test_client

    with client.websocket_connect("/ws/generate") as websocket:
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json() == {"type": "pong"}

        websocket.send_text("{")
        invalid_json = websocket.receive_json()
        assert invalid_json["code"] == 400

        websocket.send_json({"type": "generate", "request": _request_payload()})
        unauthenticated = websocket.receive_json()
        assert unauthenticated["code"] == 401

        day_key, minute_key = rate_limiter._get_rate_limit_keys(
            "user-123",
            now=fixed_time["value"],
        )
        assert fake_redis.get_count(day_key) == 0
        assert fake_redis.get_count(minute_key) == 0

        websocket.send_json({"type": "auth", "token": "valid-token"})
        assert websocket.receive_json()["type"] == "auth_ok"

        websocket.send_json({"type": "generate", "request": _request_payload()})
        success = websocket.receive_json()

    assert success["type"] == "generate_result"
    assert fake_redis.get_count(day_key) == 1
    assert fake_redis.get_count(minute_key) == 1


def test_failed_auth_does_not_consume_quota(test_client):
    client, fake_redis, fixed_time = test_client

    with client.websocket_connect("/ws/generate") as websocket:
        websocket.send_json({"type": "auth", "token": "bad-token"})
        error_message = websocket.receive_json()
        assert error_message["code"] == 401
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_text()

    day_key, minute_key = rate_limiter._get_rate_limit_keys(
        "user-123",
        now=fixed_time["value"],
    )
    assert fake_redis.get_count(day_key) == 0
    assert fake_redis.get_count(minute_key) == 0


def test_minute_limit_returns_retry_after_without_incrementing(test_client):
    client, fake_redis, fixed_time = test_client

    for _ in range(rate_limiter.MINUTE_LIMIT):
        response = client.post("/v1/generate", json=_request_payload())
        assert response.status_code == 200

    blocked = client.post("/v1/generate", json=_request_payload())

    assert blocked.status_code == 429
    body = blocked.json()["detail"]
    assert body["window"] == rate_limiter.WINDOW_MINUTE
    assert body["retry_after_seconds"] > 0
    assert blocked.headers["Retry-After"] == str(body["retry_after_seconds"])

    day_key, minute_key = rate_limiter._get_rate_limit_keys(
        "user-123",
        now=fixed_time["value"],
    )
    assert fake_redis.get_count(day_key) == rate_limiter.MINUTE_LIMIT
    assert fake_redis.get_count(minute_key) == rate_limiter.MINUTE_LIMIT


def test_daily_limit_returns_day_window(test_client):
    client, fake_redis, fixed_time = test_client

    day_key, minute_key = rate_limiter._get_rate_limit_keys(
        "user-123",
        now=fixed_time["value"],
    )
    fake_redis.seed(
        day_key,
        rate_limiter.DAILY_LIMIT,
        ttl_seconds=rate_limiter._seconds_until_next_utc_midnight(fixed_time["value"]),
    )
    fake_redis.seed(
        minute_key,
        0,
        ttl_seconds=rate_limiter._seconds_until_next_utc_minute(fixed_time["value"]),
    )

    response = client.post("/v1/generate", json=_request_payload())

    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail["window"] == rate_limiter.WINDOW_DAY
    assert detail["remaining"] == 0
    assert detail["retry_after_seconds"] > 0
    assert fake_redis.get_count(day_key) == rate_limiter.DAILY_LIMIT


def test_refund_and_sanitized_rest_error_on_generation_failure(test_client, monkeypatch):
    client, fake_redis, fixed_time = test_client

    async def failing_generate_content(request):
        raise RuntimeError("super secret upstream failure")

    monkeypatch.setattr(backend_main.service, "generate_content", failing_generate_content)

    response = client.post("/v1/generate", json=_request_payload())

    assert response.status_code == 500
    assert response.json()["detail"] == backend_main.GENERATION_ERROR_MESSAGE
    assert "super secret" not in response.text

    day_key, minute_key = rate_limiter._get_rate_limit_keys(
        "user-123",
        now=fixed_time["value"],
    )
    assert fake_redis.get_count(day_key) == 0
    assert fake_redis.get_count(minute_key) == 0


def test_refund_and_sanitized_websocket_error_on_generation_failure(test_client, monkeypatch):
    client, fake_redis, fixed_time = test_client

    async def failing_generate_content(request):
        raise RuntimeError("very sensitive failure text")

    monkeypatch.setattr(backend_main.service, "generate_content", failing_generate_content)

    with client.websocket_connect("/ws/generate") as websocket:
        websocket.send_json({"type": "auth", "token": "valid-token"})
        assert websocket.receive_json()["type"] == "auth_ok"

        websocket.send_json({"type": "generate", "request": _request_payload()})
        error_message = websocket.receive_json()

    assert error_message == {
        "type": "error",
        "code": 500,
        "detail": backend_main.GENERATION_ERROR_MESSAGE,
    }

    day_key, minute_key = rate_limiter._get_rate_limit_keys(
        "user-123",
        now=fixed_time["value"],
    )
    assert fake_redis.get_count(day_key) == 0
    assert fake_redis.get_count(minute_key) == 0


def test_websocket_auth_timeout_closes_connection(test_client, monkeypatch):
    client, fake_redis, fixed_time = test_client
    monkeypatch.setattr(backend_main, "WS_AUTH_TIMEOUT_SECONDS", 0.01)

    with client.websocket_connect("/ws/generate") as websocket:
        timeout_error = websocket.receive_json()
        assert timeout_error == {
            "type": "error",
            "code": 401,
            "detail": "Authentication timeout",
        }
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_text()

    day_key, minute_key = rate_limiter._get_rate_limit_keys(
        "user-123",
        now=fixed_time["value"],
    )
    assert fake_redis.get_count(day_key) == 0
    assert fake_redis.get_count(minute_key) == 0


def test_live_websocket_round_trip_in_backend_mode(test_client):
    client, _, _ = test_client

    with client.websocket_connect("/ws/live") as websocket:
        websocket.send_json({"type": "auth", "token": "valid-token"})
        assert websocket.receive_json() == {"type": "auth_ok", "user_id": "user-123"}

        websocket.send_json({"type": "live_start", "request": {"config": {"response_modalities": ["AUDIO"]}}})
        started = websocket.receive_json()
        assert started["type"] == "live_started"

        websocket.send_json({"type": "live_input", "text": "hello backend live"})
        event = websocket.receive_json()
        assert event["type"] == "live_event"
        assert event["event"]["server_content"]["output_transcription"]["text"] == "echo:hello backend live"


def test_live_websocket_rejects_second_concurrent_session_for_same_user(test_client):
    client, _, _ = test_client

    with client.websocket_connect("/ws/live") as first:
        first.send_json({"type": "auth", "token": "valid-token"})
        assert first.receive_json()["type"] == "auth_ok"
        first.send_json({"type": "live_start", "request": {"config": {}}})
        assert first.receive_json()["type"] == "live_started"

        with client.websocket_connect("/ws/live") as second:
            second.send_json({"type": "auth", "token": "valid-token"})
            assert second.receive_json()["type"] == "auth_ok"
            second.send_json({"type": "live_start", "request": {"config": {}}})
            blocked = second.receive_json()
            assert blocked["type"] == "error"
            assert blocked["code"] == 429
            assert blocked["detail"]["window"] == rate_limiter.WINDOW_LIVE_CONCURRENT


def test_live_websocket_requires_exactly_one_input_payload(test_client):
    client, _, _ = test_client

    with client.websocket_connect("/ws/live") as websocket:
        websocket.send_json({"type": "auth", "token": "valid-token"})
        assert websocket.receive_json()["type"] == "auth_ok"

        websocket.send_json({"type": "live_start", "request": {"config": {}}})
        assert websocket.receive_json()["type"] == "live_started"

        websocket.send_json(
            {
                "type": "live_input",
                "text": "hello",
                "audio": {"data": "", "mime_type": "audio/pcm;rate=16000"},
            }
        )
        blocked = websocket.receive_json()
        assert blocked["type"] == "error"
        assert blocked["code"] == 422
        assert "exactly one" in blocked["detail"].lower()


def test_live_rate_limiter_refresh_and_release():
    fixed_now = datetime(2026, 3, 16, 12, 0, 15, tzinfo=timezone.utc)
    state = {"value": fixed_now}
    original_now = rate_limiter._utc_now
    rate_limiter._utc_now = lambda: state["value"]
    try:
        fake_redis = FakeRedis(rate_limiter._utc_now)
        reservation = asyncio.run(
            rate_limiter.reserve_live_session_start(
                "user-123",
                "session-1",
                fake_redis,
            )
        )
        assert reservation.allowed is True
        assert (
            asyncio.run(
                rate_limiter.refresh_live_session_lease(reservation, fake_redis)
            )
            is True
        )
        assert asyncio.run(rate_limiter.release_live_session(reservation, fake_redis)) is True

        second = asyncio.run(
            rate_limiter.reserve_live_session_start(
                "user-123",
                "session-2",
                fake_redis,
            )
        )
        assert second.allowed is True
    finally:
        rate_limiter._utc_now = original_now


def test_live_rate_limiter_enforces_minute_starts():
    fixed_now = datetime(2026, 3, 16, 12, 0, 15, tzinfo=timezone.utc)
    state = {"value": fixed_now}
    original_now = rate_limiter._utc_now
    rate_limiter._utc_now = lambda: state["value"]
    try:
        fake_redis = FakeRedis(rate_limiter._utc_now)
        reservations = []
        for idx in range(rate_limiter.LIVE_SESSION_STARTS_PER_MINUTE):
            reservation = asyncio.run(
                rate_limiter.reserve_live_session_start(
                    "user-123",
                    f"session-{idx}",
                    fake_redis,
                )
            )
            assert reservation.allowed is True
            reservations.append(reservation)
            asyncio.run(rate_limiter.release_live_session(reservation, fake_redis))

        blocked = asyncio.run(
            rate_limiter.reserve_live_session_start(
                "user-123",
                "blocked-session",
                fake_redis,
            )
        )
        assert blocked.allowed is False
        assert blocked.window == rate_limiter.WINDOW_MINUTE
        assert blocked.scope == "live"
    finally:
        rate_limiter._utc_now = original_now


def test_live_rate_limiter_enforces_global_concurrency():
    fixed_now = datetime(2026, 3, 16, 12, 0, 15, tzinfo=timezone.utc)
    state = {"value": fixed_now}
    original_now = rate_limiter._utc_now
    rate_limiter._utc_now = lambda: state["value"]
    try:
        fake_redis = FakeRedis(rate_limiter._utc_now)
        first = asyncio.run(
            rate_limiter.reserve_live_session_start(
                "user-123",
                "session-1",
                fake_redis,
            )
        )
        assert first.allowed is True

        blocked = asyncio.run(
            rate_limiter.reserve_live_session_start(
                "user-456",
                "session-2",
                fake_redis,
            )
        )
        assert blocked.allowed is False
        assert blocked.window == rate_limiter.WINDOW_LIVE_CONCURRENT
        assert blocked.scope == "live_global"
    finally:
        rate_limiter._utc_now = original_now
