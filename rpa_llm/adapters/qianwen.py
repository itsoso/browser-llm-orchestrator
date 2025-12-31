# rpa_llm/adapters/qianwen.py
from __future__ import annotations

import asyncio
import re
import time
from typing import Tuple

from playwright.async_api import Frame, Locator

from ..utils import beijing_now_iso
from .base import SiteAdapter


class QianwenAdapter(SiteAdapter):
    site_id = "qianwen"
    base_url = "https://www.qianwen.com/qianwen/"

    # 千问页面 UI 版本多：优先 form 内输入，再 textarea/role，再 contenteditable
    TEXTBOX_SELECTORS = [
        "form textarea",
        "form [role='textbox']",
        "form div[contenteditable='true']",

        'textarea[placeholder*="问"]',
        'textarea[placeholder*="输入"]',
        'textarea[placeholder*="Message"]',
        "textarea",

        'div[role="textbox"][contenteditable="true"]',
        '[role="textbox"]',
        'div[contenteditable="true"]',
    ]

    # 发送按钮（备选）：不同版本可能是图标按钮/submit
    SEND_BTN = [
        "form button[type='submit']",
        "button[type='submit']",
        "button[aria-label*='发送']",
        "button[aria-label*='Send']",
        "button:has-text('发送')",
        "button:has-text('Send')",
        # 图标按钮兜底（谨慎）
        "form button:has(svg)",
    ]

    NEW_CHAT = [
        'a:has-text("新对话")',
        'button:has-text("新对话")',
        'a:has-text("新建")',
        'button:has-text("新建")',
        'a:has-text("New")',
        'button:has-text("New")',
        'button[aria-label*="新"]',
    ]

    # 回答区域：用“文本稳定”判定
    ANSWER_BLOCK = [
        "main .markdown",
        "main .prose",
        "main article",
        "main div:has(p)",
    ]

    def _log(self, msg: str) -> None:
        print(f"[{beijing_now_iso()}] [{self.site_id}] {msg}", flush=True)

    def _frames_in_priority(self) -> list[Frame]:
        mf = self.page.main_frame
        return [mf] + [f for f in self.page.frames if f != mf]

    async def _try_visible(self, loc: Locator) -> bool:
        try:
            return await loc.is_visible()
        except Exception:
            return False

    async def _dismiss_overlays(self) -> None:
        # 1) ESC 关掉大部分浮层
        try:
            await self.page.keyboard.press("Escape")
            await asyncio.sleep(0.12)
            await self.page.keyboard.press("Escape")
        except Exception:
            pass

        # 2) 常见关闭按钮
        close_selectors = [
            'div[role="dialog"] button[aria-label*="关闭"]',
            'div[role="dialog"] button[aria-label*="Close"]',
            'button[aria-label*="关闭"]',
            'button[aria-label*="Close"]',
            'button[data-testid*="close"]',
            'button:has-text("关闭")',
            'button:has-text("Close")',
            'button:has-text("×")',
            'button:has-text("我知道了")',
            'button:has-text("知道了")',
            'button:has-text("同意")',
            'button:has-text("Agree")',
        ]
        for sel in close_selectors:
            try:
                btn = self.page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(0.2)
                    return
            except Exception:
                continue

    async def _find_textbox_any_frame(self) -> Tuple[Locator, Frame, str]:
        # placeholder 中英文兜底
        ph = re.compile(r"(输入|问|提问|Message|Ask)", re.I)

        for frame in self._frames_in_priority():
            # 1) placeholder 优先
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

            # 2) role=textbox
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

            # 3) css selectors
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

        raise RuntimeError("Qianwen: cannot locate textbox in any frame.")

    async def _arm_input_events(self, tb: Locator) -> None:
        """
        触发 input/key 事件链，避免“按钮不解锁/状态机不更新”
        """
        try:
            await tb.click()
        except Exception:
            pass

        try:
            await tb.press("End")
            await tb.type(" ")
            await tb.press("Backspace")
        except Exception:
            try:
                await self.page.keyboard.type(" ")
                await self.page.keyboard.press("Backspace")
            except Exception:
                pass

    async def _try_click_send(self, frame: Frame) -> bool:
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

    async def ensure_ready(self) -> None:
        self._log("ensure_ready: start")
        await asyncio.sleep(1.0)

        total_timeout_s = 45
        t0 = time.time()
        hb = t0

        while time.time() - t0 < total_timeout_s:
            await self._dismiss_overlays()

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
        await self.manual_checkpoint("未检测到输入框（可能需要登录/弹窗遮挡/页面未完成挂载）。")

        # 人工处理后再试一次
        await self._dismiss_overlays()
        tb, frame, how = await self._find_textbox_any_frame()
        self._log(f"ensure_ready: textbox OK after manual via {how}. frame={frame.url}")

    async def new_chat(self) -> None:
        self._log("new_chat: best effort")
        await self.try_click(self.NEW_CHAT, timeout_ms=1500)
        await asyncio.sleep(0.8)

    async def ask(self, prompt: str, timeout_s: int = 480) -> Tuple[str, str]:
        self._log("ask: start")
        await self.ensure_ready()
        await self.new_chat()

        await self._dismiss_overlays()

        tb, frame, how = await self._find_textbox_any_frame()
        self._log(f"send: textbox via {how} frame={frame.url}")

        # 1) type(delay) 触发真实键盘事件链
        await tb.click()
        try:
            await tb.fill("")
            await tb.type(prompt, delay=5)
        except Exception:
            await tb.fill(prompt)

        # 2) 额外触发一次 input 事件，解锁提交按钮
        await self._arm_input_events(tb)

        # 3) Enter 优先提交（很多聊天 UI 以 Enter 为准）
        submitted = False
        try:
            await tb.press("Enter")
            submitted = True
            self._log("send: pressed Enter")
        except Exception:
            pass

        # 4) 备选：点 send/submit
        if not submitted:
            submitted = await self._try_click_send(frame)

        # 5) 仍失败：人工接管
        if not submitted:
            await self.save_artifacts("send_not_submitted")
            await self.manual_checkpoint("未能触发提交（按钮可能未解锁/页面拦截）。请手动点击发送后回车继续。")

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
        await self.manual_checkpoint("等待 千问 输出超时，请检查页面是否卡住/需要登录。")
        return await self._read_last_answer_text(), self.page.url