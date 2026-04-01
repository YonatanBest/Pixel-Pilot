from __future__ import annotations

import concurrent.futures
import io
import time
from typing import Any

import cv2
import numpy as np
from PIL import Image

import ocr_service


def _normalize_ocr_bbox(bbox: Any) -> list[list[float]]:
    normalized_bbox: list[list[float]] = []
    for point in bbox or []:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            normalized_bbox.append([float(point[0]), float(point[1])])
    return normalized_bbox


def non_max_suppression(boxes: list[list[int]], overlap_thresh: float) -> list[list[int]]:
    if len(boxes) == 0:
        return []

    boxes_array = np.array(boxes)
    pick = []

    x1 = boxes_array[:, 0]
    y1 = boxes_array[:, 1]
    x2 = boxes_array[:, 0] + boxes_array[:, 2]
    y2 = boxes_array[:, 1] + boxes_array[:, 3]
    area = (x2 - x1 + 1) * (y2 - y1 + 1)
    idxs = np.argsort(y2)

    while len(idxs) > 0:
        last = len(idxs) - 1
        i = idxs[last]
        pick.append(i)

        xx1 = np.maximum(x1[i], x1[idxs[:last]])
        yy1 = np.maximum(y1[i], y1[idxs[:last]])
        xx2 = np.minimum(x2[i], x2[idxs[:last]])
        yy2 = np.minimum(y2[i], y2[idxs[:last]])

        w = np.maximum(0, xx2 - xx1 + 1)
        h = np.maximum(0, yy2 - yy1 + 1)

        overlap = (w * h) / area[idxs[:last]]
        idxs = np.delete(
            idxs,
            np.concatenate(([last], np.where(overlap > overlap_thresh)[0])),
        )

    return boxes_array[pick].astype("int").tolist()


def find_mystery_icons_sensitive(
    img: np.ndarray,
    existing_text_boxes: list[list[int]],
) -> list[dict[str, Any]]:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    candidates = []

    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2
    )
    kernel_small = np.ones((2, 2), np.uint8)
    opened = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel_small, iterations=1)
    cnts_thresh, _ = cv2.findContours(
        opened, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
    )

    v = np.median(gray)
    sigma = 0.33
    lower = int(max(0, (1.0 - sigma) * v))
    upper = int(min(255, (1.0 + sigma) * v))
    edges = cv2.Canny(gray, lower, upper)
    edges = cv2.dilate(edges, kernel_small, iterations=1)
    cnts_edges, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    all_contours = list(cnts_thresh) + list(cnts_edges)
    raw_boxes: list[list[int]] = []

    for cnt in all_contours:
        x, y, w, h = cv2.boundingRect(cnt)
        area = w * h
        aspect = w / float(h)

        if area < 150 or area > 50000:
            continue
        if aspect > 6 or aspect < 0.2:
            continue
        raw_boxes.append([x, y, w, h])

    clean_boxes = non_max_suppression(raw_boxes, overlap_thresh=0.3)

    for x, y, w, h in clean_boxes:
        center_x = x + w // 2
        center_y = y + h // 2

        is_text = False
        box_area = w * h

        for tx, ty, tw, th in existing_text_boxes:
            ix = max(x, tx)
            iy = max(y, ty)
            iw = min(x + w, tx + tw) - ix
            ih = min(y + h, ty + th) - iy

            if iw > 0 and ih > 0:
                intersection = iw * ih
                if intersection > 0.3 * box_area:
                    is_text = True
                    break

        if not is_text:
            label = "unknown_icon"
            area = w * h
            aspect = w / float(h)
            if 0.8 < aspect < 1.2:
                if 200 < area < 1000:
                    label = "small_icon"
                elif 1000 < area < 4000:
                    label = "medium_icon"
            elif aspect > 2:
                label = "horizontal_element"
            elif aspect < 0.5:
                label = "vertical_element"

            candidates.append(
                {
                    "type": "icon_candidate",
                    "label": label,
                    "x": center_x,
                    "y": center_y,
                    "w": w,
                    "h": h,
                }
            )

    return candidates


def run_local_eye(image_bytes: bytes, *, lang: str = "en") -> dict[str, Any]:
    total_start = time.perf_counter()
    decode_start = time.perf_counter()
    with Image.open(io.BytesIO(image_bytes)) as image:
        rgb_image = image.convert("RGB")
        rgb_array = np.array(rgb_image)
    img = cv2.cvtColor(rgb_array, cv2.COLOR_RGB2BGR)
    decode_ms = int((time.perf_counter() - decode_start) * 1000)

    ocr_timings_ms = 0
    icon_detection_ms = 0
    ocr_device = "cpu"
    with concurrent.futures.ThreadPoolExecutor() as executor:
        ocr_start = time.perf_counter()
        ocr_future = executor.submit(ocr_service.run_easyocr_array, img, lang=lang)
        icon_start = time.perf_counter()
        icon_future = executor.submit(find_mystery_icons_sensitive, img, [])

        ocr_payload = ocr_future.result()
        ocr_timings_ms = int((time.perf_counter() - ocr_start) * 1000)
        raw_icons = icon_future.result()
        icon_detection_ms = int((time.perf_counter() - icon_start) * 1000)

    ocr_device = str(ocr_payload.get("device") or "cpu")

    elements: list[dict[str, Any]] = []
    text_boxes: list[list[int]] = []
    element_id = 0
    for item in ocr_payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        prob = float(item.get("confidence") or 0.0)
        if prob <= 0.3:
            continue
        bbox = _normalize_ocr_bbox(item.get("bbox") or [])
        if len(bbox) < 4:
            continue
        tl, _tr, br, _bl = bbox[:4]
        x, y = int(tl[0]), int(tl[1])
        w, h = int(br[0] - tl[0]), int(br[1] - tl[1])
        text_boxes.append([x, y, w, h])
        elements.append(
            {
                "id": element_id,
                "type": "text",
                "label": str(item.get("text") or ""),
                "confidence": prob,
                "x": x + w // 2,
                "y": y + h // 2,
                "w": w,
                "h": h,
            }
        )
        element_id += 1

    for icon in raw_icons:
        ix, iy, iw, ih = (
            int(icon["x"]) - int(icon["w"]) // 2,
            int(icon["y"]) - int(icon["h"]) // 2,
            int(icon["w"]),
            int(icon["h"]),
        )
        box_area = iw * ih
        is_text = False

        for tx, ty, tw, th in text_boxes:
            inter_x1 = max(ix, tx)
            inter_y1 = max(iy, ty)
            inter_x2 = min(ix + iw, tx + tw)
            inter_y2 = min(iy + ih, ty + th)

            if inter_x2 > inter_x1 and inter_y2 > inter_y1:
                intersection = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
                if intersection > 0.3 * box_area:
                    is_text = True
                    break

        if not is_text:
            icon_copy = dict(icon)
            icon_copy["id"] = element_id
            elements.append(icon_copy)
            element_id += 1

    total_ms = int((time.perf_counter() - total_start) * 1000)
    return {
        "provider": "local_cv_eye",
        "device": ocr_device,
        "hosted": True,
        "elements": elements,
        "timings_ms": {
            "decode": decode_ms,
            "ocr": int(ocr_payload.get("timings_ms", {}).get("ocr", ocr_timings_ms)),
            "icon_detection": icon_detection_ms,
            "total": total_ms,
        },
    }
