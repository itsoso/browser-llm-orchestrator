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
from .models import Brief, Task, ModelResult
from .prompts import SynthesisPromptConfig, build_dual_model_arbitration_prompt
from .vault import (
    make_run_paths, 
    ensure_dir, 
    write_markdown, 
    build_run_index_note,
    make_model_output_filename,
    model_output_note_body,
)
from .utils import utc_now_iso, beijing_now_iso, slugify, clean_text_code_block
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
    task_timeout_s: int = 1200,
    tags: List[str] = None,
    synthesis_left_site: str = "gemini",
    synthesis_right_site: str = "chatgpt",
    arbitrator_site: str = "gemini",
    synthesis_timeout_s: int = 600,
    enable_synthesis: bool = True,
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
                        # 保留 LLM 的原始返回结果，不在这里清理
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
                        # 在写入 Obsidian 之前清理代码块标记，确保内容能正常渲染
                        # 这样保留 LLM 的原始返回结果，只在最终写入时处理
                        cleaned_answer = clean_text_code_block(answer)
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
                            model_output_note_body(task.prompt, cleaned_answer)
                        )
                        print(f"[{beijing_now_iso()}] [chatlog] ✓ {task.site_id} 分析完成: {conv_title}")
                    except Exception as e:
                        print(f"[{beijing_now_iso()}] [chatlog] ✗ {task.site_id} 分析失败: {e}")
        else:
            # 使用本地 adapter（需要实现）
            print(f"[{beijing_now_iso()}] [chatlog] 警告: 未提供 driver_url，跳过本地 adapter 模式")
        
        print(f"\n[{beijing_now_iso()}] [chatlog] 分析完成！共处理 {len(messages)} 条消息")
        print(f"[{beijing_now_iso()}] [chatlog] 结果保存到: {vault_paths['run_root']}")
        
        # 执行 synthesis（融合）步骤
        if enable_synthesis and len(all_results) >= 2:
            # 检查是否有至少两个站点的成功结果
            ok_results = [r for r in all_results if r.ok]
            site_ids = {r.site_id for r in ok_results}
            
            if len(site_ids) >= 2:
                print(f"\n[{beijing_now_iso()}] [chatlog] 开始 synthesis（融合）步骤...")
                try:
                    # 创建一个简化的 Brief 对象用于 synthesis
                    brief = Brief(
                        topic=conv_title,
                        context=f"聊天记录分析：与 {talker} 的对话",
                        questions=[],
                        streams=[],
                        sites=list(site_ids),
                        output={},
                    )
                    
                    # 构建 synthesis prompt
                    cfg = SynthesisPromptConfig(
                        left_site=synthesis_left_site,
                        right_site=synthesis_right_site,
                        left_label=synthesis_left_site.capitalize(),
                        right_label=synthesis_right_site.capitalize(),
                    )
                    synthesis_prompt = build_dual_model_arbitration_prompt(brief, ok_results, cfg)
                    
                    print(f"[{beijing_now_iso()}] [chatlog] synthesis prompt 构建完成，发送到 {arbitrator_site}...")
                    
                    # 调用 arbitrator 站点生成最终结果
                    if driver_url:
                        payload = await asyncio.to_thread(
                            driver_run_task,
                            driver_url,
                            arbitrator_site,
                            synthesis_prompt,
                            synthesis_timeout_s
                        )
                        ok = bool(payload.get("ok"))
                        answer = payload.get("answer") or ""
                        # 保留 LLM 的原始返回结果，不在这里清理
                        url = payload.get("url") or ""
                        err = payload.get("error")
                        
                        if not ok:
                            raise RuntimeError(f"driver synthesis failed (site={arbitrator_site}): {err}")
                    else:
                        raise RuntimeError("未提供 driver_url，无法执行 synthesis")
                    
                    # 保存 synthesis 结果
                    # 在写入 Obsidian 之前清理代码块标记，确保内容能正常渲染
                    # 这样保留 LLM 的原始返回结果，只在最终写入时处理
                    cleaned_answer = clean_text_code_block(answer)
                    topic_slug = slugify(conv_title, max_len=40)
                    final_path = vault_paths["final"] / f"final__{arbitrator_site}__{topic_slug}.md"
                    write_markdown(
                        final_path,
                        {
                            "type": ["synthesis", "final_decision", "chatlog_analysis"],
                            "created": utc_now_iso(),
                            "author": arbitrator_site,
                            "run_id": run_id,
                            "topic": conv_title,
                            "url": url,
                            "tags": tags[:12],
                        },
                        cleaned_answer
                    )
                    
                    print(f"[{beijing_now_iso()}] [chatlog] ✓ synthesis 完成，结果保存到: {final_path}")
                except Exception as e:
                    print(f"[{beijing_now_iso()}] [chatlog] ✗ synthesis 失败: {e}")
            else:
                print(f"[{beijing_now_iso()}] [chatlog] 跳过 synthesis：需要至少 2 个站点的成功结果（当前: {len(site_ids)} 个站点）")
        elif not enable_synthesis:
            print(f"[{beijing_now_iso()}] [chatlog] synthesis 已禁用")
        
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
    parser.add_argument("--task-timeout-s", type=int, default=1200, help="任务超时时间（秒，默认: 1200，ChatGPT 5.2 Pro 建议 1200 或更长）")
    parser.add_argument("--tags", nargs="+", default=["Chatlog", "Multi-LLM", "Analysis"], help="标签列表")
    parser.add_argument("--synthesis-left-site", default="gemini", help="synthesis 左侧站点（默认: gemini）")
    parser.add_argument("--synthesis-right-site", default="chatgpt", help="synthesis 右侧站点（默认: chatgpt）")
    parser.add_argument("--arbitrator-site", default="gemini", help="synthesis 仲裁站点（默认: gemini）")
    parser.add_argument("--synthesis-timeout-s", type=int, default=600, help="synthesis 超时时间（秒，默认: 600）")
    parser.add_argument("--no-synthesis", action="store_true", help="禁用 synthesis（融合）步骤")
    parser.add_argument("--log-file", help="日志文件路径（如果未指定，则自动生成到 logs/ 目录）")
    
    args = parser.parse_args()
    
    # 设置日志文件
    if args.log_file:
        log_file = Path(args.log_file).expanduser().resolve()
    else:
        # 自动生成日志文件路径：logs/chatlog_YYYYMMDD_HHMMSS.log
        logs_dir = Path("logs")
        logs_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = logs_dir / f"chatlog_{timestamp}.log"
    
    # 打开日志文件（追加模式）
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_fp = open(log_file, "a", encoding="utf-8")
    
    # 创建 Tee 类，同时输出到控制台和文件
    class Tee:
        def __init__(self, *files):
            self.files = files
        
        def write(self, obj):
            for f in self.files:
                f.write(obj)
                f.flush()
        
        def flush(self):
            for f in self.files:
                f.flush()
    
    # 保存原始的 stdout 和 stderr
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    
    # 创建 Tee 对象，同时输出到控制台和文件
    tee_stdout = Tee(sys.stdout, log_fp)
    tee_stderr = Tee(sys.stderr, log_fp)
    
    # 重定向 stdout 和 stderr
    sys.stdout = tee_stdout
    sys.stderr = tee_stderr
    
    # 输出日志文件路径
    print(f"[chatlog] 日志文件: {log_file}")
    print(f"[chatlog] 日志文件路径: {log_file.absolute()}")
    
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
    synthesis_left_site = args.synthesis_left_site
    synthesis_right_site = args.synthesis_right_site
    arbitrator_site = args.arbitrator_site
    synthesis_timeout_s = args.synthesis_timeout_s
    
    if not driver_url:
        try:
            brief_path = Path("brief.yaml")
            if brief_path.exists():
                brief_data = yaml.safe_load(brief_path.read_text(encoding="utf-8"))
                output_config = brief_data.get("output", {})
                driver_url = output_config.get("driver_url", "").strip() or None
                if driver_url:
                    print(f"[{beijing_now_iso()}] [chatlog] 从 brief.yaml 读取 driver_url: {driver_url}")
                
                # 从 brief.yaml 读取 synthesis 配置（如果未通过命令行指定）
                if args.synthesis_left_site == "gemini":  # 默认值
                    synthesis_left_site = output_config.get("synthesis_left_site", "gemini")
                if args.synthesis_right_site == "chatgpt":  # 默认值
                    synthesis_right_site = output_config.get("synthesis_right_site", "chatgpt")
                if args.arbitrator_site == "gemini":  # 默认值
                    arbitrator_site = output_config.get("arbitrator_site", "gemini")
                if args.synthesis_timeout_s == 600:  # 默认值
                    synthesis_timeout_s = output_config.get("synthesis_timeout_s", 600)
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
    
    try:
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
        synthesis_left_site=synthesis_left_site,
        synthesis_right_site=synthesis_right_site,
        arbitrator_site=arbitrator_site,
        synthesis_timeout_s=synthesis_timeout_s,
            enable_synthesis=not args.no_synthesis,
        ))
    finally:
        # 恢复原始的 stdout 和 stderr
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        # 关闭日志文件
        log_fp.close()
        # 输出日志文件路径到控制台（恢复后）
        print(f"\n[chatlog] 日志已保存到: {log_file.absolute()}", file=original_stdout)


if __name__ == "__main__":
    main()

