# -*- coding: utf-8 -*-
"""
Chatlog 自动化工作流
实现从 chatlog 获取聊天记录 -> 保存 raw 文件 -> LLM 分析 -> 保存 summary 文件的完整流程
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml

import re

from .chatlog_client import ChatlogClient
from .chatlog_cli import analyze_chatlog_conversations
from .driver_client import run_task as driver_run_task
from .utils import beijing_now_iso, ensure_dir
from .vault import write_markdown


def get_week_number(date: datetime) -> int:
    """获取日期所在周数（ISO 8601 标准，周一开始）"""
    # ISO 8601: 周一是第一天，第一周是包含1月4日的那一周
    iso_year, iso_week, _ = date.isocalendar()
    return iso_week


def build_obsidian_paths(
    base_path: Path,
    talker: str,
    date: datetime,
    subdir: str = "00-raws",
) -> dict:
    """
    构建 Obsidian 目录结构
    
    Args:
        base_path: 基础路径，如 /Users/liqiuhua/work/personal/obsidian/personal/10_Sources/WeChat
        talker: 聊天对象，如 "川群-2025"
        date: 日期
        subdir: 子目录，如 "00-raws" 或 "10-Summaries"
    
    Returns:
        包含目录路径和文件路径的字典
    """
    year = date.year
    month = date.month
    week = get_week_number(date)
    
    # 构建目录路径：{subdir}/{talker}/{year}/{month}/第{week}周
    dir_path = base_path / subdir / talker / str(year) / f"{month:02d}" / f"第{week}周"
    ensure_dir(dir_path)
    
    return {
        "dir": dir_path,
        "year": year,
        "month": month,
        "week": week,
    }


def format_date_range(start: datetime, end: datetime) -> str:
    """格式化日期范围为字符串"""
    if start.date() == end.date():
        return start.strftime("%Y-%m-%d")
    return f"{start.strftime('%Y-%m-%d')}~{end.strftime('%Y-%m-%d')}"


def normalize_model_version_for_filename(model_version: str) -> str:
    """
    规范化模型版本字符串，用于文件名
    
    确保不同格式的模型版本（如 "5.2pro", "5.2-pro", "gpt-5.2-pro"）生成不同的文件名，
    同时去除特殊字符，确保文件名安全。
    
    Args:
        model_version: 原始模型版本字符串，如 "5.2pro", "5.2instant", "gpt-5.2-pro"
    
    Returns:
        规范化后的模型版本字符串，用于文件名，如 "5.2pro", "5.2instant", "gpt-5.2-pro"
    
    Examples:
        >>> normalize_model_version_for_filename("5.2pro")
        "5.2pro"
        >>> normalize_model_version_for_filename("5.2-pro")
        "5.2-pro"
        >>> normalize_model_version_for_filename("gpt-5.2-pro")
        "gpt-5.2-pro"
        >>> normalize_model_version_for_filename("5.2instant")
        "5.2instant"
        >>> normalize_model_version_for_filename("GPT-5")
        "GPT-5"
    """
    if not model_version:
        return "default"
    
    # 去除首尾空白
    normalized = model_version.strip()
    
    # 转换为小写，但保留关键区分信息（如 pro vs instant）
    # 注意：我们不全部转小写，因为 "5.2pro" 和 "5.2Pro" 应该被视为相同
    # 但 "5.2pro" 和 "5.2instant" 应该保持不同
    
    # 规范化常见的变体格式
    # "gpt-5.2-pro" -> "gpt-5.2-pro" (保持不变，因为包含 gpt 前缀)
    # "5.2-pro" -> "5.2-pro" (保持不变)
    # "5.2pro" -> "5.2pro" (保持不变)
    # "5.2Pro" -> "5.2pro" (统一转小写，但保留数字和点)
    # "GPT-5" -> "gpt-5" (统一转小写)
    
    # 如果包含大写字母，统一转小写（但保留数字、点、横线）
    if any(c.isupper() for c in normalized):
        # 保留数字、点、横线、小写字母
        normalized = re.sub(r'[A-Z]', lambda m: m.group().lower(), normalized)
    
    # 去除文件名不安全的字符（保留字母、数字、点、横线、下划线）
    normalized = re.sub(r'[^\w.\-]+', '', normalized)
    
    # 确保不为空
    if not normalized:
        return "default"
    
    return normalized


async def save_raw_file(
    messages: list,
    base_path: Path,
    talker: str,
    start: datetime,
    end: datetime,
    client: ChatlogClient,
) -> Path:
    """
    保存 raw 文件
    
    Returns:
        raw 文件路径
    """
    paths = build_obsidian_paths(base_path, talker, start, subdir="00-raws")
    
    date_range_str = format_date_range(start, end)
    filename = f"{talker} {date_range_str}-raw.md"
    raw_path = paths["dir"] / filename
    
    # 格式化聊天记录
    formatted_content = client.format_messages_for_prompt(messages, talker=talker)
    
    # 保存 raw 文件
    write_markdown(
        raw_path,
        {
            "type": ["wechat_raw", "chatlog"],
            "created": beijing_now_iso(),
            "talker": talker,
            "date_range": date_range_str,
            "message_count": len(messages),
        },
        formatted_content
    )
    
    print(f"[{beijing_now_iso()}] [automation] ✓ Raw 文件已保存: {raw_path}")
    return raw_path


async def load_template_and_generate_prompt(
    template_path: Path,
    raw_content: str,
    talker: str,
    date_range: str,
    week: int,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    raw_file_path: Optional[Path] = None,
) -> str:
    """
    从 template 文件加载并生成 prompt
    
    支持两种占位符格式：
    1. Python format: {talker}, {date_range}, {week}, {conversation_content}
    2. Template format: {{group_yq}}, {{week}}, {{period_start}}, {{period_end}}, {{raw_note}}
    
    Args:
        template_path: template 文件路径
        raw_content: raw 文件内容
        talker: 聊天对象
        date_range: 日期范围字符串
        week: 周数
        start: 开始日期（用于生成 period_start）
        end: 结束日期（用于生成 period_end）
        raw_file_path: raw 文件路径（用于生成 raw_note）
    
    Returns:
        生成的 prompt
    """
    if not template_path.exists():
        # 使用默认模板
        default_template = """你是资深研究员/分析师。请分析以下聊天记录，输出"结论清晰、证据可追溯、便于 Obsidian 阅读"的研究笔记。

