# -*- coding: utf-8 -*-
"""
ChatGPT 等待和稳定化模块

负责处理 assistant 消息的等待和输出稳定化，包括：
- 等待 assistant 消息出现
- 等待消息内容出现
- 等待输出稳定（generating=False, thinking=False, 内容不变）
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional, Tuple

from playwright.async_api import Page


class ChatGPTWaiter:
    """ChatGPT 等待和稳定化器"""
    
    # 消息容器：用于确认发送成功与回复到达
    ASSISTANT_MSG = [
        'div[data-message-author-role="assistant"]',
        'article[data-message-author-role="assistant"]',
    ]
    
    def __init__(
        self,
        page: Page,
        logger: Callable[[str], None],
        # 依赖方法
        assistant_count_fn: Callable,
        last_assistant_text_fn: Callable,
        get_assistant_text_by_index_fn: Callable,
        is_generating_fn: Callable,
        is_thinking_fn: Callable,
        ready_check_textbox_fn: Callable,
        manual_checkpoint_fn: Callable,
        save_artifacts_fn: Callable,
    ):
        self.page = page
        self._log = logger
        self._assistant_count = assistant_count_fn
        self._last_assistant_text = last_assistant_text_fn
        self._get_assistant_text_by_index = get_assistant_text_by_index_fn
        self._is_generating = is_generating_fn
        self._is_thinking = is_thinking_fn
        self._ready_check_textbox = ready_check_textbox_fn
        self.manual_checkpoint = manual_checkpoint_fn
        self.save_artifacts = save_artifacts_fn

    async def wait_for_assistant_message(
        self,
        n_assist0: int,
        last_assist_text_before: str,
        ask_start_time: float,
        timeout_s: float,
    ) -> int:
        """
        等待 assistant 消息出现
        
        Args:
            n_assist0: 发送前的 assistant 消息数量
            last_assist_text_before: 发送前的最后一条 assistant 消息文本
            ask_start_time: ask 方法开始时间
            timeout_s: 总超时时间
        
        Returns:
            新的 assistant 消息数量（n_assist1）
        """
        # P1优化：等待 assistant 消息出现（使用 wait_for_function 事件驱动，替代轮询）
        # 关键修复：优先检测 thinking 状态，避免过早触发 manual checkpoint
        self._log("ask: waiting for assistant message (using wait_for_function event-driven)...")
        t1 = time.time()
        elapsed = time.time() - ask_start_time
        remaining = timeout_s - elapsed
        # 优化：减少 assistant_wait_timeout，从 90 秒减少到 20 秒
        # 这样可以更快地检测到新消息，而不是等待 90 秒
        # 如果 20 秒内没有检测到，会立即检查文本变化，而不是继续等待
        assistant_wait_timeout = min(remaining * 0.2, 20)  # 最多20秒（从 15 秒增加到 20 秒，给 ChatGPT Pro 更多时间）
        n_assist1 = n_assist0
        
        # 关键修复：在开始等待之前，先检查一次 thinking 状态
        # 如果已经在思考，可以提前知道，避免不必要的等待
        try:
            thinking_precheck = await asyncio.wait_for(self._is_thinking(), timeout=0.3)
            if thinking_precheck:
                self._log("ask: detected thinking mode before assistant wait, will prioritize thinking detection")
        except Exception:
            pass  # 检测失败不影响主流程
        
        # P1优化：使用高频轮询 + wait_for_function 混合策略
        # 先尝试高频轮询（更快），如果失败再使用 wait_for_function（更可靠）
        combined_sel = ", ".join(self.ASSISTANT_MSG)
        
        # 优化：先尝试高频轮询（最多 2.0 秒），这样可以更快检测到新消息
        # 关键修复：在轮询过程中，定期检测 thinking 状态
        n_assist1 = n_assist0
        polling_success = False
        thinking_detected_during_polling = False
        for attempt in range(200):  # 2.0 秒 / 0.01 秒 = 200 次（增加轮询次数）
            try:
                # 每 50 次检查（0.5 秒）检测一次 thinking 状态
                if attempt > 0 and attempt % 50 == 0:
                    try:
                        thinking_check = await asyncio.wait_for(self._is_thinking(), timeout=0.1)
                        if thinking_check:
                            thinking_detected_during_polling = True
                            self._log(f"ask: detected thinking mode during polling (attempt {attempt+1})")
                    except Exception:
                        pass  # thinking 检测失败不影响轮询
                
                current_count = await asyncio.wait_for(
                    self.page.evaluate(
                        """(sel) => {
                            return document.querySelectorAll(sel).length;
                        }""",
                        combined_sel
                    ),
                    timeout=0.01  # 每次检查最多 0.01 秒（从 0.015 秒减少）
                )
                if isinstance(current_count, int) and current_count > n_assist0:
                    n_assist1 = current_count
                    self._log(f"ask: assistant_count increased to {n_assist1} (new message detected via high-frequency polling, attempt {attempt+1})")
                    polling_success = True
                    break
            except (asyncio.TimeoutError, Exception) as e:
                # 优化：捕获所有异常，包括 TargetClosedError，避免 Future exception
                # 如果是 TargetClosedError，直接抛出，不再继续
                if "TargetClosed" in str(e) or "Target page" in str(e) or "Target context" in str(e):
                    raise RuntimeError(f"Browser/page closed during assistant wait: {e}") from e
                pass  # 其他异常继续轮询
            
            await asyncio.sleep(0.01)  # 从 0.015 秒减少到 0.01 秒，更激进
        
        # 如果轮询过程中检测到 thinking，设置标志
        if thinking_detected_during_polling:
            self._log("ask: thinking mode detected during polling, will prioritize thinking detection in fallback")
        
        # 如果高频轮询失败，使用 wait_for_function（事件驱动，更可靠）
        if not polling_success:
            try:
                # 优化：减少 wait_for_function 的超时时间，加快检测
                # 修复：wait_for_function 的正确调用方式
                # Playwright 的 wait_for_function 签名：wait_for_function(expression, arg=None, timeout=None)
                # 注意：arg 和 timeout 都必须作为关键字参数传递
                # 优化：减少超时时间，从 assistant_wait_timeout 减少到 min(assistant_wait_timeout, 3) 秒
                # 修复：assistant wait 耗时 102 秒的主要原因是 wait_for_function 超时时间太长（90秒）
                # 应该使用更短的超时时间，如果超时就立即检查文本变化，而不是等待 90 秒
                # 修复：增加超时时间，从 10 秒增加到 15 秒，提高检测成功率
                # 对于 ChatGPT Pro 的思考模式，需要更长的等待时间
                wait_timeout_ms = int(min(assistant_wait_timeout, 15) * 1000)  # 最多 15 秒（从 10 秒增加到 15 秒）
                await self.page.wait_for_function(
                    """(args) => {
                        const n0 = args.n0;
                        const sel = args.sel;
                        const n = document.querySelectorAll(sel).length;
                        return n > n0;
                    }""",
                    arg={"n0": n_assist0, "sel": combined_sel},
                    timeout=wait_timeout_ms  # wait_for_function 使用毫秒，确保是整数
                )
                # 等待成功后，获取实际的 assistant_count
                n_assist1 = await self._assistant_count()
                self._log(f"ask: assistant_count increased to {n_assist1} (new message detected via wait_for_function)")
            except Exception as e:
                # wait_for_function 超时或失败，优先检查 thinking 状态
                # 关键修复：在检查文本变化之前，先检查是否在思考模式
                self._log(f"ask: wait_for_function timeout or failed ({e}), checking thinking status first...")
                
                # 优先检测 thinking 状态
                try:
                    thinking = await asyncio.wait_for(self._is_thinking(), timeout=0.5)
                    if thinking:
                        self._log("ask: detected thinking mode, continuing to wait (will not trigger manual checkpoint)")
                        # 如果检测到 thinking，继续等待，不触发 manual checkpoint
                        # 设置一个合成值，让后续逻辑继续等待
                        n_assist1 = n_assist0 + 1  # 合成值，表示"可能正在思考"
                        # 跳过 manual checkpoint，直接进入内容等待阶段
                        self._log("ask: skipping manual checkpoint due to thinking mode")
                    else:
                        # 如果没有在思考，继续原有的检查逻辑
                        self._log("ask: not in thinking mode, checking text change...")
                        try:
                            # 先快速检查计数
                            n_assist1 = await asyncio.wait_for(self._assistant_count(), timeout=0.5)
                            if n_assist1 > n_assist0:
                                self._log(f"ask: assistant_count increased to {n_assist1} (fallback check)")
                            else:
                                # 如果计数没有增加，立即检查文本变化（这是更可靠的信号）
                                try:
                                    text_quick = await asyncio.wait_for(self._last_assistant_text(), timeout=0.5)
                                    if text_quick and text_quick != last_assist_text_before:
                                        self._log("ask: text changed, assuming new message (text change detected)")
                                        n_assist1 = n_assist0 + 1  # 合成值
                                    else:
                                        # 如果文本也没有变化，再检查一次 thinking 状态（双重保险）
                                        thinking_retry = await asyncio.wait_for(self._is_thinking(), timeout=0.5)
                                        if thinking_retry:
                                            self._log("ask: detected thinking mode on retry, continuing to wait")
                                            n_assist1 = n_assist0 + 1  # 合成值，继续等待
                                        else:
                                            # 如果还是没有变化且不在思考，等待一下再检查
                                            self._log("ask: no text change and not thinking, waiting 1s and re-checking...")
                                            await asyncio.sleep(1.0)
                                            # 再次检查文本变化和 thinking 状态
                                            try:
                                                text_retry = await asyncio.wait_for(self._last_assistant_text(), timeout=0.5)
                                                thinking_final = await asyncio.wait_for(self._is_thinking(), timeout=0.5)
                                                if text_retry and text_retry != last_assist_text_before:
                                                    self._log("ask: text changed after wait, assuming new message")
                                                    n_assist1 = n_assist0 + 1
                                                elif thinking_final:
                                                    self._log("ask: still in thinking mode after wait, continuing")
                                                    n_assist1 = n_assist0 + 1  # 合成值，继续等待
                                                else:
                                                    # 如果还是没有变化且不在思考，检查计数
                                                    n_assist1 = await asyncio.wait_for(self._assistant_count(), timeout=0.5)
                                                    if n_assist1 <= n_assist0:
                                                        n_assist1 = n_assist0  # 保持原值，可能触发 manual checkpoint
                                            except Exception:
                                                n_assist1 = n_assist0  # 保持原值
                                except Exception:
                                    n_assist1 = n_assist0  # 保持原值
                        except Exception:
                            n_assist1 = n_assist0  # 保持原值
                except Exception as thinking_err:
                    # thinking 检测失败，继续原有的检查逻辑
                    self._log(f"ask: thinking detection failed ({thinking_err}), falling back to text change check...")
                    try:
                        n_assist1 = await asyncio.wait_for(self._assistant_count(), timeout=0.5)
                        if n_assist1 > n_assist0:
                            self._log(f"ask: assistant_count increased to {n_assist1} (fallback check)")
                        else:
                            n_assist1 = n_assist0  # 保持原值
                    except Exception:
                        n_assist1 = n_assist0  # 保持原值
                
                # 如果仍然没有检测到新消息，且不在思考模式，触发 manual checkpoint
                if n_assist1 <= n_assist0:
                    # 最后再检查一次 thinking 状态（三重保险）
                    try:
                        thinking_final_check = await asyncio.wait_for(self._is_thinking(), timeout=0.5)
                        if thinking_final_check:
                            self._log("ask: detected thinking mode before manual checkpoint, skipping checkpoint")
                            n_assist1 = n_assist0 + 1  # 合成值，继续等待
                        else:
                            await self.save_artifacts("no_assistant_reply")
                            # 优化：减少 manual checkpoint 的等待时间，从 30 秒减少到 15 秒
                            # 这样可以更快地检测到新消息，而不是等待 30 秒
                            elapsed = time.time() - ask_start_time
                            await self.manual_checkpoint(
                                "发送后未等到回复（可能网络/风控/页面提示）。请检查页面是否需要操作。",
                                ready_check=self._ready_check_textbox,
                                max_wait_s=min(15, timeout_s - elapsed - 5),  # 从 30 秒减少到 15 秒（P0优化）
                            )
                    except Exception:
                        # thinking 检测失败，触发 manual checkpoint
                        await self.save_artifacts("no_assistant_reply")
                        elapsed = time.time() - ask_start_time
                        await self.manual_checkpoint(
                            "发送后未等到回复（可能网络/风控/页面提示）。请检查页面是否需要操作。",
                            ready_check=self._ready_check_textbox,
                            max_wait_s=min(15, timeout_s - elapsed - 5),
                        )
                    # manual_checkpoint 后再次检查
                    try:
                        n_assist1 = await self._assistant_count()
                        if n_assist1 <= n_assist0:
                            n_assist1 = n_assist0 + 1  # 合成值
                    except Exception:
                        n_assist1 = n_assist0 + 1
        
        self._log(f"ask: assistant wait done ({time.time()-t1:.2f}s)")
        return n_assist1

    async def wait_for_message_content(
        self,
        n_assist0: int,
        n_assist1: int,
        last_assist_text_before: str,
        ask_start_time: float,
        timeout_s: float,
    ) -> bool:
        """
        等待新消息的文本内容出现
        
        Args:
            n_assist0: 发送前的 assistant 消息数量
            n_assist1: 新的 assistant 消息数量
            last_assist_text_before: 发送前的最后一条 assistant 消息文本
            ask_start_time: ask 方法开始时间
            timeout_s: 总超时时间
        
        Returns:
            True 如果找到新消息内容，False 否则
        """
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
        return new_message_found

    async def wait_for_output_stabilize(
        self,
        n_assist0: int,
        ask_start_time: float,
        timeout_s: float,
    ) -> Tuple[str, str]:
        """
        等待输出稳定
        
        Args:
            n_assist0: 发送前的 assistant 消息数量
            ask_start_time: ask 方法开始时间
            timeout_s: 总超时时间
        
        Returns:
            (final_text, page_url) 元组
        """
        # P1优化：等待输出稳定 - 只拉长度/哈希，最后一次拉全文
        self._log("ask: waiting output stabilize...")
        stable_seconds = 1.5
        last_text_len = 0
        last_text_hash = ""
        last_change = time.time()
        hb = time.time()
        # 用于检测文本是否在增长
        last_text_len_history = []
        # 标记是否已经拉取过完整文本（用于最终返回）
        final_text_fetched = False
        final_text = ""

        while time.time() - ask_start_time < timeout_s:
            elapsed = time.time() - ask_start_time
            remaining = timeout_s - elapsed
            
            if remaining <= 0:
                break
                
            try:
                # P1优化：在浏览器侧只返回长度和哈希，不传输完整文本
                # 这样可以减少跨进程传输和 DOM layout 负担
                n_assist_current = await self._assistant_count()
                if n_assist_current > n_assist0:
                    target_index = n_assist_current - 1
                else:
                    target_index = max(0, n_assist_current - 1)
                
                # 使用 JS evaluate 获取长度和哈希（不传输完整文本）
                combined_sel = ", ".join(self.ASSISTANT_MSG)
                result = await self.page.evaluate(
                    """(args) => {
                        const sel = args.sel;
                        const idx = args.idx;
                        const els = document.querySelectorAll(sel);
                        if (idx < 0 || idx >= els.length) return {len: 0, hash: ''};
                        const el = els[idx];
                        const text = (el.innerText || el.textContent || '').trim();
                        const len = text.length;
                        // 简单哈希：取前8个字符的字符码和（避免完整文本传输）
                        let hash = 0;
                        for (let i = 0; i < Math.min(8, text.length); i++) {
                            hash = ((hash << 5) - hash) + text.charCodeAt(i);
                            hash = hash & hash; // Convert to 32bit integer
                        }
                        return {len: len, hash: hash.toString(36)};
                    }""",
                    {"sel": combined_sel, "idx": target_index}
                )
                
                current_len = result.get("len", 0) if isinstance(result, dict) else 0
                current_hash = result.get("hash", "") if isinstance(result, dict) else ""
                
                # 确保获取的是新消息（不是发送前的旧消息）
                if current_len > 0 and current_hash != "":
                    # 检查长度或哈希是否变化
                    if current_len != last_text_len or current_hash != last_text_hash:
                        last_text_len = current_len
                        last_text_hash = current_hash
                        last_change = time.time()
                        if current_len > 0:
                            self._log(f"ask: text updated (len={current_len}, remaining={remaining:.1f}s)")

                    # 检查是否正在生成
                    try:
                        generating = await asyncio.wait_for(self._is_generating(), timeout=0.5)
                    except Exception:
                        generating = False
                    
                    # 关键修复：检查 ChatGPT Pro 是否还在思考中
                    # 思考模式下，即使 generating=False 且内容没有变化，也不代表处理完成
                    try:
                        thinking = await asyncio.wait_for(self._is_thinking(), timeout=0.5)
                    except Exception:
                        thinking = False
                    
                    if thinking:
                        self._log(f"ask: ChatGPT Pro 还在思考中，继续等待（len={current_len}, remaining={remaining:.1f}s）")
                        # 思考状态下，即使内容没有变化，也要继续等待
                        # 重置 last_change，避免过早认为稳定
                        last_change = time.time()
                        await asyncio.sleep(0.3)
                        continue
                    
                    # 补充逻辑：如果文本长度在增加，强制认为 generating=True
                    if not generating and current_len > 0:
                        last_text_len_history.append((time.time(), current_len))
                        last_text_len_history[:] = last_text_len_history[-3:]
                        if len(last_text_len_history) >= 2:
                            prev_len = last_text_len_history[-2][1]
                            if current_len > prev_len:
                                generating = True
                                self._log(f"ask: text growing ({prev_len}->{current_len}), forcing generating=True")
                    
                    # 如果还在等待首字，保持高频检查
                    if current_len == 0 and not generating:
                        await asyncio.sleep(0.2)
                        continue
                    
                    # 关键优化：如果内容长度长时间不变（>30秒），即使generating=True，也应该认为已经稳定
                    # 这可以避免_is_generating()误判导致的长时间等待
                    time_since_change = time.time() - last_change
                    if current_len > 0 and time_since_change >= 30.0:
                        # 内容超过30秒没有变化，即使generating=True，也认为已经稳定
                        self._log(f"ask: content unchanged for {time_since_change:.1f}s (len={current_len}), forcing stabilization even if generating={generating}")
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
                                final_text = ""  # 如果拉取失败，返回空字符串
                        
                        elapsed = time.time() - ask_start_time
                        self._log(f"ask: done (stabilized, total={elapsed:.1f}s, content unchanged for {time_since_change:.1f}s, len={current_len})")
                        return final_text, self.page.url
                    
                    # 快速路径：如果 generating=False 且文本长度在 0.5 秒内没有变化，直接认为稳定
                    # 关键修复：必须确保不在思考状态，才能认为稳定
                    if current_len > 0 and (not generating) and (not thinking) and time_since_change >= 0.5 and time_since_change >= stable_seconds:
                        # P1优化：稳定后，只拉取一次完整文本
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
                                final_text = ""  # 如果拉取失败，返回空字符串
                        
                        elapsed = time.time() - ask_start_time
                        self._log(f"ask: done (stabilized, total={elapsed:.1f}s, fast path: {time_since_change:.1f}s no change, len={current_len})")
                        return final_text, self.page.url
                    
                    # 原有逻辑：稳定时间达到且不在生成
                    # 关键修复：必须确保不在思考状态，才能认为稳定
                    if current_len > 0 and (time.time() - last_change) >= stable_seconds and (not generating) and (not thinking):
                        # P1优化：稳定后，只拉取一次完整文本
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
                                final_text = ""  # 如果拉取失败，返回空字符串
                        
                        elapsed = time.time() - ask_start_time
                        self._log(f"ask: done (stabilized, total={elapsed:.1f}s, len={current_len})")
                        return final_text, self.page.url
            except asyncio.TimeoutError:
                # DOM 查询超时，继续等待
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

            # 优化：减少检查间隔，从0.4秒减少到0.3秒，加快检测速度
            await asyncio.sleep(0.3)

        # 超时处理
        elapsed = time.time() - ask_start_time
        await self.save_artifacts("answer_timeout")
        final_text = ""
        try:
            final_text = await asyncio.wait_for(self._last_assistant_text(), timeout=2.0)
        except:
            pass
        
        if final_text:
            self._log(f"ask: timeout but got partial answer (len={len(final_text)}, elapsed={elapsed:.1f}s)")
            return final_text, self.page.url
        else:
            raise TimeoutError(
                f"ask: timeout after {elapsed:.1f}s (limit={timeout_s}s). "
                f"No valid answer received. last_text_len={last_text_len}"
            )

