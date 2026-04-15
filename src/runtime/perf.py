from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


logger = logging.getLogger("pixelpilot.perf")


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _logs_dir() -> Path:
    configured = str(os.environ.get("PIXELPILOT_LOG_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "logs"


def _rss_bytes() -> int | None:
    try:
        import psutil

        return int(psutil.Process(os.getpid()).memory_info().rss)
    except Exception:
        return None


@dataclass(slots=True)
class StartupProfiler:
    enabled: bool
    started_at: float = field(default_factory=time.perf_counter)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)

    def checkpoint(self, name: str, **details: Any) -> None:
        if not self.enabled:
            return
        elapsed_ms = int((time.perf_counter() - self.started_at) * 1000)
        payload = {
            "name": str(name or "").strip() or "checkpoint",
            "elapsedMs": elapsed_ms,
            "rssBytes": _rss_bytes(),
            "details": {key: value for key, value in details.items() if value not in ("", None)},
        }
        self.checkpoints.append(payload)
        logger.info(
            "STARTUP_PROFILE checkpoint=%s elapsed_ms=%d rss_bytes=%s",
            payload["name"],
            elapsed_ms,
            payload["rssBytes"],
        )

    def flush(self, *, status: str = "ok") -> Path | None:
        if not self.enabled:
            return None
        target_dir = _logs_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"startup-profile-{os.getpid()}.json"
        payload = {
            "status": str(status or "ok"),
            "pid": os.getpid(),
            "createdAt": int(time.time()),
            "checkpoints": list(self.checkpoints),
        }
        target.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        return target


_STARTUP_PROFILER = StartupProfiler(
    enabled=_enabled(os.environ.get("PIXELPILOT_PROFILE_STARTUP"))
)


def startup_checkpoint(name: str, **details: Any) -> None:
    _STARTUP_PROFILER.checkpoint(name, **details)


def flush_startup_profile(*, status: str = "ok") -> Path | None:
    return _STARTUP_PROFILER.flush(status=status)


@contextmanager
def slow_operation(name: str, *, threshold_ms: int | None = None, **details: Any) -> Iterator[None]:
    configured = os.environ.get("PIXELPILOT_SLOW_OP_MS")
    try:
        resolved_threshold = int(configured) if configured else int(threshold_ms or 250)
    except Exception:
        resolved_threshold = int(threshold_ms or 250)

    started_at = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        if elapsed_ms >= resolved_threshold:
            logger.warning(
                "SLOW_OPERATION name=%s elapsed_ms=%d details=%s",
                str(name or "operation").strip() or "operation",
                elapsed_ms,
                {key: value for key, value in details.items() if value not in ("", None)},
            )
