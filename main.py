"""
Virtual AI Companion — Entry Point

A desktop AI companion with Live2D avatar that can:
- See your screen (via screenshot → vision LLM)
- Hear your voice (mic + system audio → STT)
- Talk back to you (LLM → TTS with lip-sync)

Usage:
    python main.py [--config config.yaml]

Requirements:
    - Ollama installed with a vision model (e.g., llava:13b)
    - Live2D Cubism Core (loaded via CDN or local)
    - A Live2D model in the models/ directory
"""

import sys
import os
import asyncio
import logging
import argparse
import threading
from pathlib import Path

import yaml

# Add project to path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

# ── CRITICAL: Import STT before PyQt5 ──────────────────────────
# faster-whisper / CTranslate2 must initialize CUDA before Qt loads
# its OpenGL driver, otherwise both fight over the GPU and segfault.
from ai.stt import SpeechToText  # noqa: E402

# Now safe to import Qt
from PyQt5.QtCore import Qt, QTimer  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from live2d.window import Live2DWindow  # noqa: E402
from capture.screen import ScreenCapture  # noqa: E402
from capture.audio import AudioCapture  # noqa: E402
from ai.llm import LLMClient  # noqa: E402
from ai.tts import TextToSpeech  # noqa: E402
from capture.ocr import OCRReader  # noqa: E402
from core.actions import ActionExecutor  # noqa: E402
from core.controller import Controller  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("main")


def load_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    # Enable QWebEngine remote debugging on port 9222
    # Open http://localhost:9222 in Chrome to inspect JS console
    os.environ["QTWEBENGINE_REMOTE_DEBUGGING"] = "9222"
    # Allow file:// pages to access other file:// URLs (needed for PIXI to load model files)
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--allow-file-access-from-files"

    parser = argparse.ArgumentParser(description="Virtual AI Companion")
    parser.add_argument(
        "--config",
        default=os.path.join(PROJECT_ROOT, "config.yaml"),
        help="Path to config file",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    log.info(f"Config loaded from {args.config}")

    # ---- Modules (non-Qt, init before QApplication) ----
    # STT must load CUDA model BEFORE QApplication to avoid OpenGL/CUDA conflict
    stt = SpeechToText(config.get("stt", {}), PROJECT_ROOT)
    screen_cap = ScreenCapture(config.get("screen", {}))
    audio_cap = AudioCapture(config.get("audio", {}))
    llm = LLMClient(config.get("llm", {}))
    tts = TextToSpeech(config.get("tts", {}))
    ocr_reader = OCRReader(config.get("ocr", {}))
    action_executor = ActionExecutor(config.get("computer_control", {}))

    # Initialize OCR in main thread (PyTorch is NOT thread-safe)
    # Wrapped in try/except because PyTorch and CTranslate2 may conflict over CUDA
    try:
        ocr_reader.initialize()
    except Exception as e:
        log.warning(f"OCR init failed (screen reading disabled): {e}")

    # ---- PyQt Application ----
    # AA_ShareOpenGLContexts required for QtWebEngine compatibility
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # ---- Live2D Window ----
    window = Live2DWindow(config.get("live2d", {}), PROJECT_ROOT)
    window.show_window()

    # ---- Controller ----
    controller = Controller(
        config,
        window.bridge,
        screen_cap,
        audio_cap,
        stt,
        llm,
        tts,
        ocr_reader,
        action_executor,
    )

    # ---- Async Event Loop (runs in background thread) ----
    async_loop = asyncio.new_event_loop()
    controller_task = None

    def start_async_loop():
        nonlocal controller_task
        asyncio.set_event_loop(async_loop)
        controller_task = async_loop.create_task(controller.start())
        async_loop.run_forever()

    async_thread = threading.Thread(target=start_async_loop, daemon=True)
    async_thread.start()

    # Periodically pump the asyncio loop (Qt ↔ asyncio bridge)
    def pump_asyncio():
        pass  # asyncio runs in its own thread; signals handle cross-thread safety

    timer = QTimer()
    timer.timeout.connect(pump_asyncio)
    timer.start(100)

    # ---- Cleanup on exit ----
    def on_exit():
        log.info("Shutting down...")
        if controller_task:
            async_loop.call_soon_threadsafe(controller_task.cancel)
        async_loop.call_soon_threadsafe(async_loop.stop)
        audio_cap.stop()
        app.quit()

    app.aboutToQuit.connect(on_exit)

    log.info("AI Companion started. Speak to interact!")
    log.info("Press Ctrl+C in terminal to exit.")

    try:
        sys.exit(app.exec_())
    except KeyboardInterrupt:
        on_exit()


if __name__ == "__main__":
    main()
