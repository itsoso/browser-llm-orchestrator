# -*- coding: utf-8 -*-
"""
Author: xiaofan with Codex Cusor
Created: 2025-12-29 20:27:11 +0800
Modified: 2025-12-31 19:09:41 +0800
"""
# rpa_llm/adapters/chatgpt.py
from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Optional, Tuple

from playwright.async_api import Frame, Locator, Error as PlaywrightError

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
    # 优化：提升阈值到 2000，短 prompt 使用 fill/execCommand 更快更稳
    # 对于 344 chars 这样的短 prompt，不应该走 JS injection 路径
    # 短 prompt 应该使用：textarea -> fill(), contenteditable -> execCommand('insertText') 或 type()
    JS_INJECT_THRESHOLD = 2000

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

    async def _fast_ready_check(self) -> bool:
        """
        Fast-path textbox check to avoid expensive frame scans on already-loaded pages.
        """
        try:
            loc = self.page.locator('div[id="prompt-textarea"]').first
            if await loc.count() > 0 and await loc.is_visible():
                return True
        except Exception:
            pass
        try:
            loc = self.page.locator('div[contenteditable="true"][role="textbox"]').first
            if await loc.count() > 0 and await loc.is_visible():
                return True
        except Exception:
            pass
        return False

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
        # 快速路径：直接用最稳定的选择器探测
        try:
            loc = self.page.locator('div[id="prompt-textarea"]').first
            if await loc.count() > 0 and await loc.is_visible():
                self._log("ensure_ready: fast path via prompt-textarea")
                return
        except Exception:
            pass

        # Cloudflare 直接进入人工点一次（但支持 auto-continue）
        if await self._is_cloudflare():
            await self.manual_checkpoint(
                "检测到 Cloudflare 人机验证页面，请人工完成验证。",
                ready_check=self._ready_check_textbox,
                max_wait_s=90,
            )

        if await self._fast_ready_check():
            self._log("ensure_ready: fast-path textbox visible")
            return

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
        """
        获取 assistant 消息数量，使用并行执行优化性能。
        如果所有选择器都失败，返回 0。
        优化：减少单个选择器超时，从3秒减少到1.5秒，提升响应速度
        """
        async def try_selector(sel: str) -> Optional[int]:
            """尝试单个选择器，返回计数或 None"""
            try:
                # 优化：减少单个选择器超时，从3秒减少到1.5秒，提升响应速度
                return await asyncio.wait_for(
                    self.page.locator(sel).count(),
                    timeout=1.5
                )
            except (asyncio.TimeoutError, Exception):
                return None
        
        # 并行尝试所有选择器，取第一个成功的结果
        tasks = [try_selector(sel) for sel in self.ASSISTANT_MSG]
        # 优化：添加总超时保护，整个方法最多等待2秒
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=2.0
            )
        except asyncio.TimeoutError:
            # 如果总超时，返回0（表示未找到）
            return 0
        
        # 找到第一个成功的结果（非 None，非异常）
        for result in results:
            if result is not None and not isinstance(result, Exception):
                return result
        
        return 0

    async def _user_count(self) -> int:
        """
        获取用户消息数量，使用并行执行优化性能。
        如果所有选择器都失败，返回 0。
        优化：减少单个选择器超时，从3秒减少到1.5秒，提升响应速度
        """
        async def try_selector(sel: str) -> Optional[int]:
            """尝试单个选择器，返回计数或 None"""
            try:
                # 优化：减少单个选择器超时，从3秒减少到1.5秒，提升响应速度
                return await asyncio.wait_for(
                    self.page.locator(sel).count(),
                    timeout=1.5
                )
            except (asyncio.TimeoutError, Exception):
                return None
        
        # 并行尝试所有选择器，取第一个成功的结果
        tasks = [try_selector(sel) for sel in self.USER_MSG]
        # 优化：添加总超时保护，整个方法最多等待2秒
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=2.0
            )
        except asyncio.TimeoutError:
            # 如果总超时，返回0（表示未找到）
            return 0
        
        # 找到第一个成功的结果（非 None，非异常）
        for result in results:
            if result is not None and not isinstance(result, Exception):
                return result
        
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
    
    async def _get_assistant_text_by_index(self, index: int) -> str:
        """
        根据索引获取 assistant 消息文本（0-index）。
        当 assistant_count(after)=k 时，读取第 k-1 条消息（0-index）。
        
        Args:
            index: 消息索引（0-index），例如 assistant_count=3 时，index=2 表示最后一条消息
        
        Returns:
            消息文本，如果获取失败返回空字符串
        """
        if index < 0:
            return ""
        
        for sel in self.ASSISTANT_MSG:
            loc = self.page.locator(sel)
            try:
                cnt = await loc.count()
                if cnt > 0 and index < cnt:
                    # 使用索引定位，而不是 last
                    text = await loc.nth(index).inner_text()
                    if text:
                        return text.strip()
            except Exception:
                continue
        return ""

    async def _is_generating(self) -> bool:
        # 修复：使用并行检查，减少等待时间，避免 Future exception
        # 优化：显式捕获 TimeoutError，避免 Future exception
        async def check_stop(sel: str) -> bool:
            try:
                loc = self.page.locator(sel).first
                if await loc.count() > 0:
                    # 修复：使用 wait_for 而不是 is_visible，但设置短超时，避免 Future exception
                    try:
                        await loc.wait_for(state="visible", timeout=300)  # 300ms 超时
                        return True
                    except Exception:
                        return False
            except Exception:
                pass
            return False
        
        # 只检查前 2 个选择器，并行执行，总超时 0.5 秒
        try:
            tasks = [check_stop(sel) for sel in self.STOP_BTN[:2]]
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=0.5
            )
            for result in results:
                if isinstance(result, bool) and result:
                    return True
        except (asyncio.TimeoutError, Exception):
            # 显式捕获 TimeoutError，避免 Future exception
            pass
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
        1. 清理 prompt 中的换行符（避免 type() 将 \n 解释为 Enter）
        2. 使用 JS 强制清空 (解决 Node is not input 报错)
        3. 智能 fallback 输入 (type -> JS injection)
        4. 组合键发送优先 (解决按钮点击失败)
        """
        # 0. 清理 prompt 中的换行符（避免输入时触发 Enter）
        prompt = self.clean_newlines(prompt, logger=lambda msg: self._log(f"send: {msg}"))
        
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
                # manual_checkpoint 后再次尝试查找
                found = await self._find_textbox_any_frame()
                if not found:
                    # 如果 manual_checkpoint 后仍然找不到，抛出异常
                    raise RuntimeError("send: textbox not found after manual checkpoint")
        
        # 确保找到了 textbox
        if not found:
            raise RuntimeError("send: textbox not found after all retries")

        tb, frame, how = found
        self._log(f"send: textbox via {how} frame={frame.url}")

        # 记录发送前的用户消息数量，用于检测是否已经发送
        user_count_before_send = await self._user_count()
        self._log(f"send: user_count(before)={user_count_before_send}")

        # 2. 确保焦点（点击失败不致命，可能是被遮挡，JS 输入依然可能成功）
        try:
            await tb.click(timeout=5000)
        except Exception:
            pass

        # 3. 循环尝试写入 (最多 2 次)
        prompt_sent = False
        already_sent_during_input = False  # 标记是否在输入过程中已经发送
        for attempt in range(2):
            try:
                if attempt > 0:
                    self._log(f"send: attempt {attempt+1}, re-finding textbox and clearing...")
                    # 重试时重新查找元素（元素可能已变化）
                    await asyncio.sleep(1.5)  # 等待页面稳定
                    found_retry = await self._find_textbox_any_frame()
                    if found_retry:
                        tb, frame, how = found_retry
                        self._log(f"send: re-found textbox via {how}")
                    else:
                        self._log("send: textbox not found in retry, using original")
                
                # --- [关键修复] 强制清空逻辑（每次输入前都必须清空）---
                # 不要用 tb.fill("")，这在 div 上不稳定。直接用 JS 清空 DOM。
                # 必须在每次输入前清空，避免之前失败的输入影响
                self._log(f"send: clearing textbox before input (attempt {attempt+1})...")
                try:
                    # 确保元素可见和可交互，然后执行 evaluate（带超时）
                    # 使用 "attached" 状态更宽松，因为元素可能暂时不可见但已附加到 DOM
                    await tb.wait_for(state="attached", timeout=10000)
                    
                    # 优化：在 Thinking 模式渲染时，主线程可能太忙，先给页面一口喘息的机会
                    await asyncio.sleep(0.5)  # 等待 500ms，让页面稳定
                    
                    # 优化：使用统一的清空方法，优先用户等价操作（Meta/Control+A → Backspace）
                    # 对于短 prompt，只需清空一次即可，不需要多次循环
                    await self._tb_clear(tb)
                    await asyncio.sleep(0.1)  # 短暂等待，让清空生效
                    
                    # 验证是否真的清空了（使用统一的获取方法）
                    check_empty = await self._tb_get_text(tb)
                    if not check_empty.strip():
                        self._log("send: textbox cleared successfully")
                    else:
                        # 如果还有内容，再清空一次（最多2次）
                        self._log(f"send: textbox still has content after first clear, retrying...")
                        await self._tb_clear(tb)
                        await asyncio.sleep(0.1)
                        final_check = await self._tb_get_text(tb)
                        if final_check.strip():
                            self._log(f"send: warning - textbox still has content after clear: '{final_check[:50]}...'")
                        else:
                            self._log("send: textbox cleared successfully (after retry)")
                    
                    await asyncio.sleep(0.5)  # 等待 React 状态完全更新
                except Exception as e:
                    # 记录详细错误信息，包括异常类型、消息和堆栈信息
                    import traceback
                    error_msg = f"{type(e).__name__}: {str(e)}" if str(e) else f"{type(e).__name__} (no message)"
                    error_trace = traceback.format_exc()
                    self._log(f"send: JS clear failed: {error_msg}")
                    self._log(f"send: JS clear traceback: {error_trace[:200]}...")  # 只记录前200字符
                    # 清空失败不致命，继续尝试输入（但可能会影响结果）

                # --- 输入内容 ---
                prompt_len = len(prompt)
                self._log(f"send: writing prompt ({prompt_len} chars)...")
                
                # 策略：
                # 1. 对于超长 prompt (>3000 字符)，直接使用 JS 注入（更快更稳）
                # 2. 对于中等长度，使用 type() 但增加超时时间
                # 注意：prompt 已经在方法开始时清理了换行符，所以这里不需要再检查换行符
                use_js_inject = prompt_len > self.JS_INJECT_THRESHOLD
                type_success = False
                
                if use_js_inject:
                    self._log(f"send: using JS injection for speed (len={prompt_len})...")
                    try:
                        # 最终验证：确保 prompt 中没有任何换行符（JS 注入也需要清理）
                        prompt = self.clean_newlines(prompt, logger=lambda msg: self._log(f"send: {msg}"))
                        prompt_len = len(prompt)
                        
                        await tb.wait_for(state="attached", timeout=10000)
                        import json
                        # 优化：增强 JS 注入，触发所有关键事件以确保 React/Angular 状态同步
                        js_code = f"""
                        (el, text) => {{
                            el.focus();
                            // 兼容多种框架的输入方式
                            if (el.tagName === 'TEXTAREA' || el.contentEditable === 'true') {{
                                const fullText = {json.dumps(prompt)};
                                if (el.contentEditable === 'true') {{
                                    el.innerText = fullText;
                                }} else {{
                                    el.value = fullText;
                                }}
                                
                                // 关键：按顺序触发所有状态更新事件
                                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                el.dispatchEvent(new InputEvent('beforeinput', {{ bubbles: true, inputType: 'insertText', data: '' }}));
                                el.dispatchEvent(new KeyboardEvent('keydown', {{ bubbles: true, key: 'Enter' }}));
                                el.blur(); // 有时失焦能强制同步状态
                                el.focus(); // 重新聚焦，确保按钮状态更新
                            }}
                        }}
                        """
                        await asyncio.wait_for(
                            tb.evaluate(js_code),
                            timeout=20.0
                        )
                        await self._arm_input_events(tb)
                        self._log("send: injected via JS + triggered all input events (input/change/beforeinput/keydown/blur/focus)")
                        
                        # JS 注入后也检查是否已经发送
                        await asyncio.sleep(0.2)
                        try:
                            textbox_after_js = await asyncio.wait_for(tb.inner_text(), timeout=2) or ""
                            if len(textbox_after_js.strip()) < prompt_len * 0.7:
                                self._log(
                                    f"send: JS inject verification failed (len={len(textbox_after_js.strip())}/{prompt_len}), falling back to type()"
                                )
                                raise RuntimeError("JS inject verification failed")
                        except Exception as verify_err:
                            self._log(f"send: JS inject verification error: {verify_err}")
                            raise
                        try:
                            user_count_after_js = await self._user_count()
                            if user_count_after_js > user_count_before_send:
                                self._log(f"send: warning - prompt may have been sent during JS injection (user_count={user_count_after_js})")
                                # 检查输入框是否已清空
                                try:
                                    textbox_after_js = await asyncio.wait_for(tb.inner_text(), timeout=2) or ""
                                    if not textbox_after_js.strip() or len(textbox_after_js.strip()) < prompt_len * 0.1:
                                        self._log(f"send: confirmed - prompt was sent during JS injection")
                                        type_success = True
                                        prompt_sent = True
                                        already_sent_during_input = True
                                        break
                                except Exception:
                                    pass
                        except Exception:
                            pass

                        type_success = True
                    except Exception as js_err:
                        self._log(f"send: JS injection failed: {js_err}, trying type() as fallback...")
                        use_js_inject = False  # 如果 JS 注入失败，回退到 type()
                
                if not use_js_inject:
                    # 优化：短 prompt 使用轻量路径（fill/execCommand），避免 type() 的延迟
                    # 策略 A: 对于短 prompt，优先使用 _tb_set_text (fill/execCommand)
                    # 策略 B: 如果 _tb_set_text 失败，再尝试 type()
                    try:
                        # 确保元素可见和可交互
                        await tb.wait_for(state="attached", timeout=10000)
                        
                        # 最终验证：确保 prompt 中没有任何换行符（双重保险）
                        prompt = self.clean_newlines(prompt, logger=lambda msg: self._log(f"send: {msg}"))
                        prompt_len = len(prompt)
                        
                        # 优化：短 prompt 使用 _tb_set_text (fill/execCommand)，更快更稳
                        # 修复：提前初始化 timeout_ms，避免在异常情况下未定义
                        timeout_ms = max(60000, prompt_len * 50)  # 默认超时值
                        
                        try:
                            await self._tb_set_text(tb, prompt)
                            self._log(f"send: set text via _tb_set_text (len={prompt_len})")
                            type_success = True
                        except Exception as set_text_err:
                            # 如果 _tb_set_text 失败，fallback 到 type()
                            self._log(f"send: _tb_set_text failed ({set_text_err}), trying type()...")
                            
                            # 确保元素有焦点
                            try:
                                await tb.focus(timeout=3000)
                            except Exception:
                                pass  # focus 失败不致命
                            
                            # 设置超时（毫秒），根据长度动态调整
                            # 每字符至少 50ms，最小 60 秒（长 prompt 需要更多时间）
                            timeout_ms = max(60000, prompt_len * 50)
                        
                        # 在 type() 之前再次检查用户消息数量（防止在等待期间已发送）
                        try:
                            user_count_before_type = await self._user_count()
                            if user_count_before_type > user_count_before_send:
                                self._log(f"send: already sent before type() (user_count={user_count_before_type}), skipping type()")
                                type_success = True
                                prompt_sent = True
                                already_sent_during_input = True
                                break
                        except Exception:
                            pass
                        
                        # 只有在 type_success 为 False 时才尝试 type()
                        if not type_success:
                            try:
                                await tb.type(prompt, delay=0, timeout=timeout_ms)
                                self._log(f"send: typed prompt (timeout={timeout_ms/1000:.1f}s)")
                            except PlaywrightError as pe:
                                # 处理 Playwright 错误（包括 TargetClosedError）
                                if "TargetClosed" in str(pe) or "Target page" in str(pe):
                                    self._log(f"send: browser/page closed during type(), raising error")
                                    raise RuntimeError(f"Browser/page closed during input: {pe}") from pe
                                raise  # 其他 Playwright 错误继续抛出
                        
                        # type() 完成后立即检查是否已经发送（可能因为其他原因导致提前发送）
                        await asyncio.sleep(0.2)  # 减少等待时间，更快检测
                        try:
                            user_count_after_type = await self._user_count()
                            if user_count_after_type > user_count_before_send:
                                self._log(f"send: warning - prompt may have been sent during type() (user_count={user_count_after_type} > {user_count_before_send}), checking input box...")
                                # 检查输入框是否已清空（如果已清空，说明已发送）
                                try:
                                    textbox_after = await asyncio.wait_for(tb.inner_text(), timeout=2) or ""
                                    if not textbox_after.strip() or len(textbox_after.strip()) < prompt_len * 0.1:
                                        self._log(f"send: confirmed - prompt was sent during type() (textbox empty or nearly empty)")
                                        # 如果已发送，标记为成功，但需要跳过后续的发送操作
                                        type_success = True
                                        prompt_sent = True
                                        already_sent_during_input = True  # 标记已在输入过程中发送
                                        break  # 跳出输入循环，跳过验证，直接到发送检查
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        
                        type_success = True
                    except Exception as e:
                        error_str = str(e)
                        # 检查是否是超时错误
                        if "Timeout" in error_str or "timeout" in error_str.lower():
                            self._log(f"send: type() timeout ({e}), checking partial input...")
                        # 超时可能已经输入了一部分，先检查当前内容
                        try:
                            # 等待一下，让 React 状态更新
                            await asyncio.sleep(1.0)
                            partial = await asyncio.wait_for(tb.inner_text(), timeout=3) or ""
                            partial_len = len(partial.strip())
                            expected_len = len(prompt.strip())
                            partial_ratio = partial_len / expected_len if expected_len > 0 else 0
                            self._log(f"send: partial input detected (len={partial_len}/{expected_len}, ratio={partial_ratio:.2%})")
                            
                            # 如果输入了超过 95%，可能是超时但内容已完整，等待一下再验证
                            if partial_ratio >= 0.95:
                                self._log("send: partial input may be complete (>=95%), waiting for React update...")
                                # 等待更长时间，确保输入完全完成
                                await asyncio.sleep(2.0)  # 增加等待时间
                                # 再次检查，确保内容完整
                                final_check = await asyncio.wait_for(tb.inner_text(), timeout=3) or ""
                                final_len = len(final_check.strip())
                                final_ratio = final_len / expected_len if expected_len > 0 else 0
                                
                                # 检查开头和结尾是否匹配（防止中间截断）
                                final_check_clean = final_check.strip()
                                prompt_clean_check = prompt.strip()
                                start_match = final_check_clean[:50].strip() == prompt_clean_check[:50].strip() if len(final_check_clean) >= 50 and len(prompt_clean_check) >= 50 else True
                                end_match = final_check_clean[-50:].strip() == prompt_clean_check[-50:].strip() if len(final_check_clean) >= 50 and len(prompt_clean_check) >= 50 else True
                                
                                if final_ratio >= 0.95 and start_match and end_match:
                                    self._log(f"send: confirmed complete after wait (len={final_len}, ratio={final_ratio:.2%}, start_match={start_match}, end_match={end_match})")
                                    type_success = True  # 确认完整，继续验证
                                else:
                                    self._log(f"send: still incomplete after wait (len={final_len}, ratio={final_ratio:.2%}, start_match={start_match}, end_match={end_match}), will retry")
                                    # 清空后抛出异常触发重试（使用统一的清空方法）
                                    try:
                                        await self._tb_clear(tb)
                                        await asyncio.sleep(0.5)
                                    except Exception:
                                        pass
                                    raise RuntimeError(f"type() timeout: partial input incomplete (ratio={final_ratio:.2%}, start_match={start_match}, end_match={end_match})")
                            else:
                                # 输入不足 95%，对于短 prompt 不应该 fallback 到 JS injection
                                # 而是直接抛出异常触发重试
                                self._log(f"send: partial input insufficient (ratio={partial_ratio:.2%}), will retry")
                                try:
                                    await self._tb_clear(tb)
                                    await asyncio.sleep(0.3)
                                except Exception:
                                    pass
                                raise RuntimeError(f"type() failed: partial input insufficient (ratio={partial_ratio:.2%})")
                        except Exception as check_err:
                            self._log(f"send: failed to check partial input: {check_err}")
                            # 检查失败，清空后抛出异常触发重试
                            try:
                                await self._tb_clear(tb)
                            except Exception:
                                pass
                            raise  # 抛出异常触发重试

                        # 优化：对于短 prompt，如果 type() 失败，不要 fallback 到 JS injection
                        # 而是直接抛出异常触发重试，或者使用更轻量的方法
                        if not type_success:
                            if prompt_len < self.JS_INJECT_THRESHOLD:
                                # 短 prompt：type() 失败后，尝试再次使用 _tb_set_text
                                self._log(f"send: type() failed for short prompt ({e}), retrying _tb_set_text...")
                                try:
                                    await self._tb_clear(tb)
                                    await asyncio.sleep(0.2)
                                    await self._tb_set_text(tb, prompt)
                                    self._log(f"send: retry _tb_set_text successful (len={prompt_len})")
                                    type_success = True
                                except Exception as retry_err:
                                    self._log(f"send: _tb_set_text retry also failed ({retry_err}), will retry entire input")
                                    raise  # 抛出异常触发重试
                            else:
                                # 长 prompt：type() 失败后，才 fallback 到 JS injection
                                self._log(f"send: type() failed for long prompt ({e}), trying JS injection...")
                                try:
                                    # 确保元素可见和可交互
                                    await tb.wait_for(state="visible", timeout=5000)
                                    # JSON.stringify 处理转义字符
                                    import json
                                    js_code = f"el => el.innerText = {json.dumps(prompt)}"
                                    await asyncio.wait_for(
                                        tb.evaluate(js_code),
                                        timeout=20.0  # 增加到 20 秒
                                    )
                                    # 注入后必须触发 input 事件，否则发送按钮可能不亮
                                    await asyncio.wait_for(
                                        tb.evaluate("el => el.dispatchEvent(new Event('input', {bubbles: true}))"),
                                        timeout=10.0  # 增加到 10 秒
                                    )
                                    self._log("send: injected via JS + triggered input event")
                                    type_success = True
                                except Exception as js_err:
                                    self._log(f"send: JS injection also failed: {js_err}")
                                    raise  # 如果 JS 注入也失败，抛出异常触发重试

                # 等待输入完成和 React 状态更新
                await asyncio.sleep(1.0)  # 增加等待时间，确保输入完全完成

                # --- 验证内容 ---
                # 优化：对于短 prompt，简化验证逻辑，减少重试次数
                # 对于长 prompt（>1500 chars），使用更严格的验证
                is_short_prompt = prompt_len < self.JS_INJECT_THRESHOLD
                
                # 获取内容用于验证（短 prompt 只需一次读取，长 prompt 多次读取）
                actual = ""
                verify_attempts = 1 if is_short_prompt else 3
                for verify_attempt in range(verify_attempts):
                    try:
                        # 使用统一的 textbox 获取方法
                        actual = await self._tb_get_text(tb)
                        if actual:
                            break
                    except Exception:
                        pass
                    if verify_attempt < verify_attempts - 1:
                        wait_time = 0.3 if is_short_prompt else 0.8  # 短 prompt 等待时间更短
                        await asyncio.sleep(wait_time)
                
                actual_clean = (actual or "").strip()
                prompt_clean = prompt.strip()
                
                # 更严格的验证：不仅检查长度，还检查关键内容
                actual_len = len(actual_clean)
                expected_len = len(prompt_clean)
                len_ratio = actual_len / expected_len if expected_len > 0 else 0
                
                # 检查长度是否足够（至少 80% 即可接受，避免过度重试导致重复发送）
                # 如果内容已经达到 80%，即使不完全匹配，也接受（避免过度重试）
                if len_ratio < 0.80:
                    self._log(f"send: content mismatch - expected={expected_len}, actual={actual_len}, ratio={len_ratio:.2%}")
                    # 显示前 100 个字符用于调试
                    preview = actual_clean[:100] if actual_clean else "(empty)"
                    self._log(f"send: actual preview: {preview}...")
                    
                    # 在重试之前，检查是否已经有新的用户消息（如果有，说明已经发送了，不应该重试）
                    try:
                        user_count_now = await self._user_count()
                        if user_count_now > user_count_before_send:
                            self._log(f"send: warning - new user message detected (count={user_count_now} > {user_count_before_send}), content may have been sent already, accepting current input to avoid duplicate")
                            # 如果已经有新的用户消息，说明内容已经被发送了，不应该重试
                            prompt_sent = True
                            break
                    except Exception:
                        pass  # 检查失败不影响重试逻辑
                    
                    # 优化：对于短 prompt，如果内容不完整，只重新读取一次，不等待太长时间
                    if len_ratio < 0.80:
                        if is_short_prompt:
                            # 短 prompt：只等待 0.5 秒并重新读取一次
                            self._log(f"send: content incomplete (ratio={len_ratio:.2%}), re-reading once...")
                            await asyncio.sleep(0.5)
                            try:
                                actual_retry = await self._tb_get_text(tb)
                                actual_retry_clean = actual_retry.strip()
                                actual_retry_len = len(actual_retry_clean)
                                retry_ratio = actual_retry_len / expected_len if expected_len > 0 else 0
                                if retry_ratio >= 0.80:
                                    actual_clean = actual_retry_clean
                                    actual_len = actual_retry_len
                                    len_ratio = retry_ratio
                                    self._log(f"send: re-read successful (len={actual_len}, ratio={retry_ratio:.2%})")
                                else:
                                    len_ratio = retry_ratio
                            except Exception:
                                pass
                        else:
                            # 长 prompt：使用原有的复杂验证逻辑
                            # 根据不完整程度决定等待时间
                            if len_ratio < 0.5:
                                wait_time = 2.0  # 内容很少，等待更长时间
                            elif len_ratio < 0.8:
                                wait_time = 1.5  # 内容中等，等待中等时间
                            else:
                                wait_time = 1.0  # 内容接近完整，等待较短时间
                            
                            self._log(f"send: content incomplete (ratio={len_ratio:.2%}), waiting {wait_time}s and re-reading...")
                            await asyncio.sleep(wait_time)
                            
                            # 重新读取一次
                            try:
                                actual_retry = await self._tb_get_text(tb)
                                actual_retry_clean = actual_retry.strip()
                                actual_retry_len = len(actual_retry_clean)
                                retry_ratio = actual_retry_len / expected_len if expected_len > 0 else 0
                                
                                if retry_ratio >= 0.80:
                                    # 重新读取后内容达到 80%，使用新读取的内容
                                    actual_clean = actual_retry_clean
                                    actual_len = actual_retry_len
                                    len_ratio = retry_ratio
                                    self._log(f"send: re-read successful (len={actual_len}, ratio={retry_ratio:.2%})")
                                else:
                                    self._log(f"send: re-read still incomplete (len={actual_retry_len}, ratio={retry_ratio:.2%})")
                                    # 如果重新读取后仍然不完整，必须重试
                                    len_ratio = retry_ratio  # 更新为最新的比例
                            except Exception as re_read_err:
                                self._log(f"send: re-read failed: {re_read_err}")
                                # 读取失败，必须重试
                    
                    # 如果重新读取后仍然不完整（<80%），触发重试
                    if len_ratio < 0.80:
                        self._log(f"send: content still incomplete after re-read (ratio={len_ratio:.2%}), retrying...")
                        # 重试前确保彻底清空（防止两段内容叠加）
                        try:
                            # 优化：短 prompt 只需清空一次，长 prompt 多次清空
                            clear_attempts = 1 if is_short_prompt else 3
                            for clear_retry in range(clear_attempts):
                                await self._tb_clear(tb)
                                await asyncio.sleep(0.1 if is_short_prompt else 0.2)
                                # 验证是否清空（使用统一的获取方法）
                                check = await self._tb_get_text(tb)
                                if not check.strip():
                                    break
                            await asyncio.sleep(0.3 if is_short_prompt else 0.5)
                            self._log("send: cleared before retry")
                        except Exception:
                            pass
                        continue  # 触发下一次重试
                
                # 额外检查：验证开头和结尾是否匹配（防止中间截断）
                # 但如果内容已经达到 80%，即使开头/结尾不完全匹配，也接受（避免过度重试）
                if actual_clean and prompt_clean and len_ratio >= 0.80:
                    # 检查开头（前 50 个字符）
                    actual_start = actual_clean[:50].strip()
                    prompt_start = prompt_clean[:50].strip()
                    if actual_start != prompt_start:
                        self._log(f"send: content start mismatch - expected starts with '{prompt_start[:30]}...', got '{actual_start[:30]}...'")
                        # 如果内容已经达到 80%，即使开头不完全匹配，也接受（避免过度重试）
                        if len_ratio >= 0.80:
                            self._log(f"send: accepting despite start mismatch (ratio={len_ratio:.2%} >= 80%)")
                        else:
                            continue
                    
                    # 检查结尾（后 50 个字符）
                    actual_end = actual_clean[-50:].strip()
                    prompt_end = prompt_clean[-50:].strip()
                    if actual_end != prompt_end:
                        self._log(f"send: content end mismatch - expected ends with '...{prompt_end[-30:]}', got '...{actual_end[-30:]}'")
                        # 如果内容已经达到 80%，即使结尾不完全匹配，也接受（避免过度重试）
                        if len_ratio >= 0.80:
                            self._log(f"send: accepting despite end mismatch (ratio={len_ratio:.2%} >= 80%)")
                        else:
                            continue
                
                self._log(f"send: content verified OK (len={actual_len}, ratio={len_ratio:.2%})")
                prompt_sent = True
                break
                    
            except Exception as e:
                self._log(f"send: attempt {attempt+1} error: {e}")
                await asyncio.sleep(1)

        if not prompt_sent:
            raise RuntimeError("send: failed to enter prompt after retries")

        # 4. --- [关键修复] 发送逻辑升级 ---
        # 如果已经在输入过程中发送了，直接返回，不再触发发送
        if already_sent_during_input:
            self._log("send: prompt was already sent during input, skipping send trigger")
            return
        
        # 在发送前，再次检查是否已经发送（防止重复发送）
        try:
            user_count_before_trigger = await self._user_count()
            if user_count_before_trigger > user_count_before_send:
                self._log(f"send: already sent detected (user_count={user_count_before_trigger} > {user_count_before_send}), skipping send trigger")
                return  # 已经发送了，不需要再触发
        except Exception:
            pass  # 检查失败不影响发送逻辑
        
        self._log("send: triggering send...")
        send_phase_start = time.time()
        send_phase_max_s = float(os.environ.get("CHATGPT_SEND_PHASE_MAX_S", "8.0"))

        # 优化：提取辅助方法，减少重复代码
        # 优化：使用并行检查，加快检测速度
        async def check_if_sent(method_name: str) -> bool:
            """检查是否已经发送成功（多重检查：user_count、textbox cleared、stop button，并行执行）"""
            try:
                # 优化：并行检查多个信号，谁先成功就返回
                async def check_user_count():
                    try:
                        user_count_now = await asyncio.wait_for(self._user_count(), timeout=0.8)
                        if user_count_now > user_count_before_send:
                            return f"user_count={user_count_now}"
                    except (asyncio.TimeoutError, Exception):
                        pass
                    return None
                
                async def check_textbox_cleared():
                    try:
                        tb_loc = self.page.locator('div[id="prompt-textarea"]').first
                        if await tb_loc.count() > 0:
                            text_now = await asyncio.wait_for(self._tb_get_text(tb_loc), timeout=0.3)
                            if text_now is not None and text_now.strip() == "":
                                return "textbox_cleared"
                    except (asyncio.TimeoutError, Exception):
                        pass
                    return None
                
                async def check_stop_button():
                    try:
                        if await asyncio.wait_for(self._is_generating(), timeout=0.5):
                            return "stop_button_visible"
                    except (asyncio.TimeoutError, Exception):
                        pass
                    return None
                
                # 并行执行所有检查，总超时 1.0 秒
                results = await asyncio.wait_for(
                    asyncio.gather(
                        check_user_count(),
                        check_textbox_cleared(),
                        check_stop_button(),
                        return_exceptions=True
                    ),
                    timeout=1.0
                )
                
                # 检查结果，优先返回 user_count（最可靠）
                for i, result in enumerate(results):
                    if isinstance(result, str) and result:
                        signal_names = ["user_count", "textbox_cleared", "stop_button"]
                        self._log(f"send: confirmed sent via {method_name} ({signal_names[i]}: {result})")
                        return True
            except (asyncio.TimeoutError, Exception):
                pass
            return False

        # 优化：优先使用 Control+Enter（日志证明这是救世主，更可靠）
        # 很多时候 Enter 只是换行，Ctrl+Enter 才是强制提交
        # 步骤 1: 优先尝试 Control+Enter（最可靠）
        try:
            self._log("send: trying Control+Enter first (most reliable)...")
            await tb.press("Control+Enter", timeout=3000)
            await asyncio.sleep(0.15)  # 减少等待时间，从 0.2s 减少到 0.15s
            self._log("send: Control+Enter pressed")
            if await check_if_sent("Control+Enter"):
                return
        except Exception:
            pass

        # 快速检查 Control+Enter 是否生效（减少等待时间）
        await asyncio.sleep(0.2)  # 从 0.3s 减少到 0.2s
        if await check_if_sent("Control+Enter (after wait)"):
            return

        # 步骤 2: 如果 Control+Enter 没成功，尝试 Enter（作为备选）
        try:
            self._log("send: trying Enter as fallback...")
            await tb.press("Enter", timeout=3000)
            self._log("send: Enter pressed")
            await asyncio.sleep(0.15)  # 减少等待时间，从 0.2s 减少到 0.15s
            if await check_if_sent("Enter"):
                return
        except Exception:
            pass

        # 快速检查 Enter 是否生效
        await asyncio.sleep(0.2)  # 从 0.3s 减少到 0.2s
        if await check_if_sent("Enter (after wait)"):
            return

        if time.time() - send_phase_start >= send_phase_max_s:
            # 在跳过按钮前，最后检查一次是否已经发送
            if await check_if_sent("before skipping buttons"):
                return
            self._log(
                f"send: send phase reached {send_phase_max_s:.1f}s, skipping button attempts to reduce latency"
            )
            return

        # 步骤 3: 最后才找按钮 (最慢，最容易失败)
        # 只有当前面两个都不行时，才去 DOM 里挖按钮
        # 修复：在点击按钮之前，检查是否已经发送成功（防止重复点击）
        if await check_if_sent("before button attempts"):
            return
        
        for send_sel in self.SEND_BTN:
            if time.time() - send_phase_start >= send_phase_max_s:
                self._log(
                    f"send: send phase reached {send_phase_max_s:.1f}s, stopping button attempts"
                )
                return
            
            # 修复：在每次循环开始时，检查是否已经发送成功
            if await check_if_sent(f"before button {send_sel}"):
                return
            
            try:
                btn = frame.locator(send_sel).first
                # 修复：is_visible() 不接受 timeout 参数，改为 wait_for(state="visible")
                if await btn.count() > 0:
                    # 修复：在点击之前，显式检查按钮是否是"停止生成"按钮
                    try:
                        aria_label = await btn.get_attribute("aria-label") or ""
                        btn_text = await btn.inner_text() or ""
                        # 检查是否是停止按钮
                        if "停止" in aria_label or "Stop" in aria_label or "stop" in aria_label.lower():
                            self._log(f"send: button {send_sel} is a stop button (aria-label={aria_label}), skipping")
                            continue
                        if "停止" in btn_text or "Stop" in btn_text or "stop" in btn_text.lower():
                            self._log(f"send: button {send_sel} has stop text ({btn_text[:30]}), skipping")
                            continue
                    except Exception:
                        pass  # 检查失败不影响继续
                    
                    # 修复：在点击之前，再次检查是否已经发送成功
                    if await check_if_sent(f"just before clicking {send_sel}"):
                        return
                    
                    await btn.wait_for(state="visible", timeout=1000)
                    self._log(f"send: clicking send button {send_sel}...")
                    await btn.click(timeout=3000)
                    self._log(f"send: clicked send button {send_sel}")
                    # 按钮点击后也检查是否发送成功
                    await asyncio.sleep(0.3)
                    if await check_if_sent(f"button ({send_sel})"):
                        return
            except Exception:
                continue

        # 如果所有方法都尝试过了，不报错（因为 Enter 或 Control+Enter 可能已经生效）
        self._log("send: all send methods attempted (Enter/Control+Enter/Button)")

    async def ask(self, prompt: str, timeout_s: int = 480) -> Tuple[str, str]:
        """
        发送 prompt 并等待回复。
        
        使用整体超时保护，确保不会无限等待。
        超时后会抛出 TimeoutError 异常。
        """
        async def _ask_inner() -> Tuple[str, str]:
            ask_start_time = time.time()
            self._log(f"ask: start (timeout={timeout_s}s)")
            
            t_ready = time.time()
            await self.ensure_ready()
            self._log(f"ask: ensure_ready done ({time.time()-t_ready:.2f}s)")
            t_variant = time.time()
            await self.ensure_variant()
            self._log(f"ask: ensure_variant done ({time.time()-t_variant:.2f}s)")
            
            # 优化：针对 Thinking 模式的 DOM 稳定逻辑
            # ChatGPT 开启 Thinking 模式时，DOM 结构会从 Thinking 状态切换到 Text 状态
            # 等待页面不再有大面积的重绘（动画结束）
            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=5000)
                # 等待 Thinking 模式特有的加载图标消失或趋于稳定
                await asyncio.sleep(0.5)
                # 重新定位一次 Textbox，确保元素稳定
                found = await self._find_textbox_any_frame()
                if found:
                    tb, frame, how = found
                    try:
                        await tb.focus(timeout=2000)
                        self._log(f"ask: DOM stabilized, textbox refocused via {how}")
                    except Exception:
                        pass  # focus 失败不影响主流程
            except Exception as e:
                # 稳定化失败不影响主流程，记录日志即可
                self._log(f"ask: DOM stabilization warning: {e}")

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
            t_send = time.time()
            await self._send_prompt(prompt)
            self._log(f"ask: send phase done ({time.time()-t_send:.2f}s)")

            # 优化：移除冗余的 confirm phase，直接进入等待 assistant 消息
            # 策略：通过 assistant_count 增加或内容变动来反向推断发送成功
            # 实现"信号竞争"机制：同时监听多个信号（assistant_count、stop button、textbox cleared），谁先到就算谁
            self._log("ask: waiting for assistant message (using signal race confirmation)...")
            t1 = time.time()
            hb = t1
            elapsed = time.time() - ask_start_time
            remaining = timeout_s - elapsed
            assist_timeout_streak = 0
            # 优化：快速检查一次，确定超时时间
            try:
                n_assist_quick_check = await asyncio.wait_for(self._assistant_count(), timeout=0.8)
                if n_assist_quick_check > n_assist0:
                    assistant_wait_timeout = min(remaining * 0.3, 40)  # 最多40秒
                else:
                    assistant_wait_timeout = min(remaining * 0.4, 90)  # 最多90秒
            except Exception:
                assistant_wait_timeout = min(remaining * 0.4, 90)
            
            n_assist1 = n_assist0  # 初始化
            send_confirmed = False  # 标记是否已确认发送成功
            
            # 优化：实现"信号竞争"机制，同时监听多个信号，谁先到就算谁
            while time.time() - t1 < assistant_wait_timeout:
                elapsed = time.time() - ask_start_time
                if elapsed >= timeout_s - 30:
                    self._log(f"ask: timeout approaching (elapsed={elapsed:.1f}s/{timeout_s}s), breaking assistant wait")
                    break
                
                # 优化：并行检查多个信号，实现"信号竞争"机制
                async def check_assistant_count():
                    try:
                        return await asyncio.wait_for(self._assistant_count(), timeout=1.0)
                    except (asyncio.TimeoutError, Exception):
                        return None
                
                async def check_stop_button():
                    try:
                        if await self._is_generating():
                            return "stop_visible"
                    except (asyncio.TimeoutError, Exception):
                        pass
                    return None
                
                async def check_textbox_cleared():
                    try:
                        tb_loc = self.page.locator('div[id="prompt-textarea"]').first
                        if await tb_loc.count() > 0:
                            text_now = await asyncio.wait_for(self._tb_get_text(tb_loc), timeout=0.3)
                            if text_now is not None and text_now.strip() == "":
                                return "textbox_cleared"
                    except (asyncio.TimeoutError, Exception):
                        pass
                    return None
                
                async def check_text_change():
                    try:
                        text_quick = await asyncio.wait_for(self._last_assistant_text(), timeout=0.6)
                        if text_quick and text_quick != last_assist_text_before:
                            return "text_changed"
                    except (asyncio.TimeoutError, Exception):
                        pass
                    return None
                
                # 并行执行所有检查
                try:
                    results = await asyncio.wait_for(
                        asyncio.gather(
                            check_assistant_count(),
                            check_stop_button(),
                            check_textbox_cleared(),
                            check_text_change(),
                            return_exceptions=True
                        ),
                        timeout=1.2  # 总超时 1.2 秒
                    )
                    
                    # 检查 assistant_count（最可靠的信号）
                    if results[0] is not None and isinstance(results[0], int):
                        n_assist1 = results[0]
                        if n_assist1 > n_assist0:
                            self._log(f"ask: assistant_count(after)={n_assist1} (new message, signal: assistant_count)")
                            send_confirmed = True
                            break
                    
                    # 检查其他信号（stop button、textbox cleared、text change）
                    for i, result in enumerate(results[1:], 1):
                        if isinstance(result, str) and result:
                            signal_names = ["stop_button", "textbox_cleared", "text_change"]
                            self._log(f"ask: send confirmed via {signal_names[i-1]} (signal: {result})")
                            send_confirmed = True
                            # 如果检测到 stop button 或 textbox cleared，也尝试获取 assistant_count
                            if i <= 2:  # stop_button 或 textbox_cleared
                                try:
                                    n_assist1_check = await asyncio.wait_for(self._assistant_count(), timeout=0.8)
                                    if n_assist1_check > n_assist0:
                                        n_assist1 = n_assist1_check
                                        self._log(f"ask: assistant_count also increased to {n_assist1}")
                                        break
                                except Exception:
                                    pass
                            break
                except (asyncio.TimeoutError, Exception):
                    assist_timeout_streak += 1
                    # 如果并行检查超时，降级为顺序检查
                    try:
                        n_assist1 = await asyncio.wait_for(self._assistant_count(), timeout=1.0)
                        if n_assist1 > n_assist0:
                            self._log(f"ask: assistant_count(after)={n_assist1} (new message)")
                            send_confirmed = True
                            break
                    except (asyncio.TimeoutError, Exception):
                        assist_timeout_streak += 1
                        if assist_timeout_streak % 4 == 0:
                            self._log("ask: assistant_count() timeout, retrying...")
                
                # 超时过多，降级为文本变化检测
                if assist_timeout_streak >= 2 and not send_confirmed:
                    try:
                        text_quick = await asyncio.wait_for(self._last_assistant_text(), timeout=0.8)
                        if text_quick and text_quick != last_assist_text_before:
                            self._log("ask: assistant_count timeout streak, using text change as signal")
                            # 修复 Bug 1: 重新查询实际的 assistant_count
                            try:
                                n_assist1_actual = await asyncio.wait_for(self._assistant_count(), timeout=1.5)
                                if n_assist1_actual > n_assist0:
                                    n_assist1 = n_assist1_actual
                                    self._log(f"ask: re-queried assistant_count after timeout streak: {n_assist1} (actual)")
                                else:
                                    n_assist1 = n_assist0 + 1
                                    self._log(f"ask: re-query failed, using synthetic count: {n_assist1}")
                            except Exception:
                                n_assist1 = n_assist0 + 1
                                self._log(f"ask: re-query failed, using synthetic count: {n_assist1} (warning: may be inaccurate)")
                            send_confirmed = True
                            break
                    except Exception:
                        pass
                
                if time.time() - hb >= 10:
                    elapsed = time.time() - ask_start_time
                    self._log(f"ask: still waiting assistant message... (elapsed={elapsed:.1f}s/{timeout_s}s)")
                    hb = time.time()
                
                await asyncio.sleep(0.3)
            else:
                await self.save_artifacts("no_assistant_reply")
                await self.manual_checkpoint(
                    "发送后未等到回复（可能网络/风控/页面提示）。请检查页面是否需要操作。",
                    ready_check=self._ready_check_textbox,
                    max_wait_s=min(60, timeout_s - elapsed - 5),
                )
            self._log(f"ask: assistant wait done ({time.time()-t1:.2f}s)")

            # 修复 Bug 1: 在使用 n_assist1 计算 target_index 之前，重新查询实际的 assistant_count
            # 因为 n_assist1 可能是合成值（n_assist0 + 1），而实际可能有更多新消息
            # 这样可以确保 target_index 指向实际的最新消息，而不是过时的索引
            try:
                n_assist1_actual = await asyncio.wait_for(self._assistant_count(), timeout=2.0)
                if n_assist1_actual > n_assist0:
                    # 如果实际计数大于 n_assist0，使用实际计数
                    n_assist1 = n_assist1_actual
                    self._log(f"ask: using actual assistant_count for target_index: {n_assist1} (was {n_assist1} before re-query)")
                elif n_assist1 > n_assist0:
                    # 如果实际计数没有增加，但 n_assist1 已经 > n_assist0（可能是合成值），保持使用 n_assist1
                    self._log(f"ask: actual count unchanged ({n_assist1_actual}), keeping n_assist1={n_assist1}")
                else:
                    # 如果实际计数和 n_assist1 都没有增加，使用实际计数
                    n_assist1 = n_assist1_actual
                    self._log(f"ask: no new messages detected, using actual count: {n_assist1}")
            except Exception as e:
                # 重新查询失败，使用现有的 n_assist1（可能是合成值）
                self._log(f"ask: re-query assistant_count failed ({e}), using existing n_assist1={n_assist1}")

            # 优化：等待新消息的文本内容出现（使用索引定位而不是 last != before）
            # 当 assistant_count(after)=k 时，读取第 k-1 条 assistant 消息（0-index）
            self._log("ask: waiting for new message content (using index-based detection)...")
            t2 = time.time()
            hb = t2
            new_message_found = False
            elapsed = time.time() - ask_start_time
            remaining = timeout_s - elapsed
            
            # 优化：如果 assistant_count 已经增加，减少超时时间
            if n_assist1 > n_assist0:
                content_wait_timeout = min(3, remaining * 0.08)  # 最多3秒或剩余时间的8%
            else:
                content_wait_timeout = min(8, remaining * 0.12)  # 最多8秒或剩余时间的12%
            
            # 优化：使用索引定位，当 assistant_count(after)=k 时，读取第 k-1 条消息（0-index）
            # 这样可以避免读到空文本或旧节点
            # 修复 Bug 1: 现在 n_assist1 已经是最新的实际计数，可以安全地计算 target_index
            target_index = n_assist1 - 1  # 最后一条消息的索引（0-index）
            if target_index >= 0:
                # 快速路径：先快速检查一次，如果已经有新内容，直接跳过
                try:
                    current_text_quick = await asyncio.wait_for(
                        self._get_assistant_text_by_index(target_index),
                        timeout=0.8
                    )
                    if current_text_quick and current_text_quick != last_assist_text_before:
                        new_message_found = True
                        self._log(f"ask: new message content detected quickly via index {target_index} (len={len(current_text_quick)})")
                except Exception:
                    pass  # 快速检查失败，继续正常流程
                
                # 如果快速检查未成功，继续等待
                if not new_message_found:
                    while time.time() - t2 < content_wait_timeout:
                        elapsed = time.time() - ask_start_time
                        if elapsed >= timeout_s - 10:  # 留10秒给稳定等待
                            break
                        try:
                            # 使用索引定位，确保读取的是新消息
                            current_text = await asyncio.wait_for(
                                self._get_assistant_text_by_index(target_index),
                                timeout=1.2
                            )
                            if current_text and current_text != last_assist_text_before:
                                new_message_found = True
                                self._log(f"ask: new message content detected via index {target_index} (len={len(current_text)})")
                                break
                        except asyncio.TimeoutError:
                            pass
                        except Exception as e:
                            self._log(f"ask: _get_assistant_text_by_index({target_index}) error: {e}")
                            
                        if time.time() - hb >= 5:
                            self._log(f"ask: still waiting for new message content (index {target_index})... (elapsed={elapsed:.1f}s/{timeout_s}s)")
                            hb = time.time()
                        # 优化：减少等待间隔，从0.3秒减少到0.2秒，加快检测速度
                        await asyncio.sleep(0.2)
            else:
                # 如果 target_index < 0，fallback 到旧的 _last_assistant_text 方法
                self._log("ask: warning - target_index < 0, falling back to _last_assistant_text")
                try:
                    current_text_quick = await asyncio.wait_for(self._last_assistant_text(), timeout=0.8)
                    if current_text_quick and current_text_quick != last_assist_text_before:
                        new_message_found = True
                        self._log(f"ask: new message content detected quickly (len={len(current_text_quick)})")
                except Exception:
                    pass
            
            if not new_message_found:
                self._log("ask: warning: new message content not confirmed, but continuing...")
            self._log(f"ask: content wait done ({time.time()-t2:.2f}s)")

            # 等待输出稳定
            self._log("ask: waiting output stabilize...")
            # 优化：减少稳定时间，从2.0秒减少到1.5秒
            stable_seconds = 1.5
            last_text = ""
            last_change = time.time()
            hb = time.time()
            # 优化：用于检测文本是否在增长（局部变量，避免实例属性污染）
            last_text_len_history = []

            while time.time() - ask_start_time < timeout_s:
                elapsed = time.time() - ask_start_time
                remaining = timeout_s - elapsed
                
                if remaining <= 0:
                    break
                    
                try:
                    # 优化：使用索引定位，确保获取的是新消息（不是发送前的旧消息）
                    # 当 assistant_count 增加时，读取最后一条消息（索引 = n_assist_current - 1）
                    n_assist_current = await self._assistant_count()
                    if n_assist_current > n_assist0:
                        target_index = n_assist_current - 1  # 最后一条消息的索引（0-index）
                        text = await asyncio.wait_for(
                            self._get_assistant_text_by_index(target_index),
                            timeout=2.0
                        )
                    else:
                        # 如果 assistant_count 没有增加，fallback 到 _last_assistant_text
                        text = await asyncio.wait_for(self._last_assistant_text(), timeout=2.0)
                    
                    # 确保获取的是新消息（不是发送前的旧消息）
                    if text and text != last_assist_text_before:
                        if text != last_text:
                            last_text = text
                            last_change = time.time()
                            self._log(f"ask: text updated (len={len(last_text)}, remaining={remaining:.1f}s)")

                    # 优化（P1）：不要只依赖 Stop 按钮判断 generating
                    # 如果文本长度在增加，或者 assistant_count 增加了，即使 Stop 按钮没抓到，也要认为 generating=True
                    try:
                        generating = await asyncio.wait_for(self._is_generating(), timeout=0.8)  # 减少超时
                    except Exception:
                        generating = False
                    
                    # 补充逻辑：如果最后一条消息不是空的，且正在变长，那也是在 generating
                    if not generating and last_text:
                        last_text_len = len(last_text)
                        # 如果文本长度在增加，强制认为 generating=True
                        if last_text_len > 0:
                            # 检查文本是否在增长（通过比较当前长度和历史长度）
                            last_text_len_history.append((time.time(), last_text_len))
                            # 只保留最近 3 次记录
                            last_text_len_history[:] = last_text_len_history[-3:]
                            # 如果最近 2 次记录显示长度在增加，认为正在生成
                            if len(last_text_len_history) >= 2:
                                prev_len = last_text_len_history[-2][1]
                                if last_text_len > prev_len:
                                    generating = True
                                    self._log(f"ask: text growing ({prev_len}->{last_text_len}), forcing generating=True")
                    
                    # 优化：如果 last_len=0 且 generating=False，不要 sleep 太久，保持高频检查
                    if not last_text and not generating:
                        # 还在等待首字，保持高频检查（0.2秒）
                        await asyncio.sleep(0.2)
                        continue
                    
                    # 优化：添加快速路径 - 如果generating=False且文本长度在0.5秒内没有变化，直接认为稳定
                    time_since_change = time.time() - last_change
                    if last_text and (not generating) and time_since_change >= 0.5 and time_since_change >= stable_seconds:
                        elapsed = time.time() - ask_start_time
                        self._log(f"ask: done (stabilized, total={elapsed:.1f}s, fast path: {time_since_change:.1f}s no change)")
                        return last_text, self.page.url
                    # 原有逻辑：稳定时间达到且不在生成
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
                        generating = await asyncio.wait_for(self._is_generating(), timeout=0.8)  # 减少超时
                    except (asyncio.TimeoutError, Exception):
                        # 显式捕获 TimeoutError，避免 Future exception
                        generating = False
                    self._log(f"ask: generating={generating}, last_len={len(last_text)}, remaining={remaining:.1f}s ...")
                    hb = time.time()

                # 优化：减少检查间隔，从0.6秒减少到0.4秒，加快检测速度
                await asyncio.sleep(0.4)

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
