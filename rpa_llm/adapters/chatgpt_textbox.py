# -*- coding: utf-8 -*-
"""
ChatGPT 输入框操作模块

负责处理输入框的查找、定位和准备，包括：
- 多 frame 查找
- 快速路径检查
- Cloudflare 检测
- ensure_ready 逻辑
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Optional, Tuple

from playwright.async_api import Frame, Locator, Page


class ChatGPTTextboxFinder:
    """ChatGPT 输入框查找器"""
    
    # 输入框：优化后的优先级（适配当前 ChatGPT ProseMirror 实现）
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
    
    def __init__(self, page: Page, logger, manual_checkpoint_fn, save_artifacts_fn):
        self.page = page
        self._log = logger
        self.manual_checkpoint = manual_checkpoint_fn
        self.save_artifacts = save_artifacts_fn

    def _frames_in_priority(self) -> list[Frame]:
        mf = self.page.main_frame
        return [mf] + [f for f in self.page.frames if f != mf]

    async def dismiss_overlays(self) -> None:
        # 关闭可能遮挡输入框的浮层/菜单
        try:
            await self.page.keyboard.press("Escape")
            await asyncio.sleep(0.15)
            await self.page.keyboard.press("Escape")
        except Exception:
            pass

    async def is_cloudflare(self) -> bool:
        try:
            body = await self.page.inner_text("body")
        except Exception:
            return False
        return ("确认您是人类" in body) or ("Verify you are human" in body) or ("Cloudflare" in body)

    async def try_visible(self, loc: Locator) -> bool:
        try:
            return await loc.is_visible()
        except Exception:
            return False

    async def find_textbox_any_frame(self) -> Optional[Tuple[Locator, Frame, str]]:
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
                self.try_find_in_frame(mf, 'div[id="prompt-textarea"]', "main_frame_id"),
                self.try_find_in_frame(mf, 'div[contenteditable="true"]', "main_frame_contenteditable"),
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
                if await asyncio.wait_for(self.try_visible(loc), timeout=0.5):
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
                    if await asyncio.wait_for(self.try_visible(loc), timeout=0.5):
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
                    if await asyncio.wait_for(self.try_visible(loc), timeout=0.5):
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
                        if await asyncio.wait_for(self.try_visible(loc), timeout=0.5):
                            return loc, frame, f"css:{sel}"
                    except (asyncio.TimeoutError, Exception):
                        continue
                except Exception:
                    continue

        return None
    
    async def try_find_in_frame(self, frame: Frame, selector: str, how: str) -> Optional[Tuple[Locator, Frame, str]]:
        """辅助方法：在指定 frame 中尝试查找选择器"""
        try:
            loc = frame.locator(selector).first
            # 优化：减少等待时间，加快检查速度
            await loc.wait_for(state="attached", timeout=300)  # 从 500ms 减少到 300ms
            if await asyncio.wait_for(self.try_visible(loc), timeout=0.3):  # 从 0.5s 减少到 0.3s
                return loc, frame, how
        except (asyncio.TimeoutError, Exception):
            pass
        return None

    async def ready_check_textbox(self) -> bool:
        await self.dismiss_overlays()
        return (await self.find_textbox_any_frame()) is not None

    async def fast_ready_check(self) -> bool:
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

    async def ensure_ready(self) -> None:
        self._log("ensure_ready: start")
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
        if await self.is_cloudflare():
            await self.manual_checkpoint(
                "检测到 Cloudflare 人机验证页面，请人工完成验证。",
                ready_check=self.ready_check_textbox,
                max_wait_s=90,
            )

        if await self.fast_ready_check():
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
                await self.dismiss_overlays()
                last_dismiss_time = time.time()
            
            found = await self.find_textbox_any_frame()
            if found:
                _, frame, how = found
                self._log(f"ensure_ready: textbox OK via {how}. frame={frame.url} (took {time.time()-t0:.2f}s)")
                return

            check_count += 1
            if time.time() - hb >= 5:
                self._log(f"ensure_ready: still locating textbox... (attempt {check_count})")
                hb = time.time()

            # 优化：前几次快速检查，之后逐渐增加间隔，但最大不超过 0.15 秒
            # 进一步减少等待时间，加快检查频率
            sleep_time = 0.08 if check_count < 5 else 0.15  # 从 0.1/0.2 减少到 0.08/0.15，加快检查频率
            await asyncio.sleep(sleep_time)

        await self.save_artifacts("ensure_ready_failed")
        await self.manual_checkpoint(
            "未检测到输入框（可能弹窗遮挡/页面未完成挂载）。请手动点一下输入框或完成登录后继续。",
            ready_check=self.ready_check_textbox,
            max_wait_s=90,
        )

        # 人工处理后再确认一次
        if not await self.ready_check_textbox():
            await self.save_artifacts("ensure_ready_failed_after_manual")
            raise RuntimeError("ensure_ready: still cannot locate textbox after manual checkpoint.")

