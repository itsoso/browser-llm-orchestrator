# rpa_llm/adapters/chatgpt.py
from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Optional, Tuple

from playwright.async_api import Frame, Locator

from ..utils import beijing_now_iso
from .base import SiteAdapter


class ChatGPTAdapter(SiteAdapter):
    site_id = "chatgpt"
    # 可定制入口：建议用专用对话 URL（https://chatgpt.com/c/<id>）以提升稳定性
    base_url = os.environ.get("CHATGPT_ENTRY_URL", "https://chatgpt.com/")

    # 输入框：优化后的优先级（适配当前 ChatGPT ProseMirror 实现）
    # 当前 ChatGPT (GPT-4o/Canvas) 使用 contenteditable div，优先匹配新版
    TEXTBOX_CSS = [
        # 新版 ChatGPT (ProseMirror) - 最精准和常用
        'div[id="prompt-textarea"]',  # 最精准的 ID 选择器
        'div[contenteditable="true"]',  # 最通用的属性（命中率 99%）
        'div[role="textbox"][contenteditable="true"]',  # 语义化 + 属性
        'div[contenteditable="true"][role="textbox"]',  # 属性 + 语义化
        # 旧版 ChatGPT 兼容（如果还在使用）
        'textarea[data-testid="prompt-textarea"]',
        "textarea#prompt-textarea",
        'textarea[placeholder*="询问"]',
        'textarea[placeholder*="Message"]',
        # 通用兜底
        '[role="textbox"]',
        "textarea",
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
        print(f"[{beijing_now_iso()}] [{self.site_id}] {msg}", flush=True)

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
        优化版：优先检查主 Frame，使用最快选择器，减少 await 开销
        
        99% 的情况下输入框在 main_frame 中，优先检查可以大幅提升性能
        """
        mf = self.page.main_frame
        
        # 1. 优先检查主 Frame（最快路径）
        # 直接使用最可能的选择器，避免遍历所有 iframe
        try:
            # 并行检查最可能的两个选择器（id 和 contenteditable）
            # 使用 asyncio.gather 并行执行，更快
            tasks = [
                self._try_find_in_frame(mf, 'div[id="prompt-textarea"]', "main_frame_id"),
                self._try_find_in_frame(mf, 'div[contenteditable="true"]', "main_frame_contenteditable"),
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 返回第一个成功的结果
            for result in results:
                if isinstance(result, tuple) and result is not None:
                    return result
        except Exception:
            pass
        
        # 2. 如果主 Frame 没找到，再检查其他选择器（role/placeholder）
        try:
            # role=textbox（快速检查）
            loc = mf.get_by_role("textbox").first
            try:
                await loc.wait_for(state="attached", timeout=500)
                if await asyncio.wait_for(self._try_visible(loc), timeout=0.5):
                    return loc, mf, "main_frame_role"
            except (asyncio.TimeoutError, Exception):
                pass
        except Exception:
            pass
        
        # 3. 如果主 Frame 都没找到，再遍历所有 frame（兜底逻辑）
        ph = re.compile(r"(询问|Message|Ask|anything|输入)", re.I)
        
        for frame in self._frames_in_priority():
            # 跳过 main_frame（已经检查过了）
            if frame == mf:
                continue
                
            # placeholder 优先（快速检查）
            try:
                loc = frame.get_by_placeholder(ph).first
                try:
                    await loc.wait_for(state="attached", timeout=500)
                    if await asyncio.wait_for(self._try_visible(loc), timeout=0.5):
                        return loc, frame, "get_by_placeholder"
                except (asyncio.TimeoutError, Exception):
                    pass
            except Exception:
                pass

            # role=textbox（快速检查）
            try:
                loc = frame.get_by_role("textbox").first
                try:
                    await loc.wait_for(state="attached", timeout=500)
                    if await asyncio.wait_for(self._try_visible(loc), timeout=0.5):
                        return loc, frame, "get_by_role(textbox)"
                except (asyncio.TimeoutError, Exception):
                    pass
            except Exception:
                pass

            # css selectors（按优先级，找到第一个就返回）
            for sel in self.TEXTBOX_CSS:
                try:
                    loc = frame.locator(sel).first
                    try:
                        await loc.wait_for(state="attached", timeout=500)
                        if await asyncio.wait_for(self._try_visible(loc), timeout=0.5):
                            return loc, frame, f"css:{sel}"
                    except (asyncio.TimeoutError, Exception):
                        continue
                except Exception:
                    continue

        return None
    
    async def _try_find_in_frame(self, frame: Frame, selector: str, how: str) -> Optional[Tuple[Locator, Frame, str]]:
        """辅助方法：在指定 frame 中尝试查找选择器"""
        try:
            loc = frame.locator(selector).first
            await loc.wait_for(state="attached", timeout=500)
            if await asyncio.wait_for(self._try_visible(loc), timeout=0.5):
                return loc, frame, how
        except (asyncio.TimeoutError, Exception):
            pass
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
        # 减少初始延迟，页面可能已经加载完成
        await asyncio.sleep(0.2)

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
        check_count = 0

        while time.time() - t0 < total_timeout_s:
            # 前几次检查不 dismiss overlays，加快速度
            if check_count >= 3:
                await self._dismiss_overlays()
            
            found = await self._find_textbox_any_frame()
            if found:
                _, frame, how = found
                self._log(f"ensure_ready: textbox OK via {how}. frame={frame.url} (took {time.time()-t0:.2f}s)")
                return

            check_count += 1
            if time.time() - hb >= 5:
                self._log(f"ensure_ready: still locating textbox... (attempt {check_count})")
                hb = time.time()

            # 前几次快速检查，之后逐渐增加间隔
            sleep_time = 0.2 if check_count < 5 else 0.4
            await asyncio.sleep(sleep_time)

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
        # 新聊天会重绘输入框，等待页面稳定
        await asyncio.sleep(1.5)
        # 关闭可能的弹窗/遮罩
        await self._dismiss_overlays()
        await asyncio.sleep(0.5)

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
        修复版发送逻辑：
        1. 使用 JS 强制清空 (解决 Node is not input 报错)
        2. 智能 fallback 输入 (type -> JS injection)
        3. 组合键发送优先 (解决按钮点击失败)
        """
        # 1. 寻找输入框（带重试机制）
        found = None
        max_retries = 5
        for retry in range(max_retries):
            found = await self._find_textbox_any_frame()
            if found:
                break
            
            if retry < max_retries - 1:
                # 尝试关闭弹窗/遮罩
                await self._dismiss_overlays()
                self._log(f"send: textbox not found, retrying... ({retry+1}/{max_retries})")
                await asyncio.sleep(0.5)
            else:
                # 最后一次尝试失败，保存截图并触发 manual checkpoint
                await self.save_artifacts("send_no_textbox")
                await self.manual_checkpoint(
                    "发送前未找到输入框，请手动点一下输入框后继续。",
                    ready_check=self._ready_check_textbox,
                    max_wait_s=60,
                )
                found = await self._find_textbox_any_frame()
                if not found:
                    raise RuntimeError("send: textbox not found")

        tb, frame, how = found
        self._log(f"send: textbox via {how} frame={frame.url}")

        # 2. 确保焦点（点击失败不致命，可能是被遮挡，JS 输入依然可能成功）
        try:
            await tb.click(timeout=5000)
        except Exception:
            pass

        # 3. 循环尝试写入 (最多 2 次)
        prompt_sent = False
        for attempt in range(2):
            try:
                if attempt > 0:
                    self._log(f"send: attempt {attempt+1}, clearing textbox...")
                
                # --- [关键修复] 强制清空逻辑 ---
                # 不要用 tb.fill("")，这在 div 上不稳定。直接用 JS 清空 DOM。
                try:
                    # 确保元素可见和可交互，然后执行 evaluate（带超时）
                    await tb.wait_for(state="visible", timeout=5000)
                    await asyncio.wait_for(
                        tb.evaluate("el => { el.innerText = ''; el.innerHTML = ''; }"),
                        timeout=5.0
                    )
                    await asyncio.sleep(0.2)
                    self._log("send: cleared via JS")
                except Exception as e:
                    self._log(f"send: JS clear failed: {e}")
                    # 清空失败不致命，继续尝试输入

                # --- 输入内容 ---
                self._log(f"send: writing prompt ({len(prompt)} chars)...")
                
                # 策略 A: 优先尝试 type (模拟键盘，最稳，触发 React 事件)
                # 之前的 fill 容易报错 "Node is not input"，改用 type
                try:
                    # 设置超时（毫秒），根据长度动态调整
                    timeout_ms = max(30000, len(prompt) * 10)
                    await tb.type(prompt, delay=0, timeout=timeout_ms)
                    self._log(f"send: typed prompt (timeout={timeout_ms/1000:.1f}s)")
                except Exception as e:
                    self._log(f"send: type() failed ({e}), trying JS injection...")
                    # 策略 B: JS 注入 (最快，但可能不触发事件，需要后续处理)
                    try:
                        # 确保元素可见和可交互
                        await tb.wait_for(state="visible", timeout=5000)
                        # JSON.stringify 处理转义字符
                        import json
                        js_code = f"el => el.innerText = {json.dumps(prompt)}"
                        await asyncio.wait_for(
                            tb.evaluate(js_code),
                            timeout=5.0
                        )
                        # 注入后必须触发 input 事件，否则发送按钮可能不亮
                        await asyncio.wait_for(
                            tb.evaluate("el => el.dispatchEvent(new Event('input', {bubbles: true}))"),
                            timeout=3.0
                        )
                        self._log("send: injected via JS + triggered input event")
                    except Exception as js_err:
                        self._log(f"send: JS injection also failed: {js_err}")
                        raise  # 如果 JS 注入也失败，抛出异常触发重试

                await asyncio.sleep(0.5)

                # --- 验证内容 ---
                # 获取内容用于验证
                try:
                    actual = await asyncio.wait_for(tb.inner_text(), timeout=3)
                except Exception:
                    try:
                        actual = await asyncio.wait_for(tb.text_content(), timeout=3) or ""
                    except Exception:
                        actual = ""
                
                actual_clean = (actual or "").strip()
                prompt_clean = prompt.strip()
                
                if len(actual_clean) < len(prompt_clean) * 0.8:
                    self._log(f"send: mismatch (expected~={len(prompt_clean)}, actual={len(actual_clean)}). Retrying...")
                    continue  # 触发下一次重试
                else:
                    self._log(f"send: content verified OK (len={len(actual_clean)})")
                    prompt_sent = True
                    break
                    
            except Exception as e:
                self._log(f"send: attempt {attempt+1} error: {e}")
                await asyncio.sleep(1)

        if not prompt_sent:
            raise RuntimeError("send: failed to enter prompt after retries")

        # 4. --- [关键修复] 发送逻辑升级 ---
        self._log("send: triggering send...")

        # 步骤 1: 尝试 Enter (最快)
        try:
            await tb.press("Enter", timeout=3000)
            self._log("send: Enter pressed")
        except Exception:
            pass
        
        # 给一点反应时间，如果 Enter 生效了，页面会刷新，下面的逻辑其实无害
        await asyncio.sleep(0.5)

        # 步骤 2: 尝试 Control+Enter (日志证明这是救世主)
        # 很多时候 Enter 只是换行，Ctrl+Enter 才是强制提交
        try:
            self._log("send: trying Control+Enter (reliable fallback)...")
            await tb.press("Control+Enter", timeout=3000)
            await asyncio.sleep(0.5)
            self._log("send: Control+Enter pressed")
        except Exception:
            pass

        # 步骤 3: 最后才找按钮 (最慢，最容易失败)
        # 只有当前面两个都不行时，才去 DOM 里挖按钮
        for send_sel in self.SEND_BTN:
            try:
                btn = frame.locator(send_sel).first
                if await btn.is_visible(timeout=1000):
                    self._log(f"send: clicking send button {send_sel}...")
                    await btn.click(timeout=3000)
                    self._log(f"send: clicked send button {send_sel}")
                    return
            except Exception:
                continue
        
        # 如果所有方法都尝试过了，不报错（因为 Enter 或 Control+Enter 可能已经生效）
        self._log("send: all send methods attempted (Enter/Control+Enter/Button)")

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