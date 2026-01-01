# -*- coding: utf-8 -*-
"""
Author: xiaofan with Codex Cusor
Created: 2025-12-29 20:27:11 +0800
Modified: 2025-12-30 18:37:59 +0800
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional, Tuple

from playwright.async_api import Frame, Locator

from ..utils import beijing_now_iso
from .base import SiteAdapter


class GeminiAdapter(SiteAdapter):
    site_id = "gemini"
    base_url = "https://gemini.google.com/app"  # 使用 /app 路径更稳定

    # --- 核心选择器优化（适配 Google Gemini 2025）---
    
    # 输入框：Gemini 使用富文本编辑器（contenteditable div）
    # ⚠️ 重要：不要包含 'textarea'，那是隐藏元素，会被遮挡导致点击超时！
    # 可见的输入框是 div[contenteditable="true"]，textarea 只用于底层表单提交
    TEXTBOX_CSS = [
        # 优先级 1: 最稳的富文本编辑器属性（唯一真神）
        'div[contenteditable="true"]',
        
        # 优先级 2: 具体的富文本结构 (Gemini 特有)
        'rich-textarea > div',
        'rich-textarea > div > p',
        
        # 优先级 3: 属性路径
        'div[data-placeholder="Enter a prompt here"]',
        'div[data-placeholder*="Enter"]',
        'div[data-placeholder*="输入"]',
        
        # 优先级 4: 语义化 (Role) - 可能匹配到其他元素，放最后
        'div[role="textbox"]',
        
        # ⚠️ 绝对不要包含 'textarea' 标签，那是隐藏元素，会卡死脚本！
    ]

    # 发送按钮：Gemini 的发送按钮通常带 aria-label
    SEND_BTN = [
        'button[aria-label*="Send"]',
        'button[aria-label*="发送"]',
        'button.send-button',
        'button:has-text("Send")',
        'button:has-text("发送")',
        'button > span.send-icon',  # 某些版本的图标结构
    ]

    # 新聊天按钮
    NEW_CHAT = [
        'div[data-test-id="new-chat-button"]',
        'button[aria-label*="New chat"]',
        'button[aria-label*="新对话"]',
        "a:has-text('New chat')",
        "button:has-text('New chat')",
        "a:has-text('新对话')",
        "button:has-text('新对话')",
    ]

    # 结果容器：用于判断生成结束
    ASSISTANT_MSG = [
        'model-response',  # 自定义标签
        '.model-response-text',  # 类名
        'div[data-test-id="model-response"]',
        "[data-response='true']",
        "div.markdown",
        "div:has(> p)",
    ]

    # 干扰弹窗：这是 Gemini 最容易报错的原因（欢迎页、更新提示、GDPR）
    POPUPS = [
        'button[aria-label*="Close"]',  # 关闭按钮
        'button[aria-label*="关闭"]',
        'button:has-text("No thanks")',  # 拒绝更新
        'button:has-text("不，谢谢")',
        'button:has-text("Got it")',  # 知道了
        'button:has-text("知道了")',
        'button:has-text("Chat")',  # 欢迎页的 "开始聊天"
        'button:has-text("开始")',
        'button:has-text("Accept")',  # 接受条款
        'button:has-text("接受")',
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

    async def _find_textbox_any_frame(self) -> Optional[Tuple[Locator, Frame, str]]:
        """优化版：优先检查主 Frame，使用最快选择器"""
        mf = self.page.main_frame
        
        # 1. 优先检查主 Frame（最快路径）
        # ⚠️ 只检查 contenteditable，不检查 textarea（textarea 是隐藏的，会被遮挡）
        try:
            # 快速检查 contenteditable（唯一可交互的元素）
            result = await self._try_find_in_frame(mf, "div[contenteditable='true']", "main_frame_contenteditable")
            if result:
                return result
        except Exception:
            pass
        
        # 2. 如果主 Frame 没找到，再检查其他选择器
        for sel in self.TEXTBOX_CSS:
            try:
                loc = mf.locator(sel).first
                try:
                    await loc.wait_for(state="attached", timeout=500)
                    if await asyncio.wait_for(self._try_visible(loc), timeout=0.5):
                        return loc, mf, f"main_frame_{sel}"
                except (asyncio.TimeoutError, Exception):
                    pass
            except Exception:
                continue
        
        # 3. 如果主 Frame 都没找到，再遍历所有 frame（兜底）
        for frame in self._frames_in_priority():
            if frame == mf:
                continue
                
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

    async def _dismiss_popups(self) -> None:
        """
        主动清理 Gemini 的各种弹窗（关键：弹窗会遮挡输入框导致 is_visible=False）
        """
        for sel in self.POPUPS:
            try:
                loc = self.page.locator(sel).first
                if await loc.is_visible(timeout=500):  # 极短超时，不要死等
                    self._log(f"popup: dismissing {sel}")
                    await loc.click()
                    await asyncio.sleep(0.5)  # 等待动画消失
            except Exception:
                pass

    async def _find_textbox(self) -> Optional[Locator]:
        """
        查找输入框（先清理弹窗，因为弹窗会遮挡输入框导致 is_visible=False）
        优化：快速检查 contenteditable（最常见）
        """
        # 1. 先尝试清理弹窗
        await self._dismiss_popups()

        # 2. 快速检查 contenteditable（最常见，优先级最高）
        try:
            loc = self.page.locator('div[contenteditable="true"]').first
            if await loc.is_visible(timeout=500):
                self._log("_find_textbox: found via contenteditable (fast path)")
                return loc
        except Exception:
            pass

        # 3. 如果 contenteditable 没找到，遍历其他选择器
        for sel in self.TEXTBOX_CSS:
            # 跳过已经检查过的 contenteditable
            if sel == 'div[contenteditable="true"]':
                continue
            try:
                loc = self.page.locator(sel).first
                if await loc.is_visible(timeout=500):
                    self._log(f"_find_textbox: found via {sel}")
                    return loc
            except Exception:
                continue
        return None

    async def ensure_ready(self) -> None:
        """优化版：处理弹窗、登录检测、输入框定位"""
        self._log("ensure_ready: start")
        await asyncio.sleep(0.5)

        # 检查是否在登录页
        if "accounts.google.com" in self.page.url:
            async def _check_logged_in() -> bool:
                tb = await self._find_textbox()
                return tb is not None
            
            await self.manual_checkpoint(
                "检测到未登录 (Google Login)，请手动登录。",
                ready_check=_check_logged_in,
                max_wait_s=120,
            )

        # 尝试寻找输入框
        t0 = time.time()
        check_count = 0
        while time.time() - t0 < 30:  # 30秒等待
            tb = await self._find_textbox()
            if tb:
                self._log(f"ensure_ready: textbox found (took {time.time()-t0:.2f}s)")
                return

            check_count += 1
            # 没找到，可能是页面还没加载完，或者有顽固弹窗
            if check_count % 3 == 0:  # 每3次尝试关闭一次弹窗
                await self._dismiss_popups()
            
            if time.time() - t0 >= 5 and check_count % 5 == 0:
                self._log(f"ensure_ready: still locating textbox... (attempt {check_count})")
            
            await asyncio.sleep(0.5)

        # 兜底：人工介入
        await self.save_artifacts("gemini_ensure_ready_fail")
        
        async def _check_textbox_ready() -> bool:
            tb = await self._find_textbox()
            return tb is not None
        
        await self.manual_checkpoint(
            "无法找到输入框（可能有新版弹窗或未登录）。",
            ready_check=_check_textbox_ready,
            max_wait_s=120,
        )
        
        # 人工处理后再确认一次
        tb = await self._find_textbox()
        if not tb:
            await self.save_artifacts("ensure_ready_failed_after_manual")
            raise RuntimeError("ensure_ready: still cannot locate textbox after manual checkpoint.")

    async def new_chat(self) -> None:
        """Gemini 的新聊天通常在侧边栏或直接访问 base_url"""
        try:
            # 尝试点击新聊天按钮
            for sel in self.NEW_CHAT:
                try:
                    btn = self.page.locator(sel).first
                    if await btn.is_visible(timeout=1000):
                        await btn.click()
                        await asyncio.sleep(1.0)
                        return
                except Exception:
                    continue
            # 备选：直接刷新页面通常就是新会话
            await self.page.goto(self.base_url)
            await asyncio.sleep(1.0)
        except Exception:
            pass

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
        # 0. 清理 prompt 中的换行符（避免输入时触发 Enter）
        prompt = self.clean_newlines(prompt, logger=lambda msg: self._log(f"ask: {msg}"))
        
        await self.ensure_ready()
        await self.new_chat()

        # --- 1. 寻找输入框 ---
        tb = await self._find_textbox()
        if not tb:
            raise RuntimeError("Gemini textbox lost after ensure_ready")

        self._log("ask: filling prompt...")
        
        # 再次清理弹窗（new_chat 后可能有新弹窗）
        await self._dismiss_popups()

        # --- 2. 聚焦 (强制点击) ---
        try:
            # [修正] timeout 是参数，不是方法
            await tb.wait_for(state="visible", timeout=10000)  # 先等待元素可见
            await tb.click(timeout=10000)  # 增加到 10 秒
            self._log("ask: clicked contenteditable (normal)")
        except Exception:
            self._log("ask: click failed, trying force click...")
            try:
                await tb.wait_for(state="attached", timeout=10000)  # 等待元素附加
                await tb.click(force=True, timeout=10000)  # 增加到 10 秒
                self._log("ask: clicked contenteditable (force)")
            except Exception:
                # 如果强制点击也失败，尝试直接聚焦
                self._log("ask: click failed, trying focus directly")
                try:
                    await tb.wait_for(state="attached", timeout=10000)
                    await tb.focus(timeout=10000)
                except Exception:
                    pass  # 聚焦失败也不致命

        # --- 3. 清空 (使用 JS，最稳) ---
        try:
            # 确保元素可见和可交互，然后执行 evaluate（带超时）
            await tb.wait_for(state="visible", timeout=15000)  # 增加到 15 秒
            await asyncio.wait_for(
                tb.evaluate("el => el.innerText = ''"),
                timeout=15.0  # 增加到 15 秒
            )
            await asyncio.sleep(0.2)
            self._log("ask: cleared contenteditable")
        except Exception as e:
            self._log(f"ask: JS clear failed: {e} (non-fatal)")
            # 清空失败不致命，继续尝试输入
        
        # --- 4. 输入内容 ---
        prompt_len = len(prompt)
        self._log(f"ask: inputting {prompt_len} chars...")
        
        # 策略：
        # 1. 对于超长 prompt (>3000 字符)，直接使用 JS 注入（更快更稳）
        # 2. 对于中等长度，使用 type() 但增加超时时间
        # 注意：prompt 已经在方法开始时清理了换行符，所以这里不需要再检查换行符
        use_js_inject = prompt_len > 3000
        
        if use_js_inject:
            self._log(f"ask: prompt too long ({prompt_len} chars), using JS injection for speed...")
            try:
                # 最终验证：确保 prompt 中没有任何换行符（JS 注入也需要清理）
                prompt = self.clean_newlines(prompt, logger=lambda msg: self._log(f"ask: {msg}"))
                prompt_len = len(prompt)
                
                # 确保元素可见和可交互
                await tb.wait_for(state="visible", timeout=15000)
                import json
                js_code = f"el => el.innerText = {json.dumps(prompt)}"
                await asyncio.wait_for(
                    tb.evaluate(js_code),
                    timeout=20.0  # 增加到 20 秒
                )
                # 必须触发 input 事件，否则发送按钮可能不亮
                await asyncio.wait_for(
                    tb.evaluate("el => el.dispatchEvent(new Event('input', {bubbles: true}))"),
                    timeout=10.0
                )
                self._log("ask: injected via JS + triggered input event")
                
                # JS 注入后也检查是否已经发送
                await asyncio.sleep(0.2)
                try:
                    textbox_after_js = await asyncio.wait_for(tb.inner_text(), timeout=2) or ""
                    if not textbox_after_js.strip() or len(textbox_after_js.strip()) < prompt_len * 0.1:
                        self._log(f"ask: warning - prompt may have been sent during JS injection (textbox empty or nearly empty)")
                except Exception:
                    pass
            except Exception as js_err:
                self._log(f"ask: JS injection failed: {js_err}, trying type() as fallback...")
                use_js_inject = False  # 如果 JS 注入失败，回退到 type()
        
        if not use_js_inject:
            # 使用 type 模拟键盘输入（delay=0 更快）
            try:
                # 计算动态超时：每字符 50ms + 60秒基数（与 ChatGPT 保持一致）
                type_timeout = max(60000, prompt_len * 50)
                
                await tb.type(prompt, delay=0, timeout=type_timeout)
                self._log(f"ask: typed prompt (len={prompt_len}, timeout={type_timeout/1000:.1f}s)")
                
            except Exception as e:
                self._log(f"ask: type failed ({e}), trying JS inject as fallback...")
                # Fallback: JS 注入
                try:
                    # 确保元素可见和可交互
                    await tb.wait_for(state="visible", timeout=15000)
                    import json
                    js_code = f"el => el.innerText = {json.dumps(prompt)}"
                    await asyncio.wait_for(
                        tb.evaluate(js_code),
                        timeout=20.0  # 增加到 20 秒
                    )
                    # 必须触发 input 事件，否则发送按钮可能不亮
                    await asyncio.wait_for(
                        tb.evaluate("el => el.dispatchEvent(new Event('input', {bubbles: true}))"),
                        timeout=10.0
                    )
                    self._log("ask: injected via JS + triggered input event (fallback)")
                except Exception as js_err:
                    self._log(f"ask: JS injection also failed: {js_err}")
                    raise  # 如果 JS 注入也失败，抛出异常
        
        await asyncio.sleep(0.5)
        
        # --- 5. 发送 (点击按钮 或 回车) ---
        # 记录发送前的输入框内容，用于检测是否已经发送
        try:
            textbox_content_before_send = await asyncio.wait_for(tb.inner_text(), timeout=2) or ""
            textbox_len_before_send = len(textbox_content_before_send.strip())
            self._log(f"send: textbox content before send (len={textbox_len_before_send})")
        except Exception:
            textbox_content_before_send = ""
            textbox_len_before_send = 0
        
        sent = False
        for btn_sel in self.SEND_BTN:
            try:
                btn = self.page.locator(btn_sel).first
                if await btn.is_visible(timeout=5000):  # 增加到 5 秒
                    await btn.click(timeout=10000)  # 增加到 10 秒
                    sent = True
                    self._log(f"ask: clicked send button {btn_sel}")
                    # 点击后等待一下，检查是否真的发送了
                    # 优化：使用多种方式检测，避免误报
                    await asyncio.sleep(0.5)
                    sent_confirmed = False
                    
                    # 方法1: 检查输入框内容是否清空（快速但可能不准确）
                    textbox_len_after = textbox_len_before_send  # 初始化
                    try:
                        textbox_content_after = await asyncio.wait_for(tb.inner_text(), timeout=2) or ""
                        textbox_len_after = len(textbox_content_after.strip())
                        if textbox_len_after < textbox_len_before_send * 0.3:  # 降低阈值到30%，更宽松
                            self._log(f"send: confirmed sent via textbox clear (len: {textbox_len_before_send} -> {textbox_len_after})")
                            sent_confirmed = True
                        elif textbox_len_after < textbox_len_before_send * 0.7:
                            # 内容部分减少，可能是发送了但页面还在处理
                            self._log(f"send: textbox partially cleared (len: {textbox_len_before_send} -> {textbox_len_after}), likely sent")
                            sent_confirmed = True
                        # 注意：如果输入框仍有内容，不立即输出警告，先进行响应检测
                    except Exception:
                        # 如果无法读取输入框，假设已发送（可能是页面正在刷新）
                        self._log("send: cannot read textbox after click, assuming sent")
                        sent_confirmed = True
                    
                    # 方法2: 先进行快速响应检测（0.8秒），如果成功就不输出警告
                    if not sent_confirmed:
                        try:
                            # 优化：先快速检查一次（0.8秒），如果检测到响应就不输出警告
                            await asyncio.sleep(0.8)
                            assistant_text_quick = await self._last_assistant_text()
                            assistant_len_quick = len(assistant_text_quick.strip()) if assistant_text_quick else 0
                            
                            # 如果响应长度明显增加（>5%），认为已发送
                            if assistant_len_quick > 20:  # 如果有足够长的响应，认为已发送
                                self._log(f"send: confirmed sent via quick response detection (len={assistant_len_quick})")
                                sent_confirmed = True
                        except Exception:
                            pass  # 快速检测失败，继续正常流程
                    
                    # 方法3: 等待更长时间，检查是否有响应开始（更可靠）
                    if not sent_confirmed:
                        try:
                            # 优化：由于已经等待了0.8秒，这里再等待1.5秒即可（总共2.3秒）
                            await asyncio.sleep(1.5)
                            # 优化：记录发送前的 assistant 文本，用于比较
                            assistant_text_before_send = await self._last_assistant_text()
                            assistant_len_before_send = len(assistant_text_before_send.strip()) if assistant_text_before_send else 0
                            
                            # 等待后再检查（检查响应是否在增长）
                            await asyncio.sleep(0.4)
                            assistant_text_after_1 = await self._last_assistant_text()
                            assistant_len_after_1 = len(assistant_text_after_1.strip()) if assistant_text_after_1 else 0
                            
                            # 再等待一次，检查响应是否在增长
                            await asyncio.sleep(0.4)
                            assistant_text_after_2 = await self._last_assistant_text()
                            assistant_len_after_2 = len(assistant_text_after_2.strip()) if assistant_text_after_2 else 0
                            
                            # 优化：检查文本是否变化，或响应是否在增长
                            if assistant_text_after_2 and assistant_text_after_2 != assistant_text_before_send:
                                # 文本发生变化，说明有新响应
                                self._log(f"send: confirmed sent via response detection (text changed, len={assistant_len_after_2})")
                                sent_confirmed = True
                            elif assistant_len_after_2 > assistant_len_before_send * 1.1:
                                # 响应长度明显增加（>10%），说明有新响应
                                self._log(f"send: confirmed sent via response growth (len: {assistant_len_before_send} -> {assistant_len_after_2}, +{assistant_len_after_2 - assistant_len_before_send})")
                                sent_confirmed = True
                            elif assistant_len_after_2 > assistant_len_after_1 * 1.05:
                                # 响应在增长（>5%），说明有新响应
                                self._log(f"send: confirmed sent via response growth (len: {assistant_len_after_1} -> {assistant_len_after_2}, growing)")
                                sent_confirmed = True
                            elif assistant_text_after_2 and len(assistant_text_after_2.strip()) > 20:  # 如果有足够长的响应，也认为已发送
                                self._log(f"send: confirmed sent via response detection (response_len={assistant_len_after_2})")
                                sent_confirmed = True
                        except Exception:
                            pass
                    
                    # 如果已确认发送，不输出误报警告
                    if sent_confirmed:
                        # 不输出误报警告，因为已经确认发送
                        break
                    else:
                        # 只有在所有检测都失败时才输出警告
                        # 注意：只有在输入框仍有内容（>=70%）且响应检测也失败时才输出警告
                        if textbox_len_after >= textbox_len_before_send * 0.7:
                            self._log(f"send: warning - textbox still has content after button click (len={textbox_len_after}), may not have sent")
                    # 即使未确认，也继续（可能已经发送但检测不到）
                    # 即使未确认，也继续（可能已经发送但检测不到）
            except Exception as e:
                self._log(f"send: button click failed for {btn_sel}: {e}")
                continue
        
        # 如果按钮点击没有成功，或者不确定是否发送了，检查输入框状态
        if not sent:
            # 再次检查输入框内容，确认是否已经发送
            try:
                textbox_content_check = await asyncio.wait_for(tb.inner_text(), timeout=2) or ""
                textbox_len_check = len(textbox_content_check.strip())
                if textbox_len_check < textbox_len_before_send * 0.5:  # 如果内容明显减少，说明已经发送了
                    self._log(f"send: detected already sent (textbox len: {textbox_len_before_send} -> {textbox_len_check}), skipping Enter")
                    sent = True
            except Exception:
                pass
        
        if not sent:
            self._log("ask: send button not found or not confirmed, pressing Enter")
            try:
                # 确保元素可见和可交互，然后按 Enter（带超时）
                await tb.wait_for(state="visible", timeout=15000)  # 增加到 15 秒
                await tb.press("Enter", timeout=10000)  # 增加到 10 秒
                self._log("ask: Enter pressed")
                # Enter 后也检查一下是否发送了
                await asyncio.sleep(0.5)
                try:
                    textbox_content_after_enter = await asyncio.wait_for(tb.inner_text(), timeout=2) or ""
                    textbox_len_after_enter = len(textbox_content_after_enter.strip())
                    if textbox_len_after_enter < textbox_len_before_send * 0.5:
                        self._log(f"send: confirmed sent via Enter (textbox len: {textbox_len_before_send} -> {textbox_len_after_enter})")
                        sent = True
                except Exception:
                    pass
            except Exception as e:
                self._log(f"ask: Enter press failed: {e}, trying Control+Enter...")
                try:
                    # 尝试 Control+Enter 作为备选
                    await tb.press("Control+Enter", timeout=10000)  # 增加到 10 秒
                    self._log("ask: Control+Enter pressed")
                    sent = True
                except Exception as e2:
                    self._log(f"ask: Control+Enter also failed: {e2}")
                    # 即使都失败了，也不抛出异常，因为可能已经发送了
                    if not sent:
                        raise RuntimeError(f"ask: both Enter and Control+Enter failed. Enter_err={e}, CtrlEnter_err={e2}")
            
        # --- 6. 等待回复 (流式检测) ---
        self._log("ask: waiting for response...")
        
        last_text = ""
        stable_iter = 0
        last_change_time = time.time()
        start_t = time.time()
        hb = start_t
        
        while time.time() - start_t < timeout_s:
            # 获取最后一个回复的文本（使用现有的 _last_text 方法）
            text = await self._last_text()
            
            if text and len(text) > 0:
                if len(text) > len(last_text):
                    # 还在生成
                    last_text = text
                    stable_iter = 0
                    last_change_time = time.time()  # 更新最后变化时间
                elif len(text) == len(last_text) and len(text) > 5:
                    # 长度稳定
                    stable_iter += 1
                    # 优化：动态调整稳定次数（短响应3次，长响应5次）
                    # 优化：如果响应长度在1秒内没有变化，直接认为稳定（快速路径）
                    time_since_change = time.time() - last_change_time
                    if time_since_change >= 1.0 and stable_iter >= 2:
                        # 快速路径：1秒内没有变化且已稳定2次，直接认为完成
                        self._log(f"ask: response stabilized (len={len(text)}, fast path: {time_since_change:.1f}s no change)")
                        return text, self.page.url
                    
                    # 根据响应长度动态调整稳定次数
                    required_stable_iter = 3 if len(text) < 500 else 5
                    if stable_iter >= required_stable_iter:
                        self._log(f"ask: response stabilized (len={len(text)}, stable_iter={stable_iter})")
                        return text, self.page.url
            
            if time.time() - hb >= 10:
                self._log(f"ask: waiting... (elapsed={time.time()-start_t:.1f}s, stable_iter={stable_iter}, last_len={len(last_text)})")
                hb = time.time()
            
            # 优化：减少检测间隔，从0.5秒减少到0.3秒，加快检测速度
            await asyncio.sleep(0.3)
            
        await self.save_artifacts("gemini_answer_timeout")
        raise TimeoutError(f"Gemini response timeout. last_len={len(last_text)}")
