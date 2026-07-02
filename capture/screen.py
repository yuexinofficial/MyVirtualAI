"""
Screen capture using mss. Captures desktop screenshots for vision LLM context.
"""

import io
import base64
import time
import threading
from PIL import Image
import mss


class ScreenCapture:
    """Captures desktop screenshots at a throttled rate."""

    def __init__(self, config: dict):
        self._max_dim = config.get("max_dimension", 1024)
        self._quality = config.get("quality", 75)
        self._interval = config.get("capture_interval", 2.0)
        self._monitor = config.get("monitor", 0)
        self._last_capture_time = 0
        self._lock = threading.Lock()

    def capture(self) -> str | None:
        """Capture screen and return as base64-encoded JPEG.
        Returns None if throttled (called too soon since last capture).
        """
        now = time.time()
        with self._lock:
            if now - self._last_capture_time < self._interval:
                return None
            self._last_capture_time = now

        with mss.mss() as sct:
            monitor = sct.monitors[self._monitor + 1]  # mss monitors[0] is "all"
            img = sct.grab(monitor)

        pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")

        # Resize to reduce token usage for vision LLM
        w, h = pil_img.size
        if max(w, h) > self._max_dim:
            scale = self._max_dim / max(w, h)
            pil_img = pil_img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG", quality=self._quality)
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    def capture_forced(self) -> str:
        """Capture screen immediately, ignoring throttle."""
        with mss.mss() as sct:
            monitor = sct.monitors[self._monitor + 1]
            img = sct.grab(monitor)

        pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
        w, h = pil_img.size
        if max(w, h) > self._max_dim:
            scale = self._max_dim / max(w, h)
            pil_img = pil_img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG", quality=self._quality)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
