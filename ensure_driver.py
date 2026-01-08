#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
确保 Driver Server 运行的辅助脚本

用法：
    python ensure_driver.py --brief ./brief.yaml
    python ensure_driver.py --brief ./brief.yaml --wait  # 等待直到服务就绪
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    import httpx
except ImportError:
    print("⚠️  httpx 未安装，将尝试使用 urllib")
    httpx = None
    import urllib.request
    import json as json_lib


def check_driver_health(url: str, timeout: float = 2.0) -> dict:
    """
    检查 Driver Server 健康状态
    
    Returns:
        {"running": bool, "ok": bool, "sites": list, "error": str}
    """
    try:
        if httpx:
            response = httpx.get(f"{url}/health", timeout=timeout)
            response.raise_for_status()
            return {"running": True, **response.json()}
        else:
            req = urllib.request.Request(f"{url}/health")
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = json_lib.loads(response.read().decode())
                return {"running": True, **data}
    except Exception as e:
        return {"running": False, "ok": False, "sites": [], "error": str(e)}


def start_driver_server(brief_path: Path, background: bool = False):
    """
    启动 Driver Server
    
    Args:
        brief_path: brief.yaml 路径
        background: 是否后台运行
    """
    cmd = [
        sys.executable,  # 使用当前 Python 解释器
        "-u",
        "start_driver.py",
        "--brief",
        str(brief_path),
    ]
    
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    
    if background:
        # 后台运行，重定向输出到日志文件
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        log_file = log_dir / f"driver_{timestamp}.log"
        
        print(f"🚀 在后台启动 Driver Server...")
        print(f"📝 日志文件: {log_file}")
        
        with open(log_file, "w") as f:
            process = subprocess.Popen(
                cmd,
                env=env,
                stdout=f,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # 创建新会话，使其独立于父进程
            )
        
        print(f"✅ Driver Server 已启动 (PID: {process.pid})")
        return process
    else:
        # 前台运行
        print("🚀 启动 Driver Server (前台模式)...")
        print("提示: Ctrl+C 停止服务")
        print("-" * 60)
        
        try:
            subprocess.run(cmd, env=env)
        except KeyboardInterrupt:
            print("\n⚠️  用户中断")
            sys.exit(0)


def wait_for_ready(url: str, timeout: float = 60.0, check_interval: float = 2.0):
    """
    等待 Driver Server 就绪
    
    Args:
        url: Driver Server URL
        timeout: 最大等待时间（秒）
        check_interval: 检查间隔（秒）
    """
    print(f"⏳ 等待 Driver Server 就绪 (最多 {timeout:.0f} 秒)...")
    
    start_time = time.time()
    last_error = None
    
    while time.time() - start_time < timeout:
        health = check_driver_health(url, timeout=2.0)
        
        if health.get("running") and health.get("ok"):
            sites = health.get("sites", [])
            print(f"✅ Driver Server 已就绪！")
            print(f"📍 站点: {', '.join(sites)}")
            return True
        
        last_error = health.get("error", "未知错误")
        time.sleep(check_interval)
        print(".", end="", flush=True)
    
    print(f"\n❌ 超时: Driver Server 未能在 {timeout:.0f} 秒内就绪")
    if last_error:
        print(f"最后错误: {last_error}")
    return False


def main():
    parser = argparse.ArgumentParser(description="确保 Driver Server 运行")
    parser.add_argument("--brief", required=True, help="brief.yaml 文件路径")
    parser.add_argument("--url", default="http://127.0.0.1:27125", help="Driver Server URL")
    parser.add_argument("--wait", action="store_true", help="等待直到服务就绪")
    parser.add_argument("--background", action="store_true", help="后台启动 Driver Server")
    parser.add_argument("--timeout", type=float, default=60.0, help="等待超时时间（秒）")
    
    args = parser.parse_args()
    
    brief_path = Path(args.brief).resolve()
    if not brief_path.exists():
        print(f"❌ 错误: Brief 文件不存在: {brief_path}")
        sys.exit(1)
    
    print("=" * 60)
    print("🔍 Driver Server 健康检查")
    print("=" * 60)
    
    # 检查服务是否运行
    health = check_driver_health(args.url)
    
    if health.get("running"):
        if health.get("ok"):
            sites = health.get("sites", [])
            print(f"✅ Driver Server 正在运行")
            print(f"📍 URL: {args.url}")
            print(f"📍 站点: {', '.join(sites)}")
            sys.exit(0)
        else:
            print(f"⚠️  Driver Server 运行但不健康")
            print(f"错误: {health.get('error', '未知')}")
            sys.exit(1)
    
    print(f"❌ Driver Server 未运行")
    print(f"错误: {health.get('error', '未知')}")
    print()
    
    # 询问是否启动
    if not args.background and not args.wait:
        response = input("是否启动 Driver Server? [Y/n]: ").strip().lower()
        if response and response not in ("y", "yes", "是"):
            print("取消")
            sys.exit(0)
    
    print()
    
    # 启动服务
    if args.background:
        start_driver_server(brief_path, background=True)
        print()
        
        # 等待服务就绪
        if wait_for_ready(args.url, timeout=args.timeout):
            sys.exit(0)
        else:
            sys.exit(1)
    else:
        # 前台运行（阻塞）
        start_driver_server(brief_path, background=False)


if __name__ == "__main__":
    main()

