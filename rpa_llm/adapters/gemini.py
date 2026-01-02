# -*- coding: utf-8 -*-
"""
GeminiAdapter (UI Automation)

Design goals:
- Prioritize correctness + bounded latency over "smart" but fragile parallel waits.
- Avoid Locator.press() on contenteditable (often times out). Prefer page.keyboard.press().
- Keep send attempts short and confirm "send accepted" via strong signals:
  - textbox cleared/shrunk
  - stop button visible (generation started)
  - assistant count increased / assistant text hash changed
- Avoid overly-broad selectors that match unrelated DOM nodes.
- Avoid asyncio.wait_for wrapping Playwright ops (cancellation can leave noisy futures).

Author: your project
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import time
from typing import Optional, Tuple

from playwright.async_api import Frame, Locator

from ..utils import beijing_now_iso
from .base import SiteAdapter


class GeminiAdapter(SiteAdapter):
    site_id = "gemini"
    base_url = "https://gemini.google.com/app"

    # ===== Selectors (keep tight) =====

    # Visible, interactive textbox (Gemini uses rich editor, usually Quill)
    TEXTBOX_CSS = [
        'div[contenteditable="true"][role="textbox"]',
        'div.ql-editor[contenteditable="true"]',
        'div[contenteditable="true"]',
    ]

    # Send button (button only; do not include span/icon)
    SEND_BTN = [
        'button.send-button:not([aria-label*="Stop"]):not([aria-label*="停止"])',
        'button[aria-label="发送"]',
        'button[aria-label*="发送"]:not([aria-label*="停止"])',
        'button[aria-label*="Send"]:not([aria-label*="Stop"])',
    ]

    # Stop button (generation indicator)
    STOP_BTN = [
        'button[aria-label*="Stop"]',
        'button[aria-label*="停止"]',
        'button:has-text("Stop")',
        'button:has-text("停止")',
    ]

    # New chat / reset (best-effort; environment-controlled)
    NEW_CHAT = [
        'div[data-test-id="new-chat-button"]',
        'button[aria-label*="New chat"]',
        'button[aria-label*="新对话"]',
        'button:has-text("New chat")',
        'button:has-text("新对话")',
    ]

    # Assistant turn containers (each response)
    ASSISTANT_TURN = [
        "model-response",
        'div[data-test-id="model-response"]',
        'div[data-response="true"]',
        ".model-response-text",
        "div.markdown",
    ]

    # Common popups / overlays (best-effort)
    POPUPS = [
        'button[aria-label*="Close"]',
        'button[aria-label*="关闭"]',
        'button:has-text("No thanks")',
        'button:has-text("不，谢谢")',
        'button:has-text("Got it")',
        'button:has-text("知道了")',
        'button:has-text("Accept")',
        'button:has-text("接受")',
        'button:has-text("Continue")',
        'button:has-text("继续")',
        'button:has-text("Chat")',
        'button:has-text("开始")',
    ]

    # ===== Tunables =====
    # Keep send attempts short; long waits should happen in response wait, not send.
    # P1优化：减少发送硬限制时间，从 8.0 秒减少到 5.0 秒，加快发送速度
    SEND_HARD_CAP_S = float(os.environ.get("GEMINI_SEND_HARD_CAP_S", "5.0"))  # 从 8.0 秒减少到 5.0 秒
    SEND_CLICK_TIMEOUT_MS = int(os.environ.get("GEMINI_SEND_CLICK_TIMEOUT_MS", "1200"))
    SEND_ENABLE_WAIT_S = float(os.environ.get("GEMINI_SEND_ENABLE_WAIT_S", "1.0"))

    # Input: for large prompt, use JS injection; for small, prefer type.
    JS_INJECT_THRESHOLD = int(os.environ.get("GEMINI_JS_INJECT_THRESHOLD", "2500"))

    # Response stabilization
    STABLE_SECONDS = float(os.environ.get("GEMINI_STABLE_SECONDS", "1.2"))
    POLL_INTERVAL_S = float(os.environ.get("GEMINI_POLL_INTERVAL_S", "0.35"))

    def _log(self, msg: str) -> None:
        print(f"[{beijing_now_iso()}] [{self.site_id}] {msg}", flush=True)

    def _frames_in_priority(self) -> list[Frame]:
        mf = self.page.main_frame
        return [mf] + [f for f in self.page.frames if f != mf]

    def _new_chat_enabled(self) -> bool:
        return (os.environ.get("GEMINI_NEW_CHAT") or "0").strip() == "1"

    # ===== Basic helpers =====

    @staticmethod
    def _sha8(s: str) -> str:
        return hashlib.sha256((s or "").encode("utf-8", errors="ignore")).hexdigest()[:8]

    async def _try_visible(self, loc: Locator) -> bool:
        try:
            return await loc.is_visible()
        except Exception:
            return False

    async def _dismiss_popups(self) -> None:
        # quick escape
        try:
            await self.page.keyboard.press("Escape")
        except Exception:
            pass

        for sel in self.POPUPS:
            try:
                loc = self.page.locator(sel).first
                if await loc.count() == 0:
                    continue
                # short wait; do not block
                try:
                    await loc.wait_for(state="visible", timeout=250)
                except Exception:
                    continue
                try:
                    await loc.click(timeout=800, force=True, no_wait_after=True)
                    await asyncio.sleep(0.2)
                except Exception:
                    pass
            except Exception:
                continue

    async def _fast_find_textbox(self) -> Optional[Locator]:
        # Main-frame only fast path, no scans.
        # 修复：先尝试 is_visible() 检查，更健壮；bounding_box 可能因为元素不在视口而失败
        # 优化：也尝试 get_by_role("textbox")，作为备选方案
        try:
            loc = self.page.get_by_role("textbox").first
            if await loc.count() > 0:
                try:
                    if await loc.is_visible():
                        return loc
                except Exception:
                    pass
        except Exception:
            pass
        
        for sel in self.TEXTBOX_CSS:
            try:
                loc = self.page.locator(sel).first
                if await loc.count() == 0:
                    continue
                # 先检查是否可见（更快，不要求元素在视口中）
                try:
                    if await loc.is_visible():
                        return loc
                except Exception:
                    pass
                # 如果 is_visible() 失败，尝试 bounding_box（要求元素在视口中）
                try:
                    box = await loc.bounding_box()
                    if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
                        return loc
                except Exception:
                    pass
            except Exception:
                continue
        return None

    async def _find_textbox_any_frame(self) -> Optional[Tuple[Locator, Frame, str]]:
        mf = self.page.main_frame

        # 优化：先尝试 get_by_role("textbox")，作为备选方案
        try:
            loc = mf.get_by_role("textbox").first
            if await loc.count() > 0:
                try:
                    if await loc.is_visible():
                        return loc, mf, "main:get_by_role(textbox)"
                except Exception:
                    pass
                try:
                    await loc.wait_for(state="visible", timeout=300)
                    return loc, mf, "main:get_by_role(textbox)"
                except Exception:
                    pass
        except Exception:
            pass

        # Main frame first (优化：减少超时时间，加快失败速度)
        for sel in self.TEXTBOX_CSS:
            try:
                loc = mf.locator(sel).first
                if await loc.count() == 0:
                    continue
                # 优化：先快速检查 is_visible()，如果可见直接返回，避免等待 600ms
                try:
                    if await loc.is_visible():
                        return loc, mf, f"main:{sel}"
                except Exception:
                    pass
                # 如果不可见，等待可见（但减少超时时间）
                try:
                    await loc.wait_for(state="visible", timeout=300)  # 从 600ms 减少到 300ms
                    return loc, mf, f"main:{sel}"
                except Exception:
                    continue
            except Exception:
                continue

        # Fallback: other frames (优化：减少超时时间)
        for frame in self._frames_in_priority():
            if frame == mf:
                continue
            for sel in self.TEXTBOX_CSS:
                try:
                    loc = frame.locator(sel).first
                    if await loc.count() == 0:
                        continue
                    # 优化：先快速检查 is_visible()
                    try:
                        if await loc.is_visible():
                            return loc, frame, f"frame:{sel}"
                    except Exception:
                        pass
                    try:
                        await loc.wait_for(state="visible", timeout=300)  # 从 600ms 减少到 300ms
                        return loc, frame, f"frame:{sel}"
                    except Exception:
                        continue
                except Exception:
                    continue
        return None

    async def _find_textbox(self) -> Optional[Locator]:
        found = await self._find_textbox_any_frame()
        if not found:
            return None
        loc, _, how = found
        self._log(f"_find_textbox: found via {how}")
        return loc

    async def _tb_get_text(self, tb: Locator) -> str:
        # Gemini textbox is contenteditable; prefer evaluate innerText
        try:
            return await tb.evaluate("(el) => el.innerText || el.textContent || ''")
        except Exception:
            try:
                return (await tb.inner_text()) or ""
            except Exception:
                return ""

    async def _tb_clear(self, tb: Locator) -> None:
        # 修复：增强清空逻辑，确保完全清空，并验证清空结果
        # 方法1：用户式清空（Control+A + Backspace）
        try:
            await tb.focus(timeout=2000)
            try:
                await self.page.keyboard.press("Control+A")
            except Exception:
                try:
                    await self.page.keyboard.press("Meta+A")
                except Exception:
                    pass
            await self.page.keyboard.press("Backspace")
            await asyncio.sleep(0.1)  # 等待清空完成
            
            # 验证清空结果
            text_after = await self._tb_get_text(tb)
            if not text_after.strip():
                return  # 清空成功
        except Exception:
            pass

        # 方法2：JS 清空（更彻底）
        try:
            await tb.evaluate(
                """(el) => {
                    el.innerText = '';
                    el.textContent = '';
                    // 对于 Quill 编辑器，还需要清空内部结构
                    if (el.querySelector && el.querySelector('.ql-editor')) {
                        const qlEditor = el.querySelector('.ql-editor');
                        if (qlEditor) {
                            qlEditor.innerText = '';
                            qlEditor.textContent = '';
                        }
                    }
                    el.dispatchEvent(new Event('input', {bubbles:true}));
                    el.dispatchEvent(new Event('change', {bubbles:true}));
                }"""
            )
            await asyncio.sleep(0.1)  # 等待清空完成
            
            # 再次验证清空结果
            text_after = await self._tb_get_text(tb)
            if not text_after.strip():
                return  # 清空成功
        except Exception:
            pass

        # 方法3：强制清空（如果前两种方法都失败）
        try:
            await tb.evaluate(
                """(el) => {
                    // 清空所有可能的文本内容
                    if (el.innerText !== undefined) el.innerText = '';
                    if (el.textContent !== undefined) el.textContent = '';
                    // 清空 Quill 编辑器
                    const qlEditor = el.querySelector('.ql-editor');
                    if (qlEditor) {
                        qlEditor.innerText = '';
                        qlEditor.textContent = '';
                    }
                    // 清空所有子元素
                    const children = el.querySelectorAll('*');
                    children.forEach(child => {
                        if (child.innerText !== undefined) child.innerText = '';
                        if (child.textContent !== undefined) child.textContent = '';
                    });
                    el.dispatchEvent(new Event('input', {bubbles:true}));
                    el.dispatchEvent(new Event('change', {bubbles:true}));
                }"""
            )
        except Exception:
            pass

    async def _tb_set_text(self, tb: Locator, text: str) -> None:
        # 修复：Gemini 始终使用 contenteditable div（Quill 编辑器），永远不要使用 type()
        # 强制使用 JS 注入，避免误判为 textarea 导致 type() 超时
        await tb.focus(timeout=4000)

        # 修复：在设置新文本之前，先彻底清空输入框（双重保险）
        # 因为即使 ask() 方法中调用了 _tb_clear()，Quill 编辑器可能仍然有残留内容
        try:
            await tb.evaluate("""(el) => {
                // 找到实际的编辑器元素
                let targetEl = el;
                if (el.querySelector && el.querySelector('.ql-editor')) {
                    targetEl = el.querySelector('.ql-editor');
                }
                // 彻底清空：清空所有可能的文本内容
                targetEl.innerText = '';
                targetEl.textContent = '';
                // 清空 Quill 编辑器的所有子元素
                const children = targetEl.querySelectorAll('*');
                children.forEach(child => {
                    if (child.innerText !== undefined) child.innerText = '';
                    if (child.textContent !== undefined) child.textContent = '';
                });
                // 触发事件，确保状态更新
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                if (targetEl !== el) {
                    targetEl.dispatchEvent(new Event('input', { bubbles: true }));
                }
            }""")
            await asyncio.sleep(0.1)  # 等待清空完成
        except Exception:
            pass  # 清空失败不致命，继续设置文本

        # 修复：Gemini 始终使用 JS 注入，不检测元素类型（避免误判）
        # 因为 Gemini 的输入框始终是 contenteditable，即使检测失败也应该用 JS 注入
        js_code = """
        (el, t) => {
            el.focus();
            // 对于 Quill 编辑器，需要找到实际的编辑器元素
            let targetEl = el;
            if (el.querySelector && el.querySelector('.ql-editor')) {
                targetEl = el.querySelector('.ql-editor');
            }
            // 再次清空（确保没有残留内容，双重保险）
            targetEl.innerText = '';
            targetEl.textContent = '';
            // 清空所有子元素
            const children = targetEl.querySelectorAll('*');
            children.forEach(child => {
                if (child.innerText !== undefined) child.innerText = '';
                if (child.textContent !== undefined) child.textContent = '';
            });
            // 尝试使用 execCommand（更接近真实输入）
            const ok = document.execCommand && document.execCommand('insertText', false, t);
            if (!ok) {
                // fallback：直接设置文本
                targetEl.innerText = t;
                targetEl.textContent = t;
            }
            // 触发事件
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            // 对于 Quill，还需要触发其他事件
            if (targetEl !== el) {
                targetEl.dispatchEvent(new Event('input', { bubbles: true }));
            }
        }
        """
        
        # 修复：增强错误处理，确保即使 JS 注入失败也有 fallback
        try:
            await tb.evaluate(js_code, text)
            self._log(f"ask: text set via JS injection (len={len(text)})")
        except Exception as e:
            self._log(f"ask: JS injection failed: {e}, trying fallback...")
            # Fallback 1：直接设置 innerText（更简单的方法）
            try:
                await tb.evaluate("""(el, t) => {
                    // 找到实际的编辑器元素
                    let targetEl = el;
                    if (el.querySelector && el.querySelector('.ql-editor')) {
                        targetEl = el.querySelector('.ql-editor');
                    }
                    // 先清空（确保没有残留内容）
                    targetEl.innerText = '';
                    targetEl.textContent = '';
                    // 清空所有子元素
                    const children = targetEl.querySelectorAll('*');
                    children.forEach(child => {
                        if (child.innerText !== undefined) child.innerText = '';
                        if (child.textContent !== undefined) child.textContent = '';
                    });
                    // 设置新文本
                    targetEl.innerText = t;
                    targetEl.textContent = t;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""", text)
                self._log(f"ask: text set via fallback JS (len={len(text)})")
            except Exception as e2:
                self._log(f"ask: fallback JS also failed: {e2}, trying fill()...")
                # Fallback 2：使用 fill()（最后的兜底方案）
                # 注意：fill() 会自动清空，但为了保险，我们也先手动清空
                try:
                    # 先手动清空
                    await tb.evaluate("""(el) => {
                        let targetEl = el;
                        if (el.querySelector && el.querySelector('.ql-editor')) {
                            targetEl = el.querySelector('.ql-editor');
                        }
                        targetEl.innerText = '';
                        targetEl.textContent = '';
                    }""")
                    await asyncio.sleep(0.1)
                    # 然后使用 fill()
                    await tb.fill(text)
                    self._log(f"ask: text set via fill() (len={len(text)})")
                except Exception as e3:
                    # 所有方法都失败，抛出异常
                    raise RuntimeError(f"Failed to set text: JS injection failed ({e}), fallback JS failed ({e2}), fill() failed ({e3})")
        
        # Nudge to trigger state updates (some UIs unlock send button only after key events)
        try:
            await self.page.keyboard.type(" ")
            await self.page.keyboard.press("Backspace")
        except Exception:
            pass

    async def _is_generating(self) -> bool:
        # 优化：使用并行检查，减少等待时间，避免 Future exception
        # 修复：显式捕获 TimeoutError，避免 Future exception was never retrieved
        async def check_stop(sel: str) -> bool:
            try:
                loc = self.page.locator(sel).first
                if await loc.count() > 0:
                    # 使用 wait_for 而不是 is_visible，但设置短超时，避免 Future exception
                    try:
                        await loc.wait_for(state="visible", timeout=300)  # 300ms 超时
                        return True
                    except (asyncio.TimeoutError, Exception):
                        # 显式捕获 TimeoutError，避免 Future exception
                        return False
            except (asyncio.TimeoutError, Exception):
                # 显式捕获所有异常，避免 Future exception
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

    async def _assistant_turn_locator(self) -> Optional[Locator]:
        for sel in self.ASSISTANT_TURN:
            try:
                loc = self.page.locator(sel)
                if await loc.count() > 0:
                    return loc
            except Exception:
                continue
        return None

    async def _assistant_count(self) -> int:
        loc = await self._assistant_turn_locator()
        if not loc:
            return 0
        try:
            return await loc.count()
        except Exception:
            return 0

    async def _assistant_text_at(self, idx: int) -> str:
        loc = await self._assistant_turn_locator()
        if not loc:
            return ""
        try:
            cnt = await loc.count()
            if idx < 0 or idx >= cnt:
                return ""
            item = loc.nth(idx)
            try:
                await item.scroll_into_view_if_needed(timeout=1500)
            except Exception:
                # force scroll bottom to trigger virtualized render
                try:
                    await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                except Exception:
                    pass
            try:
                return (await item.inner_text()).strip()
            except Exception:
                return ((await item.text_content()) or "").strip()
        except Exception:
            return ""

    async def _last_text(self) -> str:
        cnt = await self._assistant_count()
        if cnt <= 0:
            return ""
        return await self._assistant_text_at(cnt - 1)

    # ===== Lifecycle =====

    async def ensure_ready(self) -> None:
        self._log("ensure_ready: start")
        await asyncio.sleep(0.15)

        # Login detection
        if "accounts.google.com" in (self.page.url or ""):
            async def _logged_in() -> bool:
                await self._dismiss_popups()
                tb = await self._fast_find_textbox()
                return tb is not None

            await self.manual_checkpoint(
                "检测到 Google 登录页，请手动登录后继续。",
                ready_check=_logged_in,
                max_wait_s=120,
            )

        # Fast path
        try:
            await self._dismiss_popups()
        except Exception as e:
            # 优化：如果是 TargetClosedError，直接抛出，避免 Future exception
            if "TargetClosed" in str(e) or "Target page" in str(e) or "Target context" in str(e):
                raise RuntimeError(f"Browser/page closed during dismiss_popups: {e}") from e
            pass  # 其他异常不影响继续
        
        try:
            tb = await self._fast_find_textbox()
        except Exception as e:
            # 优化：如果是 TargetClosedError，直接抛出，避免 Future exception
            if "TargetClosed" in str(e) or "Target page" in str(e) or "Target context" in str(e):
                raise RuntimeError(f"Browser/page closed during _fast_find_textbox: {e}") from e
            tb = None
        
        if tb:
            # 优化：增加 actionability 检查，确保元素不仅可见而且可操作
            # 修复：页面可能还在进行 Hydration（水合），DOM 存在但事件监听没挂载
            try:
                # 确保元素不仅可见，而且是可编辑状态
                try:
                    await asyncio.wait_for(
                        tb.wait_for(state="visible", timeout=2000),
                        timeout=2.5  # 额外 0.5 秒缓冲
                    )
                except (asyncio.TimeoutError, Exception) as e:
                    # 优化：如果是 TargetClosedError，直接抛出，避免 Future exception
                    if "TargetClosed" in str(e) or "Target page" in str(e) or "Target context" in str(e):
                        raise RuntimeError(f"Browser/page closed during wait_for visible: {e}") from e
                    raise  # 其他异常继续抛出
                
                # 检查 contenteditable 属性是否真的为 true
                try:
                    is_editable = await asyncio.wait_for(
                        tb.evaluate("""(el) => {
                            return el.getAttribute('contenteditable') === 'true' || el.contentEditable === 'true';
                        }"""),
                        timeout=1.0
                    )
                except (asyncio.TimeoutError, Exception) as e:
                    # 优化：如果是 TargetClosedError，直接抛出，避免 Future exception
                    if "TargetClosed" in str(e) or "Target page" in str(e) or "Target context" in str(e):
                        raise RuntimeError(f"Browser/page closed during evaluate: {e}") from e
                    # 检查失败，继续正常路径
                    is_editable = False
                
                if is_editable:
                    self._log("ensure_ready: textbox found quickly (fast path) and is editable")
                    return
                else:
                    self._log("ensure_ready: textbox found but not editable yet, continuing...")
            except Exception as e:
                # 优化：如果是 TargetClosedError，直接抛出，避免 Future exception
                if "TargetClosed" in str(e) or "Target page" in str(e) or "Target context" in str(e):
                    raise RuntimeError(f"Browser/page closed during fast path check: {e}") from e
                # 检查失败，继续正常路径
                pass

        # Normal path (bounded)
        t0 = time.time()
        attempts = 0
        while time.time() - t0 < 30:
            attempts += 1
            # 优化：增加弹窗清理频率，每 3 次尝试清理一次（而不是 4 次）
            if attempts % 3 == 0:
                try:
                    await self._dismiss_popups()
                except Exception as e:
                    # 优化：如果是 TargetClosedError，直接抛出，避免 Future exception
                    if "TargetClosed" in str(e) or "Target page" in str(e) or "Target context" in str(e):
                        raise RuntimeError(f"Browser/page closed during dismiss_popups: {e}") from e
                    pass  # 其他异常不影响继续

            # 优化：先尝试快速路径，如果失败再尝试完整路径
            try:
                tb = await self._fast_find_textbox()
            except Exception as e:
                # 优化：如果是 TargetClosedError，直接抛出，避免 Future exception
                if "TargetClosed" in str(e) or "Target page" in str(e) or "Target context" in str(e):
                    raise RuntimeError(f"Browser/page closed during _fast_find_textbox: {e}") from e
                tb = None
            
            if not tb:
                try:
                    tb = await self._find_textbox()
                except Exception as e:
                    # 优化：如果是 TargetClosedError，直接抛出，避免 Future exception
                    if "TargetClosed" in str(e) or "Target page" in str(e) or "Target context" in str(e):
                        raise RuntimeError(f"Browser/page closed during _find_textbox: {e}") from e
                    tb = None
            
            if tb:
                self._log(f"ensure_ready: textbox found (took {time.time()-t0:.2f}s)")
                return

            # 优化：添加调试信息，帮助诊断问题
            if time.time() - t0 >= 5 and attempts % 5 == 0:
                # 检查页面状态，帮助诊断
                try:
                    url = self.page.url
                    title = await asyncio.wait_for(self.page.title(), timeout=1.0)
                    self._log(f"ensure_ready: still locating textbox... (attempt {attempts}, url={url[:60]}, title={title[:40]})")
                except Exception as e:
                    # 优化：如果是 TargetClosedError，直接抛出，避免 Future exception
                    if "TargetClosed" in str(e) or "Target page" in str(e) or "Target context" in str(e):
                        raise RuntimeError(f"Browser/page closed during status check: {e}") from e
                    self._log(f"ensure_ready: still locating textbox... (attempt {attempts})")

            # 优化：增加循环间隔，避免频繁调用（_find_textbox 可能耗时较长）
            # 前几次快速检查（0.4秒），之后逐渐增加间隔（0.8秒）
            await asyncio.sleep(0.4 if attempts < 8 else 0.8)

        await self.save_artifacts("gemini_ensure_ready_fail")

        async def _ready_check() -> bool:
            # 修复：不仅尝试快速路径，也尝试完整路径，提高检测成功率
            try:
                await self._dismiss_popups()
            except Exception as e:
                # 优化：如果是 TargetClosedError，直接抛出，避免 Future exception
                if "TargetClosed" in str(e) or "Target page" in str(e) or "Target context" in str(e):
                    raise RuntimeError(f"Browser/page closed during dismiss_popups: {e}") from e
                pass  # 其他异常不影响继续
            
            try:
                tb = await self._fast_find_textbox()
            except Exception as e:
                # 优化：如果是 TargetClosedError，直接抛出，避免 Future exception
                if "TargetClosed" in str(e) or "Target page" in str(e) or "Target context" in str(e):
                    raise RuntimeError(f"Browser/page closed during _fast_find_textbox: {e}") from e
                tb = None
            
            if tb:
                return True
            # 如果快速路径失败，尝试完整路径（包括多 frame 扫描）
            try:
                tb = await self._find_textbox()
            except Exception as e:
                # 优化：如果是 TargetClosedError，直接抛出，避免 Future exception
                if "TargetClosed" in str(e) or "Target page" in str(e) or "Target context" in str(e):
                    raise RuntimeError(f"Browser/page closed during _find_textbox: {e}") from e
                tb = None
            return tb is not None

        await self.manual_checkpoint(
            "无法找到 Gemini 输入框（可能弹窗/未登录/页面结构变化）。请手动处理后继续。",
            ready_check=_ready_check,
            max_wait_s=120,
        )

        await self._dismiss_popups()
        # 修复：不仅尝试快速路径，也尝试完整路径
        tb = await self._fast_find_textbox()
        if not tb:
            tb = await self._find_textbox()
        if not tb:
            await self.save_artifacts("gemini_ensure_ready_fail_after_manual")
            raise RuntimeError("ensure_ready: still cannot locate textbox after manual checkpoint")

    async def new_chat(self) -> None:
        # Default: reuse conversation for stability (only do if explicitly enabled)
        if not self._new_chat_enabled():
            # Still dismiss popups; they can block typing.
            await self._dismiss_popups()
            return

        self._log("new_chat: best effort")
        await self._dismiss_popups()

        # If textbox exists, attempt clicking new chat; otherwise goto base.
        current_url = self.page.url
        for sel in self.NEW_CHAT:
            try:
                btn = self.page.locator(sel).first
                if await btn.count() == 0:
                    continue
                try:
                    await btn.wait_for(state="visible", timeout=800)
                except Exception:
                    continue
                await btn.click(timeout=1200, force=True, no_wait_after=True)

                # wait for URL change or textbox refresh
                t0 = time.time()
                while time.time() - t0 < 4.0:
                    if self.page.url != current_url:
                        break
                    tb = await self._fast_find_textbox()
                    if tb:
                        break
                    await asyncio.sleep(0.2)
                return
            except Exception:
                continue

        # Fallback: reload app
        try:
            await self.page.goto(self.base_url)
            await self.page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass

    # ===== Send + Wait =====

    # P1优化：进一步减少默认超时时间，从 1.0 秒减少到 0.8 秒，加快发送确认速度
    async def _sent_accepted(self, tb: Locator, before_len: int, assist_cnt0: int, assist_hash0: str, timeout_s: float = 0.8) -> Optional[str]:  # 从 1.0 秒减少到 0.8 秒
        """
        Return a reason string if send is accepted; else None.
        优化：实现"信号竞争"机制，并行检查多个信号，谁先到就算谁。
        Strong signals (any one):
          - textbox cleared/shrunk (最可靠)
          - stop button visible
          - assistant_count increased
          - last assistant hash changed
        """
        # 优化：使用并行检查，实现"信号竞争"机制
        t0 = time.time()
        # P1优化：进一步减少检查间隔，从 0.05 秒减少到 0.03 秒，加快响应速度
        check_interval = 0.03  # 从 0.05 秒减少到 0.03 秒
        
        while time.time() - t0 < timeout_s:
            # 并行检查所有信号，谁先成功就返回
            tasks = []
            
            # 1. 最快：textbox cleared（最可靠的信号，优先检查）
            async def check_textbox_clear():
                try:
                    cur = (await self._tb_get_text(tb)).strip()
                    cur_len = len(cur)
                    if before_len > 0 and cur_len <= max(0, int(before_len * 0.1)):
                        return f"textbox_clear({before_len}->{cur_len})"
                except Exception:
                    pass
                return None
            
            # 2. 快速：stop button visible
            async def check_stop_button():
                try:
                    if await self._is_generating():
                        return "stop_visible"
                except Exception:
                    pass
                return None
            
            # 3. 较慢：assistant_count（只在检查次数较少时检查，避免频繁调用）
            # P1优化：减少超时时间，从 0.6 秒减少到 0.4 秒，加快检查速度
            async def check_assistant_count():
                try:
                    c = await asyncio.wait_for(self._assistant_count(), timeout=0.4)  # 从 0.6 秒减少到 0.4 秒
                    if c > assist_cnt0:
                        return f"assistant_count_inc({assist_cnt0}->{c})"
                except (asyncio.TimeoutError, Exception):
                    pass
                return None
            
            # 并行执行所有检查（显式管理任务，避免 timeout 未回收导致的 Future warning）
            tasks = [
                asyncio.create_task(check_textbox_clear()),
                asyncio.create_task(check_stop_button()),
                asyncio.create_task(check_assistant_count()),
            ]
            try:
                # P1优化：减少每次检查的超时时间，从 0.6 秒减少到 0.4 秒，加快检查速度
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=min(0.4, timeout_s - (time.time() - t0))  # 从 0.6 秒减少到 0.4 秒
                )
            except (asyncio.TimeoutError, Exception):
                for t in tasks:
                    t.cancel()
                results = await asyncio.gather(*tasks, return_exceptions=True)

            # 检查结果，优先返回 textbox_clear（最可靠）
            for result in results:
                if isinstance(result, str) and result:
                    if "textbox_clear" in result:
                        return result

            # 如果没有 textbox_clear，返回其他成功的信号
            for result in results:
                if isinstance(result, str) and result:
                    return result

            await asyncio.sleep(check_interval)

        return None

    async def _trigger_send(self, tb: Locator, before_len: int, assist_cnt0: int, assist_hash0: str) -> str:
        """
        Try send with bounded latency. Prefer Control+Enter (most reliable), then Enter, then short click.
        """
        deadline = time.time() + self.SEND_HARD_CAP_S

        # A) Control+Enter first (most reliable, like ChatGPT)
        # 优化：优先使用 Control+Enter（更可靠，避免 Enter 只是换行）
        self._log("send: trying Control+Enter first (most reliable)...")
        try:
            # 优化：减少 focus 超时时间，如果 focus 失败，直接使用 page.keyboard（不依赖 focus）
            # P0优化：添加异常处理，避免 Future exception
            try:
                await tb.focus(timeout=1000)  # 从 2000ms 减少到 1000ms
            except Exception as e:
                # 优化：捕获所有异常，包括 TargetClosedError，避免 Future exception
                if "TargetClosed" in str(e) or "Target page" in str(e) or "Target context" in str(e):
                    raise RuntimeError(f"Browser/page closed during focus: {e}") from e
                # focus 失败不影响继续，直接使用 page.keyboard
                pass
            
            # P0优化：添加异常处理，避免 Future exception
            try:
                await self.page.keyboard.press("Control+Enter")
                self._log("send: Control+Enter pressed")
            except Exception as e:
                # 优化：捕获所有异常，包括 TargetClosedError，避免 Future exception
                if "TargetClosed" in str(e) or "Target page" in str(e) or "Target context" in str(e):
                    raise RuntimeError(f"Browser/page closed during Control+Enter: {e}") from e
                raise
            
            # 优化：立即检查，不等待，减少延迟
            await asyncio.sleep(0.05)  # 从 0.1s 减少到 0.05s
            
            # 快速检查：textbox 是否已清空（最可靠的信号）
            try:
                current_text = (await self._tb_get_text(tb)).strip()
                if before_len > 0 and len(current_text) <= max(0, int(before_len * 0.1)):
                    self._log("send: fast confirm - Control+Enter worked (textbox cleared)!")
                    return "control_enter:fast_confirm_textbox_cleared"
            except Exception:
                pass
            
            # 快速检查：stop button 是否出现
            try:
                if await self._is_generating():
                    self._log("send: fast confirm - Control+Enter worked (stop button visible)!")
                    return "control_enter:fast_confirm_stop"
            except Exception:
                pass
            
            # 如果快速检查失败，再等待 0.15 秒后检查一次（减少等待时间）
            await asyncio.sleep(0.15)  # 从 0.2s 减少到 0.15s
            reason = await self._sent_accepted(tb, before_len, assist_cnt0, assist_hash0, timeout_s=0.4)  # 从 0.5s 减少到 0.4s
            if reason:
                return f"control_enter:{reason}"
        except Exception as e:
            self._log(f"send: Control+Enter(keyboard) error: {type(e).__name__}: {str(e)[:120]}")

        # B) Enter as fallback (keyboard, not locator.press)
        # 优化（P0）：Enter 后立即检查输入框是否变空，避免等待按钮超时
        self._log("send: trying Enter as fallback (keyboard)")
        try:
            # 优化：减少 focus 超时时间，如果 focus 失败，直接使用 page.keyboard（不依赖 focus）
            # P0优化：添加异常处理，避免 Future exception
            try:
                await tb.focus(timeout=1000)  # 从 2000ms 减少到 1000ms
            except Exception as e:
                # 优化：捕获所有异常，包括 TargetClosedError，避免 Future exception
                if "TargetClosed" in str(e) or "Target page" in str(e) or "Target context" in str(e):
                    raise RuntimeError(f"Browser/page closed during focus: {e}") from e
                # focus 失败不影响继续，直接使用 page.keyboard
                pass
            
            # P0优化：添加异常处理，避免 Future exception
            try:
                await self.page.keyboard.press("Enter")
            except Exception as e:
                # 优化：捕获所有异常，包括 TargetClosedError，避免 Future exception
                if "TargetClosed" in str(e) or "Target page" in str(e) or "Target context" in str(e):
                    raise RuntimeError(f"Browser/page closed during Enter: {e}") from e
                raise
        except Exception as e:
            self._log(f"send: Enter(keyboard) error: {type(e).__name__}: {str(e)[:120]}")

        # 优化：激进检查 - 如果输入框在 1.0 秒内变空，直接认为发送成功，跳过后续所有按钮点击
        # 优化：强制使用 textbox_clear 判定法（最可靠，比按钮点击可靠 100 倍）
        # P0优化：添加异常处理，避免 Future exception
        try:
            # 使用 wait_for_function 快速检测输入框是否变空（最可靠的信号）
            await asyncio.wait_for(
                self.page.wait_for_function(
                    """() => {
                        const el = document.querySelector('div[contenteditable="true"][role="textbox"]') 
                            || document.querySelector('div[contenteditable="true"]');
                        return el && (el.innerText || el.textContent || '').trim() === '';
                    }""",
                    timeout=1000  # 减少到 1.0 秒，加快响应
                ),
                timeout=1.2  # 总超时 1.2 秒
            )
            self._log("send: fast confirm - Enter key worked (input cleared in 1.0s)!")
            return "enter:fast_confirm_textbox_cleared"
        except (asyncio.TimeoutError, Exception) as e:
            # Enter 没生效或超时，立即检查是否已经发送成功（可能发送成功但检测延迟）
            # 显式捕获 TimeoutError，避免 Future exception
            try:
                # 立即检查：textbox 是否已清空（即使 wait_for_function 超时，可能已经清空了）
                current_text = (await self._tb_get_text(tb)).strip()
                if before_len > 0 and len(current_text) <= max(0, int(before_len * 0.1)):
                    self._log("send: fast confirm - Enter key worked (detected after wait_for_function timeout)!")
                    return "enter:fast_confirm_after_timeout"
                
                # 立即检查：stop button 是否出现
                if await self._is_generating():
                    self._log("send: fast confirm - Enter key worked (stop button detected after timeout)!")
                    return "enter:fast_confirm_stop_after_timeout"
            except Exception:
                pass  # 检查失败不影响继续

        # 优化：使用并行"信号竞争"机制，减少等待时间
        # 优化：减少超时时间，从 0.8 秒减少到 0.5 秒，加快检测
        reason = await self._sent_accepted(tb, before_len, assist_cnt0, assist_hash0, timeout_s=0.5)  # 减少到 0.5 秒
        if reason:
            return f"enter:{reason}"

        # 修复：在点击按钮之前，再次检查是否已经发送成功（防止重复点击）
        # 因为 Enter 可能已经成功，但检测没有及时捕获到
        try:
            # 快速检查：textbox 是否已清空
            current_text = (await self._tb_get_text(tb)).strip()
            if before_len > 0 and len(current_text) <= max(0, int(before_len * 0.1)):
                self._log("send: detected textbox cleared before button click, send already successful")
                return "enter:detected_before_click"
            
            # 快速检查：stop button 是否出现
            if await self._is_generating():
                self._log("send: detected stop button before button click, send already successful")
                return "enter:detected_stop_before_click"
            
            # 快速检查：assistant_count 是否增加
            assist_cnt_now = await asyncio.wait_for(self._assistant_count(), timeout=0.5)
            if assist_cnt_now > assist_cnt0:
                self._log(f"send: detected assistant_count increased ({assist_cnt0}->{assist_cnt_now}) before button click, send already successful")
                return "enter:detected_assistant_count_before_click"
        except Exception:
            pass  # 检查失败不影响继续尝试点击

        # C) Click send (short, check disabled)
        # 重要：在点击按钮之前，优先检查是否已经发送成功，避免误点击停止按钮
        for sel in self.SEND_BTN:
            if time.time() > deadline:
                break

            # 修复：在循环开始时，先检查是否已经发送成功（最优先检查）
            # 如果已经发送成功，直接返回，不要继续尝试点击按钮
            try:
                if await self._is_generating():
                    self._log("send: detected stop button at start of button loop, send already successful (skipping all button clicks to avoid stopping)")
                    return "enter:detected_stop_at_button_loop_start"
            except Exception:
                pass

            btn = self.page.locator(sel).first
            try:
                if await btn.count() == 0:
                    continue

                # 修复：在点击之前，显式检查按钮是否是"停止生成"按钮
                # 因为即使选择器过滤了，按钮状态可能已经改变
                try:
                    aria_label = await btn.get_attribute("aria-label") or ""
                    btn_class = await btn.get_attribute("class") or ""
                    # 检查是否是停止按钮
                    if "停止" in aria_label or "Stop" in aria_label or "stop" in aria_label.lower():
                        self._log(f"send: button {sel} is a stop button (aria-label={aria_label}), skipping")
                        continue
                    if "stop" in btn_class.lower():
                        self._log(f"send: button {sel} has stop class ({btn_class}), skipping")
                        continue
                except Exception:
                    pass  # 检查失败不影响继续

                # 修复：在点击之前，再次检查是否已经发送成功（防止在检查按钮状态期间已经发送）
                try:
                    # 优先检查 stop button（最可靠的信号）
                    if await self._is_generating():
                        self._log("send: detected stop button during button check, send already successful (skipping to avoid stopping)")
                        return "enter:detected_stop_during_button_check"
                    
                    current_text = (await self._tb_get_text(tb)).strip()
                    if before_len > 0 and len(current_text) <= max(0, int(before_len * 0.1)):
                        self._log("send: detected textbox cleared during button check, send already successful")
                        return "enter:detected_during_button_check"
                except Exception:
                    pass

                # quick disabled check (修复：减少等待时间)
                t0 = time.time()
                max_wait = min(self.SEND_ENABLE_WAIT_S, 0.5)  # 最多等待 0.5 秒
                while time.time() - t0 < max_wait:
                    # 在等待期间，也检查是否已经发送成功
                    try:
                        if await self._is_generating():
                            self._log("send: detected stop button during disabled check, send already successful (skipping to avoid stopping)")
                            return "enter:detected_stop_during_disabled_check"
                    except Exception:
                        pass
                    
                    aria_dis = await btn.get_attribute("aria-disabled")
                    dis_attr = await btn.get_attribute("disabled")
                    if aria_dis != "true" and dis_attr is None:
                        break
                    await asyncio.sleep(0.1)

                # 修复：在点击之前，最后一次检查是否已经发送成功（最关键）
                try:
                    # 优先检查 stop button（最可靠的信号，避免误点击停止按钮）
                    if await self._is_generating():
                        self._log("send: detected stop button just before click, send already successful (skipping click to avoid stopping)")
                        return "enter:detected_stop_just_before_click"
                    
                    current_text = (await self._tb_get_text(tb)).strip()
                    if before_len > 0 and len(current_text) <= max(0, int(before_len * 0.1)):
                        self._log("send: detected textbox cleared just before click, send already successful")
                        return "enter:detected_just_before_click"
                except Exception:
                    pass

                # click short timeout; do not wrap with asyncio.wait_for
                await btn.click(timeout=self.SEND_CLICK_TIMEOUT_MS, force=True, no_wait_after=True)

                # 修复：减少等待时间，从 1.2 秒减少到 0.8 秒
                reason = await self._sent_accepted(tb, before_len, assist_cnt0, assist_hash0, timeout_s=0.8)
                if reason:
                    return f"click:{sel}:{reason}"
            except Exception:
                # If click throws, still check if it sent.
                # 修复：减少等待时间，从 0.6 秒减少到 0.4 秒
                reason = await self._sent_accepted(tb, before_len, assist_cnt0, assist_hash0, timeout_s=0.4)
                if reason:
                    return f"click_timeout_but_sent:{sel}:{reason}"
                continue

        # D) JS click fallback (very limited)
        # 修复：在 JS click 之前，也要检查是否已经发送成功
        # 重要：如果已经发送成功（stop button 出现），绝对不要尝试点击任何按钮，避免误点击停止按钮
        try:
            # 先检查是否已经发送成功（最优先检查，避免误点击停止按钮）
            if await self._is_generating():
                self._log("send: detected stop button before JS click, send already successful (skipping JS click to avoid stopping)")
                return "enter:detected_stop_before_js_click"
            
            current_text = (await self._tb_get_text(tb)).strip()
            if before_len > 0 and len(current_text) <= max(0, int(before_len * 0.1)):
                self._log("send: detected textbox cleared before JS click, send already successful")
                return "enter:detected_before_js_click"
            
            # 使用更精确的选择器，排除停止按钮
            # 不要使用 "button.send-button"，因为它可能匹配到停止按钮
            # 使用 SEND_BTN 中的第一个选择器（已经排除了停止按钮）
            for sel in self.SEND_BTN[:1]:  # 只使用第一个选择器（最精确的）
                btn = self.page.locator(sel).first
                if await btn.count() > 0:
                    # 再次检查按钮是否是停止按钮（双重保险）
                    try:
                        aria_label = await btn.get_attribute("aria-label") or ""
                        btn_class = await btn.get_attribute("class") or ""
                        if "停止" in aria_label or "Stop" in aria_label or "stop" in aria_label.lower():
                            self._log(f"send: JS click target is a stop button (aria-label={aria_label}), skipping")
                            continue  # 跳过这个按钮，尝试下一个
                        if "stop" in btn_class.lower():
                            self._log(f"send: JS click target has stop class ({btn_class}), skipping")
                            continue
                    except Exception:
                        pass
                    
                    # 在点击之前，最后一次检查是否已经发送成功
                    if await self._is_generating():
                        self._log("send: detected stop button just before JS click, send already successful (skipping to avoid stopping)")
                        return "enter:detected_stop_just_before_js_click"
                    
                    h = await btn.element_handle()
                    if h:
                        await h.evaluate("(el)=>el.click()")
                        reason = await self._sent_accepted(tb, before_len, assist_cnt0, assist_hash0, timeout_s=1.2)
                        if reason:
                            return f"js_click:{reason}"
                    break  # 如果点击成功，退出循环
        except Exception:
            pass

        raise RuntimeError("send not accepted (Enter/click/js_click)")

    async def ask(self, prompt: str, timeout_s: int = 180) -> Tuple[str, str]:
        """
        Send prompt and wait for final answer.
        Returns: (answer_text, url)
        """
        prompt = self.clean_newlines(prompt, logger=lambda m: self._log(f"ask: {m}"))

        self._log(f"ask: start (timeout={timeout_s}s)")
        await self.ensure_ready()
        await self.new_chat()

        await self._dismiss_popups()

        tb = await self._find_textbox()
        if not tb:
            raise RuntimeError("ask: textbox not found after ensure_ready/new_chat")

        # Baselines BEFORE sending (important)
        assist_cnt0 = await self._assistant_count()
        last0 = await self._last_text()
        assist_hash0 = self._sha8(last0)

        # Focus + clear + set
        self._log("ask: filling prompt...")
        try:
            await tb.wait_for(state="visible", timeout=8000)
        except Exception:
            pass

        try:
            await tb.click(timeout=2000, force=True)
        except Exception:
            pass

        # 修复：确保输入框完全清空后再设置文本
        await self._tb_clear(tb)
        await asyncio.sleep(0.15)  # 增加等待时间，确保清空完成
        
        # 验证清空结果，如果还有残留内容，再次清空
        check_text = await self._tb_get_text(tb)
        if check_text.strip():
            self._log(f"ask: warning - textbox still has content after clear (len={len(check_text)}), clearing again...")
            await self._tb_clear(tb)
            await asyncio.sleep(0.15)
            # 再次验证
            check_text2 = await self._tb_get_text(tb)
            if check_text2.strip():
                self._log(f"ask: warning - textbox still has content after second clear (len={len(check_text2)}), proceeding anyway...")
        
        # 修复：添加异常处理，确保即使输入失败也能触发 manual_checkpoint
        try:
            await self._tb_set_text(tb, prompt)
            await asyncio.sleep(0.15)
        except Exception as e:
            await self.save_artifacts("gemini_set_text_failed")
            self._log(f"ask: _tb_set_text failed: {type(e).__name__}: {str(e)[:180]}")
            
            # 触发 manual_checkpoint，让用户手动输入
            async def _ready_check_text_set() -> bool:
                # 检查文本是否已经设置成功
                try:
                    current_text = (await self._tb_get_text(tb)).strip()
                    if len(current_text) >= len(prompt) * 0.8:  # 至少 80% 的内容
                        return True
                except Exception:
                    pass
                return False
            
            await self.manual_checkpoint(
                f"Gemini 输入文本失败。请在浏览器中手动输入 prompt 后回到终端继续。\nPrompt 长度: {len(prompt)} 字符",
                ready_check=_ready_check_text_set,
                max_wait_s=120,
            )
            
            # 手动输入后，再次验证
            await asyncio.sleep(0.5)
            current_text = (await self._tb_get_text(tb)).strip()
            if len(current_text) < len(prompt) * 0.5:
                raise RuntimeError(f"ask: text not set after manual checkpoint (expected ~{len(prompt)} chars, got {len(current_text)} chars)")

        before_text = (await self._tb_get_text(tb)).strip()
        before_len = len(before_text)
        self._log(f"send: textbox content before send (len={before_len})")
        
        # 修复：验证输入内容是否正确，如果长度不匹配，可能是残留内容
        expected_len = len(prompt)
        if before_len != expected_len:
            len_diff = abs(before_len - expected_len)
            len_ratio = min(before_len, expected_len) / max(before_len, expected_len) if max(before_len, expected_len) > 0 else 0
            if len_ratio < 0.9:  # 长度差异超过 10%
                self._log(f"ask: warning - textbox content length mismatch (expected={expected_len}, actual={before_len}, diff={len_diff})")
                # 如果实际长度明显大于预期，可能是残留内容，尝试再次清空并设置
                if before_len > expected_len * 1.2:  # 实际长度超过预期 20%
                    self._log(f"ask: textbox content too long (may have residual content), clearing and retrying...")
                    try:
                        await self._tb_clear(tb)
                        await asyncio.sleep(0.2)
                        await self._tb_set_text(tb, prompt)
                        await asyncio.sleep(0.15)
                        # 再次验证
                        before_text_retry = (await self._tb_get_text(tb)).strip()
                        before_len_retry = len(before_text_retry)
                        self._log(f"send: textbox content after retry (len={before_len_retry})")
                        if abs(before_len_retry - expected_len) < abs(before_len - expected_len):
                            # 重试后更接近预期，使用重试后的值
                            before_text = before_text_retry
                            before_len = before_len_retry
                            self._log(f"ask: retry improved content length (now {before_len_retry} vs expected {expected_len})")
                    except Exception as retry_err:
                        self._log(f"ask: retry failed ({retry_err}), proceeding with original content")

        # Trigger send (bounded). On failure, manual checkpoint with real ready_check.
        t_send = time.time()
        try:
            method = await self._trigger_send(tb, before_len, assist_cnt0, assist_hash0)
            self._log(f"send: accepted via {method}")
        except Exception as e:
            await self.save_artifacts("gemini_send_failed")
            self._log(f"send: failed: {type(e).__name__}: {str(e)[:180]}")

            async def _ready_check_sent() -> bool:
                # user manually sends: textbox shrinks, stop appears, or assistant changes
                try:
                    cur = (await self._tb_get_text(tb)).strip()
                    if before_len > 0 and len(cur) <= max(0, int(before_len * 0.1)):
                        return True
                except Exception:
                    pass
                if await self._is_generating():
                    return True
                try:
                    if await self._assistant_count() > assist_cnt0:
                        return True
                except Exception:
                    pass
                try:
                    last = await self._last_text()
                    if last and self._sha8(last) != assist_hash0:
                        return True
                except Exception:
                    pass
                return False

            await self.manual_checkpoint(
                "Gemini 发送失败。请在浏览器里手动点击发送后回到终端继续。",
                ready_check=_ready_check_sent,
                max_wait_s=60,
            )

        self._log(f"ask: send phase done ({time.time()-t_send:.2f}s)")

        # Wait for response: prefer assistant_count increase; fallback to hash change.
        self._log("ask: waiting for response...")
        t0 = time.time()
        assistant_wait_timeout = min(max(20.0, timeout_s * 0.6), 180.0)

        n1 = assist_cnt0
        while time.time() - t0 < assistant_wait_timeout:
            if await self._is_generating():
                break  # generation started
            try:
                n1 = await self._assistant_count()
                if n1 > assist_cnt0:
                    break
            except Exception:
                pass

            try:
                last = await self._last_text()
                if last and self._sha8(last) != assist_hash0:
                    break
            except Exception:
                pass

            await asyncio.sleep(self.POLL_INTERVAL_S)

        # Determine target index (if count increased, target is last)
        n_after = await self._assistant_count()
        if n_after > assist_cnt0:
            target_idx = n_after - 1
        else:
            # No count increase; still try last slot.
            target_idx = max(0, n_after - 1)

        # Wait for content to appear (bounded)
        self._log(f"ask: waiting for message content (index={target_idx})...")
        t1 = time.time()
        content = ""
        while time.time() - t1 < 12.0:
            content = await self._assistant_text_at(target_idx) if n_after > 0 else await self._last_text()
            if content and len(content.strip()) >= 10:
                # Avoid stale reads: hash must differ OR count increased
                if n_after > assist_cnt0 or self._sha8(content) != assist_hash0:
                    break
            await asyncio.sleep(0.25)

        if not content:
            # As a fallback, read last text
            content = await self._last_text()

        # Stabilize: wait until not generating and text stable for STABLE_SECONDS
        self._log("ask: waiting output stabilize...")
        last_text = content or ""
        last_hash = self._sha8(last_text)
        last_change = time.time()

        while time.time() - t0 < timeout_s:
            try:
                generating = await self._is_generating()
            except Exception:
                generating = False

            try:
                cur = await self._assistant_text_at(target_idx) if n_after > 0 else await self._last_text()
            except Exception:
                cur = ""

            if cur and self._sha8(cur) != last_hash:
                last_text = cur
                last_hash = self._sha8(cur)
                last_change = time.time()

            if last_text and (not generating) and (time.time() - last_change) >= self.STABLE_SECONDS:
                # Final stale guard: must differ from baseline if no new count
                if (n_after <= assist_cnt0) and (self._sha8(last_text) == assist_hash0):
                    # likely stale; treat as failure
                    await self.save_artifacts("gemini_stale_response")
                    raise RuntimeError("ask: likely stale response (assistant hash unchanged)")
                self._log(f"ask: response stabilized (len={len(last_text)}, no change {time.time()-last_change:.1f}s)")
                return last_text, self.page.url

            await asyncio.sleep(self.POLL_INTERVAL_S)

        await self.save_artifacts("gemini_answer_timeout")
        raise TimeoutError(f"Gemini response timeout. last_len={len(last_text)}")
