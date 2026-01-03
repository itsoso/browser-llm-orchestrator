#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检查每周群聊记录数据是否存在

用法:
    python -m rpa_llm.check_weekly_data --talker "川群-2025" --year 2025
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple

import yaml

from .chatlog_client import ChatlogClient
from .chatlog_automation import get_week_number, format_date_range, get_week_dates
from .utils import beijing_now_iso


async def check_week_data(
    client: ChatlogClient,
    talker: str,
    year: int,
    week: int,
) -> Tuple[int, datetime, datetime, bool, int]:
    """
    检查指定周的数据是否存在
    
    Returns:
        (week, start_date, end_date, exists, message_count) 元组
    """
    start_date, end_date = get_week_dates(year, week)
    time_range = format_date_range(start_date, end_date)
    
    try:
        messages = await client.get_conversations(
            talker=talker,
            time_range=time_range,
            start=start_date,
            end=end_date,
            limit=1,  # 只检查是否有数据，不需要获取全部
        )
        exists = len(messages) > 0
        message_count = len(messages) if exists else 0
        return (week, start_date, end_date, exists, message_count)
    except Exception as e:
        print(f"[{beijing_now_iso()}] [check] 第{week}周检查失败: {e}")
        return (week, start_date, end_date, False, 0)


async def check_all_weeks(
    chatlog_url: str,
    talker: str,
    year: int = 2025,
) -> None:
    """
    检查指定年份所有周的数据是否存在
    
    Args:
        chatlog_url: chatlog 服务地址
        talker: 聊天对象
        year: 年份（默认 2025）
    """
    print(f"[{beijing_now_iso()}] [check] 开始检查 {year} 年所有周的数据")
    print(f"[{beijing_now_iso()}] [check] talker={talker}")
    print("=" * 80)
    
    client = ChatlogClient(chatlog_url)
    
    try:
        # 检查每一周（最多53周）
        results: List[Tuple[int, datetime, datetime, bool, int]] = []
        missing_weeks: List[Tuple[int, datetime, datetime]] = []
        
        # 先检查第一周，确定实际有多少周
        max_week = 53
        for week in range(1, max_week + 1):
            result = await check_week_data(client, talker, year, week)
            week_num, start_date, end_date, exists, msg_count = result
            results.append(result)
            
            if exists:
                print(f"[{beijing_now_iso()}] [check] ✓ 第{week_num:2d}周 ({start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}): {msg_count} 条消息")
            else:
                print(f"[{beijing_now_iso()}] [check] ✗ 第{week_num:2d}周 ({start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}): 无数据")
                missing_weeks.append((week_num, start_date, end_date))
            
            # 如果已经到年底，停止检查
            if end_date.year > year or (end_date.year == year and end_date.month == 12 and end_date.day == 31):
                if not exists and week > 1:
                    # 如果这一周没有数据且不是第一周，可能是已经到年底了
                    break
        
        print("=" * 80)
        print(f"\n[check] 检查完成！")
        print(f"[check] 总计: {len(results)} 周")
        print(f"[check] 有数据: {len(results) - len(missing_weeks)} 周")
        print(f"[check] 无数据: {len(missing_weeks)} 周")
        
        if missing_weeks:
            print(f"\n[check] ⚠️  以下周次缺少数据，请检查微信聊天记录同步:")
            for week_num, start_date, end_date in missing_weeks:
                print(f"  - 第{week_num:2d}周: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")
        else:
            print(f"\n[check] ✓ 所有周次都有数据！")
            
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description="检查每周群聊记录数据是否存在")
    parser.add_argument(
        "--chatlog-url",
        type=str,
        default="http://127.0.0.1:5030",
        help="chatlog 服务地址（默认: http://127.0.0.1:5030）",
    )
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
        "--config",
        type=Path,
        default=Path("chatlog_automation.yaml"),
        help="配置文件路径（默认: chatlog_automation.yaml）",
    )
    
    args = parser.parse_args()
    
    # 从配置文件读取 chatlog_url（如果存在）
    chatlog_url = args.chatlog_url
    if args.config.exists():
        try:
            with open(args.config, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
                # 支持嵌套结构
                chatlog_config = config.get("chatlog", {})
                chatlog_url = chatlog_config.get("url") or config.get("chatlog_url") or chatlog_url
                print(f"[{beijing_now_iso()}] [check] 从配置文件读取 chatlog_url: {chatlog_url}")
        except Exception as e:
            print(f"[{beijing_now_iso()}] [check] 警告: 读取配置文件失败: {e}")
    
    asyncio.run(check_all_weeks(chatlog_url, args.talker, args.year))


if __name__ == "__main__":
    main()

