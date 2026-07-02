"""
Transparent, frameless overlay window hosting the Live2D model via QWebEngineView.
Uses a local HTTP server to serve assets — no file:// CORS issues.

Click-through behavior:
- When unfocused: mouse clicks on empty areas pass through to desktop (via WM_NCHITTEST).
  Clicks on the model area activate the window.
- When focused: all interactions work, a visible border indicates selected state.
"""

import ctypes
from ctypes import wintypes
import os
import socket
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler

from PyQt5.QtCore import Qt, QUrl, QEvent, QPoint
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWebChannel import QWebChannel

from .bridge import Live2DBridge

# Windows API constants
WM_NCHITTEST = 0x0084
HTTRANSPARENT = -1
HTCLIENT = 1

# Windows MSG struct
class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class Live2DWindow(QWidget):
    """A frameless, transparent, always-on-top window showing a Live2D model."""

    def __init__(self, config: dict, project_root: str):
        super().__init__()
        self.config = config
        self.project_root = project_root
        self._bridge = Live2DBridge()
        self._http_server = None
        self._http_port = None
        self._window_active = False

        self._start_http_server()
        self._init_window()
        self._init_webview()
        self._init_bridge()
        self._position_window()

    def _start_http_server(self):
        """Start a minimal HTTP server to serve web + model files, no CORS issues."""
        project_root = self.project_root  # capture outer self for inner class

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=project_root, **kwargs)

            def log_message(self, fmt, *args):
                pass  # Suppress HTTP request logs

        self._http_port = _find_free_port()
        self._http_server = HTTPServer(("127.0.0.1", self._http_port), Handler)
        thread = threading.Thread(target=self._http_server.serve_forever, daemon=True)
        thread.start()

    def _init_window(self):
        """Configure the overlay window."""
        win_cfg = self.config.get("window", {})

        self._default_width = win_cfg.get("width", 400)
        self._default_height = win_cfg.get("height", 500)

        self.setWindowFlags(
            Qt.FramelessWindowHint  # No title bar
            | Qt.WindowStaysOnTopHint  # Always on top
            | Qt.Tool  # Don't show in taskbar
        )
        self.setAttribute(Qt.WA_TranslucentBackground)  # Transparent
        self.resize(self._default_width, self._default_height)
        self.setWindowTitle("AI Companion")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

    def _init_webview(self):
        """Set up the QWebEngineView to render the Live2D HTML page."""
        self.webview = QWebEngineView(self)
        self.webview.setAttribute(Qt.WA_TranslucentBackground)
        self.webview.setStyleSheet("QWebEngineView { border: none; outline: none; }")
        self.webview.page().setBackgroundColor(Qt.transparent)

        self.layout().addWidget(self.webview)

        # Model path relative to project root
        model_rel = self.config.get("model_path", "models/Haru/Haru.model3.json")

        # Load from local HTTP server — no CORS / file:// issues
        url = QUrl(f"http://127.0.0.1:{self._http_port}/live2d/web/index.html?model=/{model_rel}")
        self.webview.load(url)

    def _init_bridge(self):
        """Set up QWebChannel for Python ↔ JavaScript communication."""
        self._channel = QWebChannel()
        self._channel.registerObject("bridge", self._bridge)
        self.webview.page().setWebChannel(self._channel)

        # Register handlers for JS window drag/resize/reset events
        self._bridge.set_move_window_callback(self._on_js_window_move)
        self._bridge.set_resize_window_callback(self._on_js_window_resize)
        self._bridge.set_reset_window_callback(self._on_js_window_reset)

    def _on_js_window_move(self, dx: int, dy: int):
        """Move window by (dx, dy) — called from JS drag events."""
        pos = self.pos()
        self.move(pos.x() + dx, pos.y() + dy)

    def _on_js_window_resize(self, delta: int):
        """Resize window by delta, keeping aspect ratio."""
        w = max(200, min(1200, self.width() + delta))
        h = int(w * self._default_height / self._default_width)
        self.resize(w, h)

    def _on_js_window_reset(self):
        """Reset window to default size."""
        self.resize(self._default_width, self._default_height)

    def nativeEvent(self, eventType, message):
        """Handle WM_NCHITTEST: when unfocused, all clicks within the window
        activate it. JS side determines model vs window selection."""
        if eventType == "windows_generic_MSG":
            try:
                msg = MSG.from_address(int(message))
            except (ValueError, TypeError):
                return False, 0
            if msg.message == WM_NCHITTEST and not self._window_active:
                # All clicks within the window activate it — JS handles selection
                return False, 0
        return False, 0

    def changeEvent(self, event):
        """Detect window activation/deactivation changes."""
        if event.type() == QEvent.ActivationChange:
            was_active = self._window_active
            self._window_active = self.isActiveWindow()
            if self._window_active != was_active:
                self._bridge.window_focus_changed_signal.emit(self._window_active)
        super().changeEvent(event)

    def _position_window(self):
        """Position the window at the configured or default location."""
        win_cfg = self.config.get("window", {})

        x = win_cfg.get("x")
        y = win_cfg.get("y")

        if x is None or y is None:
            # Center-right of primary screen
            screen = QApplication.primaryScreen().availableGeometry()
            x = screen.right() - self.width() - 50
            y = screen.bottom() - self.height() - 100

        self.move(x, y)

    # ---------- Public API ----------

    @property
    def bridge(self) -> Live2DBridge:
        return self._bridge

    def show_window(self):
        """Show the overlay window."""
        self.show()

    def set_always_on_top(self, enabled: bool):
        """Toggle always-on-top behavior."""
        flags = self.windowFlags()
        if enabled:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()  # Re-show to apply flag change

    def closeEvent(self, event):
        if self._http_server:
            self._http_server.shutdown()
        super().closeEvent(event)
