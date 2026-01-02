# -*- coding: utf-8 -*-
"""
Chatlog 集成 CLI
从 chatlog 获取聊天记录，发送到 LLM 分析，保存到 Obsidian
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import yaml

from .chatlog_client import ChatlogClient
from .models import Task, ModelResult
from .vault import (
    make_run_paths, 
    ensure_dir, 
    write_markdown, 
    build_run_index_note,
    make_model_output_filename,
    model_output_note_body,
)
from .utils import utc_now_iso, beijing_now_iso
from .driver_client import run_task as driver_run_task


DEFAULT_PROMPT_TEMPLATE = """你是资深研究员/分析师。请分析以下聊天记录，输出"结论清晰、证据可追溯、便于 Obsidian 阅读"的研究笔记。

聊天记录：
{conversation_content}

请按以下结构输出：
## 1. 关键结论
## 2. 输出洞察
## 3. 行动建议（如有）
## 4. 相关话题（如有）
"""


async def analyze_chatlog_conversations(
    chatlog_url: str,
    chatlog_api_key: Optional[str],
    talker: str,
    time_range: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    sender: Optional[str] = None,
    keyword: Optional[str] = None,
    limit: int = 100,
    sites: List[str] = None,
    prompt_template: str = None,
    vault_path: Path = None,
    driver_url: str = None,
    task_timeout_s: int = 480,
    tags: List[str] = None,
):
    """
    从 chatlog 获取聊天记录并分析
    
    Args:
        chatlog_url: chatlog 服务地址，如 http://127.0.0.1:5030
        chatlog_api_key: API 密钥（可选，当前 API 不需要）
        talker: 聊天对象标识（必填，支持 wxid、群聊 ID、备注名、昵称等）
        time_range: 时间范围字符串，格式为 "YYYY-MM-DD" 或 "YYYY-MM-DD~YYYY-MM-DD"
        start: 起始时间（如果未提供 time_range）
        end: 结束时间（如果未提供 time_range）
        sender: 发送者（可选）
        keyword: 关键词（可选）
        limit: 返回记录数量限制（默认 100）
        sites: 使用的 LLM 站点，默认 ["chatgpt", "gemini"]
        prompt_template: 分析 prompt 模板
        vault_path: Obsidian vault 路径
        driver_url: driver_server URL
        task_timeout_s: 任务超时时间（秒）
        tags: 标签列表
    """
    if sites is None:
        sites = ["chatgpt", "gemini"]
    
    if tags is None:
        tags = ["Chatlog", "Multi-LLM", "Analysis"]
    
    # 初始化 chatlog 客户端
    client = ChatlogClient(chatlog_url, chatlog_api_key)
    
    try:
        # 获取聊天记录
        print(f"[{beijing_now_iso()}] [chatlog] 获取聊天记录: talker={talker}, time_range={time_range or 'auto'}")
        
        messages = await client.get_conversations(
            talker=talker,
            time_range=time_range,
            start=start,
            end=end,
            limit=limit,
        )
        
        print(f"[{beijing_now_iso()}] [chatlog] ✓ 获取到 {len(messages)} 条消息")
        
        if not messages:
            print(f"[{beijing_now_iso()}] [chatlog] 未获取到任何消息，退出")
            return
        
        # 为每条聊天记录创建分析任务
        run_id = utc_now_iso().replace(":", "").replace("+", "_")
        
        # 设置默认 prompt 模板
        if not prompt_template:
            prompt_template = DEFAULT_PROMPT_TEMPLATE
        
        # 设置默认 vault 路径
        if not vault_path:
            vault_path = Path("~/work/personal/obsidian/personal").expanduser()
        
        vault_paths = make_run_paths(vault_path, "10_ResearchRuns", run_id)
        for p in vault_paths.values():
            ensure_dir(p)
        
        # 确定对话标题
        if messages:
            first_msg = messages[0]
            talker_name = first_msg.get("talkerName", first_msg.get("talker", talker))
            conv_title = f"与 {talker_name} 的聊天记录"
        else:
            conv_title = f"与 {talker} 的聊天记录"
        
        # 创建 run index
        run_index_path = vault_paths["run_root"] / "README.md"
        write_markdown(
            run_index_path,
            {
                "type": ["research_run", "chatlog_analysis"],
                "created": utc_now_iso(),
                "author": "browser-orchestrator",
                "run_id": run_id,
                "topic": f"Chatlog Analysis: {conv_title}",
                "tags": tags[:12],
            },
            build_run_index_note(run_id, f"Chatlog Analysis: {conv_title}", tags)
        )
        
        # 处理聊天记录
        all_results: List[ModelResult] = []
        
        print(f"\n[{beijing_now_iso()}] [chatlog] 处理聊天记录: {conv_title} ({len(messages)} 条消息)")
        
        # 格式化聊天记录
        formatted_conv = client.format_messages_for_prompt(messages, talker=talker)
        
        # 构建 prompt
        prompt = prompt_template.format(conversation_content=formatted_conv)
            
        # 创建任务
        tasks = []
        for site in sites:
            tasks.append(Task(
                run_id=run_id,
                site_id=site,
                stream_id="chatlog_analysis",
                stream_name="Chatlog Analysis",
                topic=conv_title,
                prompt=prompt,
            ))
        
        # 执行分析（使用 driver_server 或本地 adapter）
        if driver_url:
            # 使用 driver_server
            for task in tasks:
                    try:
                        payload = await asyncio.to_thread(
                            driver_run_task, 
                            driver_url, 
                            task.site_id, 
                            task.prompt, 
                            task_timeout_s
                        )
                        ok = bool(payload.get("ok"))
                        answer = payload.get("answer") or ""
                        url = payload.get("url") or ""
                        err = payload.get("error")
                        
                        if ok:
                            result = ModelResult(
                                run_id=task.run_id,
                                site_id=task.site_id,
                                stream_id=task.stream_id,
                                stream_name=task.stream_name,
                                topic=task.topic,
                                prompt=task.prompt,
                                answer_text=answer,
                                source_url=url,
                                created_utc=utc_now_iso(),
                                ok=True,
                            )
                        else:
                            result = ModelResult(
                                run_id=task.run_id,
                                site_id=task.site_id,
                                stream_id=task.stream_id,
                                stream_name=task.stream_name,
                                topic=task.topic,
                                prompt=task.prompt,
                                answer_text="",
                                source_url=url,
                                created_utc=utc_now_iso(),
                                ok=False,
                                error=err,
                            )
                        all_results.append(result)
                        
                        # 保存到 Obsidian
                        fname = make_model_output_filename(task.topic, task.stream_id, task.site_id)
                        out_path = vault_paths["model"] / fname
                        write_markdown(
                            out_path,
                            {
                                "type": ["model_output", "chatlog_analysis"],
                                "created": utc_now_iso(),
                                "author": task.site_id,
                                "run_id": run_id,
                                "topic": task.topic,
                                "url": url,
                                "tags": tags[:12],
                            },
                            model_output_note_body(task.prompt, answer, url)
                        )
                        print(f"[{beijing_now_iso()}] [chatlog] ✓ {task.site_id} 分析完成: {conv_title}")
                    except Exception as e:
                        print(f"[{beijing_now_iso()}] [chatlog] ✗ {task.site_id} 分析失败: {e}")
        else:
            # 使用本地 adapter（需要实现）
            print(f"[{beijing_now_iso()}] [chatlog] 警告: 未提供 driver_url，跳过本地 adapter 模式")
        
        print(f"\n[{beijing_now_iso()}] [chatlog] 分析完成！共处理 {len(messages)} 条消息")
        print(f"[{beijing_now_iso()}] [chatlog] 结果保存到: {vault_paths['run_root']}")
        
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description="从 chatlog 获取聊天记录并发送到 LLM 分析")
    parser.add_argument("--chatlog-url", required=True, help="chatlog 服务地址，如 http://127.0.0.1:5030")
    parser.add_argument("--chatlog-api-key", default=None, help="chatlog API 密钥（可选，当前 API 不需要）")
    parser.add_argument("--talker", required=True, help="聊天对象标识（必填，支持 wxid、群聊 ID、备注名、昵称等）")
    parser.add_argument("--time-range", default=None, help="时间范围，格式为 YYYY-MM-DD 或 YYYY-MM-DD~YYYY-MM-DD（默认: 今天）")
    parser.add_argument("--start", default=None, help="起始时间，格式为 YYYY-MM-DD（如果未提供 time-range）")
    parser.add_argument("--end", default=None, help="结束时间，格式为 YYYY-MM-DD（如果未提供 time-range）")
    parser.add_argument("--sender", default=None, help="发送者过滤（可选）")
    parser.add_argument("--keyword", default=None, help="关键词过滤（可选）")
    parser.add_argument("--limit", type=int, default=100, help="返回记录数量限制（默认: 100）")
    parser.add_argument("--sites", nargs="+", default=["chatgpt", "gemini"], help="使用的 LLM 站点（默认: chatgpt gemini）")
    parser.add_argument("--prompt-template", default=None, help="分析 prompt 模板文件路径（可选）")
    parser.add_argument("--vault-path", default=None, help="Obsidian vault 路径（默认: ~/work/personal/obsidian/personal）")
    parser.add_argument("--driver-url", default=None, help="driver_server URL（默认: 从环境变量或 brief.yaml 读取）")
    parser.add_argument("--task-timeout-s", type=int, default=480, help="任务超时时间（秒，默认: 480）")
    parser.add_argument("--tags", nargs="+", default=["Chatlog", "Multi-LLM", "Analysis"], help="标签列表")
    
    args = parser.parse_args()
    
    # 读取 prompt 模板
    prompt_template = None
    if args.prompt_template:
        prompt_template = Path(args.prompt_template).read_text(encoding="utf-8")
    
    # 解析 vault 路径
    vault_path = None
    if args.vault_path:
        vault_path = Path(args.vault_path).expanduser().resolve()
    
    # 获取 driver_url（优先级：命令行参数 > 环境变量 > brief.yaml）
    driver_url = args.driver_url or None
    if not driver_url:
        import os
        driver_url = os.environ.get("RPA_DRIVER_URL", "").strip() or None
    
    # 如果还没有，尝试从 brief.yaml 读取
    if not driver_url:
        try:
            import yaml
            brief_path = Path("brief.yaml")
            if brief_path.exists():
                brief_data = yaml.safe_load(brief_path.read_text(encoding="utf-8"))
                driver_url = brief_data.get("output", {}).get("driver_url", "").strip() or None
                if driver_url:
                    print(f"[{beijing_now_iso()}] [chatlog] 从 brief.yaml 读取 driver_url: {driver_url}")
        except Exception as e:
            # 忽略错误，继续执行
            pass
    
    # 解析时间
    start = None
    end = None
    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d")
    if args.end:
        end = datetime.strptime(args.end, "%Y-%m-%d")
    
    asyncio.run(analyze_chatlog_conversations(
        chatlog_url=args.chatlog_url,
        chatlog_api_key=args.chatlog_api_key,
        talker=args.talker,
        time_range=args.time_range,
        start=start,
        end=end,
        sender=args.sender,
        keyword=args.keyword,
        limit=args.limit,
        sites=args.sites,
        prompt_template=prompt_template,
        vault_path=vault_path,
        driver_url=driver_url,
        task_timeout_s=args.task_timeout_s,
        tags=args.tags,
    ))


if __name__ == "__main__":
    main()

