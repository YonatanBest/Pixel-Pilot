"""
Database connections and dependencies.
Initializes MongoDB and Redis at application startup using FastAPI lifespan.
"""

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import redis.asyncio as redis
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
REDIS_URI = os.getenv("REDIS_URI")

_mongo_client: AsyncIOMotorClient = None
_mongo_db: AsyncIOMotorDatabase = None
_redis_client: redis.Redis = None


def _require_env(name: str) -> str:
    value = str(os.getenv(name, "")).strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def _validate_startup_configuration() -> tuple[str, str]:
    mongodb_uri = _require_env("MONGODB_URI")
    redis_uri = _require_env("REDIS_URI")

    import auth

    auth.ensure_auth_configuration()

    gemini_api_key = str(os.getenv("GEMINI_API_KEY", "")).strip()
    if not gemini_api_key:
        raise RuntimeError("Missing required env var: GEMINI_API_KEY")

    return mongodb_uri, redis_uri


@asynccontextmanager
async def lifespan(app):
    """
    FastAPI lifespan context manager.
    Establishes database connections at startup and closes them at shutdown.
    """
    global _mongo_client, _mongo_db, _redis_client
    mongodb_uri, redis_uri = _validate_startup_configuration()

    print("Connecting to MongoDB...")
    _mongo_client = AsyncIOMotorClient(mongodb_uri)
    _mongo_db = _mongo_client.pixelpilot

    print("Connecting to Redis...")
    _redis_client = redis.from_url(redis_uri, decode_responses=True)

    await _mongo_client.admin.command("ping")
    print("MongoDB connected successfully!")
    import auth

    await auth.ensure_auth_indexes(_mongo_db)
    await _redis_client.ping()
    print("Redis connected successfully!")

    yield

    print("Closing database connections...")
    if _mongo_client:
        _mongo_client.close()
    if _redis_client:
        await _redis_client.close()
    print("Database connections closed.")

async def get_db() -> AsyncIOMotorDatabase:
    """Dependency to get MongoDB database."""
    return _mongo_db


async def get_redis() -> redis.Redis:
    """Dependency to get Redis client."""
    return _redis_client
