#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量执行每周群聊记录分析

用法:
    python -m rpa_llm.batch_weekly_analysis --talker "川群-2025" --year 2025 --model-version 5.2pro
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple, Optional

import yaml

from .chatlog_automation import (
    run_automation,
    get_week_number,
    format_date_range,
    get_week_dates,
)
from .chatlog_client import ChatlogClient
from .utils import beijing_now_iso


async def check_week_has_data(
    client: ChatlogClient,
    talker: str,
    year: int,
    week: int,
) -> bool:
    """检查指定周是否有数据"""
    start_date, end_date = get_week_dates(year, week)
    time_range = format_date_range(start_date, end_date)
    
    try:
        messages = await client.get_conversations(
            talker=talker,
            time_range=time_range,
            start=start_date,
            end=end_date,
            limit=1,
        )
        return len(messages) > 0
    except Exception:
        return False


async def analyze_week(
    chatlog_url: str,
    talker: str,
    year: int,
    week: int,
    base_path: Path,
    template_path: Optional[Path],
    driver_url: Optional[str],
    model_version: str,
    task_timeout_s: int,
    new_chat: bool,
) -> Tuple[int, bool, Optional[str]]:
    """
    分析指定周的数据
    
    Returns:
        (week, success, error_message) 元组
    """
    start_date, end_date = get_week_dates(year, week)
    time_range = format_date_range(start_date, end_date)
    
    print(f"\n[{beijing_now_iso()}] [batch] {'='*80}")
    print(f"[{beijing_now_iso()}] [batch] 开始分析第{week:2d}周 ({time_range})")
    
    try:
        await run_automation(
            chatlog_url=chatlog_url,
            talker=talker,
            start=start_date,
            end=end_date,
            base_path=base_path,
            template_path=template_path,
            driver_url=driver_url,
            arbitrator_site="chatgpt",
            model_version=model_version,
            task_timeout_s=task_timeout_s,
            new_chat=new_chat,
        )
        print(f"[{beijing_now_iso()}] [batch] ✓ 第{week:2d}周分析完成")
        return (week, True, None)
    except Exception as e:
        error_msg = str(e)
        print(f"[{beijing_now_iso()}] [batch] ✗ 第{week:2d}周分析失败: {error_msg}")
        return (week, False, error_msg)


