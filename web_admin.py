#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Browser LLM Orchestrator Web 管理界面

提供可视化的 Web 界面来管理和操作 RPA LLM 系统，包括：
- Driver Server 状态监控和管理
- 站点预热 (Warmup)
- Chatlog 自动化执行
- 日志查看
- 配置管理
"""

from flask import Flask, render_template, jsonify, request, send_file
from flask_cors import CORS
import subprocess
import psutil
import os
import sys
import json
import yaml
from pathlib import Path
from datetime import datetime, timedelta
import asyncio
from typing import Optional, Dict, Any
import signal

# 添加项目根目录到 sys.path
sys.path.insert(0, str(Path(__file__).parent))

from rpa_llm.driver_client import health
from rpa_llm.utils import beijing_now_iso

app = Flask(__name__)
CORS(app)

# 全局状态
driver_process: Optional[subprocess.Popen] = None
chatlog_process: Optional[subprocess.Popen] = None

# 配置
BASE_DIR = Path(__file__).parent
BRIEF_PATH = BASE_DIR / "brief.yaml"
CHATLOG_CONFIG_PATH = BASE_DIR / "chatlog_automation.yaml"
LOGS_DIR = BASE_DIR / "logs"
DRIVER_URL = "http://127.0.0.1:27125"


def load_brief() -> Dict[str, Any]:
    """加载 brief.yaml 配置"""
    if BRIEF_PATH.exists():
        with open(BRIEF_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}


def load_chatlog_config() -> Dict[str, Any]:
    """加载 chatlog_automation.yaml 配置"""
    if CHATLOG_CONFIG_PATH.exists():
        with open(CHATLOG_CONFIG_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}


def get_driver_status() -> Dict[str, Any]:
    """获取 Driver Server 状态"""
    try:
        health_result = health(DRIVER_URL)
        return {
            "running": True,
            "ok": health_result.get("ok", False),
            "sites": health_result.get("sites", []),
            "url": DRIVER_URL,
            "error": None
        }
    except Exception as e:
        return {
            "running": False,
            "ok": False,
            "sites": [],
            "url": DRIVER_URL,
            "error": str(e)
        }


def get_latest_logs(log_type: str = "driver", limit: int = 50) -> list:
    """获取最新的日志文件列表"""
    pattern = f"{log_type}_*.log"
    log_files = sorted(
        LOGS_DIR.glob(pattern),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )
    return [
        {
            "name": f.name,
            "path": str(f),
            "size": f.stat().st_size,
            "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        }
        for f in log_files[:limit]
    ]


@app.route('/')
def index():
    """主页"""
    return render_template('index.html')


@app.route('/api/status')
def api_status():
    """获取系统状态"""
    driver_status = get_driver_status()
    brief = load_brief()
    
    return jsonify({
        "driver": driver_status,
        "brief": {
            "sites": brief.get("sites", []),
            "output_dir": brief.get("output", {}).get("base_path", "runs")
        },
        "timestamp": beijing_now_iso()
    })


@app.route('/api/driver/start', methods=['POST'])
def api_driver_start():
    """启动 Driver Server"""
    global driver_process
    
    # 检查是否已经运行
    driver_status = get_driver_status()
    if driver_status["running"] and driver_status["ok"]:
        return jsonify({
            "ok": False,
            "error": "Driver Server 已经在运行"
        })
    
    try:
        # 启动 Driver Server
        log_file = LOGS_DIR / f"driver_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        LOGS_DIR.mkdir(exist_ok=True)
        
        with open(log_file, 'w') as f:
            driver_process = subprocess.Popen(
                [sys.executable, "-u", "start_driver.py", "--brief", str(BRIEF_PATH)],
                stdout=f,
                stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                cwd=BASE_DIR,
                start_new_session=True
            )
        
        # 等待启动
        import time
        time.sleep(2)
        
        # 验证状态
        driver_status = get_driver_status()
        
        return jsonify({
            "ok": True,
            "pid": driver_process.pid,
            "log_file": str(log_file),
            "status": driver_status
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route('/api/driver/stop', methods=['POST'])
def api_driver_stop():
    """停止 Driver Server"""
    global driver_process
    
    try:
        # 查找并停止所有 start_driver.py 进程
        stopped_pids = []
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline', [])
                if cmdline and 'start_driver.py' in ' '.join(cmdline):
                    proc.terminate()
                    stopped_pids.append(proc.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        
        if driver_process and driver_process.poll() is None:
            driver_process.terminate()
            stopped_pids.append(driver_process.pid)
            driver_process = None
        
        return jsonify({
            "ok": True,
            "stopped_pids": stopped_pids
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route('/api/warmup/<site>', methods=['POST'])
def api_warmup(site: str):
    """预热指定站点"""
    try:
        result = subprocess.run(
            [sys.executable, "warmup.py", site],
            capture_output=True,
            text=True,
            cwd=BASE_DIR,
            timeout=180
        )
        
        return jsonify({
            "ok": result.returncode == 0,
            "site": site,
            "stdout": result.stdout,
            "stderr": result.stderr
        })
    except subprocess.TimeoutExpired:
        return jsonify({
            "ok": False,
            "error": "预热超时（3分钟）"
        }), 500
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route('/api/chatlog/run', methods=['POST'])
def api_chatlog_run():
    """运行 Chatlog 自动化"""
    global chatlog_process
    
    data = request.json
    talker = data.get('talker')
    start_date = data.get('start')
    end_date = data.get('end')
    template = data.get('template')
    model_version = data.get('model_version', '5.2pro')
    new_chat = data.get('new_chat', True)
    
    if not talker or not start_date or not end_date:
        return jsonify({
            "ok": False,
            "error": "缺少必要参数: talker, start, end"
        }), 400
    
    try:
        # 构建命令
        cmd = [
            sys.executable, "-m", "rpa_llm.chatlog_automation",
            "--talker", talker,
            "--start", start_date,
            "--end", end_date,
            "--model-version", model_version,
            "--config", str(CHATLOG_CONFIG_PATH)
        ]
        
        if template:
            cmd.extend(["--template", template])
        
        if new_chat:
            cmd.append("--new-chat")
        
        # 启动进程
        log_file = LOGS_DIR / f"chatlog_web_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        with open(log_file, 'w') as f:
            chatlog_process = subprocess.Popen(
                cmd,
                stdout=f,
                stderr=subprocess.STDOUT,
                cwd=BASE_DIR,
                start_new_session=True
            )
        
        return jsonify({
            "ok": True,
            "pid": chatlog_process.pid,
            "log_file": str(log_file),
            "message": "Chatlog 自动化已启动，请查看日志"
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route('/api/logs/<log_type>')
def api_logs_list(log_type: str):
    """获取日志文件列表"""
    try:
        logs = get_latest_logs(log_type, limit=50)
        return jsonify({
            "ok": True,
            "logs": logs
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route('/api/logs/view/<path:filename>')
def api_logs_view(filename: str):
    """查看日志文件内容"""
    try:
        log_file = LOGS_DIR / filename
        if not log_file.exists():
            return jsonify({
                "ok": False,
                "error": "日志文件不存在"
            }), 404
        
        # 读取最后 1000 行
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            content = ''.join(lines[-1000:])
        
        return jsonify({
            "ok": True,
            "filename": filename,
            "content": content,
            "total_lines": len(lines)
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route('/api/config/brief')
def api_config_brief():
    """获取 brief.yaml 配置"""
    try:
        brief = load_brief()
        return jsonify({
            "ok": True,
            "config": brief
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route('/api/config/chatlog')
def api_config_chatlog():
    """获取 chatlog_automation.yaml 配置"""
    try:
        config = load_chatlog_config()
        return jsonify({
            "ok": True,
            "config": config
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


if __name__ == '__main__':
    # 确保 logs 目录存在
    LOGS_DIR.mkdir(exist_ok=True)
    
    # 启动 Flask 应用
    port = int(os.environ.get('WEB_ADMIN_PORT', 5050))
    print(f"🚀 启动 Web 管理界面: http://127.0.0.1:{port}")
    print(f"📁 项目目录: {BASE_DIR}")
    print(f"📝 日志目录: {LOGS_DIR}")
    print()
    
    app.run(
        host='0.0.0.0',
        port=port,
        debug=True,
        use_reloader=False  # 避免重复启动
    )

