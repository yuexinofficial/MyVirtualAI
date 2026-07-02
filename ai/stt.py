"""
Speech-to-Text using faster-whisper (local, fast).

Supports two modes:
  - model_path: load from a local directory (e.g., models/whisper-medium/)
  - model_size: download from HuggingFace (e.g., "medium", "small", "large-v3")
"""

import os
import threading
import numpy as np
from faster_whisper import WhisperModel


class SpeechToText:
    """Local speech recognition using faster-whisper."""

    def __init__(self, config: dict, project_root: str = ""):
        model_path = config.get("model_path")
        if model_path and not os.path.isabs(model_path):
            model_path = os.path.join(project_root, model_path)

        device = config.get("device", "cuda")
        compute_type = config.get("compute_type", "float16")
        self._language = config.get("language")

        # model_path takes priority over model_size
        model_source = model_path if (model_path and os.path.isdir(model_path)) else config.get("model_size", "medium")
        print(f"[STT] Loading model: {model_source}")
        self._model = WhisperModel(model_source, device=device, compute_type=compute_type)
        self._lock = threading.Lock()

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        """Transcribe audio to text. Returns empty string if no speech detected."""
        with self._lock:
            segments, info = self._model.transcribe(
                audio,
                language=self._language,
                beam_size=5,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500),
            )
            text = " ".join(seg.text.strip() for seg in segments)
            return text
