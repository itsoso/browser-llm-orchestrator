# rpa_llm/adapters/base.py
from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Awaitable, Callable, List, Optional, Tuple

from playwright.async_api import BrowserContext, Page, async_playwright

from ..utils import beijing_now_iso, utc_now_iso


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

    def __init__(self, profile_dir: Path, artifacts_dir: Path, headless: bool = False):
        self.profile_dir = profile_dir
        self.artifacts_dir = artifacts_dir
        self.headless = headless

        self._pw = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

        # perf diagnostics storage (populated in __aenter__)
        self._perf: dict = {}

    async def __aenter__(self) -> "SiteAdapter":
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self._pw = await async_playwright().start()

        chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

        self._context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            executable_path=chrome_path,
            headless=False,  # strongly recommended headful for LLM sites
            viewport={"width": 1440, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-dev-shm-usage",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-infobars",
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
        self._page = pages[0] if pages else await self._context.new_page()

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
        async def _route_handler(route, request):
            try:
                rt = request.resource_type
                url = request.url
                self._perf["requests"]["total"] += 1
                by_type = self._perf["requests"]["by_type"]
                by_type[rt] = by_type.get(rt, 0) + 1

                # domain stats (rough)
                try:
                    from urllib.parse import urlparse

                    dom = urlparse(url).netloc or ""
                    by_dom = self._perf["requests"]["by_domain"]
                    by_dom[dom] = by_dom.get(dom, 0) + 1
                except Exception:
                    pass

                if rt in ("image", "media", "font"):
                    self._perf["requests"]["aborted"] += 1
                    await route.abort()
                else:
                    await route.continue_()
            except Exception:
                # never let routing break navigation
                try:
                    await route.continue_()
                except Exception:
                    pass

        await self._page.route("**/*", _route_handler)

        # Fast navigation: commit returns earlier than domcontentloaded/load for SPA sites
        self._perf["goto"]["start_utc"] = utc_now_iso()
        t0 = time.perf_counter()
        await self._page.goto(self.base_url, wait_until="commit")
        t1 = time.perf_counter()
        self._perf["goto"]["end_utc"] = utc_now_iso()
        self._perf["goto"]["duration_s"] = max(0.0, t1 - t0)

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