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
