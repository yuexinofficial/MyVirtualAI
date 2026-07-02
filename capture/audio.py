"""
Audio capture with built-in energy-based Voice Activity Detection (VAD).
Captures from microphone and optionally system audio loopback (WASAPI on Windows).

No C extensions required — uses pure-Python RMS energy detection.
"""

import collections
import threading
import time
import pyaudio
import numpy as np


class AudioCapture:
    """Captures audio from microphone + optional system loopback, with VAD."""

    def __init__(self, config: dict):
        self._sample_rate = config.get("sample_rate", 16000)
        self._vad_sensitivity = config.get("vad_sensitivity", 1.5)  # energy multiplier
        self._silence_timeout = config.get("silence_timeout", 1.0)
        self._max_duration = config.get("max_record_duration", 15.0)
        self._capture_system = config.get("capture_system_audio", True)
        self._mic_index = config.get("mic_device_index")
        self._loopback_index = config.get("loopback_device_index")

        self._pa = None
        self._mic_stream = None
        self._loopback_stream = None

        # Frame sizing
        self._frame_duration_ms = 30
        self._frame_size = int(self._sample_rate * self._frame_duration_ms / 1000)
        self._silence_threshold = int(self._silence_timeout * 1000 / self._frame_duration_ms)

        # Energy-based VAD state
        self._noise_floor = 0.0   # Running estimate of background noise level
        self._noise_smooth = 0.95  # Smoothing factor for noise floor update
        self._vad_threshold_mult = self._vad_sensitivity

        # Shared recording state (guarded by _lock)
        self._lock = threading.Lock()
        self._is_recording = False
        self._frames = collections.deque()
        self._silence_frames = 0

        # Lifecycle
        self._thread = None
        self._running = False
        self._on_speech_callback = None

    # ---------- Device discovery ----------

    @staticmethod
    def list_devices():
        """List all available audio devices."""
        pa = pyaudio.PyAudio()
        results = []
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            results.append({
                "index": i,
                "name": info["name"],
                "max_input_channels": info["maxInputChannels"],
                "max_output_channels": info["maxOutputChannels"],
                "host_api": info["hostApi"],
            })
        pa.terminate()
        return results

    @staticmethod
    def find_loopback_device():
        """Find a WASAPI loopback device on Windows."""
        pa = pyaudio.PyAudio()
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            name = info["name"].lower()
            if "loopback" in name and info["maxInputChannels"] > 0:
                pa.terminate()
                return i
        pa.terminate()
        return None

    # ---------- Lifecycle ----------

    def start(self, on_speech_callback=None):
        """Start audio capture in a background thread."""
        if self._running:
            return
        self._on_speech_callback = on_speech_callback
        self._running = True
        self._pa = pyaudio.PyAudio()
        self._init_streams()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop audio capture."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._close_streams()
        if self._pa:
            self._pa.terminate()
            self._pa = None

    def _init_streams(self):
        """Initialize audio input streams."""
        try:
            mic_idx = self._mic_index
            if mic_idx is None:
                mic_idx = self._find_default_input()
            if mic_idx is not None:
                self._mic_stream = self._pa.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=self._sample_rate,
                    input=True,
                    input_device_index=mic_idx,
                    frames_per_buffer=self._frame_size,
                    stream_callback=self._mic_callback,
                )
                print(f"[Audio] Mic device: {mic_idx}")
        except OSError as e:
            print(f"[Audio] Microphone init failed: {e}")
            self._mic_stream = None

        if self._capture_system:
            try:
                loopback_idx = self._loopback_index
                if loopback_idx is None:
                    loopback_idx = AudioCapture.find_loopback_device()
                if loopback_idx is not None:
                    self._loopback_stream = self._pa.open(
                        format=pyaudio.paInt16,
                        channels=2,
                        rate=self._sample_rate,
                        input=True,
                        input_device_index=loopback_idx,
                        frames_per_buffer=self._frame_size,
                        stream_callback=self._loopback_callback,
                    )
                    print(f"[Audio] Loopback device: {loopback_idx}")
                else:
                    print("[Audio] No loopback device found (system audio capture disabled)")
            except OSError as e:
                print(f"[Audio] Loopback init failed: {e}")
                self._loopback_stream = None

    def _close_streams(self):
        for stream in (self._mic_stream, self._loopback_stream):
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
        self._mic_stream = None
        self._loopback_stream = None

    def _find_default_input(self) -> int | None:
        if not self._pa:
            return None
        try:
            default = self._pa.get_default_input_device_info()
            return default["index"]
        except OSError:
            return None

    # ---------- Per-stream callbacks ----------

    def _mic_callback(self, in_data, frame_count, time_info, status):
        """Callback for microphone (mono)."""
        if self._running and in_data:
            self._process_frame(in_data)
        return (None, pyaudio.paContinue)

    def _loopback_callback(self, in_data, frame_count, time_info, status):
        """Callback for system loopback (stereo → mono)."""
        if self._running and in_data:
            stereo = np.frombuffer(in_data, dtype=np.int16)
            mono = (stereo[::2].astype(np.float32) + stereo[1::2].astype(np.float32)) / 2
            self._process_frame(mono.astype(np.int16).tobytes())
        return (None, pyaudio.paContinue)

    # ---------- Energy-based VAD ----------

    def _process_frame(self, mono_data: bytes):
        """Process one mono frame: compute energy, detect speech."""
        if len(mono_data) != self._frame_size * 2:
            return

        # Compute RMS energy of this frame
        samples = np.frombuffer(mono_data, dtype=np.int16).astype(np.float32)
        rms = np.sqrt(np.mean(samples ** 2) + 1e-10)

        # Update noise floor adaptively
        if self._noise_floor < 0.001:
            self._noise_floor = rms
        else:
            self._noise_floor = self._noise_smooth * self._noise_floor + (1 - self._noise_smooth) * rms

        # Speech if energy exceeds noise floor × threshold
        is_speech = rms > max(self._noise_floor * self._vad_threshold_mult, 300.0)  # reject low-level noise

        with self._lock:
            if is_speech:
                self._frames.append(mono_data)
                if not self._is_recording:
                    self._is_recording = True
                    self._frames.clear()
                    self._frames.append(mono_data)
                self._silence_frames = 0
            elif self._is_recording:
                self._frames.append(mono_data)
                self._silence_frames += 1
                if self._silence_frames >= self._silence_threshold:
                    self._finalize_utterance_locked()
                    return

            dur = len(self._frames) * self._frame_duration_ms / 1000
            if self._is_recording and dur >= self._max_duration:
                self._finalize_utterance_locked()

    def _finalize_utterance_locked(self):
        """Call while holding self._lock. Releases lock to invoke callback."""
        if not self._is_recording:
            return
        self._is_recording = False

        audio_bytes = b"".join(self._frames)
        self._frames.clear()
        self._silence_frames = 0

        self._lock.release()
        try:
            audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            if self._on_speech_callback and len(audio_np) > self._sample_rate * 1.0:
                self._on_speech_callback(audio_np, self._sample_rate)
        finally:
            self._lock.acquire()

    def _capture_loop(self):
        """Keep background thread alive while PyAudio callbacks run."""
        while self._running:
            time.sleep(0.1)
