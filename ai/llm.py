"""
LLM client — supports two providers:
  - "ollama" : Local Ollama (free, offline)
  - "openai" : OpenAI-compatible API (DeepSeek, OpenAI, etc.)

DeepSeek API: https://platform.deepseek.com → get API key → use provider "openai"
"""

import asyncio
import re
import ollama
from openai import AsyncOpenAI


class LLMClient:
    """Multi-provider LLM client (Ollama or OpenAI-compatible API)."""

    def __init__(self, config: dict):
        self._provider = config.get("provider", "ollama")

        if self._provider == "openai":
            self._api_key = config.get("api_key", "")
            self._base_url = config.get("base_url", "https://api.deepseek.com")
            self._model = config.get("model", "deepseek-chat")
            self._client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)
        else:
            self._host = config.get("host", "http://localhost:11434")
            self._model = config.get("model", "llava:13b")
            self._client = ollama.AsyncClient(host=self._host)

        self._system_prompt = config.get("system_prompt", "You are a friendly AI companion.")
        self._max_history = config.get("max_history", 10)
        self._temperature = config.get("temperature", 0.8)
        self._vision_enabled = config.get("vision_enabled", False)
        self._max_tokens = config.get("max_tokens", 512)
        self._thinking_enabled = config.get("thinking_enabled", False)
        self._screen_context_enabled = config.get("screen_context_enabled", False)
        self._computer_control_enabled = config.get("computer_control_enabled", False)

        self._history = []

    @property
    def vision_enabled(self) -> bool:
        return self._vision_enabled

    # ---------- Public API ----------

    async def chat(self, user_text: str, screen_b64: str | None = None, memory_context: str = "", screen_text: str = "") -> dict:
        """Send a message and get response. Returns {"text": str, "expression": str}."""
        messages = self._build_messages(user_text, screen_b64, memory_context, screen_text)

        try:
            if self._provider == "openai":
                reply_text = await self._chat_openai(messages)
            else:
                reply_text = await self._chat_ollama(messages)
        except Exception as e:
            reply_text = f"（后台出了点问题...稍等哦）\nError: {e}"

        expression = self._parse_expression(reply_text)
        reply_clean = self._strip_expression(reply_text)

        self._history.append({"role": "user", "content": user_text})
        self._history.append({"role": "assistant", "content": reply_clean})

        return {"text": reply_clean, "expression": expression}

    async def chat_text_only(self, text: str) -> dict:
        """Text-only chat (no screen context)."""
        return await self.chat(text, screen_b64=None)

    # ---------- Provider-specific ----------

    async def _chat_ollama(self, messages: list) -> str:
        response = await self._client.chat(
            model=self._model,
            messages=messages,
            options={"temperature": self._temperature},
            stream=False,
        )
        return response["message"]["content"]

    async def _chat_openai(self, messages: list) -> str:
        kwargs = dict(
            model=self._model,
            messages=messages,
            max_tokens=self._max_tokens,
        )
        # DeepSeek thinking mode: explicitly disable to skip chain-of-thought → faster response
        if self._thinking_enabled:
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
            kwargs["temperature"] = self._temperature  # only works when thinking is off

        response = await self._client.chat.completions.create(**kwargs)
        return response.choices[0].message.content

    # ---------- Internal ----------

    def _build_messages(self, user_text: str, screen_b64: str | None, memory_context: str = "", screen_text: str = "") -> list:
        system_content = self._system_prompt
        if memory_context:
            system_content += "\n\n" + memory_context

        # Inject screen context instructions
        if screen_text and self._screen_context_enabled:
            system_content += (
                "\n\n【屏幕感知说明】"
                "\n用户消息中包含了 [屏幕内容] 区块，这是从用户当前屏幕截图中通过OCR提取的文字。"
                "\n你可以根据屏幕内容理解用户当前正在看什么，并据此作出更贴切的回复。"
                "\n如果用户提到「屏幕」「桌面」「这个」「看到」等内容，请结合屏幕内容回答。"
            )

        # Inject computer control instructions
        if self._computer_control_enabled:
            system_content += (
                "\n\n【电脑操控说明】"
                "\n你可以通过输出特殊标签来控制用户的电脑。支持的标签格式："
                "\n- [ACTION:open_app:应用名] — 打开应用程序"
                "\n- [ACTION:search_web:搜索关键词] — 在必应搜索"
                "\n- [ACTION:open_url:网址] — 打开特定网址"
                "\n- [ACTION:type_text:要输入的文字] — 在当前光标处输入文字"
                "\n- [ACTION:press_key:组合键] — 按下键盘组合键（如 ctrl+c）"
                "\n- [ACTION:click:X坐标:Y坐标] — 点击屏幕指定位置"
                "\n- [ACTION:move:X坐标:Y坐标] — 移动鼠标到指定位置"
                "\n- [ACTION:scroll:数值] — 滚动鼠标滚轮（正数向上，负数向下）"
                "\n可以把多个动作标签串联使用，放在回复末尾。"
                "\n只有当用户明确要求执行桌面操作时才使用这些标签。"
            )

        messages = [{"role": "system", "content": system_content}]
        messages += self._history[-self._max_history * 2:]

        # Build effective user text: inject screen OCR text before user message
        if screen_text:
            effective_text = f"[屏幕内容]\n{screen_text}\n\n[用户消息]\n{user_text}"
        else:
            effective_text = user_text

        # Vision content only for models that support it
        if self._vision_enabled and screen_b64:
            user_content = [
                {"type": "text", "text": effective_text or "你看到了什么？"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screen_b64}"}},
            ]
        else:
            user_content = effective_text

        messages.append({"role": "user", "content": user_content})
        return messages

    @staticmethod
    def _parse_expression(text: str) -> str:
        match = re.search(r'\[(happy|sad|angry|surprised|neutral)\]', text, re.IGNORECASE)
        return match.group(1).lower() if match else "neutral"

    @staticmethod
    def _strip_expression(text: str) -> str:
        return re.sub(
            r'\s*\[(happy|sad|angry|surprised|neutral)\]\s*', '',
            text, flags=re.IGNORECASE
        ).strip()

    def clear_history(self):
        """Reset conversation history."""
        self._history.clear()
