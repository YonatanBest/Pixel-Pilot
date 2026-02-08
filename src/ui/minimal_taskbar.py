import sys
import logging
import os
from typing import TYPE_CHECKING, List, Dict

try:
    import keyboard
except ImportError:
    keyboard = None

from PySide6.QtCore import QTimer, Qt, QPoint, Signal
from PySide6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout, QGridLayout,
    QPushButton, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu
)
from PySide6.QtGui import QAction, QIcon

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
try:
    from tools.app_indexer import AppIndexer
except ImportError:
    AppIndexer = None

if TYPE_CHECKING:
    from desktop.desktop_manager import AgentDesktopManager

logger = logging.getLogger("pixelpilot.shell")

class StartMenu(QWidget):
    """A functional Start Menu replacement."""
    
    def __init__(self, parent=None, desktop_manager=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.dm = desktop_manager
        self.setFixedWidth(300)
        self.setFixedHeight(400)
        self.setStyleSheet("""
            QWidget {
                background-color: #1a1a2e;
                border: 1px solid #4a4a6a;
                color: white;
            }
            QLineEdit {
                background-color: #0f0f1b;
                border: 1px solid #4a4a6a;
                padding: 5px;
                color: white;
                selection-background-color: #4e4e8a;
            }
            QListWidget {
                background-color: transparent;
                border: none;
                outline: none;
            }
            QListWidget::item {
                padding: 8px;
                border-radius: 4px;
            }
            QListWidget::item:selected {
                background-color: #4e4e8a;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search apps...")
        self.search_box.textChanged.connect(self.filter_apps)
        self.search_box.returnPressed.connect(self.launch_selected)
        layout.addWidget(self.search_box)
        
        self.list_widget = QListWidget()
        self.list_widget.itemActivated.connect(self.launch_item)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self.show_context_menu)
        layout.addWidget(self.list_widget)
        
        self.indexer = None
        if AppIndexer:
            try:
                self.indexer = AppIndexer(auto_refresh=False, include_processes=False)
                self.all_apps = self._get_sorted_apps()
            except Exception as e:
                logger.error(f"Failed to init AppIndexer: {e}")
                self.all_apps = []
        else:
            self.all_apps = []
            
        self.populate_list(self.all_apps)
        
    def _get_sorted_apps(self):
        if not self.indexer: return []
        apps = []
        for key, info in self.indexer.index.items():
            if info.get('type') != 'running_process':
                apps.append(info)
        return sorted(apps, key=lambda x: x['name'])

    def populate_list(self, apps):
        self.list_widget.clear()
        for app in apps:
            item = QListWidgetItem(app['name'])
            item.setData(Qt.ItemDataRole.UserRole, app)
            self.list_widget.addItem(item)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def filter_apps(self, text):
        text = text.lower()
        filtered = []
        for app in self.all_apps:
            if text in app['name'].lower() or text in app.get('key', ''):
                filtered.append(app)
        
        if len(filtered) == 0 and self.indexer and text:
             matches = self.indexer.find_app(text, max_results=5)
             filtered = matches
             
        self.populate_list(filtered)

    def launch_selected(self):
        if self.list_widget.currentItem():
            self.launch_item(self.list_widget.currentItem())

    def launch_item(self, item, run_as_admin: bool = False):
        app_info = item.data(Qt.ItemDataRole.UserRole)
        if app_info and self.dm:
            method, cmd = self.indexer.get_launch_command(app_info) if self.indexer else ("executable", app_info.get('path'))
            cmd = f'cmd.exe /c start "" "{cmd}"'
            self.dm.launch_process(cmd, run_as_admin=run_as_admin)
            self.close()

    def show_context_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        run_admin = menu.addAction("Run as administrator")
        action = menu.exec(self.list_widget.mapToGlobal(pos))
        if action == run_admin:
            self.launch_item(item, run_as_admin=True)
            
    def showEvent(self, event):
        self.search_box.setFocus()
        self.search_box.clear()
        self.populate_list(self.all_apps)
        super().showEvent(event)

class IconButton(QWidget):
    """A desktop icon with a label."""
    def __init__(self, name, icon_char, cmd, desktop_manager):
        super().__init__()
        self.cmd = cmd
        self.dm = desktop_manager
        self.setFixedSize(80, 90)
        
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.icon_label = QLabel(icon_char)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("font-size: 32px; color: #4e4e8a; background: transparent;")
        
        self.text_label = QLabel(name)
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text_label.setStyleSheet("font-size: 11px; color: white; background: transparent;")
        self.text_label.setWordWrap(True)
        
        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label)
        
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dm.launch_process(self.cmd)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        run_admin = menu.addAction("Run as administrator")
        action = menu.exec(event.globalPos())
        if action == run_admin:
            self.dm.launch_process(self.cmd, run_as_admin=True)

class DesktopBackground(QWidget):
    """A simple fullscreen widget to provide a desktop background with icons."""
    def __init__(self, desktop_manager):
        super().__init__(None)
        self.setWindowTitle("AgentDesktopWallpaper")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet("background-color: #0f0f1b;") # Dark space theme
        
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)
        
        # Icon Grid
        self.layout = QGridLayout(self)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.layout.setContentsMargins(20, 20, 20, 20)
        self.layout.setSpacing(20)
        
        icons = [
            ("Command\nPrompt", "💻", "cmd.exe"),
            ("Notepad", "📝", "notepad.exe"),
            ("File\nExplorer", "📁", "explorer.exe"),
            ("Task\nManager", "⚙️", "taskmgr.exe"),
        ]
        
        for i, (name, icon, cmd) in enumerate(icons):
            row = i % 8
            col = i // 8
            btn = IconButton(name, icon, cmd, desktop_manager)
            self.layout.addWidget(btn, row, col)

class MinimalTaskbar(QWidget):
    """
    A minimal, always-on-top taskbar for the Agent Desktop.
    Lists open windows and provides a basic "Session" feel.
    """
    def __init__(self, desktop_manager: "AgentDesktopManager"):
        super().__init__(None)
        self.setWindowTitle("AgentDesktopTaskbar")
        self.desktop_manager = desktop_manager
        
        # Initialize background first
        self.background = DesktopBackground(desktop_manager)
        self.background.show()
        
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint 
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        
        self.setStyleSheet("""
            QWidget {
                background-color: #1a1a2e;
                border-top: 1px solid #4a4a6a;
                color: white;
            }
            QPushButton {
                background-color: #2a2a4e;
                border: 1px solid #4a4a6a;
                padding: 5px 10px;
                border_radius: 3px;
                color: #ffffff;
                font-family: 'Segoe UI', sans-serif;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #3a3a6e;
            }
            QPushButton#startBtn {
                background-color: #4e4e8a;
                font-weight: bold;
            }
            QLabel {
                padding: 0 10px;
                font-weight: bold;
                color: #8a8ab0;
                background: transparent;
            }
        """)
        
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(5, 2, 5, 2)
        self.layout.setSpacing(5)
        
        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.setFixedWidth(60)
        self.start_btn.clicked.connect(self.show_launcher)
        self.layout.addWidget(self.start_btn)
        
        self.label = QLabel("|")
        self.layout.addWidget(self.label)
        
        self.windows_container = QWidget()
        self.windows_layout = QHBoxLayout(self.windows_container)
        self.windows_layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.windows_container)
        self.layout.addStretch()
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_windows)
        self.timer.start(2000) # Refresh every 2 seconds
        
        self.start_menu = StartMenu(desktop_manager=self.desktop_manager)
        
        if keyboard:
            try:
                keyboard.add_hotkey('win', self.toggle_start_menu, suppress=False)
            except Exception as e:
                logger.error(f"Failed to register hotkey: {e}")

        self._update_geometry()

    def toggle_start_menu(self):
        if self.start_menu.isVisible():
            self.start_menu.hide()
        else:
            self.show_launcher()
        
    def show_launcher(self):
        menu_pos = self.mapToGlobal(self.start_btn.rect().topLeft())
        self.start_menu.move(menu_pos - QPoint(0, self.start_menu.height()))
        self.start_menu.show()
        self.start_menu.raise_()
        self.start_menu.activateWindow()
        self.start_menu.search_box.setFocus()

    def _update_geometry(self):
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(0, screen.height() - 40, screen.width(), 40)

    def update_windows(self):
        # Clear existing buttons
        for i in reversed(range(self.windows_layout.count())): 
            widget = self.windows_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)
            
        try:
            windows = self.desktop_manager.list_windows()
            # Filter out shell components
            filtered = [w for w in windows if w['title'] and "AgentDesktop" not in w['title']]
            
            for w in filtered[:8]: # Limit to 8 buttons
                btn = QPushButton(w['title'][:20] + ("..." if len(w['title']) > 20 else ""))
                btn.clicked.connect(lambda checked=False, hwnd=w['hwnd']: self.activate_window(hwnd))
                self.windows_layout.addWidget(btn)
        except Exception as e:
            logger.debug(f"Taskbar update error: {e}")

    def activate_window(self, hwnd):
        import ctypes
        user32 = ctypes.windll.user32
        user32.SetForegroundWindow(hwnd)
        user32.ShowWindow(hwnd, 5) # SW_SHOW

def main():
    # Helper to launch standalone on the desktop
    import os
    import sys
    
    # Add src to sys.path to allow absolute imports
    src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    
    from desktop.desktop_manager import AgentDesktopManager
    
    desktop_name = os.environ.get("AGENT_DESKTOP_NAME", "PixelPilotAgent")
    dm = AgentDesktopManager(desktop_name)
    
    app = QApplication(sys.argv)
    bar = MinimalTaskbar(dm)
    bar.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
