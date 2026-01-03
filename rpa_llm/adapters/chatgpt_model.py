# -*- coding: utf-8 -*-
"""
ChatGPT 模型版本选择模块

负责处理模型版本的选择和切换逻辑，包括：
- 模型变体识别（pro, thinking, instant, custom）
- Thinking toggle 设置
- 模型选择器交互
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Optional

from playwright.async_api import Page


class ChatGPTModelSelector:
    """ChatGPT 模型版本选择器"""
    
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
    
    def __init__(self, page: Page, logger):
        self.page = page
        self._log = logger
        self._variant_set = False
        self._model_version = None
    
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
    
    async def _set_thinking_toggle(self, want_thinking: bool) -> None:
        # 优化：添加超时机制，避免长时间等待
        t0 = asyncio.get_event_loop().time()
        timeout_s = 5.0  # 最多等待 5 秒
        
        for sel in self.THINKING_TOGGLE:
            if asyncio.get_event_loop().time() - t0 > timeout_s:
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
                if "5.2" in model_version_lower or "5-2" in model_version_lower:
                    version_parts.append(r"5[.\-]?2")
                if "4o" in model_version_lower or "4-o" in model_version_lower:
                    version_parts.append(r"4[.\-]?o")
                if "instant" in model_version_lower:
                    version_parts.append(r"\binstant\b|即时|Instant")
                if "pro" in model_version_lower:
                    version_parts.append(r"\bpro\b|专业|Professional")
                if "gpt" in model_version_lower:
                    version_parts.append(r"gpt")
                
                # 如果有关键部分，使用更精确的模式
                if version_parts:
                    enhanced_pattern = re.compile("|".join(version_parts), re.I)
                else:
                    enhanced_pattern = pattern
        else:
            enhanced_pattern = pattern
        
        for c in candidates:
            try:
                cnt = await c.count()
            except Exception:
                continue
            for i in range(min(cnt, 60)):
                try:
                    item = c.nth(i)
                    txt = (await item.inner_text()).strip()
                    if not txt:
                        continue
                    
                    # 优先使用增强模式匹配
                    if model_version and enhanced_pattern.search(txt):
                        self._log(f"mode: selecting model '{txt}' (matched by enhanced pattern)")
                        await item.click()
                        await asyncio.sleep(0.6)
                        return True
                    
                    # 回退到原始模式匹配
                    if pattern.search(txt):
                        self._log(f"mode: selecting model '{txt}' (matched by default pattern)")
                        await item.click()
                        await asyncio.sleep(0.6)
                        return True
                except Exception:
                    continue
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
                if model_version:
                    self._model_version = model_version
                return

            # 构建匹配模式
            mv = (model_version or self._model_version or "").lower()
            
            # 关键修复：优先匹配 5.2 Instant
            if "5.2instant" in mv or "5-2-instant" in mv or "5.2-instant" in mv:
                # 匹配 5.2 Instant
                pattern = re.compile(r"5[.\-]?2.*instant|instant.*5[.\-]?2|5[.\-]?2.*即时|即时.*5[.\-]?2", re.I)
            elif "5.2pro" in mv or "5-2-pro" in mv or "5.2-pro" in mv or "gpt-5.2-pro" in mv:
                # 匹配 5.2 Pro 或 GPT-5 相关模型
                pattern = re.compile(r"5[.\-]?2|gpt[.\-]?5|\bpro\b|专业|Professional", re.I)
            elif "5.2" in mv or "gpt-5" in mv:
                # 匹配 5.2 相关模型（默认 Pro）
                pattern = re.compile(r"5[.\-]?2|gpt[.\-]?5|\bpro\b|专业|Professional", re.I)
            elif "4o" in mv or "4-o" in mv:
                # 匹配 GPT-4o
                pattern = re.compile(r"4[.\-]?o|gpt[.\-]?4", re.I)
            elif "instant" in mv:
                # 匹配 Instant（单独的 instant 已经在上面处理了，这里是兜底）
                pattern = re.compile(r"\binstant\b|即时|Instant", re.I)
            else:
                # 默认匹配 Pro
                pattern = re.compile(r"\bPro\b|专业|Professional", re.I)
            
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

