# -*- coding: utf-8 -*-
"""
日志工具模块：支持同时输出到控制台和文件
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from .utils import beijing_now_iso


class Logger:
    """日志记录器：同时输出到控制台和文件"""
    
    def __init__(self, log_file: Optional[Path] = None):
        """
        初始化日志记录器
        
        Args:
            log_file: 日志文件路径，如果为 None 则只输出到控制台
        """
        self.log_file: Optional[Path] = log_file
        self._file_handle = None
        
        if self.log_file:
            # 确保日志文件目录存在
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            # 打开文件（追加模式）
            self._file_handle = open(self.log_file, "a", encoding="utf-8")
    
    def log(self, msg: str, flush: bool = True) -> None:
        """
        记录日志（同时输出到控制台和文件）
        
        Args:
            msg: 日志消息
            flush: 是否立即刷新缓冲区
        """
        # 输出到控制台
        print(msg, flush=flush)
        
        # 输出到文件
        if self._file_handle:
            try:
                self._file_handle.write(msg + "\n")
                if flush:
                    self._file_handle.flush()
            except Exception:
                # 文件写入失败不影响程序运行
                pass
    
    def close(self) -> None:
        """关闭日志文件"""
        if self._file_handle:
            try:
                self._file_handle.close()
            except Exception:
                pass
            self._file_handle = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


# 全局日志记录器实例
_global_logger: Optional[Logger] = None


def init_logger(log_file: Optional[Path] = None) -> Logger:
    """
    初始化全局日志记录器
    
    Args:
        log_file: 日志文件路径，如果为 None 则只输出到控制台
    
    Returns:
        日志记录器实例
    """
    global _global_logger
    if _global_logger:
        _global_logger.close()
    _global_logger = Logger(log_file)
    return _global_logger


def get_logger() -> Optional[Logger]:
    """获取全局日志记录器"""
    return _global_logger


def log(msg: str, flush: bool = True) -> None:
    """
    记录日志（使用全局日志记录器）
    
    Args:
        msg: 日志消息
        flush: 是否立即刷新缓冲区
    """
    if _global_logger:
        _global_logger.log(msg, flush=flush)
    else:
        # 如果没有初始化日志记录器，只输出到控制台
        print(msg, flush=flush)


def log_with_timestamp(prefix: str, msg: str, flush: bool = True) -> None:
    """
    记录带时间戳的日志
    
    Args:
        prefix: 日志前缀（如 [chatgpt], [gemini] 等）
        msg: 日志消息
        flush: 是否立即刷新缓冲区
    """
    timestamp = beijing_now_iso()
    log(f"[{timestamp}] {prefix} {msg}", flush=flush)

