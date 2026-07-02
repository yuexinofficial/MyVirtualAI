"""
Action executor for computer control.
Parses [ACTION:type:args] tags from LLM responses and executes them.

Supported actions:
  open_app:name        → open an application
  search_web:query     → search in default browser (Bing)
  open_url:url         → open a URL
  type_text:text       → type text at cursor
  press_key:combo      → press keyboard combo
  click:x:y            → click at screen coordinates (or just "click" for current position)
  move:x:y             → move mouse to coordinates
  scroll:amount        → scroll up(positive)/down(negative)
"""

import asyncio
import logging
import os
import re
import subprocess
import urllib.parse
import webbrowser

log = logging.getLogger(__name__)

ACTION_PATTERN = re.compile(r"\[ACTION:(\w+):([^\]]+)\]", re.IGNORECASE)

ALLOWED_ACTIONS = {"open_app", "search_web", "open_url", "type_text", "press_key", "click", "move", "scroll"}

ALLOWED_KEYS = {
    "enter", "tab", "escape", "backspace", "delete", "space",
    "up", "down", "left", "right",
    "ctrl", "alt", "shift", "win", "cmd",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
    "home", "end", "pageup", "pagedown",
    "ctrl+c", "ctrl+v", "ctrl+x", "ctrl+z", "ctrl+a", "ctrl+s",
    "ctrl+shift+esc", "alt+tab", "alt+f4",
    "win+d", "win+e", "win+r", "win+l",
}


class ActionExecutor:
    """Executes computer control actions from LLM output tags."""

    def __init__(self, config: dict):
        self._enabled = config.get("enabled", False)
        self._confirm_before_execute = config.get("confirm_before_execute", True)
        self._on_confirm_callback = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def confirm_before_execute(self) -> bool:
        return self._confirm_before_execute

    def set_confirm_callback(self, callback):
        """Set async callback(action_json, confirmed) for confirmation."""
        self._on_confirm_callback = callback

    @staticmethod
    def parse_actions(text: str) -> tuple:
        """Extract action tags from response text.

        Returns: (clean_text, actions_list)
          clean_text: text with action tags removed
          actions_list: list of {"type": str, "args": list[str]}
        """
        actions = []
        for match in ACTION_PATTERN.finditer(text):
            action_type = match.group(1).lower()
            args_str = match.group(2)
            args = [a.strip() for a in args_str.split(":")]
            if action_type in ALLOWED_ACTIONS:
                actions.append({"type": action_type, "args": args})

        clean_text = ACTION_PATTERN.sub("", text).strip()
        clean_text = re.sub(r"  +", " ", clean_text)
        return clean_text, actions

    async def execute(self, action: dict) -> bool:
        """Execute a single action. Returns True on success."""
        if not self._enabled:
            log.warning(f"Action execution disabled, skipping: {action}")
            return False

        action_type = action["type"]
        args = action.get("args", [])

        if action_type not in ALLOWED_ACTIONS:
            log.warning(f"Unknown action type: {action_type}")
            return False

        try:
            method = getattr(self, f"_do_{action_type}")
            await method(args)
            log.info(f"Action executed: {action_type}({args})")
            return True
        except Exception as e:
            log.error(f"Action {action_type}({args}) failed: {e}")
            return False

    async def execute_all(self, actions: list[dict]) -> int:
        """Execute a list of actions sequentially. Returns count of successes."""
        success_count = 0
        for action in actions:
            if await self.execute(action):
                success_count += 1
            await asyncio.sleep(0.3)
        return success_count

    # ---- Individual action implementations ----

    async def _do_open_app(self, args: list[str]):
        """Open an application by name or path."""
        app_name = args[0] if args else ""
        if not app_name:
            return
        try:
            os.startfile(app_name)
        except FileNotFoundError:
            subprocess.Popen(
                f'start "" "{app_name}"',
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    async def _do_search_web(self, args: list[str]):
        """Open browser with Bing search."""
        query = " ".join(args)
        if not query:
            return
        encoded = urllib.parse.quote(query)
        webbrowser.open(f"https://cn.bing.com/search?q={encoded}")

    async def _do_open_url(self, args: list[str]):
        """Open a specific URL in the default browser."""
        # URL like https://xxx gets split by : — rejoin
        url = ":".join(args)
        if not url:
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        webbrowser.open(url)

    async def _do_type_text(self, args: list[str]):
        """Type text at the current cursor position."""
        import pyautogui
        text = ":".join(args)
        if not text:
            return
        if len(text) > 500:
            text = text[:500]
            log.warning("type_text truncated to 500 chars")
        pyautogui.typewrite(text, interval=0.02)

    async def _do_press_key(self, args: list[str]):
        """Press a keyboard key or combo."""
        import pyautogui
        combo = "+".join(args).lower()
        if not combo:
            return
        if combo not in ALLOWED_KEYS:
            log.warning(f"Key combo not in whitelist: {combo}")
            return
        keys = combo.split("+")
        pyautogui.hotkey(*keys)

    async def _do_click(self, args: list[str]):
        """Click at screen coordinates, or current position if no args."""
        import pyautogui
        if len(args) >= 2 and args[0] and args[1]:
            x = int(args[0])
            y = int(args[1])
            pyautogui.click(x, y)
        else:
            pyautogui.click()

    async def _do_move(self, args: list[str]):
        """Move mouse to screen coordinates."""
        import pyautogui
        if len(args) >= 2 and args[0] and args[1]:
            x = int(args[0])
            y = int(args[1])
            pyautogui.moveTo(x, y)

    async def _do_scroll(self, args: list[str]):
        """Scroll mouse wheel. Positive=up, negative=down."""
        import pyautogui
        amount = int(args[0]) if args and args[0] else 3
        pyautogui.scroll(amount)
