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
    # ⚠️ 注意：需要排除"停止回答"按钮（也有 send-button 类）
    SEND_BTN = [
        'button[aria-label*="Send"]:not([aria-label*="停止"]):not([aria-label*="Stop"])',
        'button[aria-label*="发送"]:not([aria-label*="停止"])',
        'button.send-button:not([aria-label*="停止"]):not([aria-label*="Stop"]):not(.stop)',
        'button:has-text("Send"):not(:has-text("Stop"))',
        'button:has-text("发送"):not(:has-text("停止"))',
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
                # 修复：is_visible() 不接受 timeout 参数，改为 wait_for(state="visible")
                if await loc.count() > 0:
                    try:
                        await loc.wait_for(state="visible", timeout=500)  # 极短超时，不要死等
                        self._log(f"popup: dismissing {sel}")
                        await loc.click()
                        await asyncio.sleep(0.5)  # 等待动画消失
                    except Exception:
                        pass  # 超时或不可见，跳过
            except Exception:
                pass

    async def _find_textbox(self) -> Optional[Locator]:
        """
        查找输入框（先清理弹窗，因为弹窗会遮挡输入框导致 is_visible=False）
        优化：并行检查所有选择器，大幅提升性能
        """
        # 1. 先尝试清理弹窗（但减少频率，只在必要时清理）
        # 注意：这里不每次都清理，因为弹窗清理本身也需要时间
        
        # 2. 并行检查所有选择器（大幅提升性能）
        async def try_selector(sel: str) -> Optional[tuple]:
            """尝试单个选择器，返回 (Locator, selector) 或 None"""
            try:
                loc = self.page.locator(sel).first
                # 修复：is_visible() 不接受 timeout 参数，改为 wait_for(state="visible")
                if await loc.count() > 0:
                    try:
                        await loc.wait_for(state="visible", timeout=500)
                        return (loc, sel)
                    except Exception:
                        pass  # 超时或不可见，返回 None
            except Exception:
                pass
            return None
        
        # 并行尝试所有选择器，取第一个成功的结果
        tasks = [try_selector(sel) for sel in self.TEXTBOX_CSS]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 找到第一个成功的结果
        for result in results:
            if result is not None and not isinstance(result, Exception):
                loc, sel = result
                self._log(f"_find_textbox: found via {sel} (parallel)")
                return loc
        
        return None

    async def ensure_ready(self) -> None:
        """优化版：处理弹窗、登录检测、输入框定位，并行检查提升性能"""
        self._log("ensure_ready: start")
        # 优化：减少初始等待时间，从 0.5 秒减少到 0.2 秒
        await asyncio.sleep(0.2)

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

        # 优化：添加快速路径 - 如果页面已经加载完成，直接检查输入框
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=2000)
            # 页面已加载，直接尝试查找输入框
            tb = await self._find_textbox()
            if tb:
                self._log(f"ensure_ready: textbox found quickly (fast path)")
                return
        except Exception:
            pass  # 快速路径失败，继续正常流程

        # 尝试寻找输入框（优化：并行检查，减少等待时间）
        t0 = time.time()
        check_count = 0
        while time.time() - t0 < 30:  # 30秒等待
            tb = await self._find_textbox()
            if tb:
                self._log(f"ensure_ready: textbox found (took {time.time()-t0:.2f}s)")
                return

            check_count += 1
            # 优化：减少弹窗清理频率，从每3次改为每5次，减少不必要的操作
            if check_count % 5 == 0:  # 每5次尝试关闭一次弹窗
                await self._dismiss_popups()
            
            if time.time() - t0 >= 5 and check_count % 5 == 0:
                self._log(f"ensure_ready: still locating textbox... (attempt {check_count})")
            
            # 优化：前几次快速检查（0.2秒），之后逐渐增加间隔（0.5秒）
            sleep_time = 0.2 if check_count < 5 else 0.5
            await asyncio.sleep(sleep_time)

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
        """Gemini 的新聊天通常在侧边栏或直接访问 base_url，优化：添加明确的等待条件"""
        try:
            # 如果输入框可用，直接复用当前会话，避免 new_chat 开销
            try:
                tb = await self._find_textbox()
                if tb:
                    self._log("new_chat: textbox available, skip new_chat")
                    return
            except Exception:
                pass
            # 记录当前 URL，用于检测页面是否变化
            current_url = self.page.url
            
            # 尝试点击新聊天按钮
            for sel in self.NEW_CHAT:
                try:
                    btn = self.page.locator(sel).first
                    # 修复：is_visible() 不接受 timeout 参数，改为 wait_for(state="visible")
                    if await btn.count() > 0:
                        await btn.wait_for(state="visible", timeout=1000)
                        await btn.click()
                        # 优化：添加明确的等待条件，而不是固定 sleep
                        # 等待输入框重新出现或页面 URL 变化
                        t0 = time.time()
                        while time.time() - t0 < 5.0:  # 最多等待5秒
                            tb = await self._find_textbox()
                            if tb:
                                self._log(f"new_chat: textbox reappeared after {time.time()-t0:.2f}s")
                                return
                            # 检查 URL 是否变化
                            if self.page.url != current_url:
                                self._log(f"new_chat: URL changed after {time.time()-t0:.2f}s")
                                await asyncio.sleep(0.5)  # 等待页面加载
                                return
                            await asyncio.sleep(0.2)
                        # 如果5秒内没有检测到变化，使用较短的固定等待
                        await asyncio.sleep(0.5)
                        return
                except Exception:
                    continue
            # 备选：直接刷新页面通常就是新会话
            await self.page.goto(self.base_url)
            # 优化：等待页面加载完成，而不是固定 sleep
            await self.page.wait_for_load_state("domcontentloaded", timeout=3000)
            await asyncio.sleep(0.5)  # 额外等待，确保输入框出现
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

        t_ready = time.time()
        await self.ensure_ready()
        self._log(f"ask: ensure_ready done ({time.time()-t_ready:.2f}s)")
        t_chat = time.time()
        await self.new_chat()
        self._log(f"ask: new_chat done ({time.time()-t_chat:.2f}s)")

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
                # 优化：增强 JS 注入，触发所有关键事件以确保 React/Angular 状态同步
                js_code = """
                (el, text) => {
                    el.focus();
                    try {
                        document.execCommand('insertText', false, text);
                    } catch (e) {}
                    if (el.tagName === 'TEXTAREA' || el.contentEditable === 'true') {
                        if (el.contentEditable === 'true') {
                            el.innerText = text;
                        } else {
                            el.value = text;
                        }
                    }
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new KeyboardEvent('keydown', { key: ' ' }));
                    el.dispatchEvent(new KeyboardEvent('keyup', { key: ' ' }));
                    el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace' }));
                    el.dispatchEvent(new KeyboardEvent('keyup', { key: 'Backspace' }));
                    el.blur();
                    el.focus();
                }
                """
                await asyncio.wait_for(
                    tb.evaluate(js_code, prompt),
                    timeout=20.0  # 增加到 20 秒
                )
                self._log("ask: injected via JS + triggered input events (execCommand/keyboard)")
                
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
                    # 优化：增强 JS 注入，触发所有关键事件以确保 React/Angular 状态同步
                    js_code = """
                    (el, text) => {
                        el.focus();
                        try {
                            document.execCommand('insertText', false, text);
                        } catch (e) {}
                        if (el.tagName === 'TEXTAREA' || el.contentEditable === 'true') {
                            if (el.contentEditable === 'true') {
                                el.innerText = text;
                            } else {
                                el.value = text;
                            }
                        }
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        el.dispatchEvent(new KeyboardEvent('keydown', { key: ' ' }));
                        el.dispatchEvent(new KeyboardEvent('keyup', { key: ' ' }));
                        el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace' }));
                        el.dispatchEvent(new KeyboardEvent('keyup', { key: 'Backspace' }));
                        el.blur();
                        el.focus();
                    }
                    """
                    await asyncio.wait_for(
                        tb.evaluate(js_code, prompt),
                        timeout=20.0  # 增加到 20 秒
                    )
                    self._log("ask: injected via JS + triggered input events (fallback)")
                except Exception as js_err:
                    self._log(f"ask: JS injection also failed: {js_err}")
                    raise  # 如果 JS 注入也失败，抛出异常
        
        await asyncio.sleep(0.5)
        
        # --- 5. 发送 (点击按钮 或 回车) ---
        # 记录发送前的输入框内容，用于检测是否已经发送（使用统一的获取方法）
        try:
            textbox_content_before_send = await self._tb_get_text(tb)
            textbox_len_before_send = len(textbox_content_before_send.strip())
            self._log(f"send: textbox content before send (len={textbox_len_before_send})")
        except Exception:
            textbox_content_before_send = ""
            textbox_len_before_send = 0
        
        sent = False
        t_send = time.time()
        for btn_sel in self.SEND_BTN:
            # 修复：初始化 sent_confirmed，避免未定义错误
            sent_confirmed = False
            try:
                btn = self.page.locator(btn_sel).first
                # 修复：is_visible() 不接受 timeout 参数，改为 wait_for(state="visible")
                # 优化：等待按钮可见且稳定（不是加载中状态）
                if await btn.count() > 0:
                    await btn.wait_for(state="visible", timeout=5000)  # 增加到 5 秒
                    # 验证按钮确实是发送按钮，而不是停止按钮
                    try:
                        aria_label = await btn.get_attribute("aria-label") or ""
                        class_name = await btn.get_attribute("class") or ""
                        if "停止" in aria_label or "stop" in aria_label.lower() or "stop" in class_name.lower():
                            self._log(f"send: skipping stop button (aria-label='{aria_label}', class='{class_name}')")
                            continue  # 跳过停止按钮，尝试下一个选择器
                    except Exception:
                        pass  # 无法获取属性时，继续尝试点击
                    
                    # 优化：等待按钮进入可点击状态（不是 disabled）
                    try:
                        await btn.wait_for(state="visible", timeout=2000)
                        # 检查按钮是否被禁用
                        is_disabled = await btn.get_attribute("disabled") or await btn.get_attribute("aria-disabled")
                        if is_disabled == "true" or is_disabled is True:
                            self._log(f"send: button is disabled, waiting...")
                            await asyncio.sleep(0.5)
                            # 再次检查
                            is_disabled = await btn.get_attribute("disabled") or await btn.get_attribute("aria-disabled")
                            if is_disabled == "true" or is_disabled is True:
                                self._log(f"send: button still disabled, trying next selector")
                                continue
                    except Exception:
                        pass  # 检查失败不影响点击
                    
                    # 优化：添加 hover 操作，模拟真人点击前的准备，有助于唤醒前端监听
                    try:
                        await btn.hover(timeout=2000)
                        await asyncio.sleep(0.1)  # 短暂等待，让 hover 效果生效
                    except Exception:
                        pass  # hover 失败不影响点击
                    
                    # 优化：在点击前检查按钮状态（disabled 说明可能正在发送）
                    try:
                        is_disabled = await btn.get_attribute("disabled")
                        if is_disabled is not None:
                            self._log(f"ask: send button is disabled, may already be sending")
                    except Exception:
                        pass
                    
                    # 优化：尝试正常点击，使用 no_wait_after=True 避免等待潜在导航/网络 idle
                    try:
                        await btn.click(timeout=3000, no_wait_after=True)  # 使用 no_wait_after 避免等待
                        sent = True
                        self._log(f"ask: clicked send button {btn_sel}")
                    except Exception as click_err:
                        # 如果正常点击失败，尝试 force click
                        self._log(f"ask: normal click failed for {btn_sel}: {click_err}, trying force click...")
                        try:
                            await btn.click(force=True, timeout=3000, no_wait_after=True)  # 使用 force + no_wait_after
                            sent = True
                            self._log(f"ask: clicked send button {btn_sel} (force)")
                        except Exception as force_err:
                            # 如果 force click 也失败，尝试 JS 点击
                            self._log(f"ask: force click also failed for {btn_sel}: {force_err}, trying JS click...")
                            try:
                                await btn.evaluate("el => el.click()")
                                sent = True
                                self._log(f"ask: clicked send button {btn_sel} (JS)")
                            except Exception as js_err:
                                self._log(f"ask: JS click also failed for {btn_sel}: {js_err}")
                                # 即使所有点击方法都失败，也检查一下是否已经发送了（可能点击已经生效但 Playwright 没检测到）
                                await asyncio.sleep(0.5)
                                try:
                                    textbox_check = await self._tb_get_text(tb)
                                    textbox_len_check = len(textbox_check.strip())
                                    if textbox_len_check < textbox_len_before_send * 0.5:
                                        self._log(f"ask: detected sent despite click failure (textbox len: {textbox_len_before_send} -> {textbox_len_check})")
                                        sent = True
                                        sent_confirmed = True
                                        break  # 已发送，跳出循环
                                except Exception:
                                    pass
                                # 如果检测不到已发送，继续尝试下一个选择器
                                continue
                    
                    # 优化：点击后立即检查按钮是否变为 disabled（说明已发送）
                    try:
                        await asyncio.sleep(0.2)  # 给按钮状态一点时间更新
                        is_disabled_after = await btn.get_attribute("disabled")
                        if is_disabled_after is not None:
                            self._log(f"ask: send button became disabled after click, likely sent")
                            sent_confirmed = True
                    except Exception:
                        pass
                    
                    # 点击后等待一下，检查是否真的发送了
                    # 优化：使用多种方式检测，避免误报
                    if not sent_confirmed:
                        await asyncio.sleep(0.5)
                    
                    # 方法1: 检查输入框内容是否清空（快速但可能不准确，使用统一的获取方法）
                    textbox_len_after = textbox_len_before_send  # 初始化
                    try:
                        textbox_content_after = await self._tb_get_text(tb)
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
            # 再次检查输入框内容，确认是否已经发送（使用统一的获取方法）
            try:
                textbox_content_check = await self._tb_get_text(tb)
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
                # Enter 后也检查一下是否发送了（使用统一的获取方法）
                await asyncio.sleep(0.5)
                try:
                    textbox_content_after_enter = await self._tb_get_text(tb)
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
                    # Control+Enter 后也检查一下是否发送了
                    await asyncio.sleep(0.5)
                    try:
                        textbox_content_after_ctrl = await self._tb_get_text(tb)
                        textbox_len_after_ctrl = len(textbox_content_after_ctrl.strip())
                        if textbox_len_after_ctrl < textbox_len_before_send * 0.5:
                            self._log(f"send: confirmed sent via Control+Enter (textbox len: {textbox_len_before_send} -> {textbox_len_after_ctrl})")
                            sent = True
                    except Exception:
                        pass
                except Exception as e2:
                    self._log(f"ask: Control+Enter also failed: {e2}")
                    # 如果所有方法都失败，触发 manual checkpoint
                    await self.save_artifacts("gemini_send_failed")
                    await self.manual_checkpoint(
                        "Gemini 发送失败（按钮点击、Enter、Control+Enter 都失败）。请手动发送后继续。",
                        ready_check=lambda: True,  # 简单的 ready check
                        max_wait_s=60,
                    )
                    # manual checkpoint 后假设已发送
                    sent = True
            
        # --- 6. 等待回复 (流式检测) ---
        self._log(f"ask: send phase done ({time.time()-t_send:.2f}s)")
        self._log("ask: waiting for response...")
        t_resp = time.time()
        
        # 优化：记录发送前的最后一个响应文本长度，用于确保检测新响应
        try:
            last_text_before_send = await self._last_text()
            last_text_len_before_send = len(last_text_before_send.strip()) if last_text_before_send else 0
        except Exception:
            last_text_len_before_send = 0
        
        last_text = ""
        stable_iter = 0
        last_change_time = time.time()
        start_t = time.time()
        hb = start_t
        # 优化：添加一个阈值，允许文本长度有微小变化（±5字符）仍认为是稳定的
        LENGTH_TOLERANCE = 5
        
        while time.time() - start_t < timeout_s:
            # 获取最后一个回复的文本（使用现有的 _last_text 方法）
            try:
                text = await self._last_text()
            except Exception as e:
                # 优化：捕获异常，避免 Future exception was never retrieved
                if "Timeout" in str(e) or "timeout" in str(e).lower():
                    # 超时是正常的，继续等待
                    await asyncio.sleep(0.3)
                    continue
                # 其他异常也继续等待，不中断流程
                await asyncio.sleep(0.3)
                continue
            
            if text and len(text) > 0:
                text_len = len(text)
                last_text_len = len(last_text) if last_text else 0
                len_diff = text_len - last_text_len
                
                # 优化：确保检测的是新响应，而不是旧响应
                # 如果当前文本长度小于或等于发送前的长度，说明可能是旧响应，继续等待
                if last_text_len_before_send > 0 and text_len <= last_text_len_before_send * 1.1:
                    # 如果文本长度没有明显增加（<10%），可能是旧响应，继续等待
                    if text_len < last_text_len_before_send:
                        # 如果长度反而减少了，肯定是旧响应，重置状态
                        last_text = text
                        stable_iter = 0
                        last_change_time = time.time()
                        await asyncio.sleep(0.3)
                        continue
                    # 如果长度相近，可能是旧响应，但继续检测（可能是同一响应）
                    pass
                
                if len_diff > LENGTH_TOLERANCE:
                    # 还在生成（长度明显增加，超过容差）
                    last_text = text
                    stable_iter = 0
                    last_change_time = time.time()  # 更新最后变化时间
                elif abs(len_diff) <= LENGTH_TOLERANCE and text_len > 5:
                    # 长度稳定（变化在容差范围内）
                    if last_text != text:
                        # 文本内容有变化但长度相近，更新文本但保持稳定计数
                        last_text = text
                    stable_iter += 1
                    # 优化：动态调整稳定次数（短响应3次，长响应5次）
                    # 优化：如果响应长度在1秒内没有变化，直接认为稳定（快速路径）
                    time_since_change = time.time() - last_change_time
                    if time_since_change >= 1.0 and stable_iter >= 2:
                        # 快速路径：1秒内没有变化且已稳定2次，直接认为完成
                        self._log(f"ask: response stabilized (len={text_len}, fast path: {time_since_change:.1f}s no change)")
                        self._log(f"ask: response wait done ({time.time()-t_resp:.2f}s)")
                        return text, self.page.url
                    
                    # 根据响应长度动态调整稳定次数
                    required_stable_iter = 3 if text_len < 500 else 5
                    if stable_iter >= required_stable_iter:
                        self._log(f"ask: response stabilized (len={text_len}, stable_iter={stable_iter})")
                        self._log(f"ask: response wait done ({time.time()-t_resp:.2f}s)")
                        return text, self.page.url
                elif len_diff < -LENGTH_TOLERANCE:
                    # 长度明显减少（可能是页面刷新或内容被截断），重置状态
                    last_text = text
                    stable_iter = 0
                    last_change_time = time.time()
                else:
                    # text_len == 0 或 text_len <= 5，继续等待
                    pass
            elif last_text and len(last_text) > 5:
                # 如果之前有文本但现在获取不到，可能是页面刷新，但文本应该还在
                # 继续使用 last_text，但增加稳定计数
                stable_iter += 1
                time_since_change = time.time() - last_change_time
                required_stable_iter = 3 if len(last_text) < 500 else 5
                if time_since_change >= 2.0 and stable_iter >= required_stable_iter:
                    # 如果2秒内没有变化且已稳定足够次数，认为完成
                    self._log(f"ask: response stabilized (len={len(last_text)}, stable_iter={stable_iter}, no new text)")
                    self._log(f"ask: response wait done ({time.time()-t_resp:.2f}s)")
                    return last_text, self.page.url
            
            if time.time() - hb >= 10:
                self._log(f"ask: waiting... (elapsed={time.time()-start_t:.1f}s, stable_iter={stable_iter}, last_len={len(last_text)})")
                hb = time.time()
            
            # 优化：减少检测间隔，从0.5秒减少到0.3秒，加快检测速度
            await asyncio.sleep(0.3)
            
        await self.save_artifacts("gemini_answer_timeout")
        raise TimeoutError(f"Gemini response timeout. last_len={len(last_text)}")
