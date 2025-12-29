from __future__ import annotations

import asyncio
import time
from typing import Tuple

from .base import SiteAdapter


class GeminiAdapter(SiteAdapter):
    site_id = "gemini"
    base_url = "https://gemini.google.com/"

    # TODO：用 playwright codegen 录制后替换为更准确的 selector
    TEXTBOX = [
        "div[contenteditable='true']",
        "textarea",
        "[role='textbox']",
    ]
    SEND_BTN = [
        "button:has-text('Send')",
        "button:has-text('发送')",
        "[aria-label*='Send']",
    ]
    NEW_CHAT = [
        "a:has-text('New chat')",
        "button:has-text('New chat')",
        "a:has-text('新对话')",
        "button:has-text('新对话')",
    ]
    ASSISTANT_MSG = [
        # TODO：替换为 gemini 输出区域定位
        "[data-response='true']",
        "div.markdown",
        "div:has(> p)",
    ]

    async def ensure_ready(self) -> None:
        try:
            await self.first_visible(self.TEXTBOX, timeout_ms=8000)
        except Exception:
            await self.manual_checkpoint("未检测到输入框，可能未登录/弹窗/风控。")

    async def new_chat(self) -> None:
        await self.try_click(self.NEW_CHAT, timeout_ms=1500)
        await asyncio.sleep(1.0)

    async def _last_text(self) -> str:
        for sel in self.ASSISTANT_MSG:
            try:
                loc = self.page.locator(sel)
                cnt = await loc.count()
                if cnt > 0:
                    return (await loc.nth(cnt - 1).inner_text()).strip()
            except Exception:
                continue
        return ""

    async def ask(self, prompt: str, timeout_s: int = 180) -> Tuple[str, str]:
        await self.ensure_ready()
        await self.new_chat()

        await self.send_with_fallback(self.TEXTBOX, self.SEND_BTN, prompt)

        t0 = time.time()
        last = ""
        last_change = time.time()
        stable_seconds = 2.0

        while time.time() - t0 < timeout_s:
            text = await self._last_text()
            if text and text != last:
                last = text
                last_change = time.time()
            if (time.time() - last_change) >= stable_seconds and len(last) > 0:
                return last, self.page.url
            await asyncio.sleep(0.7)

        await self.manual_checkpoint("Gemini 等待生成超时，请人工确认页面状态。")
        return await self._last_text(), self.page.url
