"""
Python ↔ JavaScript bridge for Live2D control.
Uses QWebChannel to send commands to the Live2D renderer running in QWebEngineView.
"""

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot


class Live2DBridge(QObject):
    """Bridge object exposed to JavaScript via QWebChannel.

    Python calls methods → signals emitted → JavaScript slots receive.
    JavaScript calls slots → Python receives.
    """

    # Signals: Python → JavaScript (commands to control the Live2D model)
    set_expression_signal = pyqtSignal(str)
    set_mouth_open_signal = pyqtSignal(float)
    start_speaking_signal = pyqtSignal()
    stop_speaking_signal = pyqtSignal()
    play_random_idle_signal = pyqtSignal()
    set_param_signal = pyqtSignal(str, float)
    set_idle_interval_signal = pyqtSignal(int)
    show_response_signal = pyqtSignal(str, str)  # (text, expression)
    show_user_message_signal = pyqtSignal(str)  # STT-transcribed user speech
    confirm_action_signal = pyqtSignal(str, str)  # (action_description, action_json) for user confirmation
    window_focus_changed_signal = pyqtSignal(bool)  # True=focused, False=unfocused

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frontend_ready = False
        self._on_ready_callbacks = []
        self._on_text_callback = None
        self._on_mode_callback = None
        self._on_move_window_callback = None
        self._on_resize_window_callback = None
        self._on_reset_window_callback = None
        self._on_action_confirm_callback = None

    # ---------- Slots: called by JavaScript ----------

    @pyqtSlot(bool)
    def on_frontend_ready(self, ready):
        self._frontend_ready = ready
        if ready:
            for cb in self._on_ready_callbacks:
                cb()
            self._on_ready_callbacks.clear()

    @pyqtSlot(str)
    def on_user_text(self, text: str):
        """Called from JS when user types a chat message."""
        if self._on_text_callback and text.strip():
            self._on_text_callback(text.strip())

    @pyqtSlot(str)
    def on_mode_toggle(self, mode: str):
        """Called from JS when user toggles voice/text mode."""
        if self._on_mode_callback:
            self._on_mode_callback(mode)

    @pyqtSlot(int, int)
    def move_window(self, dx: int, dy: int):
        """Called from JS when user drags window background."""
        if self._on_move_window_callback:
            self._on_move_window_callback(dx, dy)

    @pyqtSlot(int)
    def resize_window(self, delta: int):
        """Called from JS when user scrolls to resize window."""
        if self._on_resize_window_callback:
            self._on_resize_window_callback(delta)

    @pyqtSlot()
    def reset_window(self):
        """Called from JS when user presses 0 to reset window size."""
        if self._on_reset_window_callback:
            self._on_reset_window_callback()

    @pyqtSlot(str, bool)
    def on_action_confirmed(self, action_json: str, confirmed: bool):
        """Called from JS when user confirms/rejects an action."""
        if self._on_action_confirm_callback:
            self._on_action_confirm_callback(action_json, confirmed)

    # ---------- Methods: called by Python ----------

    def set_expression(self, name: str):
        """Set the model's facial expression."""
        self.set_expression_signal.emit(name)

    def set_mouth_open(self, value: float):
        """Set mouth openness for lip-sync (0.0 to 1.0)."""
        self.set_mouth_open_signal.emit(float(value))

    def start_speaking(self):
        """Start speaking animation."""
        self.start_speaking_signal.emit()

    def stop_speaking(self):
        """Stop speaking animation and close mouth."""
        self.stop_speaking_signal.emit()

    def play_random_idle(self):
        """Play a random idle motion."""
        self.play_random_idle_signal.emit()

    def set_param(self, param_id: str, value: float):
        """Set an arbitrary Live2D parameter."""
        self.set_param_signal.emit(param_id, float(value))

    def set_idle_interval(self, interval_ms: int):
        """Set the interval between random idle motions (milliseconds)."""
        self.set_idle_interval_signal.emit(interval_ms)

    def when_ready(self, callback):
        """Register a callback to be called when the frontend is ready."""
        if self._frontend_ready:
            callback()
        else:
            self._on_ready_callbacks.append(callback)

    def set_text_callback(self, callback):
        """Register callback for user-typed text: callback(text)."""
        self._on_text_callback = callback

    def set_mode_callback(self, callback):
        """Register callback for mode toggle: callback(mode)."""
        self._on_mode_callback = callback

    def set_move_window_callback(self, callback):
        """Register callback for window drag: callback(dx, dy)."""
        self._on_move_window_callback = callback

    def set_resize_window_callback(self, callback):
        """Register callback for window resize: callback(delta)."""
        self._on_resize_window_callback = callback

    def set_reset_window_callback(self, callback):
        """Register callback for window reset: callback()."""
        self._on_reset_window_callback = callback

    def set_action_confirm_callback(self, callback):
        """Register callback for action confirmation: callback(action_json, confirmed)."""
        self._on_action_confirm_callback = callback

    def request_action_confirm(self, action_description: str, action_json: str):
        """Send action confirmation request to the frontend."""
        self.confirm_action_signal.emit(action_description, action_json)

    def show_response(self, text: str, expression: str = "neutral"):
        """Show AI response text in the chat panel."""
        self.show_response_signal.emit(text, expression)

    def show_user_message(self, text: str):
        """Show user's STT-transcribed speech in the chat panel."""
        self.show_user_message_signal.emit(text)

    @property
    def frontend_ready(self):
        return self._frontend_ready
