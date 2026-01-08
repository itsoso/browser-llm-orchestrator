#!/bin/bash
# -*- coding: utf-8 -*-
#
# 启动 Web 管理界面
#
# 用法：
#   ./start_web.sh              # 默认端口 5050
#   ./start_web.sh 8080         # 指定端口 8080

set -e

# 颜色输出
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 默认端口
PORT=${1:-5050}

echo "========================================"
echo "🚀 Browser LLM Orchestrator Web 管理界面"
echo "========================================"

# 检查并激活虚拟环境
if [ -f .venv/bin/activate ]; then
    echo -e "${GREEN}✓${NC} 激活虚拟环境: .venv"
    source .venv/bin/activate
else
    echo -e "${YELLOW}⚠${NC}  警告: 未找到虚拟环境 .venv"
    echo "提示: 首次使用请先创建虚拟环境并安装依赖:"
    echo "  python3 -m venv .venv"
    echo "  source .venv/bin/activate"
    echo "  pip install -r requirements.txt"
    exit 1
fi

# 检查依赖
if ! python -c "import flask" 2>/dev/null; then
    echo -e "${YELLOW}⚠${NC}  警告: Flask 未安装"
    echo "正在安装依赖..."
    pip install -r requirements.txt
fi

# 启动服务
echo ""
echo -e "${GREEN}✓${NC} 依赖检查完成"
echo ""
echo "========================================"
echo "🌐 启动 Web 服务"
echo "========================================"
echo "访问地址: http://127.0.0.1:${PORT}"
echo "按 Ctrl+C 停止服务"
echo "========================================"
echo ""

WEB_ADMIN_PORT=${PORT} python web_admin.py

