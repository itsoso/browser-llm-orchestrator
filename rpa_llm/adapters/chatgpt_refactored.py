# -*- coding: utf-8 -*-
"""
ChatGPT Adapter - 重构版本

将原有的大文件拆分为多个模块：
- chatgpt_model.py: 模型版本选择
- chatgpt_textbox.py: 输入框查找和操作
- chatgpt_state.py: 状态检测
- chatgpt_send.py: 发送逻辑（待创建）
- chatgpt_wait.py: 等待和稳定化（待创建）
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Optional, Tuple

from playwright.async_api import Locator

from ..utils import beijing_now_iso
from .base import SiteAdapter
from .chatgpt_model import ChatGPTModelSelector
from .chatgpt_state import ChatGPTStateDetector
from .chatgpt_textbox import ChatGPTTextboxFinder


class ChatGPTAdapter(SiteAdapter):
    site_id = "chatgpt"
    base_url = os.environ.get("CHATGPT_ENTRY_URL", "https://chatgpt.com/")
    
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
    
    # 优化：提升阈值到 2000，短 prompt 使用 fill/execCommand 更快更稳
    JS_INJECT_THRESHOLD = 2000

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 初始化各个模块
        self._model_selector = None
        self._state_detector = None
        self._textbox_finder = None

    def _log(self, msg: str) -> None:
        print(f"[{beijing_now_iso()}] [{self.site_id}] {msg}", flush=True)
    
    def _init_modules(self):
        """延迟初始化模块（在页面可用后）"""
        if self._model_selector is None:
            self._model_selector = ChatGPTModelSelector(self.page, self._log)
        if self._state_detector is None:
            self._state_detector = ChatGPTStateDetector(self.page, self._log)
        if self._textbox_finder is None:
            self._textbox_finder = ChatGPTTextboxFinder(
                self.page, 
                self._log,
                self.manual_checkpoint,
                self.save_artifacts
            )

    def _new_chat_enabled(self) -> bool:
        # CHATGPT_NEW_CHAT=1 才会每 task 点"新聊天"（更隔离，但更容易触发重绘抖动）
        return (os.environ.get("CHATGPT_NEW_CHAT") or "0").strip() == "1"

    async def ensure_variant(self, model_version: Optional[str] = None) -> None:
        """设置 ChatGPT 模型版本"""
        self._init_modules()
        await self._model_selector.ensure_variant(model_version)

    async def ensure_ready(self) -> None:
        """确保页面就绪"""
        self._init_modules()
        await self._textbox_finder.ensure_ready()

    async def new_chat(self) -> None:
        """创建新聊天"""
        self._log("new_chat: best effort")
        await self.try_click(self.NEW_CHAT, timeout_ms=2000)
        
        # 优化：使用更智能的等待策略，等待关键元素出现，而不是固定等待时间
        try:
            # 等待输入框出现（最关键的信号）
            await self.page.wait_for_selector("#prompt-textarea", timeout=10000, state="visible")
            self._log("new_chat: textarea appeared")
        except Exception:
            # 如果输入框未出现，等待页面加载完成
            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=5000)
                # 等待网络空闲（但超时时间较短，避免长时间等待）
                try:
                    await self.page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    pass  # networkidle 超时不影响继续
            except Exception:
                pass  # 页面加载超时不影响继续
        
        # 新聊天会重绘输入框，等待页面稳定（减少固定等待时间）
        await asyncio.sleep(0.5)  # 从 1.0 秒减少到 0.5 秒，因为上面已经等待了关键元素
        # 关闭可能的弹窗/遮罩
        if self._textbox_finder:
            await self._textbox_finder.dismiss_overlays()
        await asyncio.sleep(0.3)  # 从 0.5 秒减少到 0.3 秒

    # 委托方法到各个模块
    async def _assistant_count(self) -> int:
        self._init_modules()
        return await self._state_detector.assistant_count()

    async def _user_count(self) -> int:
        self._init_modules()
        return await self._state_detector.user_count()

    async def _last_assistant_text(self) -> str:
        self._init_modules()
        return await self._state_detector.last_assistant_text()
    
    async def _get_assistant_text_by_index(self, index: int) -> str:
        self._init_modules()
        return await self._state_detector.get_assistant_text_by_index(index)

    async def _is_generating(self) -> bool:
        self._init_modules()
        return await self._state_detector.is_generating()
    
    async def _is_thinking(self) -> bool:
        self._init_modules()
        return await self._state_detector.is_thinking()

    async def _find_textbox_any_frame(self):
        """查找输入框（委托给 textbox_finder）"""
        self._init_modules()
        return await self._textbox_finder.find_textbox_any_frame()

    async def _ready_check_textbox(self) -> bool:
        """检查输入框是否就绪（委托给 textbox_finder）"""
        self._init_modules()
        return await self._textbox_finder.ready_check_textbox()

    # 注意：_send_prompt 方法暂时保留在原文件中，因为太长（900+ 行）
    # 后续可以进一步拆分到 chatgpt_send.py 模块
    # 这里先保留原有实现，但可以通过导入方式引用

    async def ask(self, prompt: str, timeout_s: int = 1200, model_version: Optional[str] = None, new_chat: bool = False) -> Tuple[str, str]:
        """
        发送 prompt 并等待回复。
        
        Args:
            prompt: 要发送的提示词
            timeout_s: 超时时间（秒）
            model_version: 模型版本（如 "5.2pro", "GPT-5", "pro", "thinking", "instant"）
            new_chat: 是否在新窗口中发送（每次提交都打开新聊天）
        
        使用整体超时保护，确保不会无限等待。
        超时后会抛出 TimeoutError 异常。
        """
        # 初始化模块
        self._init_modules()
        
        # 如果提供了 model_version，设置到模型选择器
        if model_version:
            self._model_selector._model_version = model_version
        
        async def _ask_inner() -> Tuple[str, str]:
            ask_start_time = time.time()
            self._log(f"ask: start (timeout={timeout_s}s, model_version={model_version or 'default'}, new_chat={new_chat})")
            
            t_ready = time.time()
            await self.ensure_ready()
            self._log(f"ask: ensure_ready done ({time.time()-t_ready:.2f}s)")
            t_variant = time.time()
            await self.ensure_variant(model_version=model_version)
            self._log(f"ask: ensure_variant done ({time.time()-t_variant:.2f}s)")
            
            # 优化：针对 Thinking 模式的 DOM 稳定逻辑
            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=5000)
                await asyncio.sleep(0.5)
                found = await self._find_textbox_any_frame()
                if found:
                    tb, frame, how = found
                    try:
                        await tb.focus(timeout=2000)
                        self._log(f"ask: DOM stabilized, textbox refocused via {how}")
                    except Exception:
                        pass
            except Exception as e:
                self._log(f"ask: DOM stabilization warning: {e}")

            # 是否每次新聊天（优先使用参数，其次使用环境变量）
            if new_chat or self._new_chat_enabled():
                self._log("ask: creating new chat...")
                await self.new_chat()
                # 新聊天后输入框重建，重新确保 ready
                await self.ensure_ready()

            n_assist0 = await self._assistant_count()
            user0 = await self._user_count()
            last_assist_text_before = await self._last_assistant_text()
            self._log(f"ask: assistant_count(before)={n_assist0}, user_count(before)={user0}, last_assist_text_len(before)={len(last_assist_text_before)}")

            # 检查是否已经超时
            elapsed = time.time() - ask_start_time
            remaining = timeout_s - elapsed
            if remaining <= 10:
                raise TimeoutError(f"ask: timeout before sending (elapsed={elapsed:.1f}s)")

            self._log("ask: sending prompt...")
            t_send = time.time()
            # 注意：这里暂时调用原有的 _send_prompt 方法
            # 后续可以迁移到 chatgpt_send.py 模块
            await self._send_prompt(prompt)
            self._log(f"ask: send phase done ({time.time()-t_send:.2f}s)")

            # 等待 assistant 消息出现
            self._log("ask: waiting for assistant message...")
            t1 = time.time()
            elapsed = time.time() - ask_start_time
            remaining = timeout_s - elapsed
            assistant_wait_timeout = min(remaining * 0.2, 20)
            n_assist1 = n_assist0
            
            # 关键修复：在开始等待之前，先检查一次 thinking 状态
            try:
                thinking_precheck = await asyncio.wait_for(self._is_thinking(), timeout=0.3)
                if thinking_precheck:
                    self._log("ask: detected thinking mode before assistant wait, will prioritize thinking detection")
            except Exception:
                pass
            
            # 使用高频轮询 + wait_for_function 混合策略
            combined_sel = ", ".join(self._state_detector.ASSISTANT_MSG)
            polling_success = False
            thinking_detected_during_polling = False
            
            for attempt in range(200):  # 2.0 秒 / 0.01 秒 = 200 次
                try:
                    # 每 50 次检查（0.5 秒）检测一次 thinking 状态
                    if attempt > 0 and attempt % 50 == 0:
                        try:
                            thinking_check = await asyncio.wait_for(self._is_thinking(), timeout=0.1)
                            if thinking_check:
                                thinking_detected_during_polling = True
                                self._log(f"ask: detected thinking mode during polling (attempt {attempt+1})")
                        except Exception:
                            pass
                    
                    current_count = await asyncio.wait_for(
                        self.page.evaluate(
                            """(sel) => {
                                return document.querySelectorAll(sel).length;
                            }""",
                            combined_sel
                        ),
                        timeout=0.01
                    )
                    if isinstance(current_count, int) and current_count > n_assist0:
                        n_assist1 = current_count
                        self._log(f"ask: assistant_count increased to {n_assist1} (new message detected via high-frequency polling, attempt {attempt+1})")
                        polling_success = True
                        break
                except (asyncio.TimeoutError, Exception) as e:
                    if "TargetClosed" in str(e) or "Target page" in str(e):
                        raise RuntimeError(f"Browser/page closed during assistant wait: {e}") from e
                    pass
                
                await asyncio.sleep(0.01)
            
            if thinking_detected_during_polling:
                self._log("ask: thinking mode detected during polling, will prioritize thinking detection in fallback")
            
            # 如果高频轮询失败，使用 wait_for_function
            if not polling_success:
                try:
                    wait_timeout_ms = int(min(assistant_wait_timeout, 15) * 1000)
                    await self.page.wait_for_function(
                        """(args) => {
                            const n0 = args.n0;
                            const sel = args.sel;
                            const n = document.querySelectorAll(sel).length;
                            return n > n0;
                        }""",
                        arg={"n0": n_assist0, "sel": combined_sel},
                        timeout=wait_timeout_ms
                    )
                    n_assist1 = await self._assistant_count()
                    self._log(f"ask: assistant_count increased to {n_assist1} (new message detected via wait_for_function)")
                except Exception as e:
                    # wait_for_function 超时或失败，优先检查 thinking 状态
                    self._log(f"ask: wait_for_function timeout or failed ({e}), checking thinking status first...")
                    
                    try:
                        thinking = await asyncio.wait_for(self._is_thinking(), timeout=0.5)
                        if thinking:
                            self._log("ask: detected thinking mode, continuing to wait")
                            n_assist1 = n_assist0 + 1
                            self._log("ask: skipping manual checkpoint due to thinking mode")
                        else:
                            self._log("ask: not in thinking mode, checking text change...")
                            try:
                                n_assist1 = await asyncio.wait_for(self._assistant_count(), timeout=0.5)
                                if n_assist1 > n_assist0:
                                    self._log(f"ask: assistant_count increased to {n_assist1} (fallback check)")
                                else:
                                    try:
                                        text_quick = await asyncio.wait_for(self._last_assistant_text(), timeout=0.5)
                                        if text_quick and text_quick != last_assist_text_before:
                                            self._log("ask: text changed, assuming new message")
                                            n_assist1 = n_assist0 + 1
                                        else:
                                            thinking_retry = await asyncio.wait_for(self._is_thinking(), timeout=0.5)
                                            if thinking_retry:
                                                self._log("ask: detected thinking mode on retry, continuing to wait")
                                                n_assist1 = n_assist0 + 1
                                            else:
                                                n_assist1 = n_assist0
                                    except Exception:
                                        n_assist1 = n_assist0
                            except Exception:
                                n_assist1 = n_assist0
                    except Exception as thinking_err:
                        self._log(f"ask: thinking detection failed ({thinking_err}), falling back to text change check...")
                        try:
                            n_assist1 = await asyncio.wait_for(self._assistant_count(), timeout=0.5)
                            if n_assist1 > n_assist0:
                                self._log(f"ask: assistant_count increased to {n_assist1} (fallback check)")
                            else:
                                n_assist1 = n_assist0
                        except Exception:
                            n_assist1 = n_assist0
                    
                    # 如果仍然没有检测到新消息，且不在思考模式，触发 manual checkpoint
                    if n_assist1 <= n_assist0:
                        try:
                            thinking_final_check = await asyncio.wait_for(self._is_thinking(), timeout=0.5)
                            if thinking_final_check:
                                self._log("ask: detected thinking mode before manual checkpoint, skipping checkpoint")
                                n_assist1 = n_assist0 + 1
                            else:
                                await self.save_artifacts("no_assistant_reply")
                                await self.manual_checkpoint(
                                    "发送后未等到回复（可能网络/风控/页面提示）。请检查页面是否需要操作。",
                                    ready_check=self._ready_check_textbox,
                                    max_wait_s=min(15, timeout_s - elapsed - 5),
                                )
                        except Exception:
                            await self.save_artifacts("no_assistant_reply")
                            await self.manual_checkpoint(
                                "发送后未等到回复（可能网络/风控/页面提示）。请检查页面是否需要操作。",
                                ready_check=self._ready_check_textbox,
                                max_wait_s=min(15, timeout_s - elapsed - 5),
                            )
                        try:
                            n_assist1 = await self._assistant_count()
                            if n_assist1 <= n_assist0:
                                n_assist1 = n_assist0 + 1
                        except Exception:
                            n_assist1 = n_assist0 + 1
            
            self._log(f"ask: assistant wait done ({time.time()-t1:.2f}s)")

            # 重新查询实际的 assistant_count
            try:
                n_assist1_actual = await asyncio.wait_for(self._assistant_count(), timeout=1.0)
                if n_assist1_actual > n_assist0:
                    n_assist1 = n_assist1_actual
                    self._log(f"ask: using actual assistant_count for target_index: {n_assist1}")
                elif n_assist1 > n_assist0:
                    self._log(f"ask: actual count unchanged ({n_assist1_actual}), keeping n_assist1={n_assist1}")
                else:
                    n_assist1 = n_assist1_actual
                    self._log(f"ask: no new messages detected, using actual count: {n_assist1}")
            except Exception:
                pass

            # 等待新消息的文本内容出现
            self._log("ask: waiting for new message content (using index-based detection)...")
            t2 = time.time()
            hb = t2
            new_message_found = False
            elapsed = time.time() - ask_start_time
            remaining = timeout_s - elapsed
            
            if n_assist1 > n_assist0:
                content_wait_timeout = min(3, remaining * 0.08)
            else:
                content_wait_timeout = min(8, remaining * 0.12)
            
            target_index = n_assist1 - 1
            if target_index >= 0:
                try:
                    current_text_quick = await asyncio.wait_for(
                        self._get_assistant_text_by_index(target_index),
                        timeout=0.8
                    )
                    if current_text_quick and current_text_quick != last_assist_text_before:
                        new_message_found = True
                        self._log(f"ask: new message content detected quickly via index {target_index} (len={len(current_text_quick)})")
                except Exception:
                    pass
                
                if not new_message_found:
                    while time.time() - t2 < content_wait_timeout:
                        elapsed = time.time() - ask_start_time
                        if elapsed >= timeout_s - 10:
                            break
                        try:
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
                        await asyncio.sleep(0.2)
            else:
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
            stable_seconds = 1.5
            last_text_len = 0
            last_text_hash = ""
            last_change = time.time()
            hb = time.time()
            last_text_len_history = []
            final_text_fetched = False
            final_text = ""

            while time.time() - ask_start_time < timeout_s:
                elapsed = time.time() - ask_start_time
                remaining = timeout_s - elapsed
                
                if remaining <= 0:
                    break
                    
                try:
                    n_assist_current = await self._assistant_count()
                    if n_assist_current > n_assist0:
                        target_index = n_assist_current - 1
                    else:
                        target_index = max(0, n_assist_current - 1)
                    
                    combined_sel = ", ".join(self._state_detector.ASSISTANT_MSG)
                    result = await self.page.evaluate(
                        """(args) => {
                            const sel = args.sel;
                            const idx = args.idx;
                            const els = document.querySelectorAll(sel);
                            if (idx < 0 || idx >= els.length) return {len: 0, hash: ''};
                            const el = els[idx];
                            const text = (el.innerText || el.textContent || '').trim();
                            const len = text.length;
                            let hash = 0;
                            for (let i = 0; i < Math.min(8, text.length); i++) {
                                hash = ((hash << 5) - hash) + text.charCodeAt(i);
                                hash = hash & hash;
                            }
                            return {len: len, hash: hash.toString(36)};
                        }""",
                        {"sel": combined_sel, "idx": target_index}
                    )
                    
                    current_len = result.get("len", 0) if isinstance(result, dict) else 0
                    current_hash = result.get("hash", "") if isinstance(result, dict) else ""
                    
                    if current_len > 0 and current_hash != "":
                        if current_len != last_text_len or current_hash != last_text_hash:
                            last_text_len = current_len
                            last_text_hash = current_hash
                            last_change = time.time()
                            if current_len > 0:
                                self._log(f"ask: text updated (len={current_len}, remaining={remaining:.1f}s)")

                        try:
                            generating = await asyncio.wait_for(self._is_generating(), timeout=0.5)
                        except Exception:
                            generating = False
                        
                        try:
                            thinking = await asyncio.wait_for(self._is_thinking(), timeout=0.5)
                        except Exception:
                            thinking = False
                        
                        if thinking:
                            self._log(f"ask: ChatGPT Pro 还在思考中，继续等待（len={current_len}, remaining={remaining:.1f}s）")
                            last_change = time.time()
                            await asyncio.sleep(0.3)
                            continue
                        
                        if not generating and current_len > 0:
                            last_text_len_history.append((time.time(), current_len))
                            last_text_len_history[:] = last_text_len_history[-3:]
                            if len(last_text_len_history) >= 2:
                                prev_len = last_text_len_history[-2][1]
                                if current_len > prev_len:
                                    generating = True
                                    self._log(f"ask: text growing ({prev_len}->{current_len}), forcing generating=True")
                        
                        if current_len == 0 and not generating:
                            await asyncio.sleep(0.2)
                            continue
                        
                        time_since_change = time.time() - last_change
                        if current_len > 0 and (not generating) and (not thinking) and time_since_change >= 0.5 and time_since_change >= stable_seconds:
                            if not final_text_fetched:
                                try:
                                    if n_assist_current > n_assist0:
                                        final_text = await asyncio.wait_for(
                                            self._get_assistant_text_by_index(target_index),
                                            timeout=2.0
                                        )
                                    else:
                                        final_text = await asyncio.wait_for(self._last_assistant_text(), timeout=2.0)
                                    final_text_fetched = True
                                except Exception:
                                    final_text = ""
                            
                            elapsed = time.time() - ask_start_time
                            self._log(f"ask: done (stabilized, total={elapsed:.1f}s, fast path: {time_since_change:.1f}s no change, len={current_len})")
                            return final_text, self.page.url
                        
                        if current_len > 0 and (time.time() - last_change) >= stable_seconds and (not generating) and (not thinking):
                            if not final_text_fetched:
                                try:
                                    if n_assist_current > n_assist0:
                                        final_text = await asyncio.wait_for(
                                            self._get_assistant_text_by_index(target_index),
                                            timeout=2.0
                                        )
                                    else:
                                        final_text = await asyncio.wait_for(self._last_assistant_text(), timeout=2.0)
                                    final_text_fetched = True
                                except Exception:
                                    final_text = ""
                            
                            elapsed = time.time() - ask_start_time
                            self._log(f"ask: done (stabilized, total={elapsed:.1f}s, len={current_len})")
                            return final_text, self.page.url
                except asyncio.TimeoutError:
                    pass
                except Exception as e:
                    self._log(f"ask: DOM query error: {e}")

                if time.time() - hb >= 10:
                    elapsed = time.time() - ask_start_time
                    remaining = timeout_s - elapsed
                    try:
                        generating = await asyncio.wait_for(self._is_generating(), timeout=0.5)
                    except (asyncio.TimeoutError, Exception):
                        generating = False
                    try:
                        thinking = await asyncio.wait_for(self._is_thinking(), timeout=0.5)
                    except (asyncio.TimeoutError, Exception):
                        thinking = False
                    self._log(f"ask: generating={generating}, thinking={thinking}, last_len={last_text_len}, remaining={remaining:.1f}s ...")
                    hb = time.time()

                await asyncio.sleep(0.3)

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
                    f"No valid answer received. last_text_len={last_text_len}"
                )

        # 使用整体超时保护
        try:
            return await asyncio.wait_for(_ask_inner(), timeout=timeout_s + 5)
        except asyncio.TimeoutError:
            await self.save_artifacts("ask_total_timeout")
            raise TimeoutError(f"ask: total timeout exceeded ({timeout_s}s)")

