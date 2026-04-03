import asyncio
import base64
import binascii
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorDatabase
import redis.asyncio as redis
import uvicorn
import service
import ocr_service
import vision_service
import auth
import live_service
import rate_limiter
from database import lifespan, get_db, get_redis

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backend")

app = FastAPI(title="PixelPilot AI Backend", version="1.0.0", lifespan=lifespan)
security = HTTPBearer(auto_error=False)

GENERATION_ERROR_MESSAGE = "Generation failed"
SERVICE_UNAVAILABLE_MESSAGE = "Service temporarily unavailable"
WS_AUTH_TIMEOUT_SECONDS = 10
REGISTRATION_DISABLED_MESSAGE = (
    "Registration is disabled. Use the tester credentials provided to you."
)


# Request/Response models
class GenerateRequest(BaseModel):
    model: str
    contents: List[Dict[str, Any]]
    config: Optional[Dict[str, Any]] = None


class GenerateResponse(BaseModel):
    text: str
    remaining_requests: Optional[int] = None


class EasyOCRRequest(BaseModel):
    image_base64: str
    mime_type: str = "image/png"
    lang: str = "en"


class EasyOCRResult(BaseModel):
    bbox: List[List[float]]
    text: str
    confidence: float


class EasyOCRResponse(BaseModel):
    provider: str
    device: str
    hosted: bool = True
    results: List[EasyOCRResult]
    timings_ms: Dict[str, int]
    remaining_requests: Optional[int] = None


class HostedEyeElement(BaseModel):
    id: int
    type: str
    label: str
    confidence: float = 0.0
    x: int
    y: int
    w: int
    h: int


class HostedEyeResponse(BaseModel):
    provider: str
    device: str
    hosted: bool = True
    elements: List[HostedEyeElement]
    timings_ms: Dict[str, int]
    remaining_requests: Optional[int] = None


def _build_rate_limit_message(reservation: rate_limiter.RateLimitReservation) -> str:
    scope = getattr(reservation, "scope", "generate")
    if reservation.window == rate_limiter.WINDOW_MINUTE:
        prefix = "OCR" if scope == "ocr" else "Rate"
        return f"{prefix} limit exceeded. Try again in {max(1, reservation.retry_after_seconds)}s."
    if scope == "ocr":
        return (
            f"OCR daily limit exceeded ({reservation.limit} requests). "
            f"Resets at midnight UTC."
        )
    return f"Daily limit exceeded ({reservation.limit} requests). Resets at midnight UTC."


def _build_rate_limit_detail(
    reservation,
) -> Dict[str, Any]:
    return {
        "message": _build_rate_limit_message(reservation),
        "window": reservation.window,
        "limit": reservation.limit,
        "remaining": reservation.remaining,
        "retry_after_seconds": reservation.retry_after_seconds,
        "scope": getattr(reservation, "scope", "generate"),
    }


def _build_rate_limit_headers(detail: Dict[str, Any]) -> Dict[str, str]:
    headers = {
        "X-RateLimit-Limit": str(detail.get("limit", 0)),
        "X-RateLimit-Remaining": str(detail.get("remaining", 0)),
    }
    retry_after_seconds = int(detail.get("retry_after_seconds", 0) or 0)
    if retry_after_seconds > 0:
        headers["Retry-After"] = str(retry_after_seconds)
    return headers


