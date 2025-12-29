# rpa_llm/adapters/base.py
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional, Tuple

from playwright.async_api import BrowserContext, Page, async_playwright


class SiteAdapter(ABC):
    site_id: str
    base_url: str

    def __init__(self, profile_dir: Path, artifacts_dir: Path, headless: bool = False):
        self.profile_dir = profile_dir
        self.artifacts_dir = artifacts_dir
        # 对 ChatGPT 这类站点，headless 往往更容易触发风控；这里强制 headful 更稳
        self.headless = headless
        self._pw = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def __aenter__(self) -> "SiteAdapter":
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self._pw = await async_playwright().start()

        # 强烈建议使用系统 Chrome（而非 Playwright bundled Chromium）
        chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

        self._context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            executable_path=chrome_path,
            headless=False,  # 对风控站点：必须 headful
            viewport={"width": 1440, "height": 900},
            # 不要强行指定 user_agent：容易与真实 Chrome 版本不一致从而更像 bot
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-dev-shm-usage",
                # 下面两条在某些环境中能略微降低触发概率（非决定性）
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-infobars",
            ],
        )

        # 在任何页面脚本执行前注入，尽早弱化 webdriver 痕迹（非绕过，只是减少显眼特征）
        await self._context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            """
        )

        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()

        # 让首跳更“人类”：domcontentloaded 已足够，后续由 adapter 自己 wait/ensure_ready
        await self._page.goto(self.base_url, wait_until="domcontentloaded")

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
        保存截图与页面 HTML，用于排查 Cloudflare/登录/弹窗等问题。
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

    async def manual_checkpoint(self, reason: str):
        await self.save_artifacts("manual_checkpoint")
        print(f"\n[{self.site_id}] MANUAL CHECKPOINT: {reason}", flush=True)
        print("请在打开的浏览器中完成操作（Cloudflare/登录/验证码/权限弹窗等）。", flush=True)
        print("完成后回到终端按 Enter 继续。\n", flush=True)
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
    ):
        tb = await self.first_visible(textbox_selectors)
        await tb.click()
        await tb.fill(prompt)

        # 优先点击发送按钮；不行则 Enter；再不行 Ctrl+Enter
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
        ...