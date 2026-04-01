from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Optional

from config import Config


NONCE_RE = re.compile(r"^[a-f0-9]{32}$")
DEFAULT_MAX_AGE_SECONDS = float(Config.UAC_REQUEST_MAX_AGE_SECONDS)


def ipc_root() -> Path:
    override = str(os.getenv("UAC_IPC_DIR", "")).strip()
    if override:
        return Path(override)
    return Path(Config.UAC_IPC_DIR)


def ensure_ipc_root() -> Path:
    root = ipc_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _validated_nonce(nonce: str) -> str:
    clean = str(nonce or "").strip().lower()
    if not NONCE_RE.fullmatch(clean):
        raise ValueError("Invalid UAC nonce")
    return clean


def _request_paths(nonce: str) -> dict[str, Path]:
    safe_nonce = _validated_nonce(nonce)
    root = ensure_ipc_root()
    return {
        "root": root,
        "request": root / f"{safe_nonce}.request.json",
        "response": root / f"{safe_nonce}.response.json",
        "snapshot": root / f"{safe_nonce}.snapshot.bmp",
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def create_request() -> dict[str, Any]:
    nonce = secrets.token_hex(16)
    paths = _request_paths(nonce)
    created_at = time.time()
    payload = {
        "nonce": nonce,
        "created_at": created_at,
        "request_path": str(paths["request"]),
        "response_path": str(paths["response"]),
        "snapshot_path": str(paths["snapshot"]),
        "requester_pid": os.getpid(),
    }
    _write_json(paths["request"], payload)
    return payload


def load_request(
    request_path: str | Path,
    *,
    expected_nonce: Optional[str] = None,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
) -> Optional[dict[str, Any]]:
    try:
        path = Path(request_path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None

        nonce = _validated_nonce(str(raw.get("nonce") or ""))
        if expected_nonce is not None and nonce != _validated_nonce(expected_nonce):
            return None

        paths = _request_paths(nonce)
        if path.resolve() != paths["request"].resolve():
            return None

        created_at = float(raw.get("created_at") or 0.0)
        if created_at <= 0.0:
            return None
        if time.time() - created_at > max(1.0, float(max_age_seconds or DEFAULT_MAX_AGE_SECONDS)):
            return None

        if str(raw.get("response_path") or "") != str(paths["response"]):
            return None
        if str(raw.get("snapshot_path") or "") != str(paths["snapshot"]):
            return None

        raw["nonce"] = nonce
        raw["created_at"] = created_at
        return raw
    except Exception:
        return None


def write_response(
    request_payload: dict[str, Any],
    *,
    allow: bool,
    user_confirmed: bool,
    reasoning: str = "",
) -> dict[str, Any]:
    nonce = _validated_nonce(str(request_payload.get("nonce") or ""))
    paths = _request_paths(nonce)

    payload = {
        "nonce": nonce,
        "allow": bool(allow),
        "user_confirmed": bool(user_confirmed),
        "reasoning": str(reasoning or "").strip(),
        "responded_at": time.time(),
    }
    _write_json(paths["response"], payload)
    return payload


def load_response(
    request_payload: dict[str, Any],
    *,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
) -> Optional[dict[str, Any]]:
    try:
        nonce = _validated_nonce(str(request_payload.get("nonce") or ""))
        created_at = float(request_payload.get("created_at") or 0.0)
        if created_at <= 0.0:
            return None

        paths = _request_paths(nonce)
        raw = json.loads(paths["response"].read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        if _validated_nonce(str(raw.get("nonce") or "")) != nonce:
            return None

        allow = raw.get("allow")
        if not isinstance(allow, bool):
            return None

        responded_at = float(raw.get("responded_at") or 0.0)
        if responded_at < created_at:
            return None
        if time.time() - responded_at > max(1.0, float(max_age_seconds or DEFAULT_MAX_AGE_SECONDS)):
            return None

        raw["allow"] = allow
        raw["responded_at"] = responded_at
        raw["user_confirmed"] = bool(raw.get("user_confirmed"))
        return raw
    except Exception:
        return None


def cleanup_request_artifacts(request_payload: dict[str, Any]) -> None:
    try:
        nonce = _validated_nonce(str(request_payload.get("nonce") or ""))
    except Exception:
        return

    paths = _request_paths(nonce)
    for key in ("request", "response", "snapshot"):
        try:
            paths[key].unlink(missing_ok=True)
        except Exception:
            pass


def pending_request_paths(*, max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS) -> list[Path]:
    root = ensure_ipc_root()
    now = time.time()
    pending: list[Path] = []
    for path in root.glob("*.request.json"):
        try:
            age = now - path.stat().st_mtime
        except Exception:
            continue
        if age > max(1.0, float(max_age_seconds or DEFAULT_MAX_AGE_SECONDS)):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            continue
        pending.append(path)
    pending.sort(key=lambda item: item.stat().st_mtime)
    return pending
