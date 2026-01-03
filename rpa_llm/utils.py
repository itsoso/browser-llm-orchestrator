# -*- coding: utf-8 -*-
"""
Author: xiaofan with Codex Cusor
Created: 2025-12-29 20:27:11 +0800
Modified: 2025-12-30 15:29:56 +0800
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def beijing_now_iso() -> str:
    """返回北京时间（UTC+8）的 ISO 格式字符串，用于日志输出"""
    beijing_tz = timezone(timedelta(hours=8))
    return datetime.now(beijing_tz).replace(microsecond=0).isoformat()


def slugify(text: str, max_len: int = 60) -> str:
    # 简单可控的 slug：中文保留，空白转-，去掉不安全字符
    s = text.strip()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^\w\u4e00-\u9fff\-]+", "", s, flags=re.UNICODE)
    return s[:max_len] if len(s) > max_len else s


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


async def ainput(prompt: str = "") -> str:
    # 允许在 async 中等待用户输入（用于登录/验证码人工接管）
    return await asyncio.to_thread(input, prompt)


def first_nonempty(items: Iterable[Optional[str]]) -> Optional[str]:
    for x in items:
        if x:
            return x
    return None


def clean_text_code_block(content: str) -> str:
    """
    清理内容中最开头和最后结尾的 ```text 代码块标记，确保内容能在 Obsidian 中正常渲染
    
    如果 LLM 按照模板要求将内容放到 ```text 代码块中，此函数会：
    1. 只删除字符串最开头的 ```text 标记（支持 ```text、``` text、```TEXT 等变体）
    2. 只删除字符串最后结尾的 ``` 标记
    3. 删除常见的残留模式（如 "text\n复制代码\n"）
    4. 去除首尾空白字符
    5. 保留内容中间的所有 markdown 格式（包括代码块）
    
    Args:
        content: 可能包含 ```text 代码块的内容
    
    Returns:
        清理后的内容（只去除最外层代码块标记和常见残留，保留所有中间内容）
    
    Examples:
        >>> clean_text_code_block('```text\\ncontent\\n```')
        'content'
        >>> clean_text_code_block('```text\\n---\\ntype: test\\n---\\ncontent\\n```')
        '---\\ntype: test\\n---\\ncontent'
        >>> clean_text_code_block('```text\\ntext\\n复制代码\\n---\\ntype: test\\n---\\n```')
        '---\\ntype: test\\n---'
        >>> clean_text_code_block('```text\\n```python\\ncode\\n```\\n```')
        '```python\\ncode\\n```'
        >>> clean_text_code_block('content')
        'content'
    """
    if not content:
        return content
    
    # 只匹配字符串最开头的 ```text 标记（不使用 MULTILINE，确保只匹配开头）
    # 支持多种变体：```text、```text\n、``` text、``` text\n
    # 使用 ^ 锚点确保只匹配字符串开头，不区分大小写
    content = re.sub(r'^```\s*text\s*\n?', '', content, flags=re.IGNORECASE)
    
    # 只匹配字符串最后结尾的 ``` 标记（不使用 MULTILINE，确保只匹配结尾）
    # 匹配结尾的代码块标记（可能前面有换行，后面可能有空白字符）
    # 使用 $ 锚点确保只匹配字符串结尾
    content = re.sub(r'\n?```\s*$', '', content)
    
    # 删除常见的残留模式（LLM 可能在代码块内添加了这些内容）
    # 只删除开头的残留，不影响中间内容
    # 匹配 "text\n复制代码\n" 或 "text\n复制代码" 等变体（这是 ChatGPT 常见的残留）
    content = re.sub(r'^text\s*\n\s*复制代码\s*\n?', '', content, flags=re.IGNORECASE)
    # 如果开头是 "text\n" 且后面是 frontmatter（---），也删除（可能是残留）
    if content.startswith('text\n---'):
        content = re.sub(r'^text\s*\n', '', content)
    
    # 去除开头和结尾的空白字符
    content = content.strip()
    
    return content
