# -*- coding: utf-8 -*-
"""
ChatGPT 状态检测模块

负责检测 ChatGPT 页面的各种状态，包括：
- 消息计数（assistant/user）
- 消息内容获取
- 生成状态检测
- Thinking 状态检测
"""
from __future__ import annotations

import asyncio
from typing import List

from playwright.async_api import Page


class ChatGPTStateDetector:
    """ChatGPT 状态检测器"""
    
    # 消息容器：用于确认发送成功与回复到达
    ASSISTANT_MSG = [
        'div[data-message-author-role="assistant"]',
        'article[data-message-author-role="assistant"]',
    ]
    USER_MSG = [
        'div[data-message-author-role="user"]',
        'article[data-message-author-role="user"]',
    ]
    
    # 生成中按钮：用于判断是否还在生成
    STOP_BTN = [
        'button:has-text("Stop generating")',
        'button:has-text("停止生成")',
        'button[aria-label*="Stop"]',
        'button[aria-label*="停止"]',
    ]
    
    def __init__(self, page: Page, logger):
        self.page = page
        self._log = logger

    async def assistant_count(self) -> int:
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

    async def user_count(self) -> int:
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

    async def last_assistant_text(self) -> str:
        """
        获取最后一条 assistant 消息文本。
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
    
    async def get_assistant_text_by_index(self, index: int) -> str:
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

    async def is_generating(self) -> bool:
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
    
    async def is_thinking(self) -> bool:
        """
        检查 ChatGPT Pro 是否还在思考中（思考模式）。
        
        思考状态的特征：
        - 页面中包含 "思考中"、"thinking"、"Pro 思考" 等文本
        - 可能显示 "立即回答" 按钮（表示可以中断思考）
        - 思考状态通常在 assistant 消息区域显示
        - Pro 模式下可能显示进度条、思考时间等
        
        Returns:
            True 如果检测到思考状态，False 否则
        """
        try:
            # 使用 JS evaluate 直接查询，避免 Playwright 的额外开销
            is_thinking = await self.page.evaluate(
                """() => {
                    // 方法1: 查找包含思考相关关键词的文本（更宽泛的检测）
                    const bodyText = document.body.innerText || document.body.textContent || '';
                    const bodyTextLower = bodyText.toLowerCase();
                    
                    // 中英文思考关键词（Pro 模式特有的）
                    const thinkingKeywords = [
                        '思考中', '正在思考', '深度思考', 'Pro 思考', '思考用时',
                        'thinking', 'Thinking', 'reasoning', 'Reasoning',
                        'Final output', 'Finalizing', 
                        '立即回答', 'Answer immediately', 'Answer now',
                        '正在分析', '分析中', 'Analyzing',
                        '推理中', '正在推理'
                    ];
                    
                    // 检查 bodyText 中是否包含任何思考关键词
                    for (const keyword of thinkingKeywords) {
                        if (bodyText.includes(keyword) || bodyTextLower.includes(keyword.toLowerCase())) {
                            return true;  // 直接返回 true，不再额外验证
                        }
                    }
                    
                    // 方法2: 查找 "立即回答" 或相关按钮（思考模式下会出现）
                    const allElements = document.querySelectorAll('button, a, span, div');
                    for (let el of allElements) {
                        const elText = (el.innerText || el.textContent || '').trim();
                        // 检查按钮文本
                        if (elText === '立即回答' || elText === 'Answer immediately' || 
                            elText === 'Answer now' || elText === 'Skip thinking' ||
                            elText.includes('thinking for') || elText.includes('秒')) {
                            // 如果元素可见，说明还在思考中
                            if (el.offsetParent !== null && el.offsetWidth > 0) {
                                return true;
                            }
                        }
                    }
                    
                    // 方法3: 检查是否有 "stop" 或 "停止" 按钮可见（表示正在生成/思考）
                    const stopButtons = document.querySelectorAll('[aria-label*="stop"], [aria-label*="Stop"], [data-testid*="stop"]');
                    if (stopButtons.length > 0) {
                        for (let btn of stopButtons) {
                            if (btn.offsetParent !== null && btn.offsetWidth > 0) {
                                return true;
                            }
                        }
                    }
                    
                    // 方法4: 检查页面是否有动态加载指示器（思考时可能有动画）
                    const spinners = document.querySelectorAll('[class*="spinner"], [class*="loading"], [class*="animate"]');
                    // 如果有较多的动态元素，可能正在思考
                    let visibleSpinners = 0;
                    for (let spinner of spinners) {
                        if (spinner.offsetParent !== null) {
                            visibleSpinners++;
                        }
                    }
                    if (visibleSpinners >= 2) {
                        return true;
                    }
                    
                    // 方法5: 检查最后一条 assistant 消息是否很短或为空（正在生成中）
                    const assistantMsgs = document.querySelectorAll('[data-message-author-role="assistant"]');
                    if (assistantMsgs.length > 0) {
                        const lastMsg = assistantMsgs[assistantMsgs.length - 1];
                        const msgText = (lastMsg.innerText || lastMsg.textContent || '').trim();
                        // 如果消息很短（<100字符）且包含省略号或正在加载的提示
                        if (msgText.length < 100 && (msgText.includes('...') || msgText.includes('…') || msgText === '')) {
                            return true;
                        }
                    }
                    
                    return false;
                }"""
            )
            return is_thinking if isinstance(is_thinking, bool) else False
        except Exception:
            return False