聊天记录：
{conversation_content}

请按以下结构输出：
## 1. 关键结论
## 2. 输出洞察
## 3. 行动建议（如有）
## 4. 相关话题（如有）
"""
        return default_template.format(conversation_content=raw_content)
    
    template = template_path.read_text(encoding="utf-8")
    
    # 准备替换值
    period_start = start.strftime("%Y-%m-%d") if start else date_range.split("~")[0] if "~" in date_range else date_range
    period_end = end.strftime("%Y-%m-%d") if end else date_range.split("~")[-1] if "~" in date_range else date_range
    period_start_dot = period_start.replace("-", ".")
    period_end_dot = period_end.replace("-", ".")
    raw_note = str(raw_file_path) if raw_file_path else ""
    
    # 先替换 {{}} 格式的占位符（模板格式）
    replacements = {
        "{{group_yq}}": talker,
        "{{group}}": talker,
        "{{week}}": str(week),
        "{{period_start}}": period_start,
        "{{period_end}}": period_end,
        "{{period_start_dot}}": period_start_dot,
        "{{period_end_dot}}": period_end_dot,
        "{{raw_note}}": raw_note,
        "{{conversation_content}}": raw_content,
    }
    
    # 记录替换前的状态
    has_conversation_placeholder = "{{conversation_content}}" in template
    raw_content_len = len(raw_content)
    
    for placeholder, value in replacements.items():
        if placeholder in template:
            template = template.replace(placeholder, value)
            # 对于 conversation_content，记录替换信息
            if placeholder == "{{conversation_content}}":
                print(f"[{beijing_now_iso()}] [automation] 替换占位符 {placeholder} (内容长度: {len(value)} 字符)")
    
    # 验证 conversation_content 是否被替换
    if has_conversation_placeholder:
        if "{{conversation_content}}" in template:
            print(f"[{beijing_now_iso()}] [automation] ⚠️  警告: {{conversation_content}} 占位符未被替换！")
            # 尝试手动替换
            template = template.replace("{{conversation_content}}", raw_content)
            print(f"[{beijing_now_iso()}] [automation] 手动替换 {{conversation_content}} 成功")
        else:
            # 验证替换后的内容是否包含聊天内容
            if raw_content_len > 0:
                content_preview = raw_content[:100].strip()
                if content_preview and content_preview not in template:
                    print(f"[{beijing_now_iso()}] [automation] ⚠️  警告: 替换后模板中未找到聊天内容预览")
                else:
                    print(f"[{beijing_now_iso()}] [automation] ✓ {{conversation_content}} 占位符已成功替换")
    
    # 再替换 {} 格式的占位符（Python format）- 注意：此时 {{}} 格式已经被替换了
    # 使用 safe_format 避免 KeyError，只替换存在的占位符
    try:
        # 先检查是否有未替换的 {} 格式占位符
        import re
        remaining_placeholders = re.findall(r'\{(\w+)\}', template)
        if remaining_placeholders:
            # 只替换存在的占位符
            prompt = template.format(
                conversation_content=raw_content,  # 双重保险
                talker=talker,
                date_range=date_range,
                week=week,
                period_start=period_start,
                period_end=period_end,
                period_start_dot=period_start_dot,
                period_end_dot=period_end_dot,
                raw_note=raw_note,
            )
        else:
            prompt = template
    except KeyError as e:
        # 如果有些占位符没有提供，直接使用替换后的模板
        print(f"[{beijing_now_iso()}] [automation] ⚠️  Python format 替换时缺少占位符: {e}，使用已替换的模板")
        prompt = template
    
    # 最终验证：确保 prompt 中包含聊天内容
    if raw_content_len > 0:
        # 检查 prompt 中是否包含聊天内容的前100个字符
        content_preview = raw_content[:100].strip()
        if content_preview and content_preview not in prompt:
            # 检查是否至少包含一些关键词
            keywords = ["对话", "聊天", "消息", "记录", "群聊", "王川", "2026-01-02"]
            has_keywords = any(kw in prompt for kw in keywords)
            if not has_keywords:
                print(f"[{beijing_now_iso()}] [automation] ✗ 错误: Prompt 中未找到聊天内容！")
                print(f"[{beijing_now_iso()}] [automation] Raw 内容预览: {content_preview[:200]}...")
                print(f"[{beijing_now_iso()}] [automation] Prompt 预览: {prompt[:500]}...")
            else:
                print(f"[{beijing_now_iso()}] [automation] ✓ Prompt 验证通过（包含聊天内容关键词）")
        else:
            print(f"[{beijing_now_iso()}] [automation] ✓ Prompt 验证通过（包含聊天内容）")
    
    return prompt


async def save_summary_file(
    summary_content: str,
    base_path: Path,
    talker: str,
    start: datetime,
    end: datetime,
    model_version: str = "5.2pro",
) -> Path:
    """
    保存 summary 文件
    
    Args:
        summary_content: LLM 生成的摘要内容
        base_path: 基础路径
        talker: 聊天对象
        start: 开始日期
        end: 结束日期
        model_version: 模型版本，如 "5.2pro"
    
    Returns:
        summary 文件路径
    """
    paths = build_obsidian_paths(base_path, talker, start, subdir="10-Summaries")
    
    date_range_str = format_date_range(start, end)
    week = paths["week"]
    # 规范化模型版本字符串，确保不同模型版本生成不同的文件名
    normalized_model_version = normalize_model_version_for_filename(model_version)
    filename = f"{talker} 第{week}周-{date_range_str}-Sum-{normalized_model_version}.md"
    summary_path = paths["dir"] / filename
    
    # 保存 summary 文件
    write_markdown(
        summary_path,
        {
            "type": ["wechat_summary", "chatlog_analysis"],
            "created": beijing_now_iso(),
            "talker": talker,
            "date_range": date_range_str,
            "week": week,
            "model_version": model_version,
        },
        summary_content
    )
    
    print(f"[{beijing_now_iso()}] [automation] ✓ Summary 文件已保存: {summary_path}")
    return summary_path


async def run_automation(
    chatlog_url: str,
    talker: str,
    start: datetime,
    end: datetime,
    base_path: Path,
    template_path: Optional[Path] = None,
    driver_url: Optional[str] = None,
    arbitrator_site: str = "gemini",
    model_version: str = "5.2pro",
    task_timeout_s: int = 600,
    new_chat: bool = False,
):
    """
    运行完整的自动化流程
    
    1. 从 chatlog 获取聊天记录
    2. 保存 raw 文件
    3. 从 template 生成 prompt
    4. 调用 LLM 分析
    5. 保存 summary 文件
    """
    # 生成请求 ID，便于追踪和去重
    import uuid
    request_id = str(uuid.uuid4())[:8]
    
    print(f"[{beijing_now_iso()}] [automation] 开始自动化流程 (request_id={request_id})")
    print(f"[{beijing_now_iso()}] [automation] talker={talker}, date_range={format_date_range(start, end)}")
    
    # 检查是否已经有相同的 summary 文件（防止重复执行）
    from .vault import build_obsidian_paths
    paths = build_obsidian_paths(base_path, talker, start, subdir="10-Summaries")
    date_range_str = format_date_range(start, end)
    week = paths["week"]
    normalized_model_version = normalize_model_version_for_filename(model_version)
    summary_filename = f"{talker} 第{week}周-{date_range_str}-Sum-{normalized_model_version}.md"
    summary_path = paths["dir"] / summary_filename
    
    if summary_path.exists():
        print(f"[{beijing_now_iso()}] [automation] ⚠️  警告: 检测到已存在的 summary 文件: {summary_path}")
        print(f"[{beijing_now_iso()}] [automation] 如果确实需要重新分析，请先删除该文件")
        print(f"[{beijing_now_iso()}] [automation] 跳过本次执行 (request_id={request_id})")
        return
    
    # 步骤 1: 获取聊天记录
    client = ChatlogClient(chatlog_url)
    try:
        time_range = format_date_range(start, end)
        messages = await client.get_conversations(
            talker=talker,
            time_range=time_range,
            start=start,
            end=end,
            limit=1000,  # 增加限制以获取更多消息
        )
        
        if not messages:
            print(f"[{beijing_now_iso()}] [automation] ✗ 未获取到任何消息，退出")
            return
        
        print(f"[{beijing_now_iso()}] [automation] ✓ 获取到 {len(messages)} 条消息")
        
        # 步骤 2: 保存 raw 文件
        raw_path = await save_raw_file(
            messages, base_path, talker, start, end, client
        )
        
        # 读取 raw 文件内容（用于生成 prompt）
        raw_content = raw_path.read_text(encoding="utf-8")
        
        # 步骤 3: 从 template 生成 prompt
        week = get_week_number(start)
        prompt = await load_template_and_generate_prompt(
            template_path or Path(""),
            raw_content,
            talker,
            time_range,
            week,
            start=start,
            end=end,
            raw_file_path=raw_path,
        )
        
        # 验证 prompt 中是否包含聊天内容
        raw_content_preview = raw_content[:200] if len(raw_content) > 200 else raw_content
        if raw_content_preview not in prompt and "{{conversation_content}}" not in prompt and "{conversation_content}" not in prompt:
            # 检查是否至少包含一些聊天内容的关键词
            has_content = any(keyword in prompt for keyword in ["对话", "聊天", "消息", "记录", "群聊", "王川"])
            if not has_content:
                print(f"[{beijing_now_iso()}] [automation] ⚠️  警告: Prompt 中可能没有包含聊天内容")
                print(f"[{beijing_now_iso()}] [automation] Raw 内容预览: {raw_content_preview[:100]}...")
                print(f"[{beijing_now_iso()}] [automation] Prompt 预览: {prompt[:300]}...")
            else:
                print(f"[{beijing_now_iso()}] [automation] ✓ Prompt 生成完成（包含聊天内容关键词）")
        else:
            print(f"[{beijing_now_iso()}] [automation] ✓ Prompt 生成完成（长度: {len(prompt)} 字符）")
        
        # 步骤 4: 调用 LLM 分析
        if not driver_url:
            print(f"[{beijing_now_iso()}] [automation] ✗ 未提供 driver_url，跳过 LLM 分析")
            return
        
        # 检查 driver_server 健康状态
        try:
            from .driver_client import health
            health_result = await asyncio.to_thread(health, driver_url)
            if not health_result.get("ok"):
                print(f"[{beijing_now_iso()}] [automation] ⚠ 警告: driver_server 健康检查失败: {health_result.get('error', 'unknown')}")
        except Exception as health_err:
            print(f"[{beijing_now_iso()}] [automation] ⚠ 警告: 无法连接到 driver_server ({driver_url}): {health_err}")
            print(f"[{beijing_now_iso()}] [automation] 提示: 请确保 driver_server 正在运行")
            print(f"[{beijing_now_iso()}] [automation] 启动命令: python start_driver.py --brief ./brief.yaml")
        
        print(f"[{beijing_now_iso()}] [automation] 发送到 {arbitrator_site} 进行分析 (request_id={request_id}, model_version={model_version}, timeout={task_timeout_s}s)...")
        
        try:
            payload = await asyncio.to_thread(
                driver_run_task,
                driver_url,
                arbitrator_site,
                prompt,
                task_timeout_s,
                model_version,  # 传递模型版本参数
                new_chat,  # 传递 new_chat 参数
            )
        except Exception as e:
            error_msg = str(e)
            if "502" in error_msg or "Bad Gateway" in error_msg:
                raise RuntimeError(
                    f"LLM 分析失败: driver_server 返回 502 Bad Gateway\n"
                    f"可能原因：\n"
                    f"  1. driver_server 未运行或已崩溃\n"
                    f"  2. driver_server 处理请求时出错\n"
                    f"  3. 网络连接问题\n"
                    f"解决方案：\n"
                    f"  1. 检查 driver_server 是否运行: curl {driver_url}/health\n"
                    f"  2. 重启 driver_server: python start_driver.py --brief ./brief.yaml\n"
                    f"  3. 查看 driver_server 日志: logs/driver_*.log\n"
                    f"原始错误: {error_msg}"
                )
            else:
                raise RuntimeError(f"LLM 分析失败 (site={arbitrator_site}): {error_msg}")
        
        ok = bool(payload.get("ok"))
        answer = payload.get("answer") or ""
        url = payload.get("url") or ""
        err = payload.get("error")
        http_status = payload.get("http_status")
        
        if not ok:
            error_details = []
            if http_status:
                error_details.append(f"HTTP 状态码: {http_status}")
            if err:
                error_details.append(f"错误信息: {err}")
            
            error_msg = f"LLM 分析失败 (site={arbitrator_site})"
            if error_details:
                error_msg += "\n" + "\n".join(error_details)
            
            # 针对常见错误提供解决方案
            if http_status == 502:
                error_msg += (
                    f"\n\n可能原因：\n"
                    f"  1. driver_server 未运行或已崩溃\n"
                    f"  2. driver_server 处理请求时出错\n"
                    f"  3. 网络连接问题\n"
                    f"解决方案：\n"
                    f"  1. 检查 driver_server 是否运行: curl {driver_url}/health\n"
                    f"  2. 重启 driver_server: python start_driver.py --brief ./brief.yaml\n"
                    f"  3. 查看 driver_server 日志: logs/driver_*.log"
                )
            elif http_status == 503:
                error_msg += (
                    f"\n\n可能原因：\n"
                    f"  1. driver_server 正在初始化站点\n"
                    f"  2. 站点需要手动登录/验证\n"
                    f"解决方案：\n"
                    f"  1. 等待几秒后重试\n"
                    f"  2. 检查 driver_server 日志\n"
                    f"  3. 如果提示需要登录，请手动完成登录后重试"
                )
            
            raise RuntimeError(error_msg)
        
        print(f"[{beijing_now_iso()}] [automation] ✓ LLM 分析完成 (request_id={request_id})")
        
        # 步骤 5: 保存 summary 文件
        summary_path = await save_summary_file(
            answer, base_path, talker, start, end, model_version
        )
        
        print(f"[{beijing_now_iso()}] [automation] ✓ 自动化流程完成 (request_id={request_id})")
        print(f"[{beijing_now_iso()}] [automation] Raw 文件: {raw_path}")
        print(f"[{beijing_now_iso()}] [automation] Summary 文件: {summary_path}")
        
    finally:
        await client.close()


def load_config(config_path: Optional[Path] = None) -> dict:
    """
    加载配置文件
    
    Args:
        config_path: 配置文件路径，如果为 None，尝试加载 chatlog_automation.yaml
    
    Returns:
        配置字典
    """
    if config_path is None:
        config_path = Path("chatlog_automation.yaml")
    
    if not config_path.exists():
        return {}
    
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        return data or {}
    except Exception as e:
        print(f"[{beijing_now_iso()}] [automation] 警告: 加载配置文件失败: {e}", file=sys.stderr)
        return {}


def main():
    parser = argparse.ArgumentParser(description="Chatlog 自动化工作流")
    parser.add_argument("--config", default=None, help="配置文件路径（默认: chatlog_automation.yaml）")
    parser.add_argument("--chatlog-url", default=None, help="chatlog 服务地址，如 http://127.0.0.1:5030")
    parser.add_argument("--talker", required=True, help="聊天对象标识（必填）")
    parser.add_argument("--start", required=True, help="开始日期，格式为 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期，格式为 YYYY-MM-DD")
    parser.add_argument("--base-path", default=None, help="Obsidian 基础路径（默认: ~/work/personal/obsidian/personal/10_Sources/WeChat）")
    parser.add_argument("--template", default=None, help="Prompt 模板文件路径（可选）")
    parser.add_argument("--driver-url", default=None, help="driver_server URL（默认: 从环境变量或 brief.yaml 读取）")
    parser.add_argument("--arbitrator-site", default=None, help="LLM 分析站点（默认: gemini）")
    parser.add_argument("--model-version", default=None, help="模型版本（默认: 5.2pro）")
    parser.add_argument("--model_version", default=None, help="模型版本（别名，等同于 --model-version）")
    parser.add_argument("--task-timeout-s", type=int, default=None, help="任务超时时间（秒，默认: 600）")
    parser.add_argument("--new-chat", action="store_true", help="每次提交 prompt 时都打开新窗口（新聊天）")
    parser.add_argument("--new_chat", action="store_true", help="每次提交 prompt 时都打开新窗口（别名，等同于 --new-chat）")
    parser.add_argument("--log-file", default=None, help="日志文件路径（如果未指定，则自动生成到 logs/ 目录）")
    
    args = parser.parse_args()
    
    # 加载配置文件
    config_path = Path(args.config) if args.config else None
    config = load_config(config_path)
    
    # 参数优先级：命令行参数 > 配置文件 > 默认值
    chatlog_url = args.chatlog_url or config.get("chatlog", {}).get("url") or None
    if not chatlog_url:
        parser.error("--chatlog-url 是必填的（可通过命令行参数或配置文件提供）")
    
    base_path = args.base_path or config.get("obsidian", {}).get("base_path")
    template_path = args.template or config.get("obsidian", {}).get("template")
    driver_url = args.driver_url or config.get("driver", {}).get("url")
    arbitrator_site = args.arbitrator_site or config.get("llm", {}).get("arbitrator_site", "gemini")
    # 支持 --model-version 和 --model_version 两种格式
    model_version = args.model_version or getattr(args, "model_version", None) or config.get("llm", {}).get("model_version", "5.2pro")
    task_timeout_s = args.task_timeout_s or config.get("llm", {}).get("task_timeout_s", 600)
    log_file = args.log_file or config.get("logging", {}).get("log_file")
    
    # 设置日志文件（类似 chatlog_cli）
    if log_file:
        log_file = Path(log_file).expanduser().resolve()
    else:
        logs_dir = Path("logs")
        logs_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = logs_dir / f"chatlog_automation_{timestamp}.log"
    
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_fp = open(log_file, "a", encoding="utf-8")
    
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
    
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    
    tee_stdout = Tee(sys.stdout, log_fp)
    tee_stderr = Tee(sys.stderr, log_fp)
    
    sys.stdout = tee_stdout
    sys.stderr = tee_stderr
    
    print(f"[automation] 日志文件: {log_file}")
    print(f"[automation] 日志文件路径: {log_file.absolute()}")
    
    try:
        # 解析日期
        start = datetime.strptime(args.start, "%Y-%m-%d")
        end = datetime.strptime(args.end, "%Y-%m-%d")
        
        # 设置基础路径（优先级：命令行参数 > 配置文件 > 默认值）
        if base_path:
            base_path = Path(base_path).expanduser().resolve()
        else:
            base_path = Path("~/work/personal/obsidian/personal/10_Sources/WeChat").expanduser().resolve()
        
        # 设置 template 路径
        template_path_obj = None
        if template_path:
            template_path_obj = Path(template_path).expanduser().resolve()
        
        # 获取 driver_url（优先级：命令行/配置文件 > 环境变量 > brief.yaml）
        if not driver_url:
            import os
            driver_url = os.environ.get("RPA_DRIVER_URL", "").strip() or None
        
        # 如果还没有，尝试从 brief.yaml 读取
        if not driver_url:
            try:
                brief_path = Path("brief.yaml")
                if brief_path.exists():
                    brief_data = yaml.safe_load(brief_path.read_text(encoding="utf-8"))
                    driver_url = brief_data.get("output", {}).get("driver_url", "").strip() or None
                    if driver_url:
                        print(f"[{beijing_now_iso()}] [automation] 从 brief.yaml 读取 driver_url: {driver_url}")
            except Exception:
                pass
        
        # 输出配置信息
        if config_path and Path(config_path).exists():
            print(f"[{beijing_now_iso()}] [automation] 使用配置文件: {config_path}")
        print(f"[{beijing_now_iso()}] [automation] chatlog_url: {chatlog_url}")
        print(f"[{beijing_now_iso()}] [automation] arbitrator_site: {arbitrator_site}")
        print(f"[{beijing_now_iso()}] [automation] model_version: {model_version}")
        print(f"[{beijing_now_iso()}] [automation] task_timeout_s: {task_timeout_s}秒")
        
        # 读取 new_chat 参数（优先级：命令行参数 > 配置文件 > 默认值 False）
        # 支持 --new-chat 和 --new_chat 两种格式
        new_chat = args.new_chat or getattr(args, "new_chat", False) or config.get("llm", {}).get("new_chat", False)
        if new_chat:
            print(f"[{beijing_now_iso()}] [automation] new_chat: True (每次提交都打开新窗口)")
        
        asyncio.run(run_automation(
            chatlog_url=chatlog_url,
            talker=args.talker,
            start=start,
            end=end,
            base_path=base_path,
            template_path=template_path_obj,
            driver_url=driver_url,
            arbitrator_site=arbitrator_site,
            model_version=model_version,
            task_timeout_s=task_timeout_s,
            new_chat=new_chat,
        ))
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        log_fp.close()
        print(f"\n[automation] 日志已保存到: {log_file.absolute()}", file=original_stdout)


if __name__ == "__main__":
    main()