def _decode_live_media_payload(payload: Any, *, kind: str) -> tuple[bytes, str]:
    if not isinstance(payload, dict):
        raise live_service.LiveSessionError(
            422,
            f"Invalid live_input: {kind} must be an object",
        )

    mime_type = str(payload.get("mime_type") or "").strip()
    if not mime_type:
        raise live_service.LiveSessionError(
            422,
            f"Invalid live_input: {kind}.mime_type is required",
        )

    try:
        data = base64.b64decode(str(payload.get("data") or ""), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise live_service.LiveSessionError(
            422,
            f"Invalid live_input: {kind}.data must be base64",
        ) from exc

    return data, mime_type


async def _generate_with_rate_limit(
    request: GenerateRequest,
    user_id: str,
    redis_client: redis.Redis,
) -> Dict[str, Any]:
    if redis_client is None:
        raise HTTPException(status_code=503, detail=SERVICE_UNAVAILABLE_MESSAGE)

    reservation = await rate_limiter.reserve_generate_request(user_id, redis_client)
    if not reservation.allowed:
        raise HTTPException(
            status_code=429,
            detail=_build_rate_limit_detail(reservation),
        )

    try:
        logger.info(
            "Generating content for user %s with model: %s",
            user_id,
            request.model,
        )
        result = await service.generate_content(
            service.GenerationRequest(
                model=request.model,
                contents=request.contents,
                config=request.config,
            )
        )
    except Exception:
        logger.exception("Generation error for user %s", user_id)
        try:
            await rate_limiter.refund_generate_request(reservation, redis_client)
        except Exception:
            logger.exception("Failed to refund reserved quota for user %s", user_id)
        raise HTTPException(status_code=500, detail=GENERATION_ERROR_MESSAGE)

    if isinstance(result, dict):
        result["remaining_requests"] = reservation.daily_remaining
        return result

    return {"text": str(result), "remaining_requests": reservation.daily_remaining}


async def _easyocr_with_rate_limit(
    *,
    request: EasyOCRRequest,
    image_bytes: bytes,
    user_id: str,
    redis_client: redis.Redis,
) -> Dict[str, Any]:
    if redis_client is None:
        raise HTTPException(status_code=503, detail=SERVICE_UNAVAILABLE_MESSAGE)

    reservation = await rate_limiter.reserve_ocr_request(user_id, redis_client)
    if not reservation.allowed:
        raise HTTPException(
            status_code=429,
            detail=_build_rate_limit_detail(reservation),
        )

    try:
        logger.info("Running backend EasyOCR for user %s", user_id)
        result = await asyncio.to_thread(
            ocr_service.run_easyocr,
            image_bytes,
            lang=str(request.lang or "en"),
        )
    except Exception:
        logger.exception("EasyOCR error for user %s", user_id)
        try:
            await rate_limiter.refund_ocr_request(reservation, redis_client)
        except Exception:
            logger.exception("Failed to refund OCR quota for user %s", user_id)
        raise HTTPException(status_code=500, detail="OCR failed")

    if isinstance(result, dict):
        result["remaining_requests"] = reservation.daily_remaining
        return result
    raise HTTPException(status_code=500, detail="OCR failed")


async def _hosted_eye_with_rate_limit(
    *,
    request: EasyOCRRequest,
    image_bytes: bytes,
    user_id: str,
    redis_client: redis.Redis,
) -> Dict[str, Any]:
    if redis_client is None:
        raise HTTPException(status_code=503, detail=SERVICE_UNAVAILABLE_MESSAGE)

    reservation = await rate_limiter.reserve_ocr_request(user_id, redis_client)
    if not reservation.allowed:
        raise HTTPException(
            status_code=429,
            detail=_build_rate_limit_detail(reservation),
        )

    try:
        logger.info("Running backend LocalCVEye for user %s", user_id)
        result = await asyncio.to_thread(
            vision_service.run_local_eye,
            image_bytes,
            lang=str(request.lang or "en"),
        )
    except Exception:
        logger.exception("Hosted eye error for user %s", user_id)
        try:
            await rate_limiter.refund_ocr_request(reservation, redis_client)
        except Exception:
            logger.exception("Failed to refund hosted eye quota for user %s", user_id)
        raise HTTPException(status_code=500, detail="Hosted eye failed")

    if isinstance(result, dict):
        result["remaining_requests"] = reservation.daily_remaining
        return result
    raise HTTPException(status_code=500, detail="Hosted eye failed")


# Auth dependency
async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """Validate JWT token and return user info."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = credentials.credentials
    user = auth.verify_access_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


# ============ Auth Endpoints ============


@app.post("/auth/register", response_model=auth.TokenResponse)
async def register(
    request: auth.RegisterRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Disable public self-registration."""
    # Temporarily disabled while the backend is public.
    # try:
    #     user = await auth.register_user(request.email, request.password, db)
    #     token = auth.create_access_token(user["user_id"], user["email"])
    #     return auth.TokenResponse(
    #         access_token=token,
    #         user_id=user["user_id"],
    #         email=user["email"],
    #     )
    # except ValueError as e:
    #     raise HTTPException(status_code=400, detail=str(e))
    # except Exception as e:
    #     logger.error(f"Registration error: {e}")
    #     raise HTTPException(status_code=500, detail="Registration failed")
    raise HTTPException(status_code=403, detail=REGISTRATION_DISABLED_MESSAGE)


@app.post("/auth/login", response_model=auth.TokenResponse)
async def login(
    request: auth.LoginRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Login and get access token."""
    user = await auth.authenticate_user(request.email, request.password, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = auth.create_access_token(user["user_id"], user["email"])
    return auth.TokenResponse(
        access_token=token,
        user_id=user["user_id"],
        email=user["email"],
    )


@app.get("/auth/me", response_model=auth.UserInfo)
async def get_me(user: dict = Depends(get_current_user)):
    """Get current user info."""
    return auth.UserInfo(user_id=user["user_id"], email=user["email"])


# ============ Generation Endpoint (Protected) ============


@app.post("/v1/generate")
async def generate(
    request: GenerateRequest,
    user: dict = Depends(get_current_user),
    redis_client: redis.Redis = Depends(get_redis),
):
    """Generate content using Gemini API. Requires authentication."""
    try:
        return await _generate_with_rate_limit(request, user["user_id"], redis_client)
    except HTTPException as e:
        headers = None
        if e.status_code == 429 and isinstance(e.detail, dict):
            headers = _build_rate_limit_headers(e.detail)
        raise HTTPException(status_code=e.status_code, detail=e.detail, headers=headers)
    except Exception:
        logger.exception("Unexpected generation handler error")
        raise HTTPException(status_code=500, detail=GENERATION_ERROR_MESSAGE)


@app.post("/v1/vision/easyocr", response_model=EasyOCRResponse)
async def easyocr_route(
    request: EasyOCRRequest,
    user: dict = Depends(get_current_user),
    redis_client: redis.Redis = Depends(get_redis),
):
    safe_mime = str(request.mime_type or "").strip()
    try:
        image_bytes = base64.b64decode(str(request.image_base64 or ""), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail="image_base64 must be valid base64"
        ) from exc

    try:
        ocr_service.validate_image_bytes(image_bytes, safe_mime)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        return await _easyocr_with_rate_limit(
            request=request,
            image_bytes=image_bytes,
            user_id=user["user_id"],
            redis_client=redis_client,
        )
    except HTTPException as e:
        headers = None
        if e.status_code == 429 and isinstance(e.detail, dict):
            headers = _build_rate_limit_headers(e.detail)
        raise HTTPException(status_code=e.status_code, detail=e.detail, headers=headers)
    except Exception:
        logger.exception("Unexpected EasyOCR handler error")
        raise HTTPException(status_code=500, detail="OCR failed")


@app.post("/v1/vision/local-eye", response_model=HostedEyeResponse)
async def hosted_eye_route(
    request: EasyOCRRequest,
    user: dict = Depends(get_current_user),
    redis_client: redis.Redis = Depends(get_redis),
):
    safe_mime = str(request.mime_type or "").strip()
    try:
        image_bytes = base64.b64decode(str(request.image_base64 or ""), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail="image_base64 must be valid base64"
        ) from exc

    try:
        ocr_service.validate_image_bytes(image_bytes, safe_mime)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        return await _hosted_eye_with_rate_limit(
            request=request,
            image_bytes=image_bytes,
            user_id=user["user_id"],
            redis_client=redis_client,
        )
    except HTTPException as e:
        headers = None
        if e.status_code == 429 and isinstance(e.detail, dict):
            headers = _build_rate_limit_headers(e.detail)
        raise HTTPException(status_code=e.status_code, detail=e.detail, headers=headers)
    except Exception:
        logger.exception("Unexpected hosted eye handler error")
        raise HTTPException(status_code=500, detail="Hosted eye failed")


@app.websocket("/ws/generate")
async def ws_generate(websocket: WebSocket):
    await websocket.accept()

    user = None
    redis_client = await get_redis()

    try:
        while True:
            if user is None:
                try:
                    raw = await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=WS_AUTH_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "code": 401,
                            "detail": "Authentication timeout",
                        }
                    )
                    await websocket.close(code=1008)
                    return
            else:
                raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {"type": "error", "code": 400, "detail": "Invalid JSON payload"}
                )
                continue

            msg_type = message.get("type")

            if msg_type == "auth":
                if user is not None:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "code": 400,
                            "detail": "Already authenticated",
                        }
                    )
                    continue

                token = str(message.get("token") or "").strip()
                user = auth.verify_access_token(token)
                if not user:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "code": 401,
                            "detail": "Invalid or expired token",
                        }
                    )
                    await websocket.close(code=1008)
                    return
                await websocket.send_json(
                    {"type": "auth_ok", "user_id": user.get("user_id")}
                )
                continue

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if msg_type != "generate":
                await websocket.send_json(
                    {"type": "error", "code": 400, "detail": "Unknown message type"}
                )
                continue

            if not user:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": 401,
                        "detail": "Authenticate first with {'type':'auth','token':'...'}",
                    }
                )
                continue

            request_payload = message.get("request", {})
            try:
                generate_request = GenerateRequest.model_validate(request_payload)
            except Exception as e:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": 422,
                        "detail": f"Invalid generate request: {e}",
                    }
                )
                continue

            try:
                result = await _generate_with_rate_limit(
                    generate_request,
                    user["user_id"],
                    redis_client,
                )
                await websocket.send_json({"type": "generate_result", "data": result})
            except HTTPException as e:
                await websocket.send_json(
                    {"type": "error", "code": e.status_code, "detail": e.detail}
                )
            except Exception:
                logger.exception(
                    "Unexpected WebSocket generation error for user %s",
                    user["user_id"],
                )
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": 500,
                        "detail": GENERATION_ERROR_MESSAGE,
                    }
                )
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception:
        logger.exception("WebSocket connection error")


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    await websocket.accept()

    user = None
    redis_client = await get_redis()
    session: Optional[live_service.BackendLiveSession] = None

    try:
        while True:
            if user is None:
                try:
                    raw = await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=WS_AUTH_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "code": 401,
                            "detail": "Authentication timeout",
                        }
                    )
                    await websocket.close(code=1008)
                    return
            else:
                raw = await websocket.receive_text()

            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {"type": "error", "code": 400, "detail": "Invalid JSON payload"}
                )
                continue

            msg_type = message.get("type")

            if msg_type == "auth":
                if user is not None:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "code": 400,
                            "detail": "Already authenticated",
                        }
                    )
                    continue

                token = str(message.get("token") or "").strip()
                user = auth.verify_access_token(token)
                if not user:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "code": 401,
                            "detail": "Invalid or expired token",
                        }
                    )
                    await websocket.close(code=1008)
                    return

                try:
                    session = live_service.BackendLiveSession(
                        websocket=websocket,
                        user_id=str(user.get("user_id") or ""),
                        redis_client=redis_client,
                    )
                except live_service.LiveSessionError as exc:
                    await websocket.send_json(
                        {"type": "error", "code": exc.code, "detail": exc.detail}
                    )
                    await websocket.close(code=1011 if exc.code >= 500 else 1008)
                    return
                await websocket.send_json(
                    {"type": "auth_ok", "user_id": user.get("user_id")}
                )
                continue

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if not user or session is None:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": 401,
                        "detail": "Authenticate first with {'type':'auth','token':'...'}",
                    }
                )
                continue

            try:
                if msg_type == "live_start":
                    request_payload = message.get("request") or {}
                    model_payload = request_payload.get("model")
                    config_payload = request_payload.get("config") or {}
                    if model_payload is not None and not isinstance(model_payload, str):
                        await websocket.send_json(
                            {
                                "type": "error",
                                "code": 422,
                                "detail": "Invalid live_start request: model must be a string",
                            }
                        )
                        continue
                    if not isinstance(config_payload, dict):
                        await websocket.send_json(
                            {
                                "type": "error",
                                "code": 422,
                                "detail": "Invalid live_start request: config must be an object",
                            }
                        )
                        continue
                    await session.start(config_payload, model=model_payload)
                    continue

                if msg_type == "live_input":
                    if not session.started:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "code": 400,
                                "detail": "Start a live session first.",
                            }
                        )
                        continue

                    payload_kinds = [
                        kind for kind in ("text", "audio", "video") if kind in message
                    ]
                    if bool(message.get("audio_stream_end")):
                        payload_kinds.append("audio_stream_end")
                    if len(payload_kinds) != 1:
                        raise live_service.LiveSessionError(
                            422,
                            "Invalid live_input: expected exactly one of text, audio, video, or audio_stream_end",
                        )

                    if payload_kinds[0] == "text":
                        await session.send_text(str(message.get("text") or ""))
                        continue

                    if payload_kinds[0] == "audio":
                        data, mime_type = _decode_live_media_payload(
                            message.get("audio"),
                            kind="audio",
                        )
                        await session.send_audio(data, mime_type)
                        continue

                    if payload_kinds[0] == "video":
                        data, mime_type = _decode_live_media_payload(
                            message.get("video"),
                            kind="video",
                        )
                        await session.send_video(data, mime_type)
                        continue

                    if payload_kinds[0] == "audio_stream_end":
                        await session.send_audio_stream_end()
                        continue

                if msg_type == "live_tool_response":
                    if not session.started:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "code": 400,
                                "detail": "Start a live session first.",
                            }
                        )
                        continue
                    responses = message.get("responses") or []
                    if not isinstance(responses, list):
                        raise live_service.LiveSessionError(
                            422,
                            "Invalid live_tool_response: responses must be an array",
                        )
                    await session.send_tool_responses(responses)
                    continue

                if msg_type == "live_stop":
                    await session.stop(notify_client=True)
                    continue

                await websocket.send_json(
                    {"type": "error", "code": 400, "detail": "Unknown message type"}
                )
            except live_service.LiveSessionError as exc:
                await websocket.send_json(
                    {"type": "error", "code": exc.code, "detail": exc.detail}
                )
            except Exception:
                logger.exception(
                    "Unexpected WebSocket live error for user %s",
                    user["user_id"],
                )
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": 500,
                        "detail": live_service.LIVE_PROVIDER_ERROR_MESSAGE,
                    }
                )
    except WebSocketDisconnect:
        logger.info("Live WebSocket client disconnected")
    except Exception:
        logger.exception("Live WebSocket connection error")
    finally:
        if session is not None:
            try:
                await session.shutdown()
            except Exception:
                logger.debug("Failed to shut down live session", exc_info=True)


# ============ Health Check ============


@app.get("/health")
async def health_check():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
