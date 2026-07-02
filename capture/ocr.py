"""
OCR text extraction from screenshots.
Uses easyocr (pure Python, supports Chinese + English).
Must call initialize() at startup in the main thread — PyTorch is NOT thread-safe.
"""

import io
import base64
import logging
import numpy as np
from PIL import Image

log = logging.getLogger(__name__)


class OCRReader:
    """Extracts text from screenshots using easyocr."""

    def __init__(self, config: dict):
        self._enabled = config.get("enabled", True)
        self._languages = config.get("languages", ["ch_sim", "en"])
        self._gpu = config.get("gpu", True)
        self._min_confidence = config.get("min_confidence", 0.3)
        self._reader = None
        self._init_failed = False

    @property
    def enabled(self) -> bool:
        return self._enabled and not self._init_failed

    def initialize(self):
        """Initialize easyocr in the MAIN thread (PyTorch is not thread-safe).
        Call this at startup, before the async event loop starts.
        """
        if self._reader is not None or self._init_failed:
            return
        if not self._enabled:
            log.info("OCR disabled in config, skipping init")
            return
        try:
            import easyocr
            log.info("Initializing easyocr (this may take 10-30 seconds)...")
            self._reader = easyocr.Reader(self._languages, gpu=self._gpu)
            log.info("easyocr initialized successfully")
        except ImportError:
            log.warning("easyocr not installed — OCR disabled. Run: pip install easyocr")
            self._init_failed = True
        except Exception as e:
            log.warning(f"easyocr init failed: {e} — OCR disabled")
            self._init_failed = True

    def read_text(self, pil_image: Image.Image) -> str:
        """Run OCR on a PIL image. Returns concatenated detected text."""
        if self._reader is None or self._init_failed:
            return ""

        try:
            np_img = np.array(pil_image)
            results = self._reader.readtext(np_img, detail=0)
            if not results:
                return ""
            return "检测到以下屏幕文字：\n" + "\n".join(f"- {t}" for t in results)
        except Exception as e:
            log.warning(f"OCR failed: {e}")
            return ""

    def read_from_base64(self, screen_b64: str) -> str:
        """Decode base64 JPEG to PIL, then run OCR."""
        if not screen_b64 or not self._enabled:
            return ""
        try:
            img_bytes = base64.b64decode(screen_b64)
            pil_img = Image.open(io.BytesIO(img_bytes))
            return self.read_text(pil_img)
        except Exception as e:
            log.warning(f"OCR from base64 failed: {e}")
            return ""
