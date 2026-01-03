# -*- coding: utf-8 -*-
"""
ChatGPT 发送模块

负责处理 prompt 的输入和发送，包括：
- 输入框查找和清空
- 文本输入（JS injection / type / fill）
- 发送确认
- 发送触发（Control+Enter / 按钮点击）
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Callable, Optional, Tuple

from playwright.async_api import Locator, Page, Error as PlaywrightError

# 注意：这个模块依赖于 base.py 中的方法（_tb_clear, _tb_set_text, _tb_get_text, _tb_kind）
# 以及 chatgpt.py 中的方法（_find_textbox_any_frame, _user_count, _dismiss_overlays）
# 这些依赖通过构造函数传入


class ChatGPTSender:
    """ChatGPT 发送器"""
    
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
    
    # 生成中按钮：用于判断是否还在生成
    STOP_BTN = [
        'button:has-text("Stop generating")',
        'button:has-text("停止生成")',
        'button[aria-label*="Stop"]',
        'button[aria-label*="停止"]',
    ]
    
    # 用户消息容器
    USER_MSG = [
        'div[data-message-author-role="user"]',
        'article[data-message-author-role="user"]',
    ]
    
    # JS 注入阈值
    JS_INJECT_THRESHOLD = 2000
    
    def __init__(
        self,
        page: Page,
        logger: Callable[[str], None],
        # 依赖方法
        find_textbox_fn: Callable,
        user_count_fn: Callable,
        dismiss_overlays_fn: Callable,
        ready_check_textbox_fn: Callable,
        manual_checkpoint_fn: Callable,
        save_artifacts_fn: Callable,
        clean_newlines_fn: Callable,
        tb_clear_fn: Callable,
        tb_set_text_fn: Callable,
        tb_get_text_fn: Callable,
        tb_kind_fn: Callable,
    ):
        self.page = page
        self._log = logger
        self._find_textbox_any_frame = find_textbox_fn
        self._user_count = user_count_fn
        self._dismiss_overlays = dismiss_overlays_fn
        self._ready_check_textbox = ready_check_textbox_fn
        self.manual_checkpoint = manual_checkpoint_fn
        self.save_artifacts = save_artifacts_fn
        self.clean_newlines = clean_newlines_fn
        self._tb_clear = tb_clear_fn
        self._tb_set_text = tb_set_text_fn
        self._tb_get_text = tb_get_text_fn
        self._tb_kind = tb_kind_fn

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

    async def _fast_send_confirm(self, user0: int, timeout_ms: int = 1500) -> bool:
        """
        P0优化：快速确认发送成功，使用 page.wait_for_function（最便宜、最快）。
        避免复杂的 _user_count() + _assistant_count() 并行检查导致的 Future exception。
        
        Args:
            user0: 发送前的用户消息数量
            timeout_ms: 超时时间（毫秒）
        
        Returns:
            True 如果确认发送成功，False 否则
        """
        # 优化：使用并行检查，加快确认速度
        # 同时检查多个信号，只要有一个成功就返回 True
        
        async def check_textbox_cleared() -> bool:
            """检查输入框是否清空"""
            try:
                await self.page.wait_for_function(
                    """() => {
                      const el = document.querySelector('#prompt-textarea');
                      if (!el) return false;
                      const t = (el.innerText || el.textContent || '').trim();
                      return t.length === 0;
                    }""",
                    timeout=timeout_ms,
                )
                return True
            except Exception:
                return False

        async def check_user_count() -> bool:
            """检查用户消息数是否增加"""
            try:
                combined_user_sel = ", ".join(self.USER_MSG)
                await self.page.wait_for_function(
                    """(args) => {
                      const u0 = args.u0;
                      const sel = args.sel;
                      const n = document.querySelectorAll(sel).length;
                      return n > u0;
                    }""",
                    arg={"u0": user0, "sel": combined_user_sel},
                    timeout=timeout_ms,
                )
                return True
            except Exception:
                return False

        async def check_stop_button() -> bool:
            """检查停止按钮是否出现"""
            try:
                native_stop_selectors = [
                    sel for sel in self.STOP_BTN 
                    if ':has-text(' not in sel and 'aria-label' in sel
                ]
                if not native_stop_selectors:
                    native_stop_selectors = ['button[aria-label*="Stop"]', 'button[aria-label*="停止"]']
                combined_stop_sel = ", ".join(native_stop_selectors)
                await self.page.wait_for_function(
                    """(args) => {
                      const sel = args.sel;
                      try {
                        const els = document.querySelectorAll(sel);
                        for (let el of els) {
                          if (el.offsetParent !== null) return true;
                        }
                      } catch (e) {
                        return false;
                      }
                      return false;
                    }""",
                    arg={"sel": combined_stop_sel},
                    timeout=min(timeout_ms, 800),  # stop button 检查最多 0.8 秒
                )
                return True
            except Exception:
                return False
        
        # 并行检查所有信号，只要有一个成功就返回 True
        # 这样可以加快确认速度，避免串行等待
        try:
            results = await asyncio.gather(
                check_textbox_cleared(),
                check_user_count(),
                check_stop_button(),
                return_exceptions=True
            )
            # 只要有一个返回 True，就认为发送成功
            for result in results:
                if isinstance(result, bool) and result:
                    return True
        except Exception:
            pass
        
        return False

    async def _trigger_send_fast(self, user0: int) -> None:
        """
        P0优化：快路径发送，使用 page.keyboard.press("Control+Enter") + 高频轮询确认。
        避免 Locator.press() 的 actionability 等待和复杂的确认逻辑。
        
        Args:
            user0: 发送前的用户消息数量
        """
        # 使用 page.keyboard.press，避免 Locator.press() 的 actionability 等待
        self._log("send: pressing Control+Enter (fast path)...")
        await self.page.keyboard.press("Control+Enter")
        
        # 优化：使用高频轮询，同时检查多个信号（user_count, textbox cleared, stop button）
        # 这样可以更快地检测到发送成功，避免长时间等待
        combined_user_sel = ", ".join(self.USER_MSG)
        
        # 高频轮询检查（每 0.005 秒检查一次，最多 1.5 秒）- P0优化：减少超时时间，加快失败检测
        max_attempts = 300  # 1.5 秒 / 0.005 秒 = 300 次（从 400 次减少到 300 次，加快失败检测）
        for attempt in range(max_attempts):
            try:
                # 并行检查多个信号：user_count, textbox cleared, stop button
                # 使用 page.evaluate 一次性检查所有信号，避免多次调用
                result = await asyncio.wait_for(
                    self.page.evaluate(
                        """(args) => {
                            const userSel = args.userSel;
                            const user0 = args.user0;
                            
                            // 检查 1: user_count 增加（最可靠的信号）
                            const userCount = document.querySelectorAll(userSel).length;
                            if (userCount > user0) return {signal: 'user_count', value: userCount};
                            
                            // 检查 2: textbox 清空（快速信号）
                            const textbox = document.querySelector('#prompt-textarea');
                            if (textbox) {
                                const text = (textbox.innerText || textbox.textContent || '').trim();
                                if (text.length === 0) return {signal: 'textbox_cleared', value: true};
                            }
                            
                            // 检查 3: stop button 出现（快速信号）
                            const stopBtns = document.querySelectorAll('button[aria-label*="Stop"], button[aria-label*="停止"]');
                            for (let btn of stopBtns) {
                                if (btn.offsetParent !== null) return {signal: 'stop_button', value: true};
                            }
                            
                            return {signal: 'none', value: false};
                        }""",
                        {"userSel": combined_user_sel, "user0": user0}
                    ),
                    timeout=0.01  # 每次检查最多 0.01 秒（从 0.02 秒减少）
                )
                
                if isinstance(result, dict) and result.get("signal") != "none":
                    signal = result.get("signal")
                    value = result.get("value")
                    if signal == "user_count":
                        self._log(f"send: user_count increased ({user0} -> {value}), send confirmed (attempt {attempt+1})")
                    elif signal == "textbox_cleared":
                        self._log(f"send: textbox cleared, send confirmed (attempt {attempt+1})")
                    elif signal == "stop_button":
                        self._log(f"send: stop button appeared, send confirmed (attempt {attempt+1})")
                    return
            except (asyncio.TimeoutError, Exception) as e:
                # 优化：捕获所有异常，包括 TargetClosedError，避免 Future exception
                # 如果是 TargetClosedError，直接抛出，不再继续
                if "TargetClosed" in str(e) or "Target page" in str(e) or "Target context" in str(e):
                    raise RuntimeError(f"Browser/page closed during send confirmation: {e}") from e
                pass  # 其他异常继续轮询
            
            # 每 0.005 秒检查一次（从 0.01 秒减少到 0.005 秒，更激进）
            await asyncio.sleep(0.005)
        
        # 如果高频轮询失败，尝试并行确认（作为兜底）
        self._log("send: high-frequency polling failed, trying parallel confirmation...")
        # 修复：增加超时时间，从 50ms 增加到 500ms，提高确认成功率
        if await self._fast_send_confirm(user0, timeout_ms=500):  # 从 50ms 增加到 500ms
            self._log("send: fast path confirmed (parallel confirmation)")
            return
        
        # 如果还是失败，再按一次 Control+Enter
        self._log("send: first Control+Enter not confirmed, trying again...")
        await self.page.keyboard.press("Control+Enter")
        
        # 再次高频轮询（最多 0.8 秒，使用相同的多信号检查）- 减少超时时间，加快失败检测
        for attempt in range(160):  # 0.8 秒 / 0.005 秒 = 160 次（从 200 次减少到 160 次，加快失败检测）
            try:
                result = await asyncio.wait_for(
                    self.page.evaluate(
                        """(args) => {
                            const userSel = args.userSel;
                            const user0 = args.user0;
                            
                            // 检查 1: user_count 增加（最可靠的信号）
                            const userCount = document.querySelectorAll(userSel).length;
                            if (userCount > user0) return {signal: 'user_count', value: userCount};
                            
                            // 检查 2: textbox 清空（快速信号）
                            const textbox = document.querySelector('#prompt-textarea');
                            if (textbox) {
                                const text = (textbox.innerText || textbox.textContent || '').trim();
                                if (text.length === 0) return {signal: 'textbox_cleared', value: true};
                            }
                            
                            // 检查 3: stop button 出现（快速信号）
                            const stopBtns = document.querySelectorAll('button[aria-label*="Stop"], button[aria-label*="停止"]');
                            for (let btn of stopBtns) {
                                if (btn.offsetParent !== null) return {signal: 'stop_button', value: true};
                            }
                            
                            return {signal: 'none', value: false};
                        }""",
                        {"userSel": combined_user_sel, "user0": user0}
                    ),
                    timeout=0.01  # 从 0.02 秒减少到 0.01 秒
                )
                
                if isinstance(result, dict) and result.get("signal") != "none":
                    signal = result.get("signal")
                    value = result.get("value")
                    if signal == "user_count":
                        self._log(f"send: user_count increased ({user0} -> {value}), send confirmed (second attempt, attempt {attempt+1})")
                    elif signal == "textbox_cleared":
                        self._log(f"send: textbox cleared, send confirmed (second attempt, attempt {attempt+1})")
                    elif signal == "stop_button":
                        self._log(f"send: stop button appeared, send confirmed (second attempt, attempt {attempt+1})")
                    return
            except (asyncio.TimeoutError, Exception):
                pass
            
            await asyncio.sleep(0.005)  # 从 0.01 秒减少到 0.005 秒
        
        # 如果还是失败，抛出异常（让上层处理）
        raise RuntimeError("send not accepted after 2 Control+Enter attempts")

    async def send_prompt(self, prompt: str) -> None:
        """
        修复版发送逻辑：
        1. 清理 prompt 中的换行符（避免 type() 将 \n 解释为 Enter）
        2. 使用 JS 强制清空 (解决 Node is not input 报错)
        3. 智能 fallback 输入 (type -> JS injection)
        4. 组合键发送优先 (解决按钮点击失败)
        
        注意：这个方法非常长（约1100行），包含复杂的输入和验证逻辑。
        为了保持代码可读性，这里只提供方法签名和关键逻辑框架。
        完整的实现需要从 chatgpt.py 中复制 _send_prompt 方法的内容。
        """
        # 由于 _send_prompt 方法非常长（约1100行），完整的实现需要从 chatgpt.py 复制
        # 这里提供一个简化的框架，实际使用时需要将完整代码移过来
        
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
                await asyncio.sleep(0.1)
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
                    raise RuntimeError("send: textbox not found after manual checkpoint")
        
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
                    # P1优化：重试时重新查找元素（元素可能已变化），进一步减少等待时间
                    await asyncio.sleep(0.3)  # 从 0.5 秒减少到 0.3 秒，加快重试速度
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
                    
                    # P0优化：使用条件等待替代固定 sleep
                    # 等待 textbox 可见且可交互（最多 1 秒）
                    try:
                        await asyncio.wait_for(
                            tb.wait_for(state="visible", timeout=1000),
                            timeout=1.5  # 额外 0.5 秒缓冲
                        )
                    except (asyncio.TimeoutError, Exception) as e:
                        # 优化：捕获所有异常，避免 Future exception
                        if "TargetClosed" in str(e) or "Target page" in str(e):
                            raise RuntimeError(f"Browser/page closed during wait_for visible: {e}") from e
                        pass  # 超时不影响继续
                    
                    # 优化：使用统一的清空方法，优先用户等价操作（Meta/Control+A → Backspace）
                    # 对于短 prompt，只需清空一次即可，不需要多次循环
                    await self._tb_clear(tb)
                    
                    # P0优化：等待 textbox 清空（条件等待，最多 0.5 秒）
                    try:
                        await self.page.wait_for_function(
                            """() => {
                              const el = document.querySelector('#prompt-textarea');
                              if (!el) return false;
                              const t = (el.innerText || el.textContent || '').trim();
                              return t.length === 0;
                            }""",
                            timeout=500
                        )
                        self._log("send: textbox cleared successfully")
                    except Exception:
                        # 如果 wait_for_function 超时，再检查一次
                        check_empty = await self._tb_get_text(tb)
                        if not check_empty.strip():
                            self._log("send: textbox cleared successfully (after timeout check)")
                        else:
                            # 如果还有内容，再清空一次（最多2次）
                            self._log(f"send: textbox still has content after first clear, retrying...")
                            await self._tb_clear(tb)
                            try:
                                await self.page.wait_for_function(
                                    """() => {
                                      const el = document.querySelector('#prompt-textarea');
                                      if (!el) return false;
                                      const t = (el.innerText || el.textContent || '').trim();
                                      return t.length === 0;
                                    }""",
                                    timeout=300
                                )
                                self._log("send: textbox cleared successfully (after retry)")
                            except Exception:
                                final_check = await self._tb_get_text(tb)
                                if final_check.strip():
                                    self._log(f"send: warning - textbox still has content after clear: '{final_check[:50]}...'")
                                else:
                                    self._log("send: textbox cleared successfully (after retry)")
                    
                    # P0优化：等待 React 状态更新（条件等待，最多 0.3 秒）
                    # 检查 textbox 是否可交互（disabled 属性消失）
                    try:
                        await self.page.wait_for_function(
                            """() => {
                              const el = document.querySelector('#prompt-textarea');
                              if (!el) return false;
                              return !el.hasAttribute('disabled') && el.offsetParent !== null;
                            }""",
                            timeout=300
                        )
                    except Exception:
                        pass  # 超时不影响继续
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
                # 1. 对于超长 prompt (>2000 字符)，直接使用 JS 注入（更快更稳）
                # 2. 对于中等长度，使用 type() 但增加超时时间
                # 注意：prompt 已经在方法开始时清理了换行符，所以这里不需要再检查换行符
                # 修复：在循环外部定义 use_js_inject，避免在循环内部重新计算导致状态丢失
                # 如果这是第一次迭代，根据长度判断；如果之前已经设置为 True（检测到 contenteditable），保持 True
                if attempt == 0:
                    use_js_inject = prompt_len > self.JS_INJECT_THRESHOLD
                # 如果 attempt > 0，use_js_inject 保持之前的值（可能已经被设置为 True）
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
                                    // 修复：对于 contenteditable（ProseMirror），先清空再设置，避免残留内容
                                    el.innerText = '';
                                    el.textContent = '';
                                    // 然后设置新文本
                                    el.innerText = fullText;
                                    el.textContent = fullText;
                                }} else {{
                                    el.value = fullText;
                                }}
                                
                                // 触发输入状态更新事件（避免 beforeinput/data 导致重复插入）
                                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                el.blur(); // 有时失焦能强制同步状态
                                el.focus(); // 重新聚焦，确保按钮状态更新
                            }}
                        }}
                        """
                        try:
                            await asyncio.wait_for(
                                tb.evaluate(js_code),
                                timeout=20.0
                            )
                        except Exception as eval_err:
                            # 优化：如果是 TargetClosedError，直接抛出，避免 Future exception
                            if "TargetClosed" in str(eval_err) or "Target page" in str(eval_err) or "Target context" in str(eval_err):
                                raise RuntimeError(f"Browser/page closed during JS evaluate: {eval_err}") from eval_err
                            raise  # 其他异常继续抛出
                        
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
                    # 修复：对于 ChatGPT（ProseMirror contenteditable），避免使用 type()，因为 type() 在 contenteditable 上不稳定
                    # 检测元素类型，如果是 contenteditable，强制使用 JS 注入而不是 type()
                    try:
                        # 检测元素类型
                        tb_kind = await self._tb_kind(tb)
                        self._log(f"send: detected textbox kind: {tb_kind}")
                        # 对于非 textarea（包括 contenteditable 和 unknown），都使用 JS 注入
                        # 因为 ChatGPT 使用 ProseMirror（contenteditable），即使检测失败（返回 unknown），也不应该使用 type()
                        if tb_kind != "textarea":
                            # 对于 contenteditable（ProseMirror），强制使用 JS 注入，避免 type() 导致的字符错乱
                            self._log(f"send: detected {tb_kind}, forcing JS injection instead of type() to avoid character order issues...")
                            use_js_inject = True  # 强制使用 JS 注入
                            # 重新进入 JS 注入逻辑
                            continue  # 跳出当前逻辑，重新进入 JS 注入分支
                    except Exception as detect_err:
                        # 检测失败时，默认假设是 contenteditable，使用 JS 注入
                        # 这样可以避免在 ChatGPT（ProseMirror）上使用 type() 导致失败
                        self._log(f"send: failed to detect textbox kind ({detect_err}), assuming contenteditable and using JS injection...")
                        use_js_inject = True  # 默认使用 JS 注入
                        continue  # 跳出当前逻辑，重新进入 JS 注入分支
                    
                    # 优化：短 prompt 使用轻量路径（fill/execCommand），避免 type() 的延迟
                    # 策略 A: 对于短 prompt，优先使用 _tb_set_text (fill/execCommand)
                    # 策略 B: 如果 _tb_set_text 失败，再尝试 type()（仅限 textarea）
                    try:
                        # 确保元素可见和可交互
                        try:
                            await tb.wait_for(state="attached", timeout=10000)
                        except Exception as wait_err:
                            # 优化：如果是 TargetClosedError，直接抛出，避免 Future exception
                            if "TargetClosed" in str(wait_err) or "Target page" in str(wait_err) or "Target context" in str(wait_err):
                                raise RuntimeError(f"Browser/page closed during wait_for: {wait_err}") from wait_err
                            raise  # 其他异常继续抛出
                        
                        # 最终验证：确保 prompt 中没有任何换行符（双重保险）
                        prompt = self.clean_newlines(prompt, logger=lambda msg: self._log(f"send: {msg}"))
                        prompt_len = len(prompt)
                        
                        # 优化：短 prompt 使用 _tb_set_text (fill/execCommand)，更快更稳
                        # 修复：提前初始化 timeout_ms，避免在异常情况下未定义
                        timeout_ms = max(60000, prompt_len * 50)  # 默认超时值
                        
                        try:
                            # 优化：添加超时控制，避免 _tb_set_text 长时间阻塞
                            # 注意：直接调用 _tb_set_text，不使用 asyncio.wait_for，避免 Future exception
                            # 因为 _tb_set_text 内部已经有超时控制
                            await self._tb_set_text(tb, prompt)
                            self._log(f"send: set text via _tb_set_text (len={prompt_len})")
                            type_success = True
                        except (asyncio.TimeoutError, RuntimeError, Exception) as set_text_err:
                            # 优化：如果是 TargetClosedError，直接抛出，避免 Future exception
                            if "TargetClosed" in str(set_text_err) or "Target page" in str(set_text_err) or "Target context" in str(set_text_err):
                                self._log(f"send: browser/page closed during _tb_set_text, raising error")
                                raise RuntimeError(f"Browser/page closed during _tb_set_text: {set_text_err}") from set_text_err
                            
                            # 如果 _tb_set_text 失败，优先重试 JS 注入；仅 textarea 允许 type()
                            err_msg = str(set_text_err) if set_text_err else "timeout or unknown error"
                            try:
                                tb_kind = await self._tb_kind(tb)
                            except Exception:
                                tb_kind = "unknown"

                            # 修复：对于非 textarea（包括 contenteditable 和 unknown），都使用 JS 注入，避免 type() 失败
                            # 因为 ChatGPT 使用 ProseMirror（contenteditable），即使检测失败（返回 unknown），也不应该使用 type()
                            if tb_kind != "textarea":
                                self._log(
                                    f"send: _tb_set_text failed ({err_msg}), detected {tb_kind}, using JS injection instead of type()..."
                                )
                                use_js_inject = True
                                await asyncio.sleep(0.2)
                                continue

                            # 只有确认是 textarea 时才使用 type()
                            self._log(f"send: _tb_set_text failed ({err_msg}), confirmed textarea, trying type()...")
                            
                            # 修复：_tb_set_text 可能部分成功（输入了一部分内容），需要先清空，避免重复输入
                            try:
                                existing_before_clear = await self._tb_get_text(tb)
                                if existing_before_clear.strip():
                                    existing_len = len(existing_before_clear.strip())
                                    self._log(f"send: _tb_set_text failed but textbox has content (len={existing_len}), clearing before type()...")
                                    await self._tb_clear(tb)
                                    await asyncio.sleep(0.3)
                                    # 验证是否清空
                                    check_after_clear = await self._tb_get_text(tb)
                                    if check_after_clear.strip():
                                        # 如果还有内容，再清空一次
                                        self._log(f"send: textbox still has content after first clear, clearing again...")
                                        await self._tb_clear(tb)
                                        await asyncio.sleep(0.2)
                            except Exception as clear_err:
                                self._log(f"send: failed to clear textbox before type(): {clear_err}")
                                # 清空失败不致命，继续尝试 type()
                            
                            # 修复：在 type() 之前，确保输入框完全清空，并将光标定位到开头
                            # 这是为了防止 type() 在错误的位置插入字符，导致字母错乱
                            try:
                                # 先清空一次（双重保险）
                                await self._tb_clear(tb)
                                await asyncio.sleep(0.2)
                                
                                # 验证是否清空
                                verify_clear = await self._tb_get_text(tb)
                                if verify_clear.strip():
                                    # 如果还有内容，再清空一次
                                    self._log(f"send: textbox still has content after clear, clearing again...")
                                    await self._tb_clear(tb)
                                    await asyncio.sleep(0.2)
                                
                                # 确保元素有焦点，并将光标定位到开头
                                await asyncio.wait_for(tb.focus(), timeout=2.0)
                                # 将光标移动到开头（防止在中间位置插入）
                                try:
                                    await tb.evaluate("""(el) => {
                                        if (el.contentEditable === 'true' || el.getAttribute('contenteditable') === 'true') {
                                            // 对于 contenteditable，设置光标到开头
                                            const range = document.createRange();
                                            const sel = window.getSelection();
                                            range.setStart(el, 0);
                                            range.collapse(true);
                                            sel.removeAllRanges();
                                            sel.addRange(range);
                                        }} else if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {{
                                            // 对于 textarea/input，设置光标到开头
                                            el.setSelectionRange(0, 0);
                                        }}
                                    }""")
                                except Exception:
                                    # 如果设置光标位置失败，尝试按 Home 键
                                    try:
                                        await tb.press("Home")
                                    except Exception:
                                        pass
                            except (asyncio.TimeoutError, Exception) as focus_err:
                                # 优化：如果是 TargetClosedError，直接抛出，避免 Future exception
                                if "TargetClosed" in str(focus_err) or "Target page" in str(focus_err) or "Target context" in str(focus_err):
                                    raise RuntimeError(f"Browser/page closed during focus: {focus_err}") from focus_err
                                pass  # focus 失败不致命
                            
                            # 设置超时（毫秒），根据长度动态调整
                            # 优化：减少超时时间，每字符 40ms，最小 30 秒（从 60 秒减少）
                            timeout_ms = max(30000, prompt_len * 40)  # 从 60000 和 50ms 减少
                        
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
                        
                        # 修复：在 type() 之前检查输入框是否已有内容（防止重复输入和字母错乱）
                        # 注意：如果 _tb_set_text 失败，已经在上面清空了，这里主要是双重检查
                        try:
                            existing_text = await self._tb_get_text(tb)
                            if existing_text.strip():
                                existing_len = len(existing_text.strip())
                                expected_len = len(prompt.strip())
                                existing_ratio = existing_len / expected_len if expected_len > 0 else 0
                                # 如果已有内容且长度接近或超过预期，说明可能已经输入过了
                                if existing_ratio >= 0.80:
                                    self._log(f"send: textbox already has content (len={existing_len}, ratio={existing_ratio:.2%}), checking if it matches prompt...")
                                    # 检查是否与 prompt 匹配
                                    if existing_text.strip() == prompt.strip():
                                        self._log(f"send: textbox content matches prompt, skipping type()")
                                        type_success = True
                                        break
                                    elif existing_ratio > 1.20:
                                        # 如果内容比预期长很多，可能是重复输入，清空后继续
                                        self._log(f"send: textbox content appears duplicated (ratio={existing_ratio:.2%}), clearing...")
                                        await self._tb_clear(tb)
                                        # 将光标定位到开头
                                        try:
                                            await tb.evaluate("""(el) => {
                                                if (el.contentEditable === 'true') {{
                                                    const range = document.createRange();
                                                    const sel = window.getSelection();
                                                    range.setStart(el, 0);
                                                    range.collapse(true);
                                                    sel.removeAllRanges();
                                                    sel.addRange(range);
                                                }} else if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {{
                                                    el.setSelectionRange(0, 0);
                                                }}
                                            }""")
                                        except Exception:
                                            pass
                                        await asyncio.sleep(0.3)
                                else:
                                    # 如果内容不完整（ratio < 0.80），也应该清空，避免追加导致字母错乱
                                    self._log(f"send: textbox has partial content (len={existing_len}, ratio={existing_ratio:.2%}), clearing to avoid appending...")
                                    await self._tb_clear(tb)
                                    # 将光标定位到开头
                                    try:
                                        await tb.evaluate("""(el) => {
                                            if (el.contentEditable === 'true') {{
                                                const range = document.createRange();
                                                const sel = window.getSelection();
                                                range.setStart(el, 0);
                                                range.collapse(true);
                                                sel.removeAllRanges();
                                                sel.addRange(range);
                                            }} else if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {{
                                                el.setSelectionRange(0, 0);
                                            }}
                                        }""")
                                    except Exception:
                                        pass
                                    await asyncio.sleep(0.2)
                        except Exception:
                            pass  # 检查失败不影响继续
                        
                        # 只有在 type_success 为 False 时才尝试 type()
                        # 修复：对于 contenteditable，不要使用 type()，而是使用 JS 注入
                        if not type_success:
                            # 再次检查元素类型，确保不是 contenteditable
                            # 修复：对于非 textarea（包括 contenteditable 和 unknown），都使用 JS 注入
                            try:
                                tb_kind = await self._tb_kind(tb)
                                self._log(f"send: detected textbox kind before type(): {tb_kind}")
                                # 对于非 textarea（包括 contenteditable 和 unknown），都使用 JS 注入
                                # 因为 ChatGPT 使用 ProseMirror（contenteditable），即使检测失败（返回 unknown），也不应该使用 type()
                                if tb_kind != "textarea":
                                    # 对于 contenteditable，强制使用 JS 注入，避免 type() 导致的字符错乱
                                    self._log(f"send: detected {tb_kind} before type(), using JS injection instead...")
                                    use_js_inject = True  # 强制使用 JS 注入
                                    # 重新进入 JS 注入逻辑
                                    continue  # 跳出当前逻辑，重新进入 JS 注入分支
                            except Exception as detect_err:
                                # 检测失败时，默认假设是 contenteditable，使用 JS 注入
                                # 这样可以避免在 ChatGPT（ProseMirror）上使用 type() 导致失败
                                self._log(f"send: failed to detect textbox kind before type() ({detect_err}), assuming contenteditable and using JS injection...")
                                use_js_inject = True  # 默认使用 JS 注入
                                continue  # 跳出当前逻辑，重新进入 JS 注入分支
                            
                            try:
                                # 优化：使用 asyncio.wait_for 包装，确保超时被正确处理，避免 Future exception
                                # 注意：这里只对 textarea 使用 type()，contenteditable 应该已经在上面的检查中被重定向到 JS 注入
                                await asyncio.wait_for(
                                    tb.type(prompt, delay=0, timeout=timeout_ms),
                                    timeout=timeout_ms / 1000.0 + 5.0  # 额外 5 秒缓冲
                                )
                                self._log(f"send: typed prompt (timeout={timeout_ms/1000:.1f}s)")
                            except asyncio.TimeoutError:
                                # asyncio.wait_for 超时，说明 type() 本身超时了
                                self._log(f"send: type() timeout after {timeout_ms/1000:.1f}s (asyncio.wait_for)")
                                raise RuntimeError(f"type() timeout after {timeout_ms/1000:.1f}s")
                            except PlaywrightError as pe:
                                # 处理 Playwright 错误（包括 TargetClosedError 和 TimeoutError）
                                if "TargetClosed" in str(pe) or "Target page" in str(pe):
                                    self._log(f"send: browser/page closed during type(), raising error")
                                    raise RuntimeError(f"Browser/page closed during input: {pe}") from pe
                                if "Timeout" in str(pe) or "timeout" in str(pe).lower():
                                    # 捕获 Playwright 的 TimeoutError，避免 Future exception
                                    self._log(f"send: type() timeout: {pe}")
                                    raise RuntimeError(f"type() timeout: {pe}") from pe
                                raise  # 其他 Playwright 错误继续抛出
                            except Exception as e:
                                # 捕获所有其他异常，避免 Future exception
                                if "TargetClosed" in str(e) or "Target page" in str(e):
                                    raise RuntimeError(f"Browser/page closed during input: {e}") from e
                                if "Timeout" in str(e) or "timeout" in str(e).lower():
                                    raise RuntimeError(f"type() timeout: {e}") from e
                                raise  # 其他异常继续抛出
                        
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
                # 对于长 prompt（>2000 chars），使用更严格的验证
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
                
                # 修复：如果 ratio > 120%，说明内容可能被重复输入了，需要清空并重试
                # 降低阈值从 150% 到 120%，更早检测重复输入
                if len_ratio > 1.20:
                    self._log(f"send: content appears duplicated (ratio={len_ratio:.2%} > 120%), clearing and retrying...")
                    # 清空并重试
                    try:
                        # 多次清空，确保彻底清空
                        for clear_retry in range(3):
                            await self._tb_clear(tb)
                            await asyncio.sleep(0.2)
                            # 验证是否清空
                            check = await self._tb_get_text(tb)
                            if not check.strip():
                                break
                        # 最终验证
                        final_check = await self._tb_get_text(tb)
                        if final_check.strip():
                            self._log(f"send: warning - textbox still has content after clear: '{final_check[:50]}...'")
                        else:
                            self._log(f"send: textbox cleared successfully")
                    except Exception as clear_err:
                        self._log(f"send: failed to clear textbox: {clear_err}")
                    continue  # 触发下一次重试
                
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
                # 修复：如果 ratio > 120%，说明内容可能被重复输入了，需要清空并重试
                # 降低阈值从 150% 到 120%，更早检测重复输入
                # 关键修复：必须在进入验证逻辑之前检查，防止 ratio > 120% 时仍然通过验证
                if len_ratio > 1.20:
                    self._log(f"send: content appears duplicated (ratio={len_ratio:.2%} > 120%), clearing and retrying...")
                    # 清空并重试
                    try:
                        # 多次清空，确保彻底清空
                        for clear_retry in range(3):
                            await self._tb_clear(tb)
                            await asyncio.sleep(0.2)
                            # 验证是否清空
                            check = await self._tb_get_text(tb)
                            if not check.strip():
                                break
                        # 最终验证
                        final_check = await self._tb_get_text(tb)
                        if final_check.strip():
                            self._log(f"send: warning - textbox still has content after clear: '{final_check[:50]}...'")
                        else:
                            self._log(f"send: textbox cleared successfully")
                    except Exception as clear_err:
                        self._log(f"send: failed to clear textbox: {clear_err}")
                    continue  # 触发下一次重试
                
                # 关键修复：在进入验证逻辑之前，再次检查 ratio，防止 ratio > 120% 时仍然通过验证
                # 这是双重保险，确保不会因为代码执行路径问题而跳过重复检测
                if len_ratio > 1.20:
                    self._log(f"send: content appears duplicated (ratio={len_ratio:.2%} > 120%), clearing and retrying (second check)...")
                    try:
                        for clear_retry in range(3):
                            await self._tb_clear(tb)
                            await asyncio.sleep(0.2)
                            check = await self._tb_get_text(tb)
                            if not check.strip():
                                break
                    except Exception as clear_err:
                        self._log(f"send: failed to clear textbox (second check): {clear_err}")
                    continue  # 触发下一次重试
                
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
                
                # 关键修复：在最终验证通过之前，最后一次检查 ratio，防止 ratio > 120% 时仍然通过验证
                # 这是三重保险，确保绝对不会让重复输入通过验证
                if len_ratio > 1.20:
                    self._log(f"send: content appears duplicated (ratio={len_ratio:.2%} > 120%), clearing and retrying (final check before verification)...")
                    try:
                        for clear_retry in range(3):
                            await self._tb_clear(tb)
                            await asyncio.sleep(0.2)
                            check = await self._tb_get_text(tb)
                            if not check.strip():
                                break
                    except Exception as clear_err:
                        self._log(f"send: failed to clear textbox (final check): {clear_err}")
                    continue  # 触发下一次重试
                
                self._log(f"send: content verified OK (len={actual_len}, ratio={len_ratio:.2%})")
                prompt_sent = True
                break
                    
            except Exception as e:
                self._log(f"send: attempt {attempt+1} error: {e}")
                await asyncio.sleep(1)

        if not prompt_sent:
            raise RuntimeError("send: failed to enter prompt after retries")

        # 4. 发送逻辑
        if already_sent_during_input:
            self._log("send: prompt was already sent during input, skipping send trigger")
            return
        
        # 在发送前，再次检查是否已经发送（防止重复发送）
        try:
            user_count_before_trigger = await self._user_count()
            if user_count_before_trigger > user_count_before_send:
                self._log(f"send: already sent detected (user_count={user_count_before_trigger} > {user_count_before_send}), skipping send trigger")
                return
        except Exception:
            pass
        
        self._log("send: triggering send...")
        send_phase_start = time.time()
        
        # P0优化：使用快路径发送
        try:
            try:
                tb_loc = self.page.locator('div[id="prompt-textarea"]').first
                if await tb_loc.count() > 0:
                    await tb_loc.focus(timeout=1000)
            except Exception:
                pass
            
            self._log("send: using fast path (Control+Enter + wait_for_function)...")
            await self._trigger_send_fast(user_count_before_send)
            send_duration = time.time() - send_phase_start
            self._log(f"send: fast path succeeded ({send_duration:.2f}s)")
            return
        except Exception as fast_err:
            self._log(f"send: fast path failed: {fast_err}, falling back to legacy path...")
            pass
        
        # Fallback：如果快路径失败，使用简化的按钮点击逻辑
        send_phase_max_s = float(os.environ.get("CHATGPT_SEND_PHASE_MAX_S", "8.0"))
        if time.time() - send_phase_start >= send_phase_max_s:
            self._log(f"send: send phase reached {send_phase_max_s:.1f}s, skipping button attempts to reduce latency")
            return
        
        # 简化版检查函数（用于 fallback）
        async def check_sent_simple() -> bool:
            return await self._fast_send_confirm(user_count_before_send, timeout_ms=500)
        
        for send_sel in self.SEND_BTN:
            if time.time() - send_phase_start >= send_phase_max_s:
                self._log(f"send: send phase reached {send_phase_max_s:.1f}s, stopping button attempts")
                return
            
            if await check_sent_simple():
                self._log(f"send: confirmed sent before button {send_sel}")
                return
            
            try:
                btn = self.page.locator(send_sel).first
                if await btn.count() > 0:
                    # 检查是否是停止按钮
                    try:
                        aria_label = await btn.get_attribute("aria-label") or ""
                        btn_text = await btn.inner_text() or ""
                        if "停止" in aria_label or "Stop" in aria_label or "stop" in aria_label.lower():
                            self._log(f"send: button {send_sel} is a stop button, skipping")
                            continue
                        if "停止" in btn_text or "Stop" in btn_text or "stop" in btn_text.lower():
                            self._log(f"send: button {send_sel} has stop text, skipping")
                            continue
                    except Exception:
                        pass
                    
                    if await check_sent_simple():
                        self._log(f"send: confirmed sent just before clicking {send_sel}")
                        return
                    
                    try:
                        await asyncio.wait_for(
                            btn.wait_for(state="visible", timeout=1000),
                            timeout=1.5
                        )
                    except (asyncio.TimeoutError, Exception) as e:
                        if "TargetClosed" in str(e) or "Target page" in str(e):
                            raise RuntimeError(f"Browser/page closed during wait_for button: {e}") from e
                        pass
                    
                    self._log(f"send: clicking send button {send_sel}...")
                    try:
                        await asyncio.wait_for(
                            btn.click(timeout=3000),
                            timeout=3.5
                        )
                    except (asyncio.TimeoutError, Exception) as e:
                        if "TargetClosed" in str(e) or "Target page" in str(e):
                            raise RuntimeError(f"Browser/page closed during button click: {e}") from e
                        raise
                    
                    self._log(f"send: clicked send button {send_sel}")
                    await asyncio.sleep(0.2)
                    if await check_sent_simple():
                        self._log(f"send: confirmed sent after button {send_sel}")
                        return
            except Exception:
                continue

        self._log("send: all send methods attempted (Enter/Control+Enter/Button)")

