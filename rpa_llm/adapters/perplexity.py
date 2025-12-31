# rpa_llm/adapters/perplexity.py
from __future__ import annotations

import asyncio
import re
import time
from typing import Tuple

from playwright.async_api import Frame, Locator

from ..utils import beijing_now_iso
from .base import SiteAdapter


class PerplexityAdapter(SiteAdapter):
    site_id = "perplexity"
    base_url = "https://www.perplexity.ai/"

    TEXTBOX_SELECTORS = [
        'textarea[placeholder*="Ask"]',
        'textarea[placeholder*="Search"]',
        'textarea[placeholder*="问"]',
        'textarea[placeholder*="搜索"]',
        "textarea",
        'div[role="textbox"][contenteditable="true"]',
        '[role="textbox"]',
    ]

    # 按钮在不同版本里差异大：这里只作为“备选”
    SEND_BTN = [
        'button[type="submit"]',
        'form button[type="submit"]',
        'button[aria-label*="Submit"]',
        'button[aria-label*="Send"]',
        'button:has-text("Ask")',
        'button:has-text("Search")',
        'button:has-text("提问")',
        'button:has-text("搜索")',
    ]

    NEW_THREAD = [
        'a:has-text("New")',
        'button:has-text("New")',
        'a:has-text("新")',
        'button:has-text("新")',
        'button[aria-label*="New"]',
    ]

    ANSWER_BLOCK = [
        "main .prose",
        "main div.prose",
        "main article",
        "main [data-testid*='answer']",
        "main div:has(p)",
    ]

    def _log(self, msg: str) -> None:
        print(f"[{beijing_now_iso()}] [{self.site_id}] {msg}", flush=True)

    def _frames_in_priority(self) -> list[Frame]:
        mf = self.page.main_frame
        return [mf] + [f for f in self.page.frames if f != mf]

    async def _dismiss_overlays(self) -> None:
        try:
            await self.page.keyboard.press("Escape")
            await asyncio.sleep(0.15)
            await self.page.keyboard.press("Escape")
        except Exception:
            pass

    async def _try_visible(self, loc: Locator) -> bool:
        try:
            return await loc.is_visible()
        except Exception:
            return False

    async def _find_textbox_any_frame(self) -> Tuple[Locator, Frame, str]:
        ph = re.compile(r"(Ask|Search|问|搜索)", re.I)

        for frame in self._frames_in_priority():
            # placeholder 优先
            try:
                loc = frame.get_by_placeholder(ph).first
                if await loc.count() > 0:
                    try:
                        await loc.scroll_into_view_if_needed(timeout=1000)
                    except Exception:
                        pass
                    if await self._try_visible(loc):
                        return loc, frame, "get_by_placeholder"
            except Exception:
                pass

            # role=textbox
            try:
                loc = frame.get_by_role("textbox").first
                if await loc.count() > 0:
                    try:
                        await loc.scroll_into_view_if_needed(timeout=1000)
                    except Exception:
                        pass
                    if await self._try_visible(loc):
                        return loc, frame, "get_by_role(textbox)"
            except Exception:
                pass

            # css selectors
            for sel in self.TEXTBOX_SELECTORS:
                try:
                    loc = frame.locator(sel).first
                    if await loc.count() > 0:
                        try:
                            await loc.scroll_into_view_if_needed(timeout=1000)
                        except Exception:
                            pass
                        if await self._try_visible(loc):
                            return loc, frame, f"css:{sel}"
                except Exception:
                    continue

        raise RuntimeError("Perplexity: cannot locate textbox in any frame.")

    async def _read_last_answer_text(self) -> str:
        for sel in self.ANSWER_BLOCK:
            try:
                loc = self.page.locator(sel)
                cnt = await loc.count()
                if cnt > 0:
                    return (await loc.nth(cnt - 1).inner_text()).strip()
            except Exception:
                continue
        return ""

    async def _arm_input_events(self, tb: Locator) -> None:
        """
        关键：触发前端的 input/key 事件链，让“提交按钮”进入可用状态。
        """
        try:
            await tb.click()
        except Exception:
            pass

        # 如果是 textarea，fill + 空格退格通常能触发状态机
        try:
            await tb.press("End")
            await tb.type(" ")          # 触发 input
            await tb.press("Backspace") # 恢复
        except Exception:
            # 对 contenteditable 退化：使用 keyboard.type
            try:
                await self.page.keyboard.type(" ")
                await self.page.keyboard.press("Backspace")
            except Exception:
                pass

    async def _try_click_send(self, frame: Frame) -> bool:
        """
        尝试点击可用的 submit/send 按钮（作为备选）。
        """
        for sel in self.SEND_BTN:
            try:
                btn = frame.locator(sel).first
                if await btn.count() > 0:
                    try:
                        await btn.scroll_into_view_if_needed(timeout=1000)
                    except Exception:
                        pass
                    if await btn.is_visible() and await btn.is_enabled():
                        await btn.click()
                        self._log(f"send: clicked {sel}")
                        return True
            except Exception:
                continue
        return False

    async def ensure_ready(self) -> None:
        self._log("ensure_ready: start")
        await asyncio.sleep(1.0)
        await self._dismiss_overlays()

        t0 = time.time()
        hb = t0
        while time.time() - t0 < 30:
            try:
                tb, frame, how = await self._find_textbox_any_frame()
                self._log(f"ensure_ready: textbox OK via {how}. frame={frame.url}")
                return
            except Exception:
                pass

            if time.time() - hb >= 5:
                self._log("ensure_ready: still locating textbox...")
                hb = time.time()
            await asyncio.sleep(0.4)

        await self.save_artifacts("ensure_ready_failed")
        await self.manual_checkpoint("未检测到输入框（可能需要登录/有弹窗/地区限制）。")

        tb, frame, how = await self._find_textbox_any_frame()
        self._log(f"ensure_ready: textbox OK after manual via {how}. frame={frame.url}")

    async def new_chat(self) -> None:
        self._log("new_chat: best effort")
        await self.try_click(self.NEW_THREAD, timeout_ms=1500)
        await asyncio.sleep(0.8)

    async def ask(self, prompt: str, timeout_s: int = 480) -> Tuple[str, str]:
        self._log("ask: start")
        await self.ensure_ready()
        await self.new_chat()

        tb, frame, how = await self._find_textbox_any_frame()
        self._log(f"send: textbox via {how} frame={frame.url}")

        # 1) 用 type 触发真实事件（不要只 fill）
        await tb.click()
        try:
            # 清空再输入，确保事件链完整
            await tb.fill("")
            await tb.type(prompt, delay=5)  # delay 略微像人类输入
        except Exception:
            # contenteditable 退化方案
            await tb.fill(prompt)

        # 2) 额外触发一次 input 事件，解锁提交按钮
        await self._arm_input_events(tb)

        # 3) 首选：按 Enter 提交（Perplexity 很多版本以 Enter 为准）
        submitted = False
        try:
            await tb.press("Enter")
            submitted = True
            self._log("send: pressed Enter")
        except Exception:
            pass

        # 4) 备选：如果 Enter 没生效，尝试点 submit
        if not submitted:
            submitted = await self._try_click_send(frame)

        # 5) 如果还是没生效，人工接管一次
        if not submitted:
            await self.save_artifacts("send_not_submitted")
            await self.manual_checkpoint("未能触发提交（按钮可能未解锁/页面拦截）。请手动点击提交后回车继续。")

        # 6) 等待回答稳定（文本不再增长）
        self._log("ask: waiting answer stabilize...")
        t0 = time.time()
        stable_seconds = 2.0
        last_text = ""
        last_change = time.time()
        hb = time.time()

        while time.time() - t0 < timeout_s:
            text = await self._read_last_answer_text()
            if text and text != last_text:
                last_text = text
                last_change = time.time()

            if last_text and (time.time() - last_change) >= stable_seconds:
                self._log("ask: done (stabilized)")
                return last_text, self.page.url

            if time.time() - hb >= 10:
                self._log(f"ask: last_len={len(last_text)} ...")
                hb = time.time()

            await asyncio.sleep(0.7)

        await self.save_artifacts("answer_timeout")
        await self.manual_checkpoint("等待 Perplexity 输出超时，请检查页面是否卡住/需要登录。")
        return await self._read_last_answer_text(), self.page.url