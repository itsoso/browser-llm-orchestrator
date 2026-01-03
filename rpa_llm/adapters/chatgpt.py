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
from .chatgpt_model import ChatGPTModelSelector
from .chatgpt_state import ChatGPTStateDetector
from .chatgpt_textbox import ChatGPTTextboxFinder
from .chatgpt_send import ChatGPTSender
from .chatgpt_wait import ChatGPTWaiter


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
        # 侧边栏新聊天按钮（最常见）
        'nav a[href="/"]',
        'a[data-testid="create-new-chat-button"]',
        'button[data-testid="create-new-chat-button"]',
        # 文本匹配
        'a:has-text("新聊天")',
        'button:has-text("新聊天")',
        'a:has-text("New chat")',
        'button:has-text("New chat")',
        # aria-label 匹配
        'a[aria-label*="New chat"]',
        'button[aria-label*="New chat"]',
        'a[aria-label*="新聊天"]',
        'button[aria-label*="新聊天"]',
        # 侧边栏图标按钮（备用）
        'nav button:first-child',
        'aside a[href="/"]',
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
        self._model_version = None  # 存储当前请求的模型版本
        
        # 初始化模块化组件（延迟初始化，因为需要 page 对象）
        self._textbox_finder = None
        self._state_detector = None
        self._model_selector = None
        self._sender = None
        self._waiter = None
    
    def _init_modules(self) -> None:
        """延迟初始化模块化组件（需要 page 对象）"""
        if self._textbox_finder is None:
            self._textbox_finder = ChatGPTTextboxFinder(
                page=self.page,
                logger=self._log,
                manual_checkpoint_fn=self.manual_checkpoint,
                save_artifacts_fn=self.save_artifacts,
            )
        
        if self._state_detector is None:
            self._state_detector = ChatGPTStateDetector(
                page=self.page,
                logger=self._log,
            )
        
        if self._model_selector is None:
            self._model_selector = ChatGPTModelSelector(
                page=self.page,
                logger=self._log,
            )
        
        if self._sender is None:
            self._sender = ChatGPTSender(
                page=self.page,
                logger=self._log,
                find_textbox_fn=self._find_textbox_any_frame,
                user_count_fn=self._user_count,
                dismiss_overlays_fn=self._dismiss_overlays,
                ready_check_textbox_fn=self._ready_check_textbox,
                manual_checkpoint_fn=self.manual_checkpoint,
                save_artifacts_fn=self.save_artifacts,
                clean_newlines_fn=self.clean_newlines,
                tb_clear_fn=self._tb_clear,
                tb_set_text_fn=self._tb_set_text,
                tb_get_text_fn=self._tb_get_text,
                tb_kind_fn=self._tb_kind,
            )
        
        if self._waiter is None:
            self._waiter = ChatGPTWaiter(
                page=self.page,
                logger=self._log,
                assistant_count_fn=self._assistant_count,
                last_assistant_text_fn=self._last_assistant_text,
                get_assistant_text_by_index_fn=self._get_assistant_text_by_index,
                is_generating_fn=self._is_generating,
                is_thinking_fn=self._is_thinking,
                ready_check_textbox_fn=self._ready_check_textbox,
                manual_checkpoint_fn=self.manual_checkpoint,
                save_artifacts_fn=self.save_artifacts,
            )

    def _log(self, msg: str) -> None:
        print(f"[{beijing_now_iso()}] [{self.site_id}] {msg}", flush=True)

    def _desired_variant(self) -> str:
        """
        确定所需的 ChatGPT 变体类型
        
        返回:
            "pro": 需要打开模型选择器选择 Pro 相关模型
            "thinking": 只需要设置 thinking toggle
            "instant": 需要打开模型选择器选择 Instant 相关模型，或只设置 thinking toggle
            "custom": 需要打开模型选择器选择自定义模型（如 5.2instant, 5.2pro）
        """
        # 优先使用实例变量（从 ask 方法传入），其次使用环境变量
        if self._model_version:
            v = self._model_version.strip().lower()
            # 关键修复：先检查完整的组合匹配，再检查部分匹配
            # 这样可以确保 "5.2instant" 不会被误判为 "pro"
            
            # 1. 检查完整的组合（优先级最高）
            if "5.2instant" in v or "5-2-instant" in v or "5.2-instant" in v:
                return "custom"  # 需要打开模型选择器选择 5.2 Instant
            if "5.2pro" in v or "5-2-pro" in v or "5.2-pro" in v or "gpt-5.2-pro" in v:
                return "pro"  # 需要打开模型选择器选择 5.2 Pro
            
            # 2. 检查部分匹配（通用匹配）
            if "thinking" in v:
                return "thinking"
            if "instant" in v:
                # 如果是单独的 "instant"，只需要设置 thinking toggle
                # 如果是 "5.2instant" 已经在上面处理了
                return "instant"
            if "gpt-5" in v or "gpt5" in v:
                return "pro"  # GPT-5 相关默认是 pro
            if "pro" in v:
                return "pro"
            
            # 如果无法识别，返回 "custom" 让 ensure_variant 处理
            return "custom"
        
        # CHATGPT_VARIANT=instant|thinking|pro|5.2pro|5.2instant|gpt-5.2-pro
        v = (os.environ.get("CHATGPT_VARIANT") or "thinking").strip().lower()
        
        # 先检查精确匹配
        if v in ("instant", "thinking", "pro"):
            return v
        
        # 检查完整的组合
        if "5.2instant" in v or "5-2-instant" in v or "5.2-instant" in v:
            return "custom"
        if "5.2pro" in v or "5-2-pro" in v or "5.2-pro" in v or "gpt-5.2-pro" in v:
            return "pro"
        
        # 检查部分匹配（需要排除已处理的组合）
        if "5.2" in v:
            # 如果包含 5.2 但不包含 instant，默认是 pro
            if "instant" not in v:
                return "pro"
            # 如果包含 5.2 和 instant，已经在上面处理了
            return "custom"
        
        if "gpt-5" in v or "gpt5" in v:
            return "pro"  # GPT-5 相关默认是 pro
        if "pro" in v and "thinking" not in v and "instant" not in v:
            return "pro"
        
        return "thinking"

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
            # 优化：减少等待时间，加快检查速度
            await loc.wait_for(state="attached", timeout=300)  # 从 500ms 减少到 300ms
            if await asyncio.wait_for(self._try_visible(loc), timeout=0.3):  # 从 0.5s 减少到 0.3s
                return loc, frame, how
        except (asyncio.TimeoutError, Exception):
            pass
        return None

    async def _ready_check_textbox(self) -> bool:
        """检查输入框是否就绪（用于 manual checkpoint 的 ready_check）"""
        try:
            await self._dismiss_overlays()
        except Exception:
            pass  # dismiss_overlays 失败不影响继续检查
        
        # 多次尝试查找，增加成功率
        for attempt in range(3):
            try:
                found = await self._find_textbox_any_frame()
                if found:
                    return True
            except Exception:
                pass
            
            if attempt < 2:
                await asyncio.sleep(0.3)  # 等待一下再重试
        
        return False

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
        # 优化：添加超时机制，避免长时间等待
        t0 = time.time()
        timeout_s = 5.0  # 最多等待 5 秒
        
        for sel in self.THINKING_TOGGLE:
            if time.time() - t0 > timeout_s:
                self._log(f"mode: thinking toggle timeout after {timeout_s}s, skip")
                return
            
            try:
                btn = self.page.locator(sel).first
                # 优化：使用更快的检查方式，减少等待时间
                try:
                    count = await asyncio.wait_for(btn.count(), timeout=0.5)
                    if count == 0:
                        continue
                except (asyncio.TimeoutError, Exception):
                    continue
                
                try:
                    if not await asyncio.wait_for(btn.is_visible(), timeout=0.5):
                        continue
                except (asyncio.TimeoutError, Exception):
                    continue
                
                pressed = await asyncio.wait_for(btn.get_attribute("aria-pressed"), timeout=0.5)
                is_on = (pressed == "true")
                if want_thinking != is_on:
                    await btn.click(timeout=2000)  # 2秒超时
                    await asyncio.sleep(0.2)  # 从 0.4s 减少到 0.2s
                    self._log(f"mode: set thinking={want_thinking} via {sel}")
                return
            except (asyncio.TimeoutError, Exception):
                continue

    async def _select_model_menu_item(self, pattern: re.Pattern, model_version: Optional[str] = None) -> bool:
        """
        从下拉菜单中选择模型
        
        Args:
            pattern: 用于匹配模型名称的正则表达式
            model_version: 可选的模型版本字符串（如 "5.2pro", "GPT-5", "GPT-4o"），用于更精确的匹配
        
        Returns:
            是否成功选择模型
        """
        candidates = [
            self.page.get_by_role("menuitem"),
            self.page.get_by_role("option"),
            self.page.locator("div[role='menuitem']"),
            self.page.locator("button"),
        ]
        
        # 如果提供了 model_version，构建更精确的匹配模式
        if model_version:
            model_version_lower = model_version.lower()
            # 关键修复：优先处理完整的组合匹配，再处理部分匹配
            # 这样可以确保 "5.2instant" 优先匹配 Instant，而不是 Pro
            
            # 1. 优先检查完整的组合（优先级最高）
            if "5.2instant" in model_version_lower or "5-2-instant" in model_version_lower or "5.2-instant" in model_version_lower:
                # 5.2 Instant 的精确匹配
                enhanced_pattern = re.compile(r"5[.\-]?2.*instant|instant.*5[.\-]?2|5[.\-]?2.*即时|即时.*5[.\-]?2", re.I)
            elif "5.2pro" in model_version_lower or "5-2-pro" in model_version_lower or "5.2-pro" in model_version_lower:
                # 5.2 Pro 的精确匹配
                enhanced_pattern = re.compile(r"5[.\-]?2.*pro|pro.*5[.\-]?2|5[.\-]?2.*专业|专业.*5[.\-]?2", re.I)
            else:
                # 2. 部分匹配（通用匹配）
                version_parts = []
                # 根据日志分析，ChatGPT 菜单选项格式如下：
                # - "Instant 即刻回答"
                # - "Pro 研究级智能模型"
                # - "Thinking 思考更充分，回答更优质"
                # - "Auto 自动决定思考时长"
                if "instant" in model_version_lower:
                    version_parts.append(r"\bInstant\b|即刻")
                if "pro" in model_version_lower:
                    version_parts.append(r"\bPro\b|研究级")
                if "thinking" in model_version_lower:
                    version_parts.append(r"\bThinking\b|思考更充分")
                if "auto" in model_version_lower:
                    version_parts.append(r"\bAuto\b|自动")
                if "4o" in model_version_lower or "4-o" in model_version_lower:
                    version_parts.append(r"4[.\-]?o")
                
                # 如果有关键部分，使用更精确的模式
                if version_parts:
                    enhanced_pattern = re.compile("|".join(version_parts), re.I)
                else:
                    enhanced_pattern = pattern
        else:
            enhanced_pattern = pattern
        
        # 调试：记录所有看到的菜单项
        all_items_seen = []
        
        for c in candidates:
            try:
                cnt = await c.count()
                self._log(f"mode: checking candidate with {cnt} items")
            except Exception:
                continue
            for i in range(min(cnt, 60)):
                try:
                    item = c.nth(i)
                    txt = (await item.inner_text()).strip()
                    if not txt:
                        continue
                    
                    # 记录看到的菜单项（前30个字符）
                    txt_short = txt[:30].replace('\n', ' ')
                    all_items_seen.append(txt_short)
                    
                    # 优先使用增强模式匹配
                    if model_version and enhanced_pattern.search(txt):
                        self._log(f"mode: selecting model '{txt_short}' (matched by enhanced pattern)")
                        await item.click()
                        await asyncio.sleep(0.6)
                        return True
                    
                    # 回退到原始模式匹配
                    if pattern.search(txt):
                        self._log(f"mode: selecting model '{txt_short}' (matched by default pattern)")
                        await item.click()
                        await asyncio.sleep(0.6)
                        return True
                except Exception:
                    continue
        
        # 如果没有找到匹配的模型，记录所有看到的菜单项以便调试
        if all_items_seen:
            self._log(f"mode: no match found. Seen items: {all_items_seen[:10]}")
        else:
            self._log("mode: no menu items found (picker may not have opened)")
        
        return False

    async def ensure_variant(self, model_version: Optional[str] = None) -> None:
        """
        设置 ChatGPT 模型版本（best-effort，只设置一次）
        
        Args:
            model_version: 模型版本字符串（如 "5.2pro", "GPT-5", "pro", "thinking", "instant"）
                          如果提供，会优先使用此参数，而不是环境变量或实例变量
        """
        if self._variant_set and not model_version:
            return
        
        # 如果提供了 model_version 参数，临时设置到实例变量
        original_model_version = self._model_version
        if model_version:
            self._model_version = model_version
            self._variant_set = False  # 允许重新设置
        
        v = self._desired_variant()
        self._log(f"mode: desired={v}, model_version={model_version or self._model_version or 'env'}")

        # 处理 thinking 模式（只需要设置 toggle，不需要打开模型选择器）
        if v == "thinking":
            await self._set_thinking_toggle(want_thinking=True)
            self._variant_set = True
            if model_version:
                self._model_version = model_version
            return

        # 处理 instant 模式（单独的 "instant"，只需要设置 toggle）
        if v == "instant":
            await self._set_thinking_toggle(want_thinking=False)
            self._variant_set = True
            if model_version:
                self._model_version = model_version
            return

        # 处理需要打开模型选择器的情况：pro, custom (如 5.2instant, 5.2pro)
        # 或者明确指定了 model_version 且不是 thinking/instant
        if v in ("pro", "custom") or (model_version and model_version.lower() not in ("thinking", "instant")):
            opened = False
            self._log(f"mode: trying to open model picker for variant={v}, model_version={model_version}")
            for sel in self.MODEL_PICKER_BTN:
                try:
                    btn = self.page.locator(sel).first
                    btn_count = await btn.count()
                    if btn_count > 0:
                        is_visible = await btn.is_visible()
                        self._log(f"mode: found button '{sel}' (count={btn_count}, visible={is_visible})")
                        if is_visible:
                            await btn.click()
                            await asyncio.sleep(0.8)  # 增加等待时间，确保菜单打开
                            opened = True
                            self._log(f"mode: clicked model picker button '{sel}'")
                            break
                except Exception as e:
                    self._log(f"mode: button '{sel}' failed: {e}")
                    continue

            if not opened:
                # 找不到模型选择器，不阻塞
                self._log("mode: model picker not found; skip")
                self._variant_set = True
                if model_version:
                    self._model_version = model_version
                return

            # 构建匹配模式
            mv = (model_version or self._model_version or "").lower()
            
            # 根据日志分析，ChatGPT 菜单选项格式如下：
            # - "Auto 自动决定思考时长"
            # - "Instant 即刻回答"
            # - "Thinking 思考更充分，回答更优质"
            # - "Pro 研究级智能模型"
            # - "传统模型"
            
            # 关键修复：匹配菜单中的实际文本
            if "instant" in mv:
                # 匹配 Instant 或 即刻回答（适用于 5.2instant, instant 等）
                pattern = re.compile(r"\bInstant\b|即刻", re.I)
                self._log(f"mode: using Instant pattern for model_version={mv}")
            elif "pro" in mv:
                # 匹配 Pro 或 研究级智能模型（适用于 5.2pro, pro 等）
                pattern = re.compile(r"\bPro\b|研究级", re.I)
                self._log(f"mode: using Pro pattern for model_version={mv}")
            elif "thinking" in mv:
                # 匹配 Thinking（更精确，避免匹配 "自动决定思考时长"）
                pattern = re.compile(r"\bThinking\b|思考更充分", re.I)
                self._log(f"mode: using Thinking pattern for model_version={mv}")
            elif "auto" in mv:
                # 匹配 Auto 或 自动
                pattern = re.compile(r"\bAuto\b|自动", re.I)
                self._log(f"mode: using Auto pattern for model_version={mv}")
            elif "4o" in mv or "4-o" in mv:
                # 匹配 GPT-4o（在传统模型菜单中）
                pattern = re.compile(r"4[.\-]?o|gpt[.\-]?4", re.I)
                self._log(f"mode: using GPT-4o pattern for model_version={mv}")
            else:
                # 默认匹配 Pro
                pattern = re.compile(r"\bPro\b|研究级", re.I)
                self._log(f"mode: using default Pro pattern for model_version={mv}")
            
            ok = await self._select_model_menu_item(pattern, model_version=model_version or self._model_version)
            if not ok:
                self._log(f"mode: cannot auto-select model (version={model_version or self._model_version}); skip")
            else:
                self._log(f"mode: successfully selected model (version={model_version or self._model_version})")
            self._variant_set = True
            if model_version:
                self._model_version = model_version
            return
        
        # 恢复原始 model_version（如果提供了临时参数）
        if model_version:
            self._model_version = original_model_version

    async def ensure_ready(self) -> None:
        self._log("ensure_ready: start")
        # 初始化模块化组件（需要 page 对象）
        self._init_modules()
        # 减少初始延迟，页面可能已经加载完成
        await asyncio.sleep(0.1)  # 从 0.2 秒减少到 0.1 秒
        
        # 快速路径：直接用最稳定的选择器探测（优化：使用 page.evaluate 更快）
        try:
            # 优化：使用 page.evaluate 直接检查，避免 Playwright 的额外开销
            result = await asyncio.wait_for(
                self.page.evaluate("""() => {
                    const el = document.querySelector('div[id="prompt-textarea"]');
                    if (!el) return false;
                    // 检查元素是否可见（简化检查，不等待 actionability）
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null;
                }"""),
                timeout=0.2  # P1优化：从 0.5 秒减少到 0.2 秒，加快 fast path 检查
            )
            if result:
                self._log("ensure_ready: fast path via prompt-textarea")
                return
        except (asyncio.TimeoutError, Exception):
            pass
        
        # 优化：如果快速路径失败，尝试使用 locator（但减少超时）
        # 修复：loc.count() 返回 coroutine，需要 await
        # P1优化：从 0.3 秒减少到 0.15 秒，加快 fast path 检查
        try:
            loc = self.page.locator('div[id="prompt-textarea"]').first
            count = await asyncio.wait_for(loc.count(), timeout=0.15)  # 从 0.3 秒减少到 0.15 秒
            if count > 0:
                # 不等待 is_visible()，直接返回（如果元素存在，通常就是可见的）
                self._log("ensure_ready: fast path via prompt-textarea (count check)")
                return
        except (asyncio.TimeoutError, Exception):
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

        # 优化：减少总超时时间，从 60 秒减少到 30 秒
        # 如果 30 秒内找不到，立即触发 manual checkpoint，而不是继续等待
        total_timeout_s = 30  # 从 60 秒减少到 30 秒
        t0 = time.time()
        hb = t0
        check_count = 0
        # 优化：如果连续多次找不到，增加 dismiss overlays 的频率
        last_dismiss_time = t0

        while time.time() - t0 < total_timeout_s:
            # 优化：每 1.5 秒 dismiss overlays 一次（从 2 秒减少），加快检查频率
            if time.time() - last_dismiss_time >= 1.5:  # 从 2.0 秒减少到 1.5 秒
                await self._dismiss_overlays()
                last_dismiss_time = time.time()
            
            found = await self._find_textbox_any_frame()
            if found:
                _, frame, how = found
                self._log(f"ensure_ready: textbox OK via {how}. frame={frame.url} (took {time.time()-t0:.2f}s)")
                return

            check_count += 1
            if time.time() - hb >= 5:
                self._log(f"ensure_ready: still locating textbox... (attempt {check_count})")
                hb = time.time()

            # 优化：前几次快速检查，之后逐渐增加间隔，但最大不超过 0.12 秒
            # 进一步减少等待时间，加快检查频率
            sleep_time = 0.05 if check_count < 8 else 0.12  # 从 0.08/0.15 减少到 0.05/0.12，加快检查频率
            await asyncio.sleep(sleep_time)

        await self.save_artifacts("ensure_ready_failed")
        await self.manual_checkpoint(
            "未检测到输入框（可能弹窗遮挡/页面未完成挂载）。请手动点一下输入框或完成登录后继续。",
            ready_check=self._ready_check_textbox,
            max_wait_s=90,
        )

        # 人工处理后再确认一次（增加重试和等待时间）
        # 给页面更多时间稳定，因为用户可能刚刚完成了操作
        await asyncio.sleep(1.0)  # 等待页面稳定
        
        # 多次尝试，增加成功率
        for retry in range(5):
            try:
                if await self._ready_check_textbox():
                    self._log(f"ensure_ready: textbox found after manual checkpoint (retry {retry+1})")
                    return
            except Exception as e:
                self._log(f"ensure_ready: ready_check_textbox error (retry {retry+1}): {e}")
            
            if retry < 4:
                await asyncio.sleep(0.5)  # 等待后重试
        
        # 如果所有重试都失败，再尝试一次快速检查
        try:
            if await self._fast_ready_check():
                self._log("ensure_ready: textbox found via fast_ready_check after manual checkpoint")
                return
        except Exception:
            pass
        
        await self.save_artifacts("ensure_ready_failed_after_manual")
        raise RuntimeError("ensure_ready: still cannot locate textbox after manual checkpoint.")

    async def _click_new_chat_button(self) -> bool:
        """
        P0-1 修复：使用多策略点击新聊天按钮。
        
        策略顺序：
        1. 使用 JS 直接点击（最可靠，避免 actionability 等待）
        2. 使用 Playwright try_click（作为备用）
        3. 使用键盘快捷键（如果有的话）
        
        Returns:
            True 如果点击成功，False 如果所有策略都失败
        """
        # 策略 1：使用 JS 直接点击（优先）
        # 这避免了 Playwright 的 actionability 等待和遮挡检测
        js_selectors = [
            'nav a[href="/"]',
            'a[data-testid="create-new-chat-button"]',
            'button[data-testid="create-new-chat-button"]',
            'a[href="/"]',
        ]
        
        for sel in js_selectors:
            try:
                clicked = await self.page.evaluate(
                    """(selector) => {
                        const el = document.querySelector(selector);
                        if (el && el.offsetParent !== null) {
                            // 确保元素可见
                            el.scrollIntoView({behavior: 'instant', block: 'center'});
                            // 触发点击
                            el.click();
                            return true;
                        }
                        return false;
                    }""",
                    sel
                )
                if clicked:
                    self._log(f"new_chat: JS click succeeded on {sel}")
                    return True
            except Exception as e:
                self._log(f"new_chat: JS click failed on {sel}: {e}")
                continue
        
        # 策略 2：使用文本匹配的 JS 点击
        try:
            clicked = await self.page.evaluate(
                """() => {
                    // 查找包含"新聊天"或"New chat"的链接或按钮
                    const texts = ['新聊天', 'New chat', 'New Chat'];
                    for (const text of texts) {
                        const elements = document.querySelectorAll('a, button');
                        for (const el of elements) {
                            if (el.textContent && el.textContent.includes(text)) {
                                if (el.offsetParent !== null) {
                                    el.scrollIntoView({behavior: 'instant', block: 'center'});
                                    el.click();
                                    return true;
                                }
                            }
                        }
                    }
                    return false;
                }"""
            )
            if clicked:
                self._log("new_chat: JS text-based click succeeded")
                return True
        except Exception as e:
            self._log(f"new_chat: JS text-based click failed: {e}")
        
        # 策略 3：使用 Playwright try_click（作为最后手段）
        # 使用较短的超时，因为我们已经尝试了 JS 点击
        try:
            result = await self.try_click(self.NEW_CHAT, timeout_ms=2000)
            if result:
                self._log("new_chat: Playwright try_click succeeded")
                return True
        except Exception as e:
            self._log(f"new_chat: Playwright try_click failed: {e}")
        
        return False

    async def new_chat(self) -> None:
        """
        创建新聊天窗口。
        
        关键修复：必须确认新对话真正创建，不能只检查 textarea 存在。
        检测标准：
        1. URL 变化（从 /c/xxx 变为 / 或新的 /c/yyy）
        2. 或 assistant_count 变为 0（新对话没有历史消息）
        3. 如果点击失败，强制导航到 chatgpt.com 首页
        """
        self._log("new_chat: start")
        
        # 记录当前状态
        original_url = self.page.url
        original_assistant_count = await self._assistant_count()
        self._log(f"new_chat: original_url={original_url}, assistant_count={original_assistant_count}")
        
        # P0-1 修复：使用多策略点击新聊天按钮
        click_success = await self._click_new_chat_button()
        self._log(f"new_chat: click result={click_success}")
        
        # 等待新对话确认（URL 变化或 assistant_count 变为 0）
        t0 = time.time()
        max_wait_s = 8.0  # 最多等待 8 秒
        check_interval = 0.1
        new_chat_confirmed = False
        
        while time.time() - t0 < max_wait_s:
            try:
                current_url = self.page.url
                current_assistant_count = await self._assistant_count()
                
                # 检查是否是新对话
                url_changed = current_url != original_url
                is_home_page = not "/c/" in current_url  # 首页没有 /c/ 路径
                no_messages = current_assistant_count == 0
                
                if url_changed or is_home_page or no_messages:
                    elapsed = time.time() - t0
                    self._log(f"new_chat: confirmed (url_changed={url_changed}, is_home={is_home_page}, no_messages={no_messages}, elapsed={elapsed:.2f}s)")
                    new_chat_confirmed = True
                    break
                    
            except Exception as e:
                self._log(f"new_chat: check failed: {e}")
            
            await asyncio.sleep(check_interval)
        
        # 如果点击"新聊天"失败，强制导航到首页
        if not new_chat_confirmed:
            self._log("new_chat: click did not work, forcing navigation to homepage...")
            try:
                # 直接导航到 ChatGPT 首页
                await self.page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=15000)
                self._log("new_chat: navigated to homepage")
                
                # 等待页面稳定
                await asyncio.sleep(1.0)
                
                # 再次确认
                current_assistant_count = await self._assistant_count()
                if current_assistant_count == 0:
                    self._log("new_chat: homepage confirmed (assistant_count=0)")
                    new_chat_confirmed = True
                else:
                    self._log(f"new_chat: homepage has {current_assistant_count} assistant messages, may have loaded history")
                    
            except Exception as e:
                self._log(f"new_chat: navigation failed: {e}")
        
        # 等待 textarea 出现
        t1 = time.time()
        textarea_wait_s = 5.0
        while time.time() - t1 < textarea_wait_s:
            try:
                has_textarea = await self.page.evaluate(
                    """() => {
                        const textarea = document.querySelector('#prompt-textarea');
                        return textarea && textarea.offsetParent !== null;
                    }"""
                )
                if has_textarea:
                    self._log(f"new_chat: textarea appeared ({time.time() - t1:.2f}s)")
                    break
            except Exception:
                pass
            await asyncio.sleep(0.05)
        
        # P0-3 修复：增加 DOM 稳定等待，确保 textbox 可用
        await asyncio.sleep(0.5)  # 增加等待时间（从 0.3 到 0.5）
        await self._dismiss_overlays()
        await asyncio.sleep(0.3)  # 增加等待时间（从 0.2 到 0.3）
        
        # P0-3 修复：等待 textbox 真正可交互（不仅仅是可见）
        t2 = time.time()
        textbox_ready_wait_s = 3.0
        textbox_ready = False
        while time.time() - t2 < textbox_ready_wait_s:
            try:
                is_ready = await self.page.evaluate(
                    """() => {
                        const textarea = document.querySelector('#prompt-textarea');
                        if (!textarea) return false;
                        // 检查是否可见
                        if (textarea.offsetParent === null) return false;
                        // 检查是否可交互（不是 disabled 或 readonly）
                        if (textarea.disabled || textarea.readOnly) return false;
                        // 检查是否有父元素遮挡
                        const rect = textarea.getBoundingClientRect();
                        const centerX = rect.left + rect.width / 2;
                        const centerY = rect.top + rect.height / 2;
                        const topElement = document.elementFromPoint(centerX, centerY);
                        // 如果中心点被其他元素遮挡，可能不可交互
                        if (topElement && !textarea.contains(topElement) && topElement !== textarea) {
                            // 检查遮挡元素是否是弹窗或对话框
                            const tagName = topElement.tagName.toLowerCase();
                            if (tagName === 'dialog' || topElement.getAttribute('role') === 'dialog') {
                                return false;
                            }
                        }
                        return true;
                    }"""
                )
                if is_ready:
                    textbox_ready = True
                    self._log(f"new_chat: textbox ready ({time.time() - t2:.2f}s)")
                    break
            except Exception:
                pass
            await asyncio.sleep(0.1)
        
        if not textbox_ready:
            self._log("new_chat: warning - textbox may not be fully ready")
        
        # 最终确认
        final_url = self.page.url
        final_assistant_count = await self._assistant_count()
        self._log(f"new_chat: done (url={final_url}, assistant_count={final_assistant_count})")

    async def _assistant_count(self) -> int:
        """
        获取 assistant 消息数量，使用 JS evaluate 直接查询（P0优化）。
        避免 Playwright locator.count() + asyncio.wait_for 导致的 Future exception。
        """
        # P0优化：使用 JS evaluate 直接查询，避免 Playwright actionability 等待和 Future exception
        combined_selector = ", ".join(self.ASSISTANT_MSG)
        try:
            count = await self.page.evaluate(
                """(sel) => {
                    return document.querySelectorAll(sel).length;
                }""",
                combined_selector
            )
            return count if isinstance(count, int) else 0
        except Exception:
            return 0

    async def _user_count(self) -> int:
        """
        获取用户消息数量，使用 JS evaluate 直接查询（P0优化）。
        避免 Playwright locator.count() + asyncio.wait_for 导致的 Future exception。
        """
        # P0优化：使用 JS evaluate 直接查询，避免 Playwright actionability 等待和 Future exception
        combined_selector = ", ".join(self.USER_MSG)
        try:
            count = await self.page.evaluate(
                """(sel) => {
                    return document.querySelectorAll(sel).length;
                }""",
                combined_selector
            )
            return count if isinstance(count, int) else 0
        except Exception:
            return 0

    async def _last_assistant_text(self) -> str:
        """
        获取最后一条 assistant 消息的文本。
        修复：添加显式超时，避免默认 30 秒超时导致 Future exception was never retrieved。
        """
        for sel in self.ASSISTANT_MSG:
            loc = self.page.locator(sel)
            try:
                cnt = await loc.count()
                if cnt > 0:
                    # 修复：使用显式超时（2秒），避免默认 30 秒超时导致 Future exception
                    return (await loc.nth(cnt - 1).inner_text(timeout=2000)).strip()
            except Exception:
                continue
        return ""
    
    async def _get_assistant_text_by_index(self, index: int) -> str:
        """
        根据索引获取 assistant 消息文本（0-index）。
        当 assistant_count(after)=k 时，读取第 k-1 条消息（0-index）。
        
        修复：添加显式超时，避免默认 30 秒超时导致 Future exception was never retrieved。
        
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
                    # 修复：使用显式超时（2秒），避免默认 30 秒超时导致 Future exception
                    text = await loc.nth(index).inner_text(timeout=2000)
                    if text:
                        return text.strip()
            except Exception:
                continue
        return ""

    async def _is_generating(self) -> bool:
        """
        检查是否正在生成，使用 JS evaluate 直接查询（P0优化）。
        避免 Playwright locator + wait_for 导致的 Future exception。
        """
        # P0优化：使用 JS evaluate 直接查询，避免 Playwright actionability 等待和 Future exception
        # 注意：`:has-text()` 是 Playwright 特有的选择器，不能用于原生 querySelectorAll
        # 只使用原生 CSS 选择器（aria-label 属性选择器）
        # 过滤掉包含 `:has-text()` 的选择器
        native_selectors = [
            sel for sel in self.STOP_BTN 
            if ':has-text(' not in sel and 'aria-label' in sel
        ]
        
        if not native_selectors:
            # 如果没有原生选择器，fallback 到简单的 button 查询
            native_selectors = ['button[aria-label*="Stop"]', 'button[aria-label*="停止"]']
        
        combined_selector = ", ".join(native_selectors)
        try:
            has_stop = await self.page.evaluate(
                """(sel) => {
                    try {
                        const els = document.querySelectorAll(sel);
                        for (let el of els) {
                            if (el.offsetParent !== null) {  // 检查是否可见
                                return true;
                            }
                        }
                    } catch (e) {
                        // 选择器无效，返回 false
                        return false;
                    }
                    return false;
                }""",
                combined_selector
            )
            return has_stop if isinstance(has_stop, bool) else False
        except Exception:
            return False
    
    async def _is_thinking(self) -> bool:
        """
        检查 ChatGPT Pro 是否还在思考中（思考模式）。
        
        思考状态的特征：
        - 页面中包含 "思考中"、"thinking"、"Pro 思考" 等文本
        - 可能显示 "立即回答" 按钮（表示可以中断思考）
        - 思考状态通常在 assistant 消息区域显示
        
        Returns:
            True 如果检测到思考状态，False 否则
        """
        try:
            # 使用 JS evaluate 直接查询，避免 Playwright 的额外开销
            is_thinking = await self.page.evaluate(
                """() => {
                    // 方法1: 查找包含 "思考中" 或 "thinking" 的文本
                    const bodyText = document.body.innerText || document.body.textContent || '';
                    const thinkingKeywords = ['思考中', 'thinking', 'Pro 思考', 'Final output', '立即回答'];
                    for (const keyword of thinkingKeywords) {
                        if (bodyText.includes(keyword)) {
                            // 进一步验证：确保是在 assistant 消息区域
                            // 查找最近的 assistant 消息元素
                            const assistantMsgs = document.querySelectorAll('[data-message-author-role="assistant"]');
                            if (assistantMsgs.length > 0) {
                                const lastMsg = assistantMsgs[assistantMsgs.length - 1];
                                const msgText = lastMsg.innerText || lastMsg.textContent || '';
                                // 如果最后一条消息包含思考关键词，或者消息很短（可能是思考占位符）
                                if (msgText.includes(keyword) || (msgText.length < 50 && keyword === '思考中')) {
                                    return true;
                                }
                            }
                        }
                    }
                    
                    // 方法2: 查找 "立即回答" 按钮（思考模式下会出现）
                    const answerNowButtons = document.querySelectorAll('button, a, span');
                    for (let btn of answerNowButtons) {
                        const btnText = (btn.innerText || btn.textContent || '').trim();
                        if (btnText === '立即回答' || btnText === 'Answer immediately') {
                            // 如果按钮可见，说明还在思考中
                            if (btn.offsetParent !== null) {
                                return true;
                            }
                        }
                    }
                    
                    return false;
                }"""
            )
            return is_thinking if isinstance(is_thinking, bool) else False
        except Exception:
            return False

    # 注意：_arm_input_events, _fast_send_confirm, _trigger_send_fast 已迁移到 chatgpt_send.py 模块

    async def _send_prompt(self, prompt: str) -> None:
        """
        修复版发送逻辑（已重构到 chatgpt_send.py 模块）：
        1. 清理 prompt 中的换行符（避免 type() 将 \n 解释为 Enter）
        2. 使用 JS 强制清空 (解决 Node is not input 报错)
        3. 智能 fallback 输入 (type -> JS injection)
        4. 组合键发送优先 (解决按钮点击失败)
        """
        # 使用模块化的发送器
        await self._sender.send_prompt(prompt)

    async def ask(self, prompt: str, timeout_s: int = 1200, model_version: Optional[str] = None, new_chat: bool = False) -> Tuple[str, str]:
        """
        发送 prompt 并等待回复。
        
        Args:
            prompt: 要发送的提示词
            timeout_s: 超时时间（秒）
            model_version: 模型版本（可选）
            new_chat: 是否打开新聊天窗口
        
        Returns:
            (response_text, page_url) 元组
        """
        # 关键修复：如果是 Pro 模式，自动延长超时时间到 40 分钟（2400 秒）
        # 因为 Pro 模式的思考时间可能很长
        is_pro_mode = False
        if model_version:
            model_v_lower = model_version.strip().lower()
            # 检查是否是 Pro 模式（包括 5.2pro, pro, gpt-5.2-pro 等）
            is_pro_mode = (
                "pro" in model_v_lower and "instant" not in model_v_lower
            ) or "5.2pro" in model_v_lower or "5-2-pro" in model_v_lower or "gpt-5.2-pro" in model_v_lower
        else:
            # 如果没有指定 model_version，检查环境变量或默认变体
            variant = self._desired_variant()
            is_pro_mode = variant == "pro"
        
        # 如果是 Pro 模式，且超时时间小于 40 分钟，自动延长到 40 分钟
        if is_pro_mode and timeout_s < 2400:
            original_timeout = timeout_s
            timeout_s = 2400  # 40 分钟
            self._log(f"ask: Pro 模式检测到，自动延长超时时间从 {original_timeout}s 到 {timeout_s}s (40 分钟)")
        
        async def _ask_inner() -> Tuple[str, str]:
            ask_start_time = time.time()
            self._log(f"ask: start (timeout={timeout_s}s, model_version={model_version or 'default'}, new_chat={new_chat})")
            
            # 初始化模块化组件（需要 page 对象）
            self._init_modules()
            
            # 确保页面就绪
            await self.ensure_ready()
            
            # 确保模型版本
            await self.ensure_variant(model_version)
            
            # 如果需要，打开新聊天窗口
            if new_chat:
                self._log("ask: creating new chat...")
                await self.new_chat()
                # 新聊天后需要重新确保就绪
                await self.ensure_ready()
                # 关键修复：新聊天窗口打开后，等待一小段时间确保页面完全刷新
                await asyncio.sleep(0.5)  # 等待页面稳定
                
                # 重新查询状态，确保获取的是新窗口的状态
                n_assist0 = await self._assistant_count()
                user0 = await self._user_count()
                last_assist_text_before = await self._last_assistant_text()
                
                # 关键验证：新聊天窗口应该没有历史消息
                if n_assist0 > 0 or user0 > 0:
                    self._log(f"ask: WARNING - new_chat requested but messages exist (assistant={n_assist0}, user={user0})")
                    self._log("ask: retrying new_chat with forced navigation...")
                    # 强制再次尝试
                    try:
                        await self.page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(1.0)
                        await self._dismiss_overlays()
                        await self.ensure_ready()
                        # 再次查询状态
                        n_assist0 = await self._assistant_count()
                        user0 = await self._user_count()
                        last_assist_text_before = await self._last_assistant_text()
                        self._log(f"ask: after forced navigation - assistant_count={n_assist0}, user_count={user0}")
                    except Exception as e:
                        self._log(f"ask: forced navigation failed: {e}")
                
                self._log(f"ask: new chat opened, reset state - assistant_count(before)={n_assist0}, user_count(before)={user0}, last_assist_text_len(before)={len(last_assist_text_before)}")
            else:
                # 记录发送前的状态（非新聊天窗口）
                n_assist0 = await self._assistant_count()
                user0 = await self._user_count()
                last_assist_text_before = await self._last_assistant_text()
                self._log(f"ask: assistant_count(before)={n_assist0}, user_count(before)={user0}, last_assist_text_len(before)={len(last_assist_text_before)}")
            
            # 发送 prompt
            self._log("ask: sending prompt...")
            await self._send_prompt(prompt)
            
            # 等待 assistant 消息出现
            n_assist1 = await self._waiter.wait_for_assistant_message(
                n_assist0=n_assist0,
                last_assist_text_before=last_assist_text_before,
                ask_start_time=ask_start_time,
                timeout_s=timeout_s,
            )
            
            # 修复 Bug 1: 在使用 n_assist1 计算 target_index 之前，重新查询实际的 assistant_count
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
                pass  # 静默失败，使用现有的 n_assist1
            
            # 等待新消息的文本内容出现
            await self._waiter.wait_for_message_content(
                n_assist0=n_assist0,
                n_assist1=n_assist1,
                last_assist_text_before=last_assist_text_before,
                ask_start_time=ask_start_time,
                timeout_s=timeout_s,
            )
            
            # 等待输出稳定
            return await self._waiter.wait_for_output_stabilize(
                n_assist0=n_assist0,
                ask_start_time=ask_start_time,
                timeout_s=timeout_s,
                last_assist_text_before=last_assist_text_before,
            )
        
        # 使用整体超时保护
        # 关键修复：如果是 Pro 模式，额外增加缓冲时间（因为思考时间可能很长）
        timeout_buffer = 10 if is_pro_mode else 5  # Pro 模式多给 10 秒缓冲
        try:
            return await asyncio.wait_for(_ask_inner(), timeout=timeout_s + timeout_buffer)
        except asyncio.TimeoutError:
            await self.save_artifacts("ask_total_timeout")
            raise TimeoutError(f"ask: total timeout exceeded ({timeout_s}s)")
