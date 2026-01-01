# -*- coding: utf-8 -*-
"""
Author: xiaofan with Codex Cusor
Created: 2025-12-29 20:27:11 +0800
Modified: 2025-12-31 19:09:41 +0800
"""
# rpa_llm/adapters/grok.py
from __future__ import annotations

import asyncio
import re
import time
from typing import Optional, Tuple

from playwright.async_api import Frame, Locator

from ..utils import beijing_now_iso
from .base import SiteAdapter


class GrokAdapter(SiteAdapter):
    site_id = "grok"
    base_url = "https://grok.com/"

    TEXTBOX_SELECTORS = [
        # 更贴近 Grok 首页结构：输入条通常在 form 内
        "form textarea",
        "form [role='textbox']",
        "form div[contenteditable='true']",

        # 常见 textarea/role
        'textarea[placeholder*="Ask"]',
        'textarea[placeholder*="Grok"]',
        'textarea[placeholder*="Message"]',
        'textarea[placeholder*="问"]',
        'textarea[placeholder*="询问"]',
        "textarea",

        # 兜底
        'div[role="textbox"][contenteditable="true"]',
        '[role="textbox"]',
        'div[contenteditable="true"]',
    ]

    # 发送按钮只作为备选：很多版本以 Enter 为主
    SEND_BTN = [
        "form button[type='submit']",
        "button[type='submit']",
        "button[aria-label*='Send']",
        "button[aria-label*='发送']",
        "button:has-text('Send')",
        "button:has-text('发送')",
        # 图标按钮兜底（最后）
        "form button:has(svg)",
    ]

    NEW_CHAT = [
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

    async def _dismiss_overlays_basic(self) -> None:
        # ESC 对很多浮层有效
        try:
            await self.page.keyboard.press("Escape")
            await asyncio.sleep(0.12)
            await self.page.keyboard.press("Escape")
        except Exception:
            pass

    async def _close_upgrade_card_by_bbox(self) -> bool:
        """
        关键：关闭你截图里的“Grok 图像升级”卡片。
        X 很可能是 svg 图标，没有稳定 aria-label，所以用 bbox 坐标点击右上角。
        """
        try:
            title = self.page.locator("text=Grok 图像升级").first
            if await title.count() == 0:
                return False

            # 取包含该标题的卡片容器（多取几层，尽量找到一个较大盒子）
            card = title.locator("xpath=ancestor::*[self::div or self::section][1]")
            for _ in range(3):
                box = await card.bounding_box()
                if box and box["width"] > 200 and box["height"] > 80:
                    # 点右上角内侧（避开边缘点击失败）
                    x = box["x"] + box["width"] - 14
                    y = box["y"] + 14
                    await self.page.mouse.click(x, y)
                    await asyncio.sleep(0.2)
                    return True
                card = card.locator("xpath=ancestor::*[self::div or self::section][1]")

        except Exception:
            return False

        return False

    async def _close_popups(self) -> None:
        # 1) 先 ESC
        await self._dismiss_overlays_basic()

        # 2) 常规 Close selector（有些弹窗是 dialog）
        close_selectors = [
            'div[role="dialog"] button[aria-label*="Close"]',
            'div[role="dialog"] button[aria-label*="关闭"]',
            'button[aria-label*="Close"]',
            'button[aria-label*="关闭"]',
            'button[data-testid*="close"]',
            'button[data-testid*="dismiss"]',
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

        # 3) 针对“Grok 图像升级”促销卡：bbox 点击右上角 X
        await self._close_upgrade_card_by_bbox()

    async def _find_textbox_any_frame(self) -> Tuple[Locator, Frame, str]:
        ph = re.compile(r"(Ask|Grok|Message|问|询问)", re.I)

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

        raise RuntimeError("Grok: cannot locate textbox in any frame.")

    async def _arm_input_events(self, tb: Locator) -> None:
        # 触发 input/key 事件链
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
            await self._close_popups()

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
        await self.manual_checkpoint("未检测到输入框（可能需要登录/浮层遮挡/页面未挂载完成）。")

        # 人工处理后再试一次
        await self._close_popups()
        tb, frame, how = await self._find_textbox_any_frame()
        self._log(f"ensure_ready: textbox OK after manual via {how}. frame={frame.url}")

    async def new_chat(self) -> None:
        self._log("new_chat: best effort")
        await self.try_click(self.NEW_CHAT, timeout_ms=1500)
        await asyncio.sleep(0.8)

    async def ask(self, prompt: str, timeout_s: int = 480) -> Tuple[str, str]:
        # 清理 prompt 中的换行符（避免输入时触发 Enter）
        prompt = self.clean_newlines(prompt, logger=lambda msg: self._log(f"ask: {msg}"))
        
        self._log("ask: start")
        await self.ensure_ready()
        await self.new_chat()

        await self._close_popups()

        tb, frame, how = await self._find_textbox_any_frame()
        self._log(f"send: textbox via {how} frame={frame.url}")

        # 用 type(delay) 触发真实键盘事件
        # 最终验证：确保 prompt 中没有任何换行符（双重保险）
        prompt = self.clean_newlines(prompt, logger=lambda msg: self._log(f"ask: {msg}"))
        
        await tb.click()
        try:
            await tb.fill("")
            await tb.type(prompt, delay=5)
            # type() 完成后立即检查是否已经发送
            await asyncio.sleep(0.2)
            try:
                textbox_after = await asyncio.wait_for(tb.inner_text(), timeout=2) or ""
                if not textbox_after.strip() or len(textbox_after.strip()) < len(prompt) * 0.1:
                    self._log(f"ask: warning - prompt may have been sent during type() (textbox empty or nearly empty)")
            except Exception:
                pass
        except Exception:
            await tb.fill(prompt)

        await self._arm_input_events(tb)

        # Enter 优先提交
        submitted = False
        try:
            await tb.press("Enter")
            submitted = True
            self._log("send: pressed Enter")
        except Exception:
            pass

        # 按钮备选
        if not submitted:
            submitted = await self._try_click_send(frame)

        if not submitted:
            await self.save_artifacts("send_not_submitted")
            await self.manual_checkpoint("未能触发提交（可能浮层遮挡/按钮未解锁）。请手动发送后回车继续。")

        # 等待回答稳定
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
        await self.manual_checkpoint("等待 Grok 输出超时，请检查页面是否卡住/需要登录。")
        return await self._read_last_answer_text(), self.page.url