async def batch_analyze_all_weeks(
    chatlog_url: str,
    talker: str,
    year: int,
    base_path: Path,
    template_path: Optional[Path],
    driver_url: Optional[str],
    model_version: str = "5.2pro",
    task_timeout_s: int = 1200,
    new_chat: bool = False,
    skip_existing: bool = True,
    skip_missing: bool = True,
) -> None:
    """
    批量分析指定年份所有周的数据
    
    Args:
        chatlog_url: chatlog 服务地址
        talker: 聊天对象
        year: 年份
        base_path: Obsidian 基础路径
        template_path: 模板文件路径
        driver_url: driver_server URL
        model_version: 模型版本
        task_timeout_s: 任务超时时间（秒）
        new_chat: 是否每次打开新聊天窗口
        skip_existing: 是否跳过已存在的 summary 文件
        skip_missing: 是否跳过没有数据的周
    """
    print(f"[{beijing_now_iso()}] [batch] 开始批量分析 {year} 年所有周的数据")
    print(f"[{beijing_now_iso()}] [batch] talker={talker}")
    print(f"[{beijing_now_iso()}] [batch] model_version={model_version}")
    print(f"[{beijing_now_iso()}] [batch] task_timeout_s={task_timeout_s}")
    print(f"[{beijing_now_iso()}] [batch] new_chat={new_chat}")
    print(f"[{beijing_now_iso()}] [batch] skip_existing={skip_existing}")
    print(f"[{beijing_now_iso()}] [batch] skip_missing={skip_missing}")
    print("=" * 80)
    
    client = ChatlogClient(chatlog_url)
    
    try:
        # 先检查所有周，确定哪些周有数据
        print(f"\n[{beijing_now_iso()}] [batch] 正在检查所有周的数据情况...")
        weeks_with_data: List[int] = []
        weeks_without_data: List[int] = []
        
        max_week = 53
        for week in range(1, max_week + 1):
            start_date, end_date = get_week_dates(year, week)
            # 如果已经超过该年，停止检查
            if end_date.year > year:
                break
            
            has_data = await check_week_has_data(client, talker, year, week)
            if has_data:
                weeks_with_data.append(week)
            else:
                weeks_without_data.append(week)
                if skip_missing:
                    print(f"[{beijing_now_iso()}] [batch] 跳过第{week:2d}周（无数据）")
        
        print(f"\n[{beijing_now_iso()}] [batch] 检查完成:")
        print(f"[{beijing_now_iso()}] [batch]   有数据: {len(weeks_with_data)} 周")
        print(f"[{beijing_now_iso()}] [batch]   无数据: {len(weeks_without_data)} 周")
        
        if not weeks_with_data:
            print(f"[{beijing_now_iso()}] [batch] ✗ 没有找到任何有数据的周，退出")
            return
        
        # 分析每一周
        results: List[Tuple[int, bool, Optional[str]]] = []
        success_count = 0
        fail_count = 0
        skip_count = 0
        
        for week in weeks_with_data:
            # 检查是否已存在（如果启用跳过）
            if skip_existing:
                from .chatlog_automation import build_obsidian_paths, normalize_model_version_for_filename
                paths = build_obsidian_paths(base_path, talker, get_week_dates(year, week)[0], subdir="10-Summaries")
                start_date, end_date = get_week_dates(year, week)
                date_range_str = format_date_range(start_date, end_date)
                normalized_model_version = normalize_model_version_for_filename(model_version)
                summary_filename = f"{talker} 第{week}周-{date_range_str}-Sum-{normalized_model_version}.md"
                summary_path = paths["dir"] / summary_filename
                
                if summary_path.exists():
                    print(f"[{beijing_now_iso()}] [batch] 跳过第{week:2d}周（已存在 summary 文件）")
                    skip_count += 1
                    continue
            
            # 执行分析
            result = await analyze_week(
                chatlog_url=chatlog_url,
                talker=talker,
                year=year,
                week=week,
                base_path=base_path,
                template_path=template_path,
                driver_url=driver_url,
                model_version=model_version,
                task_timeout_s=task_timeout_s,
                new_chat=new_chat,
            )
            results.append(result)
            
            week_num, success, error = result
            if success:
                success_count += 1
            else:
                fail_count += 1
            
            # 短暂休息，避免请求过快
            await asyncio.sleep(1)
        
        # 输出总结
        print("\n" + "=" * 80)
        print(f"[{beijing_now_iso()}] [batch] 批量分析完成！")
        print(f"[{beijing_now_iso()}] [batch] 总计: {len(weeks_with_data)} 周")
        print(f"[{beijing_now_iso()}] [batch] 成功: {success_count} 周")
        print(f"[{beijing_now_iso()}] [batch] 失败: {fail_count} 周")
        print(f"[{beijing_now_iso()}] [batch] 跳过: {skip_count} 周")
        
        if fail_count > 0:
            print(f"\n[{beijing_now_iso()}] [batch] 失败的周次:")
            for week_num, success, error in results:
                if not success:
                    start_date, end_date = get_week_dates(year, week_num)
                    print(f"  - 第{week_num:2d}周 ({format_date_range(start_date, end_date)}): {error}")
        
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description="批量执行每周群聊记录分析")
    parser.add_argument(
        "--talker",
        type=str,
        required=True,
        help="聊天对象（必填）",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=2025,
        help="年份（默认: 2025）",
    )
    parser.add_argument(
        "--model-version",
        type=str,
        default="5.2pro",
        help="模型版本（默认: 5.2pro）",
    )
    parser.add_argument(
        "--task-timeout-s",
        type=int,
        default=1200,
        help="任务超时时间（秒，默认: 1200）",
    )
    parser.add_argument(
        "--new-chat",
        action="store_true",
        help="每次提交都打开新聊天窗口",
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="不跳过已存在的 summary 文件（默认会跳过）",
    )
    parser.add_argument(
        "--no-skip-missing",
        action="store_true",
        help="不跳过没有数据的周（默认会跳过）",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("chatlog_automation.yaml"),
        help="配置文件路径（默认: chatlog_automation.yaml）",
    )
    
    args = parser.parse_args()
    
    # 从配置文件读取参数
    config = {}
    if args.config.exists():
        try:
            with open(args.config, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
                print(f"[{beijing_now_iso()}] [batch] 从配置文件读取参数")
        except Exception as e:
            print(f"[{beijing_now_iso()}] [batch] 警告: 读取配置文件失败: {e}")
    
    # 从配置文件读取参数（支持嵌套结构）
    chatlog_config = config.get("chatlog", {})
    llm_config = config.get("llm", {})
    obsidian_config = config.get("obsidian", {})
    driver_config = config.get("driver", {})
    
    chatlog_url = chatlog_config.get("url") or config.get("chatlog_url", "http://127.0.0.1:5030")
    model_version = llm_config.get("model_version") or args.model_version
    task_timeout_s = llm_config.get("task_timeout_s") or args.task_timeout_s
    new_chat = llm_config.get("new_chat", False) if not args.new_chat else args.new_chat
    
    base_path_str = obsidian_config.get("base_path") or config.get("base_path", "~/work/personal/obsidian/personal/10_Sources/WeChat")
    base_path = Path(base_path_str).expanduser()
    
    template_path_str = obsidian_config.get("template") or config.get("template_path")
    template_path = None
    if template_path_str:
        template_path = Path(template_path_str)
        if not template_path.is_absolute():
            template_path = Path(".") / template_path
    
    driver_url = driver_config.get("url") or config.get("driver_url")
    if not driver_url:
        # 从 brief.yaml 读取
        brief_path = Path("brief.yaml")
        if brief_path.exists():
            try:
                with open(brief_path, "r", encoding="utf-8") as f:
                    brief_config = yaml.safe_load(f)
                    if brief_config and "driver" in brief_config:
                        driver_url = brief_config["driver"].get("url")
            except Exception:
                pass
    
    if not driver_url:
        print(f"[{beijing_now_iso()}] [batch] ⚠️  警告: 未提供 driver_url，将无法执行 LLM 分析")
        print(f"[{beijing_now_iso()}] [batch] 请通过 --config 或 brief.yaml 配置 driver_url")
        return
    
    asyncio.run(
        batch_analyze_all_weeks(
            chatlog_url=chatlog_url,
            talker=args.talker,
            year=args.year,
            base_path=base_path,
            template_path=template_path,
            driver_url=driver_url,
            model_version=args.model_version,
            task_timeout_s=args.task_timeout_s,
            new_chat=args.new_chat,
            skip_existing=not args.no_skip_existing,
            skip_missing=not args.no_skip_missing,
        )
    )


if __name__ == "__main__":
    main()

