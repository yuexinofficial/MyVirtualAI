"""
Main controller — async state machine orchestrating all modules.
States: IDLE → LISTENING → THINKING → SPEAKING → IDLE
"""

import asyncio
import logging

from live2d.bridge import Live2DBridge
from capture.screen import ScreenCapture
from capture.audio import AudioCapture
from ai.stt import SpeechToText
from ai.llm import LLMClient
from ai.tts import TextToSpeech
from ai.memory import MemoryStore
from core.actions import ActionExecutor

log = logging.getLogger(__name__)


class Controller:
    """Orchestrates the AI companion interaction loop."""

    STATES = ("IDLE", "LISTENING", "THINKING", "EXECUTING", "SPEAKING")

    def __init__(
        self,
        config: dict,
        bridge: Live2DBridge,
        screen_capture: ScreenCapture,
        audio_capture: AudioCapture,
        stt: SpeechToText,
        llm: LLMClient,
        tts: TextToSpeech,
        ocr_reader=None,
        action_executor=None,
    ):
        self._cfg = config
        self._bridge = bridge
        self._screen = screen_capture
        self._audio = audio_capture
        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._ocr = ocr_reader
        self._actions = action_executor

        # Wire up action confirmation callback
        if self._actions and self._actions.enabled:
            self._bridge.set_action_confirm_callback(self._on_action_confirmed)

        mem_cfg = config.get("memory", {})
        self._memory = MemoryStore(
            db_path=mem_cfg.get("db_path", "data/memory.db"),
            use_chromadb=mem_cfg.get("use_chromadb", True),
        )
        self._memory_max_results = mem_cfg.get("max_search_results", 3)
        self._memory_auto_facts = mem_cfg.get("auto_extract_facts", True)
        self._session_id = None

        self._state = "IDLE"
        self._running = False
        self._loop = None
        self._speech_queue = asyncio.Queue()
        self._pending_wake = asyncio.Event()
        self._response_mode = self._cfg.get("behavior", {}).get("response_mode", "voice")

    # ---------- Public API ----------

    async def start(self):
        """Start the interaction loop."""
        self._running = True
        self._loop = asyncio.get_running_loop()
        self._session_id = self._memory.new_session_id()
        log.info(f"Session: {self._session_id}")

        # Wire up audio → speech handling
        self._audio.start(on_speech_callback=self._on_speech_detected)

        # Wire up text chat → direct LLM (typed messages skip STT)
        self._bridge.set_text_callback(self._on_text_typed)

        # Wire up mode toggle
        self._bridge.set_mode_callback(self._on_mode_toggle)

        # Enable idle motions
        if self._cfg.get("behavior", {}).get("idle_animations", True):
            interval = self._cfg.get("behavior", {}).get("idle_motion_interval", 15000)
            self._bridge.set_idle_interval(interval)

        # Auto-greet
        if self._cfg.get("behavior", {}).get("greet_on_start", True):
            self._bridge.when_ready(lambda: asyncio.ensure_future(self._auto_greet()))

        log.info("Controller started. State: IDLE")
        await self._main_loop()

    async def stop(self):
        """Stop the interaction loop."""
        self._running = False
        self._audio.stop()
        self._memory.close()

    # ---------- Audio callback (called from PyAudio thread) ----------

    def _on_speech_detected(self, audio, sample_rate: int):
        """Called from audio thread when speech is detected."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._handle_speech(audio, sample_rate),
                self._loop,
            )

    # ---------- Main Loop ----------

    async def _main_loop(self):
        """Main state machine loop."""
        while self._running:
            if self._state == "IDLE":
                # Wait for speech or keep idle
                try:
                    await asyncio.wait_for(self._pending_wake.wait(), timeout=1.0)
                    self._pending_wake.clear()
                except asyncio.TimeoutError:
                    pass

            await asyncio.sleep(0.05)

    # ---------- Text Chat (typed, no STT needed) ----------

    def _on_text_typed(self, text: str):
        """Called when user types a message in the chat panel."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._handle_text(text),
                self._loop,
            )

    def _on_mode_toggle(self, mode: str):
        """Called when user toggles voice/text mode from the UI."""
        self._response_mode = mode
        log.info(f"Response mode: {mode}")

    async def _handle_text(self, text: str):
        """Process typed text: recall memory → think → speak (skip STT)."""
        if self._state != "IDLE":
            return

        log.info(f"Typed: {text}")
        self._set_state("THINKING")
        self._bridge.set_expression("neutral")

        # Recall relevant memories
        memory_context = self._memory.recall(text, self._memory_max_results)

        screen_b64 = None
        screen_text = ""
        if self._llm.vision_enabled:
            screen_b64 = self._screen.capture()
        else:
            screen_text = await self._get_screen_context()

        try:
            response = await self._llm.chat(text, screen_b64, memory_context, screen_text)
        except Exception as e:
            log.error(f"LLM failed: {e}")
            response = {"text": "诶？我脑子有点转不过来了...", "expression": "sad"}

        # Parse and execute any action tags
        clean_text, actions_executed = await self._process_actions(response["text"])
        if actions_executed:
            response["text"] = clean_text
            response["expression"] = LLMClient._parse_expression(clean_text)

        log.info(f"Response: {response['text']}")

        # --- Save to long-term memory ---
        self._memory.save_turn(self._session_id, "user", text)
        self._memory.save_turn(self._session_id, "assistant", response["text"], response["expression"])
        self._memory.embed_content(f"用户: {text}", "user_message", self._session_id)
        self._memory.embed_content(f"芙宁娜: {response['text']}", "assistant_message", self._session_id)

        # Auto-extract facts from user input
        if self._memory_auto_facts:
            facts = self._memory.extract_facts(text, self._session_id)
            for fact, category in facts:
                self._memory.save_fact(fact, category, self._session_id)

        # Show response text in chat panel (always)
        self._bridge.show_response(response["text"], response["expression"])

        if self._response_mode == "text":
            # Text-only mode: set expression briefly, no TTS
            self._bridge.set_expression(response["expression"])
            self._set_state("IDLE")
            return

        # Voice mode: TTS + lip-sync
        self._set_state("SPEAKING")
        self._bridge.set_expression(response["expression"])
        self._bridge.start_speaking()

        try:
            await self._tts.speak_and_play(
                response["text"],
                on_lip_sync=lambda v: self._bridge.set_mouth_open(v),
            )
        except Exception as e:
            log.error(f"TTS failed: {e}")

        self._bridge.stop_speaking()
        self._bridge.set_expression("neutral")
        self._set_state("IDLE")

    # ---------- Speech Handling ----------

    async def _handle_speech(self, audio, sample_rate: int):
        """Process detected speech: transcribe → think → speak."""
        if self._state != "IDLE":
            return  # Already processing

        self._set_state("LISTENING")

        # Transcribe
        try:
            text = await self._loop.run_in_executor(
                None, self._stt.transcribe, audio, sample_rate
            )
        except Exception as e:
            log.error(f"STT failed: {e}")
            self._set_state("IDLE")
            return

        if not text or not text.strip():
            log.info("No speech recognized, returning to IDLE")
            self._set_state("IDLE")
            return

        log.info(f"Recognized: {text}")

        # Show user's speech in chat panel
        self._bridge.show_user_message(text)

        # Thinking
        self._set_state("THINKING")
        self._bridge.set_expression("neutral")

        # Recall relevant memories
        memory_context = self._memory.recall(text, self._memory_max_results)

        screen_b64 = None
        screen_text = ""
        if self._llm.vision_enabled:
            screen_b64 = self._screen.capture()
        else:
            screen_text = await self._get_screen_context()

        try:
            response = await self._llm.chat(text, screen_b64, memory_context, screen_text)
        except Exception as e:
            log.error(f"LLM failed: {e}")
            response = {
                "text": "诶？我脑子有点转不过来了...",
                "expression": "sad",
            }

        # Parse and execute any action tags
        clean_text, actions_executed = await self._process_actions(response["text"])
        if actions_executed:
            response["text"] = clean_text
            response["expression"] = LLMClient._parse_expression(clean_text)

        log.info(f"Response: {response['text']}")

        # --- Save to long-term memory ---
        self._memory.save_turn(self._session_id, "user", text)
        self._memory.save_turn(self._session_id, "assistant", response["text"], response["expression"])
        self._memory.embed_content(f"用户: {text}", "user_message", self._session_id)
        self._memory.embed_content(f"芙宁娜: {response['text']}", "assistant_message", self._session_id)

        # Auto-extract facts from user input
        if self._memory_auto_facts:
            facts = self._memory.extract_facts(text, self._session_id)
            for fact, category in facts:
                self._memory.save_fact(fact, category, self._session_id)

        # Show response text in chat panel (always)
        self._bridge.show_response(response["text"], response["expression"])

        if self._response_mode == "text":
            # Text-only mode: set expression briefly, no TTS
            self._bridge.set_expression(response["expression"])
            self._set_state("IDLE")
            return

        # Voice mode: TTS + lip-sync
        self._set_state("SPEAKING")
        self._bridge.set_expression(response["expression"])
        self._bridge.start_speaking()

        try:
            await self._tts.speak_and_play(
                response["text"],
                on_lip_sync=lambda v: self._bridge.set_mouth_open(v),
            )
        except Exception as e:
            log.error(f"TTS failed: {e}")

        self._bridge.stop_speaking()
        self._bridge.set_expression("neutral")
        self._set_state("IDLE")

    async def _auto_greet(self):
        """Generate and speak a greeting on startup."""
        greeting = self._cfg.get("behavior", {}).get("greeting_message")
        if not greeting:
            try:
                resp = await self._llm.chat_text_only("（你刚刚被启动了，请主动打个招呼）")
                greeting = resp["text"]
            except Exception:
                greeting = "你好呀！我来啦~"

        self._set_state("SPEAKING")
        self._bridge.set_expression("happy")
        self._bridge.start_speaking()

        try:
            await self._tts.speak_and_play(
                greeting,
                on_lip_sync=lambda v: self._bridge.set_mouth_open(v),
            )
        except Exception as e:
            log.error(f"Greeting TTS failed: {e}")

        self._bridge.stop_speaking()
        self._bridge.set_expression("neutral")
        self._set_state("IDLE")

    async def _get_screen_context(self) -> str:
        """Capture screen and run OCR if enabled. Returns extracted text or ""."""
        ocr_enabled = self._cfg.get("screen", {}).get("ocr_enabled", False)
        if not ocr_enabled or self._ocr is None or not self._ocr.enabled:
            return ""

        screen_b64 = await self._loop.run_in_executor(None, self._screen.capture)
        if not screen_b64:
            return ""

        try:
            text = await self._loop.run_in_executor(
                None, self._ocr.read_from_base64, screen_b64
            )
            if text:
                log.info(f"OCR extracted {len(text)} chars from screen")
            return text or ""
        except Exception as e:
            log.warning(f"OCR failed: {e}")
            return ""

    async def _process_actions(self, response_text: str) -> tuple:
        """Parse and optionally execute action tags from LLM response.

        Returns: (clean_text, actions_executed)
        """
        if self._actions is None or not self._actions.enabled:
            return response_text, False

        clean_text, actions = ActionExecutor.parse_actions(response_text)

        if not actions:
            return clean_text, False

        log.info(f"Found {len(actions)} action(s) in response: {actions}")

        if self._actions.confirm_before_execute:
            desc_parts = []
            for a in actions:
                desc_parts.append(f"[{a['type']}] {' '.join(a['args'])}")
            desc = "; ".join(desc_parts)
            log.info(f"Action confirmation required (skipping): {desc}")
            self._bridge.show_response(
                f"我想执行这些操作，可以吗？\n{desc}",
                "neutral"
            )
            return clean_text, False

        self._set_state("EXECUTING")
        success_count = await self._actions.execute_all(actions)
        log.info(f"Executed {success_count}/{len(actions)} actions")
        return clean_text, True

    def _on_action_confirmed(self, action_json: str, confirmed: bool):
        """Called from bridge when user confirms/rejects an action."""
        log.info(f"Action confirmation: {action_json} -> {confirmed}")

    def _set_state(self, state: str):
        """Update state and log transitions."""
        if state != self._state:
            log.info(f"State: {self._state} → {state}")
            self._state = state
        if state == "IDLE":
            self._pending_wake.clear()
