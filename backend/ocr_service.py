from __future__ import annotations

import io
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger("backend.ocr")

OCR_USE_GPU = (os.getenv("OCR_USE_GPU", "auto").strip().lower() or "auto")
OCR_MODEL_DIR = Path(
    os.getenv(
        "EASYOCR_MODEL_DIR",
        str(Path(__file__).resolve().parent / ".easyocr"),
    )
)

_READER_LOCK = threading.Lock()
_READERS: dict[tuple[str, bool], Any] = {}


def _resolve_gpu_mode() -> bool:
    mode = OCR_USE_GPU
    if mode not in {"auto", "off", "require"}:
        mode = "auto"

    if mode == "off":
        return False

    try:
        import torch

        available = bool(torch.cuda.is_available())
    except Exception as exc:  # noqa: BLE001
        if mode == "require":
            raise RuntimeError(
                f"OCR_USE_GPU=require but CUDA detection failed: {exc}"
            ) from exc
        logger.debug("Torch CUDA detection failed; falling back to CPU", exc_info=True)
        return False

    if mode == "require" and not available:
        raise RuntimeError("OCR_USE_GPU=require but CUDA is unavailable.")

    return available


def _reader_key(lang: str, use_gpu: bool) -> tuple[str, bool]:
    return ((lang or "en").strip().lower() or "en", bool(use_gpu))


def _device_name(use_gpu: bool) -> str:
    return "cuda" if use_gpu else "cpu"


def _create_reader(lang: str, use_gpu: bool):
    import easyocr

    OCR_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Initializing backend EasyOCR reader (lang=%s, device=%s)",
        lang,
        _device_name(use_gpu),
    )
    return easyocr.Reader(
        [lang],
        gpu=use_gpu,
        model_storage_directory=str(OCR_MODEL_DIR),
    )


def _get_reader(lang: str) -> tuple[Any, str]:
    use_gpu = _resolve_gpu_mode()
    key = _reader_key(lang, use_gpu)
    reader = _READERS.get(key)
    if reader is not None:
        return reader, _device_name(use_gpu)

    with _READER_LOCK:
        reader = _READERS.get(key)
        if reader is None:
            reader = _create_reader(key[0], use_gpu)
            _READERS[key] = reader
    return reader, _device_name(use_gpu)


def prefetch_reader(lang: str = "en") -> str:
    _, device = _get_reader(lang)
    return device


def validate_image_bytes(image_bytes: bytes, mime_type: str) -> None:
    safe_mime = str(mime_type or "").strip().lower()
    if not safe_mime.startswith("image/"):
        raise ValueError("mime_type must be an image/* type")
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.verify()
    except UnidentifiedImageError as exc:
        raise ValueError("Unsupported image payload") from exc
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid image payload: {exc}") from exc


def run_easyocr_array(np_image: np.ndarray, *, lang: str = "en") -> dict[str, Any]:
    total_start = time.perf_counter()
    reader, device = _get_reader(lang)

    ocr_start = time.perf_counter()
    raw_results = reader.readtext(np_image)
    ocr_ms = int((time.perf_counter() - ocr_start) * 1000)

    results: list[dict[str, Any]] = []
    for item in raw_results or []:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        bbox, text, confidence = item[0], item[1], item[2]
        normalized_bbox: list[list[float]] = []
        for point in bbox or []:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                normalized_bbox.append([float(point[0]), float(point[1])])
        if not normalized_bbox:
            continue
        results.append(
            {
                "bbox": normalized_bbox,
                "text": str(text or ""),
                "confidence": float(confidence or 0.0),
            }
        )

    total_ms = int((time.perf_counter() - total_start) * 1000)
    return {
        "provider": "easyocr",
        "device": device,
        "hosted": True,
        "results": results,
        "timings_ms": {
            "ocr": ocr_ms,
            "total": total_ms,
        },
    }


def run_easyocr(image_bytes: bytes, *, lang: str = "en") -> dict[str, Any]:
    total_start = time.perf_counter()
    decode_start = time.perf_counter()
    with Image.open(io.BytesIO(image_bytes)) as image:
        rgb_image = image.convert("RGB")
        np_image = np.array(rgb_image)
    decode_ms = int((time.perf_counter() - decode_start) * 1000)

    payload = run_easyocr_array(np_image, lang=lang)
    timings_ms = dict(payload.get("timings_ms") or {})
    timings_ms["decode"] = decode_ms
    timings_ms["total"] = int((time.perf_counter() - total_start) * 1000)
    payload["timings_ms"] = timings_ms
    return payload
