# -*- coding: utf-8 -*-
"""
Author: xiaofan with Codex Cusor
Created: 2025-12-29 20:27:11 +0800
Modified: 2025-12-30 17:38:02 +0800
"""
# rpa_llm/adapters/base.py
from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Awaitable, Callable, List, Optional, Tuple

from playwright.async_api import BrowserContext, Locator, Page, async_playwright

from ..utils import beijing_now_iso, utc_now_iso

# 可选：集成 playwright-stealth 抗风控
# 支持 playwright-stealth 2.0.0+ (Stealth 类) 和旧版本 (stealth_async 函数)
try:
    from playwright_stealth import Stealth
    stealth_available = True
    stealth_async_func = None  # 新版本使用 Stealth 类
except ImportError:
    try:
        # 尝试导入旧版本的 stealth_async 函数
        from playwright_stealth import stealth_async as stealth_async_func
        Stealth = None
        stealth_available = True
    except ImportError:
        Stealth = None
        stealth_async_func = None
        stealth_available = False


class SiteAdapter(ABC):
    """
    Base adapter for browser-based LLM sites.

    Goals:
    - Persistent profiles (user_data_dir) to retain login state.
    - Headful (system Chrome) for stability vs anti-bot.
    - Faster navigation: block heavy resources, goto(wait_until="commit").
    - Performance diagnostics:
        * request counts (total/aborted, by resource type)
        * navigation duration
        * optional console/network error hooks
        * dump perf json alongside screenshot/html artifacts
    - Human checkpoint with optional auto-continue (ready_check).
    """

    site_id: str
    base_url: str

    def __init__(self, profile_dir: Path, artifacts_dir: Path, headless: bool = False, stealth: bool = True):
        self.profile_dir = profile_dir
        self.artifacts_dir = artifacts_dir
        self.headless = headless
        self.stealth_enabled = stealth

        self._pw = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

        # perf diagnostics storage (populated in __aenter__)
        self._perf: dict = {}

    async def __aenter__(self) -> "SiteAdapter":
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        # 清理可能存在的过时锁文件（避免 "profile already in use" 错误）
        # Chrome 使用 SingletonLock、SingletonCookie、SingletonSocket 来防止多实例
        singleton_files = [
            self.profile_dir / "SingletonLock",
            self.profile_dir / "SingletonCookie",
            self.profile_dir / "SingletonSocket",
        ]
        for lock_file in singleton_files:
            if lock_file.exists():
                try:
                    # 如果是符号链接，删除链接本身
                    if lock_file.is_symlink():
                        lock_file.unlink()
                        print(f"[{beijing_now_iso()}] [{self.site_id}] cleaned stale lock: {lock_file.name}", flush=True)
                    # 如果是普通文件，也尝试删除
                    elif lock_file.is_file():
                        lock_file.unlink()
                        print(f"[{beijing_now_iso()}] [{self.site_id}] cleaned stale lock: {lock_file.name}", flush=True)
                except Exception as e:
                    # 如果删除失败（可能正在使用），记录但不阻塞
                    print(f"[{beijing_now_iso()}] [{self.site_id}] warning: could not clean lock {lock_file.name}: {e}", flush=True)

        self._pw = await async_playwright().start()

        chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

        self._context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            executable_path=chrome_path,
            headless=self.headless,  # headful recommended; allow override for speed/testing
            viewport={"width": 1440, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-dev-shm-usage",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-infobars",
                # 性能优化参数
                "--disable-background-networking",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows",
                "--disable-ipc-flooding-protection",
                "--disable-hang-monitor",
                "--disable-prompt-on-repost",
                "--disable-sync",
                "--disable-translate",
                "--metrics-recording-only",
                "--no-first-run",
                "--safebrowsing-disable-auto-update",
                "--enable-automation",
                "--password-store=basic",
                "--use-mock-keychain",
            ],
        )

        # Default timeouts to avoid hanging forever
        self._context.set_default_timeout(30_000)
        self._context.set_default_navigation_timeout(45_000)

        # Reduce obvious webdriver signal (best-effort)
        await self._context.add_init_script(
            """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
"""
        )

        pages = self._context.pages
        if pages:
            # Keep a single tab to reduce overhead from restored sessions/extensions.
            self._page = pages[0]
            for p in pages[1:]:
                try:
                    await p.close()
                except Exception:
                    pass
        else:
            self._page = await self._context.new_page()

        # -------------------------
        # Anti-detection: 注入 Stealth 脚本（降低 Cloudflare 触发率）
        # -------------------------
        if self.stealth_enabled:
            if stealth_available:
                try:
                    if Stealth is not None:
                        # 新版本 (2.0.0+): 使用 Stealth 类
                        stealth = Stealth()
                        await stealth.apply_stealth_async(self._page)
                        print(f"[{beijing_now_iso()}] [{self.site_id}] stealth mode enabled (v2.0.0+)", flush=True)
                    elif stealth_async_func is not None:
                        # 旧版本: 使用 stealth_async 函数
                        await stealth_async_func(self._page)
                        print(f"[{beijing_now_iso()}] [{self.site_id}] stealth mode enabled (legacy)", flush=True)
                except Exception as e:
                    print(f"[{beijing_now_iso()}] [{self.site_id}] stealth mode failed (non-fatal): {e}", flush=True)
            else:
                print(f"[{beijing_now_iso()}] [{self.site_id}] stealth mode not available (install: pip install playwright-stealth)", flush=True)
        else:
            print(f"[{beijing_now_iso()}] [{self.site_id}] stealth mode disabled (configured in brief.yaml)", flush=True)

        # -------------------------
        # Performance diagnostics
        # -------------------------
        self._perf = {
            "site_id": self.site_id,
            "base_url": self.base_url,
            "local_start": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "utc_start": utc_now_iso(),
            "goto": {},
            "requests": {
                "total": 0,
                "aborted": 0,
                "by_type": {},
                "by_domain": {},
                "errors": 0,
            },
            "console": {
                "errors": 0,
                "warnings": 0,
            },
        }

        # Console hooks (lightweight)
        def _on_console(msg):
            try:
                t = msg.type
                if t == "error":
                    self._perf["console"]["errors"] += 1
                elif t == "warning":
                    self._perf["console"]["warnings"] += 1
            except Exception:
                pass

        self._page.on("console", _on_console)

        # Request failed hook (network errors)
        def _on_request_failed(req):
            try:
                self._perf["requests"]["errors"] += 1
            except Exception:
                pass

        self._page.on("requestfailed", _on_request_failed)

        # Route: block heavy resources + count requests
        # 性能优化：激进拦截监控/统计代码，减少页面加载时间
        # 拦截关键词列表：监控、统计、广告服务
        _block_keywords = [
            "sentry",           # 错误监控
            "statsig",          # 统计分析
            "segment",          # 数据收集
            "intercom",         # 客服聊天
            "clarity",          # 用户行为分析
            "google-analytics", # Google 分析
            "analytics",        # 通用分析
            "doubleclick",      # Google 广告
            "adservice",        # 广告服务
            "googletagmanager", # GTM
            "facebook.net",     # Facebook 追踪
            "hotjar",           # 热力图分析
            "mixpanel",         # 产品分析
            "amplitude",        # 产品分析
            "heap",             # 产品分析
            "fullstory",        # 会话回放
            "logrocket",        # 会话回放
            "datadog",          # 监控
            "newrelic",         # 监控
        ]
        
        async def _route_handler(route, request):
            try:
                rt = request.resource_type
                url = request.url.lower()  # 转换为小写以便匹配
                
                # 快速路径1：拦截图片/媒体/字体
                if rt in ("image", "media", "font"):
                    self._perf["requests"]["aborted"] += 1
                    await route.abort()
                    return
                
                # 快速路径2：拦截监控/统计/广告服务（激进拦截）
                if any(keyword in url for keyword in _block_keywords):
                    self._perf["requests"]["aborted"] += 1
                    await route.abort()
                    return
                
                # 其他资源：继续并统计（异步，不阻塞）
                self._perf["requests"]["total"] += 1
                by_type = self._perf["requests"]["by_type"]
                by_type[rt] = by_type.get(rt, 0) + 1

                # domain stats (异步处理，不阻塞路由)
                try:
                    from urllib.parse import urlparse
                    dom = urlparse(url).netloc or ""
                    by_dom = self._perf["requests"]["by_domain"]
                    by_dom[dom] = by_dom.get(dom, 0) + 1
                except Exception:
                    pass

                await route.continue_()
            except Exception:
                # never let routing break navigation
                try:
                    await route.continue_()
                except Exception:
                    pass

        await self._page.route("**/*", _route_handler)

        # Fast navigation: commit returns earlier than domcontentloaded/load for SPA sites
        # commit 最快，适合 SPA（React/Vue 等）
        current_url = ""
        try:
            current_url = self._page.url or ""
        except Exception:
            current_url = ""
        if not (current_url and current_url.startswith(self.base_url)):
            self._perf["goto"]["start_utc"] = utc_now_iso()
            t0 = time.perf_counter()
            await self._page.goto(self.base_url, wait_until="commit", timeout=30000)
            t1 = time.perf_counter()
            self._perf["goto"]["end_utc"] = utc_now_iso()
            self._perf["goto"]["duration_s"] = max(0.0, t1 - t0)
        else:
            try:
                print(
                    f"[{beijing_now_iso()}] [{self.site_id}] goto(skip) already at base_url",
                    flush=True,
                )
            except Exception:
                pass
        
        # 等待页面基本就绪（但不等待所有资源）
        # 对于 SPA，commit 后 DOM 可能还没完全加载，给一点时间让 React/Vue 挂载
        await asyncio.sleep(0.3)

        # Print a concise perf line (useful in terminal)
        try:
            dur = self._perf["goto"]["duration_s"]
            total = self._perf["requests"]["total"]
            aborted = self._perf["requests"]["aborted"]
            cerr = self._perf["console"]["errors"]
            rerr = self._perf["requests"]["errors"]
            print(
                f"[{beijing_now_iso()}] [{self.site_id}] goto(commit) dur={dur:.2f}s req_total={total} aborted={aborted} "
                f"console_err={cerr} req_err={rerr}",
                flush=True,
            )
        except Exception:
            pass

        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._context:
            await self._context.close()
        if self._pw:
            await self._pw.stop()

    @property
    def page(self) -> Page:
        assert self._page is not None
        return self._page

    async def save_artifacts(self, name: str) -> None:
        """
        Save screenshot + HTML + URL + perf JSON for debugging.
        """
        try:
            png = self.artifacts_dir / f"{self.site_id}__{name}.png"
            await self.page.screenshot(path=str(png), full_page=True)
        except Exception:
            pass

        try:
            html = await self.page.content()
            html_path = self.artifacts_dir / f"{self.site_id}__{name}.html"
            html_path.write_text(html, encoding="utf-8")
        except Exception:
            pass

        try:
            url_path = self.artifacts_dir / f"{self.site_id}__{name}.url.txt"
            url_path.write_text(self.page.url, encoding="utf-8")
        except Exception:
            pass

        try:
            # finalize perf snapshot
            self._perf["utc_snapshot"] = utc_now_iso()
            perf_path = self.artifacts_dir / f"{self.site_id}__{name}.perf.json"
            perf_path.write_text(json.dumps(self._perf, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    async def manual_checkpoint(
        self,
        reason: str,
        ready_check: Optional[Callable[[], Awaitable[bool]]] = None,
        max_wait_s: int = 60,
    ) -> None:
        """
        Human-in-the-loop checkpoint.

        - Saves artifacts.
        - If ready_check is provided, auto-polls for max_wait_s and continues automatically.
        - Otherwise (or on timeout), waits for user Enter.
        """
        await self.save_artifacts("manual_checkpoint")

        print(f"\n[{beijing_now_iso()}] [{self.site_id}] MANUAL CHECKPOINT: {reason}", flush=True)
        print(f"[{beijing_now_iso()}] 请在打开的浏览器中完成操作（Cloudflare/登录/验证码/弹窗等）。", flush=True)

        if ready_check is not None:
            print(f"[{beijing_now_iso()}] [{self.site_id}] auto-wait up to {max_wait_s}s ...", flush=True)
            loop = asyncio.get_event_loop()
            t0 = loop.time()
            hb = t0
            while (loop.time() - t0) < max_wait_s:
                try:
                    if await ready_check():
                        print(f"[{beijing_now_iso()}] [{self.site_id}] auto-continue: ready condition met.", flush=True)
                        return
                except Exception:
                    pass

                if (loop.time() - hb) >= 5:
                    print(f"[{beijing_now_iso()}] [{self.site_id}] auto-waiting ...", flush=True)
                    hb = loop.time()

                await asyncio.sleep(1.0)

        print(f"[{beijing_now_iso()}] 完成后回到终端按 Enter 继续。\n", flush=True)
        await asyncio.to_thread(input, "Press Enter to continue...")

    async def first_visible(self, selectors: List[str], timeout_ms: int = 5000):
        last_err = None
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                await loc.wait_for(state="visible", timeout=timeout_ms)
                return loc
            except Exception as e:
                last_err = e
        raise RuntimeError(f"[{self.site_id}] No visible element found. {last_err}")

    @staticmethod
    def clean_newlines(prompt: str, logger=None) -> str:
        """
        清理 prompt 中的换行符，避免输入时触发 Enter 导致提前提交。
        
        支持清理所有类型的换行符，包括：
        - 标准换行符：\\n, \\r, \\r\\n
        - Unicode 换行符：\\u2028 (行分隔符), \\u2029 (段落分隔符), \\u0085 (下一行)
        
        Args:
            prompt: 原始 prompt 字符串
            logger: 可选的日志记录器（如果有 _log 方法）
        
        Returns:
            清理后的 prompt 字符串（所有换行符替换为空格）
        
        Raises:
            RuntimeError: 如果清理后仍然包含换行符（不应该发生）
        """
        import re
        
        original_prompt = prompt
        # 检测所有可能的换行符（包括 Unicode 换行符）
        newline_chars = ['\n', '\r', '\r\n', '\u2028', '\u2029', '\u0085']
        newline_count = sum(prompt.count(char) for char in newline_chars)
        
        if newline_count == 0:
            return prompt  # 没有换行符，直接返回
        
        # 清理所有类型的换行符（包括 Unicode）
        prompt = re.sub(r'[\r\n\u2028\u2029\u0085]+', ' ', prompt)
        # 清理多余的空格（多个连续空格合并为一个）
        prompt = re.sub(r' +', ' ', prompt)
        # 去除首尾空格
        prompt = prompt.strip()
        
        # 最终验证：确保没有任何换行符残留
        if '\n' in prompt or '\r' in prompt:
            # 强制清理（不应该到达这里，但作为最后的安全网）
            if logger:
                logger(f"clean_newlines: warning - newlines still present after cleaning, forcing removal")
            prompt = prompt.replace('\n', ' ').replace('\r', ' ')
            prompt = re.sub(r' +', ' ', prompt).strip()
        
        # 最终验证：如果仍然包含换行符，抛出异常
        if '\n' in prompt or '\r' in prompt:
            raise RuntimeError(f"clean_newlines: failed to clean newlines from prompt (still contains newlines)")
        
        # 记录清理信息（如果有 logger）
        if logger:
            logger(f"clean_newlines: cleaned {newline_count} newlines from prompt (original_len={len(original_prompt)}, cleaned_len={len(prompt)})")
        
        return prompt

    async def _tb_kind(self, tb: Locator) -> str:
        """
        判断 textbox 类型：'textarea' | 'contenteditable' | 'unknown'
        
        Args:
            tb: Playwright Locator 对象
            
        Returns:
            元素类型字符串
        """
        try:
            return await tb.evaluate("""(el) => {
                const tag = (el.tagName || '').toLowerCase();
                if (tag === 'textarea' || tag === 'input') return 'textarea';
                if (el.isContentEditable) return 'contenteditable';
                return 'unknown';
            }""")
        except Exception:
            return "unknown"

    async def _tb_get_text(self, tb: Locator) -> str:
        """
        统一获取 textbox 文本内容，自动适配 textarea 和 contenteditable。
        
        Args:
            tb: Playwright Locator 对象
            
        Returns:
            文本内容字符串
        """
        kind = await self._tb_kind(tb)
        try:
            if kind == "textarea":
                return (await tb.input_value()) or ""
            # contenteditable
            return (await tb.evaluate("(el) => el.innerText || el.textContent || ''")) or ""
        except Exception:
            return ""

    async def _tb_clear(self, tb: Locator) -> None:
        """
        统一清空 textbox，优先使用"用户等价"操作（Ctrl+A → Backspace），
        避免破坏编辑器 DOM 结构（如 ProseMirror）。
        
        Args:
            tb: Playwright Locator 对象
        """
        # 首选用户等价清空，避免破坏编辑器 DOM
        try:
            await tb.focus()
            # Mac 上 Meta+A 更常见；Windows/Linux 用 Control+A
            try:
                await tb.press("Meta+A")
            except Exception:
                try:
                    await tb.press("Control+A")
                except Exception:
                    pass
            await tb.press("Backspace")
            # 验证是否清空
            await asyncio.sleep(0.1)
            text_after = await self._tb_get_text(tb)
            if not text_after.strip():
                return  # 清空成功
        except Exception:
            pass

        # 兜底：按类型轻量 JS 清空（不要 innerHTML=''，避免破坏 ProseMirror）
        try:
            kind = await self._tb_kind(tb)
            if kind == "textarea":
                await tb.evaluate("""(el) => { 
                    el.value = ''; 
                    el.dispatchEvent(new Event('input', {bubbles:true})); 
                }""")
            else:
                # contenteditable：只清 innerText/textContent，不清 innerHTML
                await tb.evaluate("""(el) => { 
                    el.innerText = ''; 
                    el.textContent = ''; 
                    el.dispatchEvent(new Event('input', {bubbles:true})); 
                }""")
        except Exception:
            pass

    async def _tb_set_text(self, tb: Locator, text: str) -> None:
        """
        统一设置 textbox 文本内容，自动适配 textarea 和 contenteditable。
        优化：减少等待时间，加快输入速度。
        
        Args:
            tb: Playwright Locator 对象
            text: 要设置的文本内容
        """
        # 优化：对于短文本，直接使用 evaluate，避免 focus() 的等待
        if len(text) < 500:
            try:
                # 快速路径：直接使用 evaluate，不等待 focus
                await tb.evaluate("""(el, t) => {
                    el.focus();
                    if (el.tagName === 'TEXTAREA') {
                        el.value = t;
                        el.dispatchEvent(new Event('input', {bubbles:true}));
                    } else if (el.contentEditable === 'true') {
                        const ok = document.execCommand && document.execCommand('insertText', false, t);
                        if (!ok) el.innerText = t;
                        el.dispatchEvent(new Event('input', {bubbles:true}));
                    }
                }""", text)
                return
            except Exception:
                pass  # 失败后 fallback 到原有逻辑
        
        # 原有逻辑（用于长文本或快速路径失败）
        kind = await self._tb_kind(tb)
        # 优化：减少 focus 超时时间
        try:
            await tb.focus(timeout=1000)  # 从默认超时减少到 1 秒
        except Exception:
            pass  # focus 失败不影响继续
        
        if kind == "textarea":
            # textarea 用 fill 最稳
            await tb.fill(text)
            return

        # contenteditable：用 insertText 更接近真实输入；失败再 innerText
        try:
            await tb.evaluate("""(el, t) => {
                el.focus();
                const ok = document.execCommand && document.execCommand('insertText', false, t);
                if (!ok) el.innerText = t;
                el.dispatchEvent(new Event('input', {bubbles:true}));
            }""", text)
        except Exception:
            await tb.type(text, delay=0)

    async def try_click(self, selectors: List[str], timeout_ms: int = 1500) -> bool:
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                await loc.wait_for(state="visible", timeout=timeout_ms)
                await loc.click()
                return True
            except Exception:
                continue
        return False

    async def send_with_fallback(
        self,
        textbox_selectors: List[str],
        send_button_selectors: List[str],
        prompt: str,
    ) -> None:
        tb = await self.first_visible(textbox_selectors)
        await tb.click()
        await tb.fill(prompt)

        if await self.try_click(send_button_selectors):
            return

        try:
            await tb.press("Enter")
            return
        except Exception:
            pass

        await tb.press("Control+Enter")

    @abstractmethod
    async def ensure_ready(self):
        ...

    @abstractmethod
    async def new_chat(self):
        ...

    @abstractmethod
    async def ask(self, prompt: str, timeout_s: int = 180) -> Tuple[str, str]:
        """
        Return (answer_text, source_url)
        """
        ...
