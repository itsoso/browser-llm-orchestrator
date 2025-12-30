#!/usr/bin/env python3
"""
从 brief.yaml 读取配置并启动 driver_server
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import yaml

from rpa_llm.driver_server import DriverServer


def load_driver_config(brief_path: Path) -> dict:
    """从 brief.yaml 加载 driver_server 配置"""
    with open(brief_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    
    driver_config = data.get("driver_server", {})
    
    # 默认值
    defaults = {
        "sites": "chatgpt,gemini,perplexity,grok,qianwen",
        "port": 27125,
        "profiles_root": "profiles",
        "artifacts_root": "runs/driver/artifacts",
        "host": "127.0.0.1",
        "headless": False,
        "prewarm": True,
    }
    
    # 合并配置
    config = {**defaults, **driver_config}
    
    # 处理 sites（可能是字符串或列表）
    if isinstance(config["sites"], str):
        config["sites"] = [s.strip() for s in config["sites"].split(",") if s.strip()]
    elif isinstance(config["sites"], list):
        config["sites"] = [s.strip() for s in config["sites"] if s.strip()]
    else:
        config["sites"] = defaults["sites"].split(",")
    
    return config


async def main_async():
    ap = argparse.ArgumentParser(description="启动 driver_server（从 brief.yaml 读取配置）")
    ap.add_argument("--brief", default="brief.yaml", help="brief.yaml 文件路径")
    ap.add_argument("--sites", help="覆盖配置中的 sites（逗号分隔）")
    ap.add_argument("--port", type=int, help="覆盖配置中的 port")
    ap.add_argument("--profiles-root", help="覆盖配置中的 profiles_root")
    ap.add_argument("--artifacts-root", help="覆盖配置中的 artifacts_root")
    ap.add_argument("--host", help="覆盖配置中的 host")
    ap.add_argument("--headless", action="store_true", help="覆盖配置中的 headless")
    ap.add_argument("--no-prewarm", action="store_true", help="禁用预热")
    ap.add_argument("--check-warmup", action="store_true", help="启动前检查是否需要预热（仅提示，不自动运行）")
    args = ap.parse_args()

    brief_path = Path(args.brief).expanduser().resolve()
    if not brief_path.exists():
        print(f"错误: 找不到文件 {brief_path}", file=sys.stderr)
        sys.exit(1)

    # 从 brief.yaml 加载配置
    config = load_driver_config(brief_path)
    
    # 命令行参数覆盖配置
    if args.sites:
        config["sites"] = [s.strip() for s in args.sites.split(",") if s.strip()]
    if args.port:
        config["port"] = args.port
    if args.profiles_root:
        config["profiles_root"] = args.profiles_root
    if args.artifacts_root:
        config["artifacts_root"] = args.artifacts_root
    if args.host:
        config["host"] = args.host
    if args.headless:
        config["headless"] = True
    if args.no_prewarm:
        config["prewarm"] = False

    print(f"[driver] 从 {brief_path} 加载配置")
    print(f"[driver] sites: {','.join(config['sites'])}")
    print(f"[driver] port: {config['port']}")
    print(f"[driver] profiles_root: {config['profiles_root']}")
    print(f"[driver] artifacts_root: {config['artifacts_root']}")
    print(f"[driver] host: {config['host']}")
    print(f"[driver] headless: {config['headless']}")
    print(f"[driver] prewarm: {config['prewarm']}")
    
    # 检查预热状态（可选）
    if args.check_warmup:
        profiles_root = Path(config["profiles_root"]).resolve()
        print(f"\n[driver] 检查预热状态...")
        for site_id in config["sites"]:
            profile_dir = profiles_root / site_id
            if not profile_dir.exists() or not any(profile_dir.iterdir()):
                print(f"[driver] ⚠️  {site_id}: Profile 目录不存在或为空，建议运行: python warmup.py {site_id}")
            else:
                print(f"[driver] ✓  {site_id}: Profile 目录存在")
        print()

    server = DriverServer(
        host=config["host"],
        port=config["port"],
        sites=config["sites"],
        profiles_root=Path(config["profiles_root"]).resolve(),
        artifacts_root=Path(config["artifacts_root"]).resolve(),
        headless=config["headless"],
        prewarm=config["prewarm"],
    )

    import signal
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(server.stop()))
        except NotImplementedError:
            pass

    await server.start()
    await server._stop.wait()


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

