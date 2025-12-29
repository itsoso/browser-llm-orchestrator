# rpa_llm/adapters/chatgpt.py
from __future__ import annotations

import asyncio
import re
import time
from typing import Optional, Tuple

from playwright.async_api import Frame, Locator

from .base import SiteAdapter


class ChatGPTAdapter(SiteAdapter):
    site_id = "chatgpt"
    base_url = "https://chatgpt.com/"

    # 更保守的 textbox selector：避免过于泛化的 div[contenteditable="true"] 作为常规兜底
    # 注意：我们仍允许在“最后兜底”时用 contenteditable，但优先用 placeholder/role 等语义化定位。
    TEXTBOX_SELECTORS = [
        'textarea[data-testid="prompt-textarea"]',
        "textarea#prompt-textarea",
        'textarea[placeholder*="询问"]',
        'textarea[placeholder*="Message"]',
        'div[role="textbox"][contenteditable="true"]',
        'div[contenteditable="true"][role="textbox"]',
        '[role="textbox"]',
        "textarea",
    ]

    # 发送按钮 selector：尽量覆盖常见形态，并提供 submit 兜底
    SEND_BTN = [
        'button[data-testid="send-button"]',
        'button[aria-label*="Send"]',
        'button[aria-label*="发送"]',
        'button:has-text("发送")',
        'button:has-text("Send")',
        'form button[type="submit"]',
        'button[type="submit"]',
    ]

    NEW_CHAT = [
        'a:has-text("新聊天")',
        'button:has-text("新聊天")',
        'a:has-text("New chat")',
        'button:has-text("New chat")',
        'a[aria-label*="New chat"]',
        'button[aria-label*="New chat"]',
    ]

    STOP_BTN = [
        'button:has-text("Stop generating")',
        'button:has-text("停止生成")',
        'button[aria-label*="Stop"]',
        'button[aria-label*="停止"]',
    ]

    ASSISTANT_MSG = [
        'div[data-message-author-role="assistant"]',
        'article[data-message-author-role="assistant"]',
    ]

    USER_MSG = [
        'div[data-message-author-role="user"]',
        'article[data-message-author-role="user"]',
    ]

    def _log(self, msg: str) -> None:
        print(f"[{self.site_id}] {msg}", flush=True)

    def _frames_in_priority(self) -> list[Frame]:
        mf = self.page.main_frame
        return [mf] + [f for f in self.page.frames if f != mf]

    async def _dismiss_overlays(self) -> None:
        # 关闭可能遮挡输入框的弹窗/浮层：ESC 通常有效
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

    async def _find_textbox_any_frame(self) -> Optional[Tuple[Locator, Frame, str]]:
        """
        返回 (textbox_locator, frame, how)
        优先：placeholder/role（更稳定）
        其次：css selectors
        最后：contenteditable 兜底（仅在确实需要时）
        """
        ph = re.compile(r"(询问|Message|Ask|anything)", re.I)

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

            # 4) 最后兜底：contenteditable（有些 UI 只有这个能命中）
            try:
                loc = frame.locator('div[contenteditable="true"]').first
                if await loc.count() > 0:
                    try:
                        await loc.scroll_into_view_if_needed(timeout=1000)
                    except Exception:
                        pass
                    if await self._try_visible(loc):
                        return loc, frame, 'css:div[contenteditable="true"]'
            except Exception:
                pass

        return None

    async def _assistant_count(self) -> int:
        for sel in self.ASSISTANT_MSG:
            try:
                return await self.page.locator(sel).count()
            except Exception:
                continue
        return 0

    async def _user_count(self) -> int:
        # 统计 user 消息数，用于确认“发送成功”
        for sel in self.USER_MSG:
            try:
                return await self.page.locator(sel).count()
            except Exception:
                continue
        return 0

    async def _last_assistant_text(self) -> str:
        for sel in self.ASSISTANT_MSG:
            loc = self.page.locator(sel)
            try:
                cnt = await loc.count()
                if cnt > 0:
                    return (await loc.nth(cnt - 1).inner_text()).strip()
            except Exception:
                continue
        return ""

    async def _is_generating(self) -> bool:
        for sel in self.STOP_BTN:
            try:
                loc = self.page.locator(sel).first
                if await loc.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def ensure_ready(self) -> None:
        self._log("ensure_ready: start")
        await asyncio.sleep(0.8)

        # Cloudflare 粗检：避免在验证页死等
        try:
            body = await self.page.inner_text("body")
        except Exception:
            body = ""

        if "确认您是人类" in body or "Verify you are human" in body or "Cloudflare" in body:
            self._log("ensure_ready: detected Cloudflare -> manual_checkpoint")
            await self.manual_checkpoint("检测到 Cloudflare 人机验证页面，请人工完成验证。")

        total_timeout_s = 60
        t0 = time.time()
        hb = t0

        while time.time() - t0 < total_timeout_s:
            await self._dismiss_overlays()

            found = await self._find_textbox_any_frame()
            if found:
                _, frame, how = found
                self._log(f"ensure_ready: textbox OK via {how}. frame={frame.url}")
                return

            if time.time() - hb >= 5:
                self._log("ensure_ready: still locating textbox...")
                hb = time.time()

            await asyncio.sleep(0.4)

        await self.save_artifacts("ensure_ready_failed")
        self._log("ensure_ready: textbox NOT found -> manual_checkpoint")
        await self.manual_checkpoint("未检测到输入框（可能弹窗遮挡/页面未完成挂载/需要手动点一下输入框）。")

        # 人工处理后再探测一次
        found = await self._find_textbox_any_frame()
        if found:
            _, frame, how = found
            self._log(f"ensure_ready: textbox OK after manual via {how}. frame={frame.url}")
            return

        raise RuntimeError("ensure_ready: still cannot locate textbox after manual checkpoint.")

    async def new_chat(self) -> None:
        self._log("new_chat: click '新聊天' (best effort)")
        await self.try_click(self.NEW_CHAT, timeout_ms=2000)
        await asyncio.sleep(0.8)

    async def _send_prompt(self, prompt: str) -> None:
        found = await self._find_textbox_any_frame()
        if not found:
            await self.save_artifacts("send_no_textbox")
            await self.manual_checkpoint("发送前未找到输入框，请手动点一下输入框后回车继续。")
            found = await self._find_textbox_any_frame()
            if not found:
                raise RuntimeError("send: textbox still not found")

        tb, frame, how = found
        self._log(f"send: textbox via {how} frame={frame.url}")

        await tb.click()
        await tb.fill(prompt)

        # 优先点击发送按钮（比 Enter 更可靠）
        for send_sel in self.SEND_BTN:
            try:
                btn = frame.locator(send_sel).first
                if await btn.count() > 0:
                    try:
                        await btn.scroll_into_view_if_needed(timeout=1000)
                    except Exception:
                        pass
                    if await btn.is_visible():
                        await btn.click()
                        self._log(f"send: clicked send button {send_sel}")
                        return
            except Exception:
                continue

        self._log("send: send button not found, fallback Enter/Ctrl+Enter")
        try:
            await tb.press("Enter")
        except Exception:
            await tb.press("Control+Enter")

    async def ask(self, prompt: str, timeout_s: int = 240) -> Tuple[str, str]:
        self._log("ask: start")
        await self.ensure_ready()
        await self.new_chat()

        n_assist0 = await self._assistant_count()
        user0 = await self._user_count()
        self._log(f"ask: assistant_count(before)={n_assist0}, user_count(before)={user0}")

        self._log("ask: sending prompt...")
        await self._send_prompt(prompt)

        # 关键：确认 user 消息出现（避免 Enter 变换行/没真正发送）
        self._log("ask: confirming user message appeared...")
        t0 = time.time()
        hb = t0
        while time.time() - t0 < 30:
            user1 = await self._user_count()
            if user1 > user0:
                self._log(f"ask: user_count(after)={user1} (sent OK)")
                break

            if time.time() - hb >= 5:
                self._log("ask: still waiting user message to appear...")
                hb = time.time()

            await asyncio.sleep(0.5)
        else:
            await self.save_artifacts("send_not_confirmed")
            await self.manual_checkpoint("未确认消息发送成功（可能 Enter 变换行/发送按钮未点到）。请手动点击发送后回车继续。")

        # 等待 assistant 消息出现
        self._log("ask: waiting for assistant message...")
        t1 = time.time()
        hb = t1
        while time.time() - t1 < timeout_s:
            n_assist1 = await self._assistant_count()
            if n_assist1 > n_assist0:
                self._log(f"ask: assistant_count(after)={n_assist1} (new message)")
                break

            if time.time() - hb >= 10:
                self._log("ask: still waiting assistant message...")
                hb = time.time()

            await asyncio.sleep(0.6)
        else:
            await self.save_artifacts("no_assistant_reply")
            await self.manual_checkpoint("发送后未等到模型回复（可能风控/网络/页面弹窗）。请检查页面是否有提示。")

        # 等待输出稳定
        self._log("ask: waiting output stabilize...")
        stable_seconds = 2.0
        last_text = ""
        last_change = time.time()
        hb = time.time()

        while time.time() - t1 < timeout_s:
            text = await self._last_assistant_text()
            if text and text != last_text:
                last_text = text
                last_change = time.time()

            generating = await self._is_generating()
            if (time.time() - last_change) >= stable_seconds and (not generating) and last_text:
                self._log("ask: done (stabilized)")
                return last_text, self.page.url

            if time.time() - hb >= 10:
                self._log(f"ask: generating={generating}, last_len={len(last_text)} ...")
                hb = time.time()

            await asyncio.sleep(0.6)

        await self.save_artifacts("answer_timeout")
        await self.manual_checkpoint("等待回答完成超时，请检查页面是否仍在生成或需要继续操作。")
        return (await self._last_assistant_text()), self.page.url