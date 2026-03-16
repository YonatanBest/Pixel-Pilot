from __future__ import annotations

import os
import sys
import types
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
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
