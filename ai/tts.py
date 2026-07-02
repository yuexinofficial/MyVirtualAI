"""
Text-to-Speech using edge-tts with lip-sync data extraction.
Uses bundled ffmpeg from imageio-ffmpeg for MP3 decoding.
"""

import asyncio
import io
import os
import subprocess
import threading
import time
import numpy as np
import edge_tts
import sounddevice as sd
import imageio_ffmpeg


class TextToSpeech:
    """Edge-TTS based speech synthesis with lip-sync support."""

    def __init__(self, config: dict):
        self._voice = config.get("voice", "zh-CN-XiaoxiaoNeural")
        self._rate = config.get("rate", "+5%")
        self._pitch = config.get("pitch", "+0Hz")
        self._ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        if not os.path.isfile(self._ffmpeg):
            raise RuntimeError(
                f"ffmpeg not found at: {self._ffmpeg}\n"
                "imageio-ffmpeg may need to download the binary on first run."
            )

    async def speak(self, text: str) -> tuple[np.ndarray, list]:
        """Generate speech audio and lip-sync envelope.

        Returns:
            (audio_array, lip_sync_data)
            audio_array: float32 numpy array at 22050 Hz
            lip_sync_data: list of (time_seconds, amplitude_0_to_1) tuples
        """
        if not text.strip():
            return np.array([], dtype=np.float32), []

        # Stream MP3 audio from edge-tts into in-memory buffer
        communicate = edge_tts.Communicate(text, self._voice, rate=self._rate, pitch=self._pitch)

        audio_buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_buf.write(chunk["data"])

        # Decode MP3 → raw PCM via ffmpeg subprocess (bypasses pydub)
        try:
            proc = subprocess.run(
                [self._ffmpeg, '-i', 'pipe:0', '-f', 's16le',
                 '-acodec', 'pcm_s16le', '-ar', '22050', '-ac', '1', 'pipe:1'],
                input=audio_buf.getvalue(),
                capture_output=True,
                timeout=30,
            )
            if proc.returncode != 0:
                stderr = proc.stderr.decode('utf-8', errors='replace')
                raise RuntimeError(f"ffmpeg decode failed:\n{stderr}")
        except FileNotFoundError:
            raise RuntimeError(
                f"ffmpeg binary not found at: {self._ffmpeg}\n"
                "Try: pip uninstall imageio-ffmpeg && pip install imageio-ffmpeg"
            )
        except Exception as e:
            raise RuntimeError(f"TTS audio decoding failed: {e}") from e

        sr = 22050
        samples = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0

        lip_sync = self._compute_lip_sync(samples, sr)
        return samples, lip_sync

    def _compute_lip_sync(self, audio: np.ndarray, sr: int, window_ms: int = 50) -> list:
        """Compute mouth amplitude envelope from audio.

        Returns list of (time_seconds, amplitude_0_to_1).
        """
        if len(audio) == 0:
            return []

        window = int(sr * window_ms / 1000)
        hop = window // 2

        if len(audio) < window:
            return [(0.0, 0.0)]

        result = []
        for start in range(0, len(audio) - window, hop):
            chunk = audio[start:start + window]
            rms = np.sqrt(np.mean(chunk ** 2))
            # Scale and apply non-linear curve (more responsive at low volumes)
            amp = min(1.0, rms * 5.0) ** 0.7
            result.append((start / sr, float(amp)))

        return result

    def play(self, audio: np.ndarray, sr: int = 22050):
        """Play audio through default output device (blocking)."""
        if len(audio) == 0:
            return
        sd.play(audio, samplerate=sr)
        sd.wait()

    async def speak_and_play(self, text: str, on_lip_sync=None) -> None:
        """Generate TTS and play it back, driving lip-sync in real-time.

        on_lip_sync is called with float amplitudes (0.0–1.0) during playback.
        """
        audio, lip_sync = await self.speak(text)
        if len(audio) == 0:
            return

        done = threading.Event()
        start_ts = None

        def _play():
            nonlocal start_ts
            start_ts = time.perf_counter()
            sd.play(audio, samplerate=22050)
            sd.wait()
            done.set()

        thread = threading.Thread(target=_play, daemon=True)
        thread.start()

        # Wait for playback to actually start
        while start_ts is None and not done.is_set():
            await asyncio.sleep(0.01)

        # Drive lip-sync in sync with playback
        lip_idx = 0
        while not done.is_set():
            elapsed = time.perf_counter() - (start_ts or time.perf_counter())

            while lip_idx < len(lip_sync) and lip_sync[lip_idx][0] < elapsed:
                lip_idx += 1

            if lip_idx < len(lip_sync):
                if on_lip_sync:
                    on_lip_sync(lip_sync[lip_idx][1])
            else:
                if on_lip_sync:
                    on_lip_sync(0.0)
                break

            await asyncio.sleep(0.03)

        thread.join()
        if on_lip_sync:
            on_lip_sync(0.0)
