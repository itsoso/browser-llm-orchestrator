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
from rpa_llm.daily_recap import DailyRecapManager
from rpa_llm.template_manager import get_template_manager, PromptTemplate, TalkerTemplateMapping

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
    # 特殊处理 web_admin 日志
    if log_type == "web_admin":
        web_admin_log = Path("/tmp/web_admin.log")
        if web_admin_log.exists():
            return [
                {
                    "name": "web_admin.log",
                    "path": str(web_admin_log),
                    "size": web_admin_log.stat().st_size,
                    "modified": datetime.fromtimestamp(web_admin_log.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                }
            ]
        return []
    
    # 常规日志文件
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
        # 特殊处理 web_admin.log
        if filename == "web_admin.log":
            log_file = Path("/tmp/web_admin.log")
        else:
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


# ========== 每日复盘相关路由 ==========

@app.route('/api/recap/talkers')
def api_recap_talkers():
    """获取可用的群聊/联系人列表"""
    try:
        days = int(request.args.get('days', 7))
        manager = DailyRecapManager()
        
        # 在新的事件循环中运行异步函数
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            talkers = loop.run_until_complete(manager.get_available_talkers(days=days))
        finally:
            loop.close()
        
        return jsonify({
            "ok": True,
            "talkers": talkers,
            "count": len(talkers)
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route('/api/recap/create', methods=['POST'])
def api_recap_create():
    """创建复盘批次"""
    try:
        data = request.json
        talkers = data.get('talkers', [])
        
        # 支持日期范围或单个日期
        date_start = data.get('date_start')
        date_end = data.get('date_end')
        date = data.get('date')  # 兼容旧版本
        
        # 如果提供了 date_start 和 date_end，使用日期范围
        if date_start and date_end:
            # 暂时只支持单天，如果是范围则使用开始日期
            date = date_start
            # TODO: 未来可以支持真正的日期范围，为每一天创建任务
        elif date:
            # 使用单个日期
            pass
        else:
            # 默认使用今天
            date = datetime.now().strftime('%Y-%m-%d')
        
        llm_site = data.get('llm_site', 'chatgpt')
        model_version = data.get('model_version', '5.2instant')
        template_id = data.get('template_id')  # 可选的模板 ID
        public = data.get('public', False)
        
        if not talkers:
            return jsonify({
                "ok": False,
                "error": "请选择至少一个群聊"
            }), 400
        
        manager = DailyRecapManager()
        batch = manager.create_batch(
            talkers=talkers,
            date=date,
            llm_site=llm_site,
            model_version=model_version,
            template_id=template_id,
            public=public
        )
        
        return jsonify({
            "ok": True,
            "batch": batch.to_dict(),
            "batch_id": batch.batch_id
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route('/api/recap/batches')
def api_recap_batches():
    """列出所有批次"""
    try:
        limit = int(request.args.get('limit', 50))
        manager = DailyRecapManager()
        batches = manager.list_batches(limit=limit)
        
        return jsonify({
            "ok": True,
            "batches": batches,
            "count": len(batches)
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route('/api/recap/batch/<batch_id>')
def api_recap_batch(batch_id):
    """获取批次详情"""
    try:
        manager = DailyRecapManager()
        batch = manager.load_batch(batch_id)
        
        if not batch:
            return jsonify({
                "ok": False,
                "error": f"批次不存在: {batch_id}"
            }), 404
        
        return jsonify({
            "ok": True,
            "batch": batch.to_dict()
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route('/api/recap/process/<batch_id>', methods=['POST'])
def api_recap_process(batch_id):
    """处理批次（启动后台任务）"""
    try:
        data = request.json or {}
        timeout = data.get('timeout', 2400)  # 默认 2400 秒（40 分钟），适配 Pro 模式
        template = data.get('template', None)
        
        manager = DailyRecapManager()
        
        # 在后台线程中运行处理任务
        def run_process():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                template_path = Path(template) if template else None
                loop.run_until_complete(
                    manager.process_batch(
                        batch_id=batch_id,
                        template_path=template_path,
                        timeout=timeout
                    )
                )
            finally:
                loop.close()
        
        import threading
        thread = threading.Thread(target=run_process, daemon=True)
        thread.start()
        
        return jsonify({
            "ok": True,
            "message": f"批次 {batch_id} 已开始处理",
            "batch_id": batch_id
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route('/api/recap/public/<batch_id>')
def api_recap_public(batch_id):
    """
    获取公开的批次结果（用于小程序等外部访问）
    
    注意：只有标记为 public=True 的批次才能通过此接口访问
    """
    try:
        manager = DailyRecapManager()
        batch = manager.load_batch(batch_id)
        
        if not batch:
            return jsonify({
                "ok": False,
                "error": "批次不存在"
            }), 404
        
        if not batch.public:
            return jsonify({
                "ok": False,
                "error": "此批次未公开"
            }), 403
        
        # 只返回必要的信息
        result = {
            "ok": True,
            "batch_id": batch.batch_id,
            "date": batch.date,
            "status": batch.status,
            "created_at": batch.created_at,
            "completed_at": batch.completed_at,
            "summaries": []
        }
        
        # 读取每个任务的总结内容
        for task in batch.tasks:
            if task.status == "completed" and task.result_path:
                try:
                    with open(task.result_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    result["summaries"].append({
                        "talker": task.display_name,
                        "date": task.date,
                        "message_count": task.message_count,
                        "content": content
                    })
                except Exception as e:
                    print(f"读取总结文件失败 {task.result_path}: {e}")
        
        return jsonify(result)
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


# ============================================================
# Prompt 模板管理 API
# ============================================================

@app.route('/api/templates', methods=['GET'])
def api_templates_list():
    """获取所有模板"""
    try:
        tm = get_template_manager()
        llm_type = request.args.get('llm_type')
        templates = tm.list_templates(llm_type=llm_type)
        
        return jsonify({
            "ok": True,
            "templates": [t.to_dict() for t in templates]
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route('/api/templates/<template_id>', methods=['GET'])
def api_template_get(template_id: str):
    """获取单个模板"""
    try:
        tm = get_template_manager()
        template = tm.get_template(template_id)
        
        if not template:
            return jsonify({
                "ok": False,
                "error": "模板不存在"
            }), 404
        
        return jsonify({
            "ok": True,
            "template": template.to_dict()
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route('/api/templates', methods=['POST'])
def api_template_create():
    """创建模板"""
    try:
        data = request.json
        tm = get_template_manager()
        
        # 创建模板对象
        template = PromptTemplate(
            id=data['id'],
            name=data['name'],
            description=data.get('description', ''),
            content=data['content'],
            llm_type=data.get('llm_type', 'all'),
            base_template_id=data.get('base_template_id'),
            variables=data.get('variables', []),
        )
        
        created = tm.create_template(template)
        
        return jsonify({
            "ok": True,
            "template": created.to_dict()
        })
    except ValueError as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 400
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route('/api/templates/<template_id>', methods=['PUT'])
def api_template_update(template_id: str):
    """更新模板"""
    try:
        data = request.json
        tm = get_template_manager()
        
        updated = tm.update_template(template_id, data)
        
        return jsonify({
            "ok": True,
            "template": updated.to_dict()
        })
    except ValueError as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 400
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route('/api/templates/<template_id>', methods=['DELETE'])
def api_template_delete(template_id: str):
    """删除模板"""
    try:
        tm = get_template_manager()
        tm.delete_template(template_id)
        
        return jsonify({
            "ok": True
        })
    except ValueError as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 400
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route('/api/templates/<template_id>/content', methods=['GET'])
def api_template_get_content(template_id: str):
    """获取模板的完整内容（包含基础模板合并）"""
    try:
        tm = get_template_manager()
        content = tm.get_template_content(template_id)
        
        return jsonify({
            "ok": True,
            "content": content
        })
    except ValueError as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 404
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


# ============================================================
# 群聊-模板映射 API
# ============================================================

@app.route('/api/template-mappings', methods=['GET'])
def api_mappings_list():
    """获取所有映射"""
    try:
        tm = get_template_manager()
        mappings = tm.list_mappings()
        
        return jsonify({
            "ok": True,
            "mappings": [m.to_dict() for m in mappings]
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route('/api/template-mappings/<talker>', methods=['GET'])
def api_mapping_get(talker: str):
    """获取群聊的模板映射"""
    try:
        tm = get_template_manager()
        mapping = tm.get_mapping(talker)
        
        if not mapping:
            return jsonify({
                "ok": False,
                "error": "映射不存在"
            }), 404
        
        return jsonify({
            "ok": True,
            "mapping": mapping.to_dict()
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route('/api/template-mappings', methods=['POST'])
def api_mapping_create():
    """创建或更新映射"""
    try:
        data = request.json
        tm = get_template_manager()
        
        mapping = TalkerTemplateMapping(
            talker=data['talker'],
            template_id=data['template_id'],
            llm_type=data.get('llm_type'),
            priority=data.get('priority', 0)
        )
        
        created = tm.create_mapping(mapping)
        
        return jsonify({
            "ok": True,
            "mapping": created.to_dict()
        })
    except ValueError as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 400
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route('/api/template-mappings/<talker>', methods=['DELETE'])
def api_mapping_delete(talker: str):
    """删除映射"""
    try:
        tm = get_template_manager()
        tm.delete_mapping(talker)
        
        return jsonify({
            "ok": True
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

