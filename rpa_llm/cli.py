# -*- coding: utf-8 -*-
"""
Author: xiaofan with Codex Cusor
Created: 2025-12-29 20:27:11 +0800
Modified: 2025-12-29 20:27:11 +0800
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from .orchestrator import run_all
from .utils import utc_now_iso


def main():
    parser = argparse.ArgumentParser(description="Browser-based multi-LLM research orchestrator (Playwright).")
    parser.add_argument("--brief", required=True, help="Path to brief YAML file")
    parser.add_argument("--run-id", default=None, help="Run id (default: utc timestamp)")
    parser.add_argument("--headless", action="store_true", help="Run headless (NOT recommended for LLM sites)")
    parser.add_argument("--log-file", help="日志文件路径（如果未指定，则自动生成到 logs/ 目录）")
    parser.add_argument(
        "--model-version",
        help="指定 ChatGPT 模型版本（如：5.2pro, 5.2instant, thinking）。会覆盖 brief.yaml 中的配置。"
    )
    parser.add_argument(
        "--prompt-file",
        help="自定义 prompt 文件路径（支持 Obsidian 文件路径，如：/path/to/file.md）。如果指定，将使用文件内容作为 prompt，忽略 brief.yaml 中的 prompt_template。"
    )
    args = parser.parse_args()

    brief_path = Path(args.brief).expanduser().resolve()
    run_id = args.run_id or utc_now_iso().replace(":", "").replace("+", "_")
    
    # 处理自定义 prompt 文件
    prompt_file_path = None
    if args.prompt_file:
        prompt_file_path = Path(args.prompt_file).expanduser().resolve()
        if not prompt_file_path.exists():
            print(f"[cli] 错误: prompt 文件不存在: {prompt_file_path}", file=sys.stderr)
            sys.exit(1)
        print(f"[cli] 使用自定义 prompt 文件: {prompt_file_path}")
    
    # 处理模型版本
    model_version = args.model_version
    if model_version:
        print(f"[cli] 指定模型版本: {model_version} (将覆盖 brief.yaml 中的配置)")

    # 设置日志文件
    if args.log_file:
        log_file = Path(args.log_file).expanduser().resolve()
    else:
        # 自动生成日志文件路径：logs/cli_YYYYMMDD_HHMMSS.log
        logs_dir = Path("logs")
        logs_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = logs_dir / f"cli_{timestamp}.log"
    
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
    print(f"[cli] 日志文件: {log_file}")
    print(f"[cli] 日志文件路径: {log_file.absolute()}")
    
    try:
        run_index_path, results = asyncio.run(
            run_all(
                brief_path,
                run_id=run_id,
                headless=args.headless,
                model_version=model_version,
                prompt_file_path=prompt_file_path,
            )
        )

        ok = sum(1 for r in results if r.ok)
        total = len(results)
        print(f"\nDone. OK {ok}/{total}")
        print(f"Run index note: {run_index_path}")
        print(f"日志文件: {log_file.absolute()}\n")
    finally:
        # 恢复原始的 stdout 和 stderr
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        # 关闭日志文件
        log_fp.close()
        # 输出日志文件路径到控制台（恢复后）
        print(f"\n[cli] 日志已保存到: {log_file.absolute()}", file=original_stdout)


if __name__ == "__main__":
    main()
