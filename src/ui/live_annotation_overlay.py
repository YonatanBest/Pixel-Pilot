from __future__ import annotations

import ctypes
import uuid
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget


@dataclass
class _BoxAnnotation:
    annotation_id: str
    x: float
    y: float
    width: float
    height: float
    color: str
    stroke_width: float
    opacity: float
    corner_radius: float


@dataclass
class _TextAnnotation:
    annotation_id: str
    x: float
    y: float
    text: str
    color: str
    font_size: int
    font_family: str
    align: str
    baseline: str
    max_width: int
    panel_bg: str
    panel_bg_secondary: str
    accent_glow: str
    panel_border: str


@dataclass
class _PointerAnnotation:
    annotation_id: str
    x: float
    y: float
    color: str
    dot_color: str
    radius: float
    ring_radius: float
    ring_width: float
    line_width: float
    text: str
    text_x: Optional[float]
    text_y: Optional[float]
    font_size: int
    font_family: str
    text_max_width: int


class LiveAnnotationOverlay(QWidget):
    """
    Full-screen, click-through overlay for live teaching annotations.
    """

    def __init__(self) -> None:
        super().__init__(None)

        self._origin_x = 0
        self._origin_y = 0
        self._virtual_width = 1
        self._virtual_height = 1
        self._source_origin_x = 0
        self._source_origin_y = 0
        self._source_width = 1
        self._source_height = 1
        self._source_to_overlay_x = 1.0
        self._source_to_overlay_y = 1.0

        self._boxes: dict[str, _BoxAnnotation] = {}
        self._texts: dict[str, _TextAnnotation] = {}
        self._pointers: dict[str, _PointerAnnotation] = {}

        self._init_window_flags()
        self._bind_screen_events()
        self._refresh_geometry()
        self.hide()

    def _init_window_flags(self) -> None:
        self.setWindowTitle("Pixy Live Annotation Overlay")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

    def _bind_screen_events(self) -> None:
        app = QGuiApplication.instance()
        if not app:
            return
        app.screenAdded.connect(self._refresh_geometry)
        app.screenRemoved.connect(self._refresh_geometry)
        for screen in app.screens():
            try:
                screen.geometryChanged.connect(self._refresh_geometry)
            except Exception:
                continue

    def _virtual_rect(self) -> QRect:
        app = QGuiApplication.instance()
        screens = app.screens() if app else []
        if not screens:
            return QRect(0, 0, 1920, 1080)

        left = min(screen.geometry().left() for screen in screens)
        top = min(screen.geometry().top() for screen in screens)
        right = max(screen.geometry().right() for screen in screens)
        bottom = max(screen.geometry().bottom() for screen in screens)
        return QRect(left, top, max(1, right - left + 1), max(1, bottom - top + 1))

    @staticmethod
    def _physical_virtual_rect() -> Optional[QRect]:
        try:
            user32 = ctypes.windll.user32
            left = int(user32.GetSystemMetrics(76))  # SM_XVIRTUALSCREEN
            top = int(user32.GetSystemMetrics(77))  # SM_YVIRTUALSCREEN
            width = int(user32.GetSystemMetrics(78))  # SM_CXVIRTUALSCREEN
            height = int(user32.GetSystemMetrics(79))  # SM_CYVIRTUALSCREEN
            if width > 0 and height > 0:
                return QRect(left, top, width, height)
        except Exception:
            pass
        return None

    def _refresh_geometry(self, *_args) -> None:
        rect = self._virtual_rect()
        self._origin_x = int(rect.x())
        self._origin_y = int(rect.y())
        self._virtual_width = max(1, int(rect.width()))
        self._virtual_height = max(1, int(rect.height()))

        source_rect = self._physical_virtual_rect()
        if source_rect is not None:
            self._source_origin_x = int(source_rect.x())
            self._source_origin_y = int(source_rect.y())
            self._source_width = max(1, int(source_rect.width()))
            self._source_height = max(1, int(source_rect.height()))
        else:
            self._source_origin_x = self._origin_x
            self._source_origin_y = self._origin_y
            self._source_width = self._virtual_width
            self._source_height = self._virtual_height

        self._source_to_overlay_x = self._virtual_width / max(1.0, float(self._source_width))
        self._source_to_overlay_y = self._virtual_height / max(1.0, float(self._source_height))
        self.setGeometry(rect)
        self.update()

    @staticmethod
    def _safe_float(value, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return float(default)

    @staticmethod
    def _safe_int(value, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return int(default)

    @staticmethod
    def _safe_color(value: str, fallback: str) -> QColor:
        candidate = QColor(str(value or "").strip())
        if candidate.isValid():
            return candidate
        return QColor(fallback)

    @staticmethod
    def _normalize_align(value: str) -> str:
        clean = str(value or "left").strip().lower()
        if clean in {"left", "center", "right"}:
            return clean
        return "left"

    @staticmethod
    def _normalize_baseline(value: str) -> str:
        clean = str(value or "top").strip().lower()
        if clean in {"top", "middle", "center", "bottom"}:
            return "middle" if clean == "center" else clean
        return "top"

    def _next_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:8]}"

    def _to_abs_x(self, value: float, normalized: bool) -> float:
        if normalized:
            if 0.0 <= value <= 1.0:
                return self._origin_x + (value * self._virtual_width)
            if 0.0 <= value <= 1000.0:
                return self._origin_x + ((value / 1000.0) * self._virtual_width)
        return self._origin_x + ((value - self._source_origin_x) * self._source_to_overlay_x)

    def _to_abs_y(self, value: float, normalized: bool) -> float:
        if normalized:
            if 0.0 <= value <= 1.0:
                return self._origin_y + (value * self._virtual_height)
            if 0.0 <= value <= 1000.0:
                return self._origin_y + ((value / 1000.0) * self._virtual_height)
        return self._origin_y + ((value - self._source_origin_y) * self._source_to_overlay_y)

    def _to_abs_w(self, value: float, normalized: bool) -> float:
        if normalized:
            if 0.0 <= value <= 1.0:
                return value * self._virtual_width
            if 0.0 <= value <= 1000.0:
                return (value / 1000.0) * self._virtual_width
        return value * self._source_to_overlay_x

    def _to_abs_h(self, value: float, normalized: bool) -> float:
        if normalized:
            if 0.0 <= value <= 1.0:
                return value * self._virtual_height
            if 0.0 <= value <= 1000.0:
                return (value / 1000.0) * self._virtual_height
        return value * self._source_to_overlay_y

    def _abs_to_local(self, x: float, y: float) -> QPointF:
        return QPointF(x - self._origin_x, y - self._origin_y)

    def _schedule_expiry(self, kind: str, annotation_id: str, ttl_ms: int) -> None:
        if ttl_ms <= 0:
            return

        def _expire() -> None:
            if kind == "box":
                self._boxes.pop(annotation_id, None)
            elif kind == "text":
                self._texts.pop(annotation_id, None)
            elif kind == "pointer":
                self._pointers.pop(annotation_id, None)
            self._refresh_visibility()
            self.update()

        QTimer.singleShot(ttl_ms, _expire)

    def _refresh_visibility(self) -> None:
        has_items = bool(self._boxes or self._texts or self._pointers)
        if has_items:
            self._refresh_geometry()
            if not self.isVisible():
                self.show()
            self.raise_()
        elif self.isVisible():
            self.hide()

    def clear_annotations(self) -> None:
        self._boxes.clear()
        self._texts.clear()
        self._pointers.clear()
        self._refresh_visibility()
        self.update()

    def remove_annotation(self, annotation_id: str) -> bool:
        clean_id = str(annotation_id or "").strip()
        if not clean_id:
            return False
        removed = False
        removed = self._boxes.pop(clean_id, None) is not None or removed
        removed = self._texts.pop(clean_id, None) is not None or removed
        removed = self._pointers.pop(clean_id, None) is not None or removed
        if removed:
            self._refresh_visibility()
            self.update()
        return removed

    def apply_command(self, payload: dict) -> dict:
        action = str(payload.get("action") or "").strip().lower()
        if action in {"overlay_clear", "clear"}:
            self.clear_annotations()
            return {"ok": True, "annotation_id": None}
        if action in {"overlay_remove", "remove"}:
            removed = self.remove_annotation(str(payload.get("id") or ""))
            return {"ok": removed, "annotation_id": str(payload.get("id") or "")}
        if action in {"overlay_draw_box", "draw_box"}:
            return self._apply_draw_box(payload)
        if action in {"overlay_draw_text", "draw_text"}:
            return self._apply_draw_text(payload)
        if action in {"overlay_draw_pointer", "draw_pointer"}:
            return self._apply_draw_pointer(payload)
        return {"ok": False, "error": "unknown_overlay_action"}

    def _apply_draw_box(self, payload: dict) -> dict:
        normalized = bool(payload.get("normalized", False))
        annotation_id = str(payload.get("id") or self._next_id("box"))

        x = payload.get("x")
        y = payload.get("y")
        width = payload.get("width")
        height = payload.get("height")
        x_min = payload.get("x_min")
        y_min = payload.get("y_min")
        x_max = payload.get("x_max")
        y_max = payload.get("y_max")

        if x is None or y is None or width is None or height is None:
            if None in {x_min, y_min, x_max, y_max}:
                return {"ok": False, "error": "invalid_args", "message": "Missing box coordinates."}
            left = self._to_abs_x(self._safe_float(x_min, 0.0), normalized)
            top = self._to_abs_y(self._safe_float(y_min, 0.0), normalized)
            right = self._to_abs_x(self._safe_float(x_max, 0.0), normalized)
            bottom = self._to_abs_y(self._safe_float(y_max, 0.0), normalized)
            x = left
            y = top
            width = right - left
            height = bottom - top
        else:
            x = self._to_abs_x(self._safe_float(x, 0.0), normalized)
            y = self._to_abs_y(self._safe_float(y, 0.0), normalized)
            width = self._to_abs_w(self._safe_float(width, 0.0), normalized)
            height = self._to_abs_h(self._safe_float(height, 0.0), normalized)

        width = max(1.0, float(width))
        height = max(1.0, float(height))

        self._boxes[annotation_id] = _BoxAnnotation(
            annotation_id=annotation_id,
            x=float(x),
            y=float(y),
            width=width,
            height=height,
            color=str(payload.get("color") or "#47E17B"),
            stroke_width=max(1.0, self._safe_float(payload.get("stroke_width"), 3.0)),
            opacity=max(0.05, min(1.0, self._safe_float(payload.get("opacity"), 0.95))),
            corner_radius=max(0.0, self._safe_float(payload.get("corner_radius"), 10.0)),
        )

        ttl_ms = max(0, self._safe_int(payload.get("ttl_ms"), 0))
        self._schedule_expiry("box", annotation_id, ttl_ms)
        self._refresh_visibility()
        self.update()
        return {"ok": True, "annotation_id": annotation_id}

    def _apply_draw_text(self, payload: dict) -> dict:
        normalized = bool(payload.get("normalized", False))
        annotation_id = str(payload.get("id") or self._next_id("text"))

        text = str(payload.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "invalid_args", "message": "Text is required."}

        x = self._to_abs_x(self._safe_float(payload.get("x"), 0.0), normalized)
        y = self._to_abs_y(self._safe_float(payload.get("y"), 0.0), normalized)

        max_width = self._safe_int(payload.get("max_width"), 360)
        if normalized:
            max_width = int(self._to_abs_w(float(max_width), True))
        max_width = max(120, min(max_width, self._virtual_width - 24))

        self._texts[annotation_id] = _TextAnnotation(
            annotation_id=annotation_id,
            x=float(x),
            y=float(y),
            text=text,
            color=str(payload.get("color") or "#DFF3FF"),
            font_size=max(12, self._safe_int(payload.get("font_size"), 18)),
            font_family=str(payload.get("font_family") or "Segoe UI"),
            align=self._normalize_align(str(payload.get("align") or "left")),
            baseline=self._normalize_baseline(str(payload.get("baseline") or "top")),
            max_width=max_width,
            panel_bg=str(payload.get("panel_bg") or "#0F1117E6"),
            panel_bg_secondary=str(payload.get("panel_bg_secondary") or "#253B63DA"),
            accent_glow=str(payload.get("accent_glow") or "#3E66BACC"),
            panel_border=str(payload.get("panel_border") or "#FFFFFF26"),
        )

        ttl_ms = max(0, self._safe_int(payload.get("ttl_ms"), 0))
        self._schedule_expiry("text", annotation_id, ttl_ms)
        self._refresh_visibility()
        self.update()
        return {"ok": True, "annotation_id": annotation_id}

    def _apply_draw_pointer(self, payload: dict) -> dict:
        normalized = bool(payload.get("normalized", False))
        annotation_id = str(payload.get("id") or self._next_id("ptr"))

        x = self._to_abs_x(self._safe_float(payload.get("x"), 0.0), normalized)
        y = self._to_abs_y(self._safe_float(payload.get("y"), 0.0), normalized)

        text = str(payload.get("text") or "").strip()
        text_x = payload.get("text_x")
        text_y = payload.get("text_y")
        resolved_text_x = (
            self._to_abs_x(self._safe_float(text_x, 0.0), normalized)
            if text_x is not None
            else None
        )
        resolved_text_y = (
            self._to_abs_y(self._safe_float(text_y, 0.0), normalized)
            if text_y is not None
            else None
        )

        text_max_width = self._safe_int(payload.get("text_max_width"), 320)
        if normalized:
            text_max_width = int(self._to_abs_w(float(text_max_width), True))
        text_max_width = max(120, min(text_max_width, self._virtual_width - 24))

        self._pointers[annotation_id] = _PointerAnnotation(
            annotation_id=annotation_id,
            x=float(x),
            y=float(y),
            color=str(payload.get("color") or "#66B7FF"),
            dot_color=str(payload.get("dot_color") or "#FFFFFF"),
            radius=max(2.0, self._safe_float(payload.get("radius"), 5.0)),
            ring_radius=max(4.0, self._safe_float(payload.get("ring_radius"), 18.0)),
            ring_width=max(1.0, self._safe_float(payload.get("ring_width"), 2.0)),
            line_width=max(1.0, self._safe_float(payload.get("line_width"), 2.0)),
            text=text,
            text_x=resolved_text_x,
            text_y=resolved_text_y,
            font_size=max(12, self._safe_int(payload.get("font_size"), 17)),
            font_family=str(payload.get("font_family") or "Segoe UI"),
            text_max_width=text_max_width,
        )

        ttl_ms = max(0, self._safe_int(payload.get("ttl_ms"), 0))
        self._schedule_expiry("pointer", annotation_id, ttl_ms)
        self._refresh_visibility()
        self.update()
        return {"ok": True, "annotation_id": annotation_id}

    def paintEvent(self, event) -> None:  # noqa: N802
        if not (self._boxes or self._texts or self._pointers):
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        for item in self._boxes.values():
            self._paint_box(painter, item)

        for item in self._texts.values():
            self._paint_text(painter, item)

        for item in self._pointers.values():
            self._paint_pointer(painter, item)

    def _paint_box(self, painter: QPainter, item: _BoxAnnotation) -> None:
        main_color = self._safe_color(item.color, "#47E17B")
        main_color.setAlphaF(item.opacity)

        top_left = self._abs_to_local(item.x, item.y)
        rect = QRectF(top_left.x(), top_left.y(), item.width, item.height)

        # Outer glow pass for neon highlight look.
        glow_color = QColor(main_color)
        glow_color.setAlpha(max(18, int(95 * item.opacity)))
        painter.setPen(QPen(glow_color, item.stroke_width + 6.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, item.corner_radius, item.corner_radius)

        # Main stroke.
        painter.setPen(QPen(main_color, item.stroke_width))
        painter.drawRoundedRect(rect, item.corner_radius, item.corner_radius)

        # Subtle inner white highlight.
        inner_color = QColor(255, 255, 255, max(18, int(56 * item.opacity)))
        painter.setPen(QPen(inner_color, 1.0))
        painter.drawRoundedRect(rect, item.corner_radius, item.corner_radius)

    def _layout_text_bubble(
        self,
        painter: QPainter,
        *,
        x_abs: float,
        y_abs: float,
        text: str,
        font_size: int,
        font_family: str,
        align: str,
        baseline: str,
        max_width: int,
    ) -> tuple[QRectF, QRectF]:
        local = self._abs_to_local(x_abs, y_abs)
        font = QFont(font_family, font_size)
        painter.setFont(font)
        metrics = QFontMetricsF(font)

        inner_width = max(80.0, float(max_width))
        text_bounds = metrics.boundingRect(
            QRectF(0.0, 0.0, inner_width, 1200.0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap),
            text,
        )

        pad_x = 12.0
        pad_y = 10.0
        bubble_w = max(96.0, text_bounds.width() + (pad_x * 2.0))
        bubble_h = max(42.0, text_bounds.height() + (pad_y * 2.0))

        x = local.x()
        y = local.y()
        if align == "center":
            x -= bubble_w * 0.5
        elif align == "right":
            x -= bubble_w

        if baseline == "middle":
            y -= bubble_h * 0.5
        elif baseline == "bottom":
            y -= bubble_h

        margin = 6.0
        x = min(max(margin, x), max(margin, self.width() - bubble_w - margin))
        y = min(max(margin, y), max(margin, self.height() - bubble_h - margin))

        bubble_rect = QRectF(x, y, bubble_w, bubble_h)
        text_rect = bubble_rect.adjusted(pad_x, pad_y, -pad_x, -pad_y)
        return bubble_rect, text_rect

    def _paint_text_bubble(
        self,
        painter: QPainter,
        *,
        x_abs: float,
        y_abs: float,
        text: str,
        font_size: int,
        font_family: str,
        align: str,
        baseline: str,
        max_width: int,
        text_color: str,
        panel_bg: str,
        panel_bg_secondary: str,
        accent_glow: str,
        panel_border: str,
    ) -> QRectF:
        bubble_rect, text_rect = self._layout_text_bubble(
            painter,
            x_abs=x_abs,
            y_abs=y_abs,
            text=text,
            font_size=font_size,
            font_family=font_family,
            align=align,
            baseline=baseline,
            max_width=max_width,
        )

        bg_primary = self._safe_color(panel_bg, "#0F1117E6")
        bg_secondary = self._safe_color(panel_bg_secondary, "#253B63DA")
        accent = self._safe_color(accent_glow, "#3E66BACC")
        border_color = self._safe_color(panel_border, "#FFFFFF26")
        txt_color = self._safe_color(text_color, "#F2F5F8")
        radius = 18.0

        # Soft shadow stack.
        shadow_outer = QRectF(bubble_rect)
        shadow_outer.translate(0.0, 6.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 84))
        painter.drawRoundedRect(shadow_outer, radius, radius)

        shadow_inner = QRectF(bubble_rect)
        shadow_inner.translate(0.0, 2.0)
        painter.setBrush(QColor(0, 0, 0, 42))
        painter.drawRoundedRect(shadow_inner, radius, radius)

        # Base gradient.
        grad = QLinearGradient(bubble_rect.topLeft(), bubble_rect.bottomRight())
        grad.setColorAt(0.0, bg_primary)
        grad.setColorAt(1.0, bg_secondary)
        painter.setBrush(QBrush(grad))
        painter.setPen(QPen(border_color, 1.2))
        painter.drawRoundedRect(bubble_rect, radius, radius)

        # Accent radial glow from lower-right.
        radial = QRadialGradient(
            bubble_rect.right(),
            bubble_rect.bottom(),
            max(40.0, bubble_rect.width() * 0.9),
        )
        bright = QColor(accent)
        bright.setAlpha(max(20, bright.alpha()))
        transparent = QColor(accent)
        transparent.setAlpha(0)
        radial.setColorAt(0.0, bright)
        radial.setColorAt(1.0, transparent)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(radial))
        painter.drawRoundedRect(bubble_rect, radius, radius)

        # Top edge specular line.
        painter.setPen(QPen(QColor(255, 255, 255, 26), 1.0))
        painter.drawLine(
            QPointF(bubble_rect.left() + 14.0, bubble_rect.top() + 1.0),
            QPointF(bubble_rect.right() - 14.0, bubble_rect.top() + 1.0),
        )

        text_font = QFont(font_family, font_size)
        text_font.setWeight(QFont.Weight.Medium)
        painter.setFont(text_font)
        painter.setPen(QPen(txt_color, 1.0))
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap), text)
        return bubble_rect

    def _paint_text(self, painter: QPainter, item: _TextAnnotation) -> None:
        self._paint_text_bubble(
            painter,
            x_abs=item.x,
            y_abs=item.y,
            text=item.text,
            font_size=item.font_size,
            font_family=item.font_family,
            align=item.align,
            baseline=item.baseline,
            max_width=item.max_width,
            text_color=item.color,
            panel_bg=item.panel_bg,
            panel_bg_secondary=item.panel_bg_secondary,
            accent_glow=item.accent_glow,
            panel_border=item.panel_border,
        )

    def _paint_pointer(self, painter: QPainter, item: _PointerAnnotation) -> None:
        center_local = self._abs_to_local(item.x, item.y)

        ring_color = self._safe_color(item.color, "#66B7FF")
        dot_color = self._safe_color(item.dot_color, "#FFFFFF")

        painter.setPen(QPen(ring_color, item.ring_width))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center_local, item.ring_radius, item.ring_radius)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot_color)
        painter.drawEllipse(center_local, item.radius, item.radius)

        if not item.text:
            return

        text_x = item.text_x if item.text_x is not None else (item.x + 24.0)
        text_y = item.text_y if item.text_y is not None else (item.y - 24.0)
        bubble_rect = self._paint_text_bubble(
            painter,
            x_abs=text_x,
            y_abs=text_y,
            text=item.text,
            font_size=item.font_size,
            font_family=item.font_family,
            align="left",
            baseline="top",
            max_width=item.text_max_width,
            text_color="#DFF3FF",
            panel_bg="#14181CD9",
            panel_bg_secondary="#22365AD4",
            accent_glow="#3E66BAAA",
            panel_border="#9ED1FF80",
        )

        target = bubble_rect.center()
        painter.setPen(QPen(self._safe_color(item.color, "#66B7FF"), item.line_width))
        painter.drawLine(center_local, target)
