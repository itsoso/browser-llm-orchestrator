# rpa_llm/adapters/chatgpt.py
from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Optional, Tuple

from playwright.async_api import Frame, Locator

from ..utils import utc_now_iso
from .base import SiteAdapter


class ChatGPTAdapter(SiteAdapter):
    site_id = "chatgpt"
    # 可定制入口：建议用专用对话 URL（https://chatgpt.com/c/<id>）以提升稳定性
    base_url = os.environ.get("CHATGPT_ENTRY_URL", "https://chatgpt.com/")

    # 输入框：优先语义化（placeholder/role），css 只作兜底
    TEXTBOX_CSS = [
        'textarea[data-testid="prompt-textarea"]',
        "textarea#prompt-textarea",
        'textarea[placeholder*="询问"]',
        'textarea[placeholder*="Message"]',
        'div[role="textbox"][contenteditable="true"]',
        'div[contenteditable="true"][role="textbox"]',
        '[role="textbox"]',
        "textarea",
        # 最后兜底（容易波动，但有时只能靠它）
        'div[contenteditable="true"]',
    ]

    # 发送按钮：优先 data-testid，其次 submit
    SEND_BTN = [
        'button[data-testid="send-button"]',
        'button[aria-label*="Send"]',
        'button[aria-label*="发送"]',
        'button:has-text("发送")',
        'button:has-text("Send")',
        'form button[type="submit"]',
        'button[type="submit"]',
    ]

    # 新聊天（可选）
    NEW_CHAT = [
        'a:has-text("新聊天")',
        'button:has-text("新聊天")',
        'a:has-text("New chat")',
        'button:has-text("New chat")',
        'a[aria-label*="New chat"]',
        'button[aria-label*="New chat"]',
    ]

    # 生成中按钮：用于判断是否还在生成
    STOP_BTN = [
        'button:has-text("Stop generating")',
        'button:has-text("停止生成")',
        'button[aria-label*="Stop"]',
        'button[aria-label*="停止"]',
    ]

    # 消息容器：用于确认发送成功与回复到达
    ASSISTANT_MSG = [
        'div[data-message-author-role="assistant"]',
        'article[data-message-author-role="assistant"]',
    ]
    USER_MSG = [
        'div[data-message-author-role="user"]',
        'article[data-message-author-role="user"]',
    ]

    # 模式/模型选择（best-effort，失败会静默跳过）
    THINKING_TOGGLE = [
        'button:has-text("Extended thinking")',
        'button:has-text("深度思考")',
        'button:has-text("扩展思考")',
        'button[aria-label*="thinking"]',
    ]
    MODEL_PICKER_BTN = [
        'button[aria-label*="Model"]',
        'button[aria-label*="模型"]',
        '[data-testid*="model"] button',
        'button:has-text("GPT")',
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._variant_set = False

    def _log(self, msg: str) -> None:
        print(f"[{utc_now_iso()}] [{self.site_id}] {msg}", flush=True)

    def _desired_variant(self) -> str:
        # CHATGPT_VARIANT=instant|thinking|pro
        v = (os.environ.get("CHATGPT_VARIANT") or "thinking").strip().lower()
        return v if v in ("instant", "thinking", "pro") else "thinking"

    def _new_chat_enabled(self) -> bool:
        # CHATGPT_NEW_CHAT=1 才会每 task 点“新聊天”（更隔离，但更容易触发重绘抖动）
        return (os.environ.get("CHATGPT_NEW_CHAT") or "0").strip() == "1"

    def _frames_in_priority(self) -> list[Frame]:
        mf = self.page.main_frame
        return [mf] + [f for f in self.page.frames if f != mf]

    async def _dismiss_overlays(self) -> None:
        # 关闭可能遮挡输入框的浮层/菜单
        try:
            await self.page.keyboard.press("Escape")
            await asyncio.sleep(0.15)
            await self.page.keyboard.press("Escape")
        except Exception:
            pass

    async def _is_cloudflare(self) -> bool:
        try:
            body = await self.page.inner_text("body")
        except Exception:
            return False
        return ("确认您是人类" in body) or ("Verify you are human" in body) or ("Cloudflare" in body)

    async def _try_visible(self, loc: Locator) -> bool:
        try:
            return await loc.is_visible()
        except Exception:
            return False

    async def _find_textbox_any_frame(self) -> Optional[Tuple[Locator, Frame, str]]:
        """
        返回 (textbox_locator, frame, how) 或 None
        优先：placeholder/role（更稳）
        其次：CSS
        """
        ph = re.compile(r"(询问|Message|Ask|anything|输入)", re.I)

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
            for sel in self.TEXTBOX_CSS:
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

        return None

    async def _ready_check_textbox(self) -> bool:
        await self._dismiss_overlays()
        return (await self._find_textbox_any_frame()) is not None

    async def _set_thinking_toggle(self, want_thinking: bool) -> None:
        for sel in self.THINKING_TOGGLE:
            try:
                btn = self.page.locator(sel).first
                if await btn.count() == 0 or not await btn.is_visible():
                    continue
                pressed = await btn.get_attribute("aria-pressed")
                is_on = (pressed == "true")
                if want_thinking != is_on:
                    await btn.click()
                    await asyncio.sleep(0.4)
                    self._log(f"mode: set thinking={want_thinking} via {sel}")
                return
            except Exception:
                continue

    async def _select_model_menu_item(self, pattern: re.Pattern) -> bool:
        candidates = [
            self.page.get_by_role("menuitem"),
            self.page.get_by_role("option"),
            self.page.locator("div[role='menuitem']"),
            self.page.locator("button"),
        ]
        for c in candidates:
            try:
                cnt = await c.count()
            except Exception:
                continue
            for i in range(min(cnt, 60)):
                try:
                    item = c.nth(i)
                    txt = (await item.inner_text()).strip()
                    if txt and pattern.search(txt):
                        await item.click()
                        await asyncio.sleep(0.6)
                        return True
                except Exception:
                    continue
        return False

    async def ensure_variant(self) -> None:
        """
        best-effort：只设置一次
        """
        if self._variant_set:
            return
        v = self._desired_variant()
        self._log(f"mode: desired={v}")

        if v in ("thinking", "instant"):
            await self._set_thinking_toggle(want_thinking=(v == "thinking"))
            self._variant_set = True
            return

        if v == "pro":
            opened = False
            for sel in self.MODEL_PICKER_BTN:
                try:
                    btn = self.page.locator(sel).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.click()
                        await asyncio.sleep(0.5)
                        opened = True
                        break
                except Exception:
                    continue

            if not opened:
                # 找不到模型选择器，不阻塞
                self._log("mode: model picker not found; skip")
                self._variant_set = True
                return

            ok = await self._select_model_menu_item(re.compile(r"\bPro\b|专业|Professional", re.I))
            if not ok:
                self._log("mode: cannot auto-select Pro; skip")
            self._variant_set = True

    async def ensure_ready(self) -> None:
        self._log("ensure_ready: start")
        await asyncio.sleep(0.6)

        # Cloudflare 直接进入人工点一次（但支持 auto-continue）
        if await self._is_cloudflare():
            await self.manual_checkpoint(
                "检测到 Cloudflare 人机验证页面，请人工完成验证。",
                ready_check=self._ready_check_textbox,
                max_wait_s=90,
            )

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
        await self.manual_checkpoint(
            "未检测到输入框（可能弹窗遮挡/页面未完成挂载）。请手动点一下输入框或完成登录后继续。",
            ready_check=self._ready_check_textbox,
            max_wait_s=90,
        )

        # 人工处理后再确认一次
        if not await self._ready_check_textbox():
            await self.save_artifacts("ensure_ready_failed_after_manual")
            raise RuntimeError("ensure_ready: still cannot locate textbox after manual checkpoint.")

    async def new_chat(self) -> None:
        self._log("new_chat: best effort")
        await self.try_click(self.NEW_CHAT, timeout_ms=2000)
        # 新聊天会重绘输入框，给一点时间
        await asyncio.sleep(1.0)

    async def _assistant_count(self) -> int:
        for sel in self.ASSISTANT_MSG:
            try:
                return await self.page.locator(sel).count()
            except Exception:
                continue
        return 0

    async def _user_count(self) -> int:
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

    async def _arm_input_events(self, tb: Locator) -> None:
        """
        触发输入事件链（空格+退格），提高发送按钮解锁概率
        """
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

    async def _send_prompt(self, prompt: str) -> None:
        """
        强健发送：type(delay) + 超时保护 + 发送按钮优先
        """
        found = await self._find_textbox_any_frame()
        if not found:
            await self.save_artifacts("send_no_textbox")
            await self.manual_checkpoint(
                "发送前未找到输入框，请手动点一下输入框后继续。",
                ready_check=self._ready_check_textbox,
                max_wait_s=60,
            )
            found = await self._find_textbox_any_frame()
            if not found:
                raise RuntimeError("send: textbox still not found")

        tb, frame, how = found
        self._log(f"send: textbox via {how} frame={frame.url}")

        # click with hard timeout
        try:
            await asyncio.wait_for(tb.click(), timeout=15)
        except Exception as e:
            await self.save_artifacts("send_hang_click")
            raise RuntimeError(f"send: textbox click timeout/failed: {e}")

        # clear (best-effort)
        try:
            await asyncio.wait_for(tb.fill(""), timeout=10)
        except Exception:
            pass

        # type (more reliable than fill for some SPA inputs)
        try:
            await asyncio.wait_for(tb.type(prompt, delay=3), timeout=45)
        except Exception as e:
            await self.save_artifacts("send_hang_type")
            raise RuntimeError(f"send: textbox type timeout/failed: {e}")

        # arm input events
        try:
            await asyncio.wait_for(self._arm_input_events(tb), timeout=10)
        except Exception:
            pass

        # click send button first (more reliable than Enter for ChatGPT)
        for send_sel in self.SEND_BTN:
            try:
                btn = frame.locator(send_sel).first
                if await btn.count() > 0:
                    try:
                        await btn.scroll_into_view_if_needed(timeout=1000)
                    except Exception:
                        pass
                    if await btn.is_visible():
                        self._log(f"send: try click send button {send_sel}")
                        await asyncio.wait_for(btn.click(), timeout=15)
                        self._log(f"send: clicked send button {send_sel}")
                        return
            except Exception:
                continue

        # fallback: Enter
        self._log("send: send button not found/click failed, fallback Enter")
        try:
            await asyncio.wait_for(tb.press("Enter"), timeout=5)
        except Exception:
            await tb.press("Control+Enter")

    async def ask(self, prompt: str, timeout_s: int = 240) -> Tuple[str, str]:
        """
        发送 prompt 并等待回复。
        
        使用整体超时保护，确保不会无限等待。
        超时后会抛出 TimeoutError 异常。
        """
        async def _ask_inner() -> Tuple[str, str]:
            ask_start_time = time.time()
            self._log(f"ask: start (timeout={timeout_s}s)")
            
            await self.ensure_ready()
            await self.ensure_variant()

            # 是否每次新聊天（默认关闭，提高稳定性与速度）
            if self._new_chat_enabled():
                await self.new_chat()
                # 新聊天后输入框重建，重新确保 ready
                await self.ensure_ready()

            n_assist0 = await self._assistant_count()
            user0 = await self._user_count()
            # 记录发送前的最后一个 assistant 消息文本，用于区分新旧消息
            last_assist_text_before = await self._last_assistant_text()
            self._log(f"ask: assistant_count(before)={n_assist0}, user_count(before)={user0}, last_assist_text_len(before)={len(last_assist_text_before)}")

            # 检查是否已经超时
            elapsed = time.time() - ask_start_time
            remaining = timeout_s - elapsed
            if remaining <= 10:
                raise TimeoutError(f"ask: timeout before sending (elapsed={elapsed:.1f}s)")

            self._log("ask: sending prompt...")
            await self._send_prompt(prompt)

            # 确认 user 消息出现（证明发送成功）
            self._log("ask: confirming user message appeared...")
            t0 = time.time()
            hb = t0
            user_wait_timeout = min(45, remaining - 5)  # 最多45秒，但不超过剩余时间
            while time.time() - t0 < user_wait_timeout:
                elapsed = time.time() - ask_start_time
                if elapsed >= timeout_s - 5:  # 留5秒缓冲
                    break
                try:
                    user1 = await asyncio.wait_for(self._user_count(), timeout=2.0)
                    if user1 > user0:
                        self._log(f"ask: user_count(after)={user1} (sent OK)")
                        break
                except asyncio.TimeoutError:
                    self._log("ask: user_count() timeout, retrying...")
                except Exception as e:
                    self._log(f"ask: user_count() error: {e}")
                
                if time.time() - hb >= 5:
                    self._log("ask: still waiting user message to appear...")
                    hb = time.time()
                await asyncio.sleep(0.5)
            else:
                await self.save_artifacts("send_not_confirmed")
                await self.manual_checkpoint(
                    "未确认消息发送成功（可能页面重绘/按钮未点到）。请手动检查对话里是否有你的消息。",
                    ready_check=self._ready_check_textbox,
                    max_wait_s=min(60, remaining - 5),
                )

            # 等待 assistant 消息出现（计数增加）
            self._log("ask: waiting for assistant message...")
            t1 = time.time()
            hb = t1
            elapsed = time.time() - ask_start_time
            remaining = timeout_s - elapsed
            assistant_wait_timeout = min(remaining * 0.6, 180)  # 最多用60%的时间等待assistant出现，但不超过180秒
            
            while time.time() - t1 < assistant_wait_timeout:
                elapsed = time.time() - ask_start_time
                if elapsed >= timeout_s - 30:  # 留30秒给后续阶段
                    self._log(f"ask: timeout approaching (elapsed={elapsed:.1f}s/{timeout_s}s), breaking assistant wait")
                    break
                try:
                    n_assist1 = await asyncio.wait_for(self._assistant_count(), timeout=2.0)
                    if n_assist1 > n_assist0:
                        self._log(f"ask: assistant_count(after)={n_assist1} (new message)")
                        break
                except asyncio.TimeoutError:
                    self._log("ask: assistant_count() timeout, retrying...")
                except Exception as e:
                    self._log(f"ask: assistant_count() error: {e}")
                    
                if time.time() - hb >= 10:
                    elapsed = time.time() - ask_start_time
                    self._log(f"ask: still waiting assistant message... (elapsed={elapsed:.1f}s/{timeout_s}s)")
                    hb = time.time()
                await asyncio.sleep(0.6)
            else:
                await self.save_artifacts("no_assistant_reply")
                await self.manual_checkpoint(
                    "发送后未等到回复（可能网络/风控/页面提示）。请检查页面是否需要操作。",
                    ready_check=self._ready_check_textbox,
                    max_wait_s=min(60, timeout_s - elapsed - 5),
                )

            # 等待新消息的文本内容出现（确保不是旧消息）
            self._log("ask: waiting for new message content (different from before)...")
            t2 = time.time()
            hb = t2
            new_message_found = False
            elapsed = time.time() - ask_start_time
            remaining = timeout_s - elapsed
            content_wait_timeout = min(30, remaining * 0.3)  # 最多30秒或剩余时间的30%
            
            while time.time() - t2 < content_wait_timeout:
                elapsed = time.time() - ask_start_time
                if elapsed >= timeout_s - 10:  # 留10秒给稳定等待
                    break
                try:
                    current_text = await asyncio.wait_for(self._last_assistant_text(), timeout=2.0)
                    # 新消息必须：1) 有内容 2) 与发送前的消息不同
                    if current_text and current_text != last_assist_text_before:
                        new_message_found = True
                        self._log(f"ask: new message content detected (len={len(current_text)})")
                        break
                except asyncio.TimeoutError:
                    pass
                except Exception as e:
                    self._log(f"ask: _last_assistant_text() error: {e}")
                    
                if time.time() - hb >= 5:
                    self._log(f"ask: still waiting for new message content... (elapsed={elapsed:.1f}s/{timeout_s}s)")
                    hb = time.time()
                await asyncio.sleep(0.6)
            
            if not new_message_found:
                self._log("ask: warning: new message content not confirmed, but continuing...")

            # 等待输出稳定
            self._log("ask: waiting output stabilize...")
            stable_seconds = 2.0
            last_text = ""
            last_change = time.time()
            hb = time.time()

            while time.time() - ask_start_time < timeout_s:
                elapsed = time.time() - ask_start_time
                remaining = timeout_s - elapsed
                
                if remaining <= 0:
                    break
                    
                try:
                    text = await asyncio.wait_for(self._last_assistant_text(), timeout=2.0)
                    # 确保获取的是新消息（不是发送前的旧消息）
                    if text and text != last_assist_text_before:
                        if text != last_text:
                            last_text = text
                            last_change = time.time()
                            self._log(f"ask: text updated (len={len(last_text)}, remaining={remaining:.1f}s)")

                    generating = await asyncio.wait_for(self._is_generating(), timeout=1.0)
                    if last_text and (time.time() - last_change) >= stable_seconds and (not generating):
                        elapsed = time.time() - ask_start_time
                        self._log(f"ask: done (stabilized, total={elapsed:.1f}s)")
                        return last_text, self.page.url
                except asyncio.TimeoutError:
                    # DOM 查询超时，继续等待
                    pass
                except Exception as e:
                    self._log(f"ask: DOM query error: {e}")

                if time.time() - hb >= 10:
                    elapsed = time.time() - ask_start_time
                    remaining = timeout_s - elapsed
                    try:
                        generating = await asyncio.wait_for(self._is_generating(), timeout=1.0)
                    except:
                        generating = False
                    self._log(f"ask: generating={generating}, last_len={len(last_text)}, remaining={remaining:.1f}s ...")
                    hb = time.time()

                await asyncio.sleep(0.6)

            # 超时处理
            elapsed = time.time() - ask_start_time
            await self.save_artifacts("answer_timeout")
            final_text = ""
            try:
                final_text = await asyncio.wait_for(self._last_assistant_text(), timeout=2.0)
            except:
                pass
            
            if final_text and final_text != last_assist_text_before:
                self._log(f"ask: timeout but got partial answer (len={len(final_text)}, elapsed={elapsed:.1f}s)")
                return final_text, self.page.url
            else:
                raise TimeoutError(
                    f"ask: timeout after {elapsed:.1f}s (limit={timeout_s}s). "
                    f"No valid answer received. last_text_len={len(last_text)}"
                )

        # 使用整体超时保护
        try:
            return await asyncio.wait_for(_ask_inner(), timeout=timeout_s + 5)  # 多给5秒缓冲
        except asyncio.TimeoutError:
            await self.save_artifacts("ask_total_timeout")
            raise TimeoutError(f"ask: total timeout exceeded ({timeout_s}s)")