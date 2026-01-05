# -*- coding: utf-8 -*-
"""
Author: xiaofan with Codex Cusor
Created: 2025-12-30 18:37:59 +0800
Modified: 2025-12-30 18:37:59 +0800
"""
#!/usr/bin/env python3
"""
预热脚本：手动登录并保存浏览器状态

用法：
    python warmup.py chatgpt    # 预热 ChatGPT
    python warmup.py gemini      # 预热 Gemini
    python warmup.py all         # 预热所有站点
"""

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

# 尝试导入 stealth (支持 2.0.0+ 版本)
try:
    from playwright_stealth import Stealth
    stealth_helper = Stealth()
except ImportError:
    stealth_helper = None
    print("⚠️  playwright-stealth 未安装，建议运行: pip install playwright-stealth")


SITES = {
    "chatgpt": {
        "url": "https://chatgpt.com/",
        "profile": "chatgpt",
        "instructions": [
            "1. 完成 Cloudflare 验证（如果出现）",
            "2. 登录你的 ChatGPT 账号",
            "3. 确保能看到聊天输入框",
            "4. 可以发一条测试消息确认正常",
        ],
    },
    "gemini": {
        "url": "https://gemini.google.com/app",
        "profile": "gemini",
        "instructions": [
            "1. 登录你的 Google 账号（如果未登录）",
            "2. 确保能看到 Gemini 聊天界面",
            "3. 可以发一条测试消息确认正常",
        ],
    },
    "perplexity": {
        "url": "https://www.perplexity.ai/",
        "profile": "perplexity",
        "instructions": [
            "1. 登录你的 Perplexity 账号（如果需要）",
            "2. 确保能看到聊天输入框",
        ],
    },
    "grok": {
        "url": "https://grok.com/",
        "profile": "grok",
        "instructions": [
            "1. 登录你的 Grok 账号",
            "2. 确保能看到 Grok 聊天界面",
        ],
    },
    "qianwen": {
        "url": "https://tongyi.aliyun.com/qianwen",
        "profile": "qianwen",
        "instructions": [
            "1. 登录你的阿里云账号",
            "2. 确保能看到通义千问聊天界面",
        ],
    },
}


async def warmup_site(site_id: str, profiles_root: Path = Path("profiles")):
    """预热单个站点"""
    if site_id not in SITES:
        print(f"❌ 未知站点: {site_id}")
        print(f"可用站点: {', '.join(SITES.keys())}")
        return False

    config = SITES[site_id]
    user_data_dir = profiles_root / config["profile"]
    url = config["url"]

    print(f"\n{'='*60}")
    print(f"🔥 预热站点: {site_id.upper()}")
    print(f"📁 Profile 目录: {user_data_dir}")
    print(f"🌐 URL: {url}")
    print(f"{'='*60}\n")

    async with async_playwright() as p:
        chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

        # 使用与 RPA 相同的参数启动
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            executable_path=chrome_path,
            headless=False,
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
                "--safebrowsing-disable-auto-update",
                "--enable-automation",
                "--password-store=basic",
                "--use-mock-keychain",
            ],
        )

        # 默认超时设置
        context.set_default_timeout(30_000)
        context.set_default_navigation_timeout(45_000)

        # 减少 webdriver 信号
        await context.add_init_script(
            """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
"""
        )

        pages = context.pages
        page = pages[0] if pages else await context.new_page()

        # 注入 Stealth 脚本（如果可用）
        if stealth_helper:
            try:
                await stealth_helper.apply_stealth_async(page)
                print("✅ Stealth 模式已启用 (v2.0.0+)\n")
            except Exception as e:
                print(f"⚠️  Stealth 模式启用失败: {e}\n")
        else:
            print("⚠️  Stealth 模式不可用（建议安装: pip install playwright-stealth）\n")

        # 打开目标页面
        print(f"🌐 正在打开: {url}")
        await page.goto(url, wait_until="domcontentloaded")

        print("\n" + "=" * 60)
        print("📋 请按照以下步骤操作：")
        for instruction in config["instructions"]:
            print(f"   {instruction}")
        print("=" * 60)
        print("\n💡 提示：")
        print("   - 浏览器窗口已打开，请手动完成登录和验证")
        print("   - 完成后，回到终端按回车键保存状态并关闭浏览器")
        print("   - 保存的状态（Cookies）将被用于后续的 RPA 运行\n")

        input("✅ 完成后，请按回车键继续...")

        # 保存当前 URL 作为验证
        final_url = page.url
        print(f"\n📌 最终 URL: {final_url}")

        # 关闭浏览器（状态会自动保存到 user_data_dir）
        await context.close()

        print(f"✅ {site_id.upper()} 预热完成！状态已保存到: {user_data_dir}\n")
        return True


async def main():
    """主函数"""
    if len(sys.argv) < 2:
        print("用法:")
        print(f"  python {sys.argv[0]} <site_id>")
        print(f"  python {sys.argv[0]} all")
        print("\n可用站点:")
        for site_id, config in SITES.items():
            print(f"  - {site_id:12} -> {config['url']}")
        sys.exit(1)

    site_arg = sys.argv[1].lower()
    profiles_root = Path("profiles")

    if site_arg == "all":
        # 预热所有站点
        success_count = 0
        for site_id in SITES.keys():
            try:
                if await warmup_site(site_id, profiles_root):
                    success_count += 1
            except KeyboardInterrupt:
                print("\n\n⚠️  用户中断")
                break
            except Exception as e:
                print(f"\n❌ {site_id} 预热失败: {e}\n")

        print(f"\n{'='*60}")
        print(f"📊 预热完成: {success_count}/{len(SITES)} 个站点成功")
        print(f"{'='*60}\n")
    else:
        # 预热单个站点
        try:
            await warmup_site(site_arg, profiles_root)
        except KeyboardInterrupt:
            print("\n\n⚠️  用户中断")
        except Exception as e:
            print(f"\n❌ 预热失败: {e}")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

