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
    SEND_HARD_CAP_S = float(os.environ.get("GEMINI_SEND_HARD_CAP_S", "8.0"))
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
        # 修复：Gemini 使用 contenteditable div（Quill 编辑器），type() 在 contenteditable 上可能很慢且不稳定
        # 对于 contenteditable，直接使用 JS 注入，而不是 type()
        await tb.focus(timeout=4000)

        # 检测元素类型（修复：更可靠的检测方式）
        is_contenteditable = False
        tag_name = ""
        try:
            tag_name = await tb.evaluate("(el) => el.tagName?.toLowerCase() || ''")
            is_contenteditable = await tb.evaluate("(el) => el.contentEditable === 'true' || el.getAttribute('contenteditable') === 'true'")
            # 额外检查：如果元素有 Quill 编辑器特征，也认为是 contenteditable
            has_ql_editor = await tb.evaluate("(el) => el.classList?.contains('ql-editor') || el.querySelector?.('.ql-editor') !== null")
            if has_ql_editor:
                is_contenteditable = True
        except Exception:
            # 检测失败，默认认为是 contenteditable（Gemini 通常使用 contenteditable）
            is_contenteditable = True

        # 对于 contenteditable 元素，直接使用 JS 注入（更快更可靠）
        # 对于 textarea，可以使用 type() 或 fill()
        if is_contenteditable or tag_name != "textarea":
            # contenteditable：使用 JS 注入（与 base.py 保持一致）
            # 修复：增强 JS 注入，确保 Quill 编辑器也能正确处理
            js_code = """
            (el, t) => {
                el.focus();
                // 对于 Quill 编辑器，需要找到实际的编辑器元素
                let targetEl = el;
                if (el.querySelector && el.querySelector('.ql-editor')) {
                    targetEl = el.querySelector('.ql-editor');
                }
                // 先清空（确保没有残留内容）
                targetEl.innerText = '';
                targetEl.textContent = '';
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
            try:
                await tb.evaluate(js_code, text)
            except Exception as e:
                self._log(f"ask: JS injection failed: {e}, trying fallback...")
                # Fallback：直接设置 innerText
                try:
                    await tb.evaluate("""(el, t) => {
                        el.innerText = t;
                        el.textContent = t;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                    }""", text)
                except Exception:
                    raise RuntimeError(f"Failed to set text via JS injection: {e}")
            
            # Nudge to trigger state updates (some UIs unlock send button only after key events)
            try:
                await self.page.keyboard.type(" ")
                await self.page.keyboard.press("Backspace")
            except Exception:
                pass
            return

        # textarea：对于短 prompt 使用 type()，长 prompt 使用 fill()
        # 修复：即使检测为 textarea，如果内容很长，也使用 fill() 避免超时
        if len(text) < self.JS_INJECT_THRESHOLD:
            # Type quickly; keep timeout bounded.
            timeout_ms = max(15000, len(text) * 20)
            try:
                await tb.type(text, delay=0, timeout=timeout_ms)
            except Exception as type_err:
                # type() 失败，fallback 到 fill()
                self._log(f"ask: type() failed ({type_err}), using fill() as fallback...")
                await tb.fill(text)
        else:
            # 长 prompt 直接使用 fill()
            await tb.fill(text)

    async def _is_generating(self) -> bool:
        for sel in self.STOP_BTN:
            try:
                loc = self.page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    return True
            except Exception:
                continue
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
        await self._dismiss_popups()
        tb = await self._fast_find_textbox()
        if tb:
            self._log("ensure_ready: textbox found quickly (fast path)")
            return

        # Normal path (bounded)
        t0 = time.time()
        attempts = 0
        while time.time() - t0 < 30:
            attempts += 1
            # 优化：增加弹窗清理频率，每 3 次尝试清理一次（而不是 4 次）
            if attempts % 3 == 0:
                await self._dismiss_popups()

            # 优化：先尝试快速路径，如果失败再尝试完整路径
            tb = await self._fast_find_textbox()
            if not tb:
                tb = await self._find_textbox()
            
            if tb:
                self._log(f"ensure_ready: textbox found (took {time.time()-t0:.2f}s)")
                return

            # 优化：添加调试信息，帮助诊断问题
            if time.time() - t0 >= 5 and attempts % 5 == 0:
                # 检查页面状态，帮助诊断
                try:
                    url = self.page.url
                    title = await self.page.title()
                    self._log(f"ensure_ready: still locating textbox... (attempt {attempts}, url={url[:60]}, title={title[:40]})")
                except Exception:
                    self._log(f"ensure_ready: still locating textbox... (attempt {attempts})")

            # 优化：增加循环间隔，避免频繁调用（_find_textbox 可能耗时较长）
            # 前几次快速检查（0.4秒），之后逐渐增加间隔（0.8秒）
            await asyncio.sleep(0.4 if attempts < 8 else 0.8)

        await self.save_artifacts("gemini_ensure_ready_fail")

        async def _ready_check() -> bool:
            # 修复：不仅尝试快速路径，也尝试完整路径，提高检测成功率
            await self._dismiss_popups()
            tb = await self._fast_find_textbox()
            if tb:
                return True
            # 如果快速路径失败，尝试完整路径（包括多 frame 扫描）
            tb = await self._find_textbox()
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

    async def _sent_accepted(self, tb: Locator, before_len: int, assist_cnt0: int, assist_hash0: str, timeout_s: float = 1.3) -> Optional[str]:
        """
        Return a reason string if send is accepted; else None.
        Strong signals (any one):
          - textbox cleared/shrunk
          - stop button visible
          - assistant_count increased
          - last assistant hash changed
        """
        # 修复：优化检查顺序，先检查最快的信号（textbox cleared），最后检查最慢的（assistant_count/hash）
        t0 = time.time()
        check_count = 0
        max_checks = int(timeout_s / 0.15)  # 根据 timeout_s 计算最大检查次数
        
        while time.time() - t0 < timeout_s and check_count < max_checks:
            check_count += 1
            
            # 1. 最快：textbox cleared（最可靠的信号）
            try:
                cur = (await self._tb_get_text(tb)).strip()
                cur_len = len(cur)
                if before_len > 0 and cur_len <= max(0, int(before_len * 0.1)):
                    return f"textbox_clear({before_len}->{cur_len})"
            except Exception:
                pass

            # 2. 快速：stop button visible
            try:
                if await self._is_generating():
                    return "stop_visible"
            except Exception:
                pass

            # 3. 较慢：assistant_count（只在检查次数较少时检查，避免频繁调用）
            if check_count % 2 == 0:  # 每 2 次检查一次
                try:
                    c = await asyncio.wait_for(self._assistant_count(), timeout=0.8)  # 减少超时时间
                    if c > assist_cnt0:
                        return f"assistant_count_inc({assist_cnt0}->{c})"
                except Exception:
                    pass

            # 4. 最慢：assistant hash（只在检查次数较多时检查，避免频繁调用）
            if check_count >= 3:  # 前 3 次不检查 hash
                try:
                    last = await asyncio.wait_for(self._last_text(), timeout=0.8)  # 减少超时时间
                    if last:
                        h = self._sha8(last)
                        if assist_hash0 and h != assist_hash0:
                            return f"assistant_hash_changed({assist_hash0}->{h})"
                except Exception:
                    pass

            await asyncio.sleep(0.15)  # 稍微增加间隔，减少检查频率

        return None

    async def _trigger_send(self, tb: Locator, before_len: int, assist_cnt0: int, assist_hash0: str) -> str:
        """
        Try send with bounded latency. Prefer keyboard Enter (page-level), then short click.
        """
        deadline = time.time() + self.SEND_HARD_CAP_S

        # A) Enter first (keyboard, not locator.press)
        # 优化（P0）：Enter 后立即检查输入框是否变空，避免等待按钮超时
        self._log("send: trying Enter first (keyboard)")
        try:
            await tb.focus(timeout=2000)
            await self.page.keyboard.press("Enter")
        except Exception as e:
            self._log(f"send: Enter(keyboard) error: {type(e).__name__}: {str(e)[:120]}")

        # 优化：激进检查 - 如果输入框在 2 秒内变空，直接认为发送成功，跳过后续所有按钮点击
        try:
            # 使用 wait_for_function 快速检测输入框是否变空
            await asyncio.wait_for(
                self.page.wait_for_function(
                    """() => {
                        const el = document.querySelector('div[contenteditable="true"][role="textbox"]') 
                            || document.querySelector('div[contenteditable="true"]');
                        return el && (el.innerText || el.textContent || '').trim() === '';
                    }""",
                    timeout=2000
                ),
                timeout=2.2  # 总超时 2.2 秒
            )
            self._log("send: fast confirm - Enter key worked (input cleared in 2s)!")
            return "enter:fast_confirm_textbox_cleared"
        except Exception:
            # Enter 没生效或超时，继续走后面的检查逻辑
            pass

        # 修复：减少等待时间，从 1.3 秒减少到 1.0 秒
        reason = await self._sent_accepted(tb, before_len, assist_cnt0, assist_hash0, timeout_s=1.0)
        if reason:
            return f"enter:{reason}"

        # B) Click send (short, check disabled)
        for sel in self.SEND_BTN:
            if time.time() > deadline:
                break

            btn = self.page.locator(sel).first
            try:
                if await btn.count() == 0:
                    continue

                # quick disabled check (修复：减少等待时间)
                t0 = time.time()
                max_wait = min(self.SEND_ENABLE_WAIT_S, 0.5)  # 最多等待 0.5 秒
                while time.time() - t0 < max_wait:
                    aria_dis = await btn.get_attribute("aria-disabled")
                    dis_attr = await btn.get_attribute("disabled")
                    if aria_dis != "true" and dis_attr is None:
                        break
                    await asyncio.sleep(0.1)

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

        # C) JS click fallback (very limited)
        try:
            btn = self.page.locator("button.send-button").first
            if await btn.count() > 0:
                h = await btn.element_handle()
                if h:
                    await h.evaluate("(el)=>el.click()")
                    reason = await self._sent_accepted(tb, before_len, assist_cnt0, assist_hash0, timeout_s=1.2)
                    if reason:
                        return f"js_click:{reason}"
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
        
        await self._tb_set_text(tb, prompt)
        await asyncio.sleep(0.15)

        before_text = (await self._tb_get_text(tb)).strip()
        before_len = len(before_text)
        self._log(f"send: textbox content before send (len={before_len})")
        
        # 修复：验证输入内容是否正确，如果长度不匹配，记录警告
        expected_len = len(prompt)
        if before_len != expected_len:
            len_diff = abs(before_len - expected_len)
            len_ratio = min(before_len, expected_len) / max(before_len, expected_len) if max(before_len, expected_len) > 0 else 0
            if len_ratio < 0.9:  # 长度差异超过 10%
                self._log(f"ask: warning - textbox content length mismatch (expected={expected_len}, actual={before_len}, diff={len_diff})")

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