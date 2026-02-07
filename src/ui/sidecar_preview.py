"""
Sidecar Preview Widget

Displays a live, read-only preview of the Agent Desktop attached to
the right edge of the main PixelPilot window.
"""

import logging
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QTimer, Qt, QPoint
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout

if TYPE_CHECKING:
    from desktop.desktop_manager import AgentDesktopManager
    from PySide6.QtWidgets import QMainWindow

logger = logging.getLogger("pixelpilot.sidecar")


class SidecarPreview(QWidget):
    """
    A frameless, read-only preview window that shows the Agent Desktop.

    The sidecar:
    - Attaches to the right edge of the main window
    - Updates at configurable FPS (default 5)
    - Ignores all mouse/keyboard input
    - Scales the desktop capture to fit preview size
    """

    DEFAULT_WIDTH = 400
    DEFAULT_HEIGHT = 300
    DEFAULT_FPS = 5

    def __init__(
        self,
        parent_window: "QMainWindow",
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        fps: int = DEFAULT_FPS,
    ):
        super().__init__(None)  # No Qt parent to avoid clipping

        self.parent_window = parent_window
        self.preview_width = width
        self.preview_height = height
        self.fps = fps
        self.desktop_manager: Optional["AgentDesktopManager"] = None

        self._setup_ui()
        self._setup_timer()

    def _setup_ui(self):
        """Configure the widget appearance."""
        self.setWindowTitle("Agent Desktop Preview")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        # Make read-only: ignore mouse/keyboard
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.setFixedSize(self.preview_width, self.preview_height)
        self.setStyleSheet("""
            QWidget {
                background-color: #1a1a2e;
                border: 2px solid #4a4a6a;
                border-radius: 8px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        # Preview label
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("""
            QLabel {
                background-color: #0d0d1a;
                border-radius: 4px;
                color: #666688;
            }
        """)
        self.preview_label.setText("Agent Desktop\n(Waiting...)")
        layout.addWidget(self.preview_label)

    def _setup_timer(self):
        """Setup the refresh timer."""
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh_preview)
        interval_ms = int(1000 / self.fps) if self.fps > 0 else 200
        self.refresh_timer.setInterval(interval_ms)

    def set_capture_source(self, desktop_manager: "AgentDesktopManager"):
        """
        Set the desktop manager to capture from.

        Args:
            desktop_manager: The AgentDesktopManager instance.
        """
        self.desktop_manager = desktop_manager
        if self.isVisible():
            self.refresh_timer.start()

    def attach_to_window(self):
        """Position the sidecar to the right of the parent window."""
        if not self.parent_window:
            return

        parent_geo = self.parent_window.geometry()
        new_x = parent_geo.right() + 10  # 10px gap
        new_y = parent_geo.top()

        self.move(new_x, new_y)

    def reattach(self):
        """Reposition after parent window moves/resizes."""
        self.attach_to_window()

    def showEvent(self, event):
        """Handle show event."""
        super().showEvent(event)
        self.attach_to_window()
        if self.desktop_manager:
            self.refresh_timer.start()

    def hideEvent(self, event):
        """Handle hide event."""
        super().hideEvent(event)
        self.refresh_timer.stop()

    def _refresh_preview(self):
        """Capture and display the Agent Desktop."""
        if not self.desktop_manager or not self.desktop_manager.is_created:
            return

        try:
            image = self.desktop_manager.capture_desktop()
            if image is None:
                return

            # Convert PIL Image to QPixmap
            image = image.convert("RGB")
            data = image.tobytes("raw", "RGB")
            qimage = QImage(
                data,
                image.width,
                image.height,
                image.width * 3,
                QImage.Format.Format_RGB888,
            )

            # Scale to fit preview
            pixmap = QPixmap.fromImage(qimage)
            scaled_pixmap = pixmap.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

            self.preview_label.setPixmap(scaled_pixmap)

        except Exception as e:
            logger.debug(f"Preview refresh error: {e}")

    def close(self):
        """Clean up resources."""
        self.refresh_timer.stop()
        super().close()
