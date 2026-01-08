#!/bin/bash
# -*- coding: utf-8 -*-
#
# 自动确保 Driver Server 运行的包装脚本
#
# 用法：
#   ./run_with_driver.sh python -m rpa_llm.chatlog_automation --talker "群名" --start 2026-01-01 --end 2026-01-07
#   ./run_with_driver.sh python -m rpa_llm.cli --brief ./brief.yaml

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 配置
DRIVER_URL="${RPA_DRIVER_URL:-http://127.0.0.1:27125}"
BRIEF_FILE="${BRIEF_FILE:-./brief.yaml}"
MAX_WAIT=60  # 最大等待时间（秒）

echo "========================================"
echo "🔍 Driver Server 自动检查"
echo "========================================"

# 检查 driver server 是否运行
check_driver() {
    curl -s --max-time 2 "${DRIVER_URL}/health" > /dev/null 2>&1
    return $?
}

# 等待 driver server 就绪
wait_for_driver() {
    local count=0
    local interval=2
    
    echo -e "${BLUE}⏳ 等待 Driver Server 就绪...${NC}"
    
    while [ $count -lt $MAX_WAIT ]; do
        if check_driver; then
            echo -e "${GREEN}✅ Driver Server 已就绪！${NC}"
            return 0
        fi
        sleep $interval
        count=$((count + interval))
        echo -n "."
    done
    
    echo -e "\n${RED}❌ 超时: Driver Server 未能在 ${MAX_WAIT} 秒内就绪${NC}"
    return 1
}

# 启动 driver server
start_driver() {
    echo -e "${YELLOW}🚀 启动 Driver Server...${NC}"
    
    # 激活虚拟环境（如果存在）
    if [ -f .venv/bin/activate ]; then
        source .venv/bin/activate
    fi
    
    # 在后台启动 driver server
    PYTHONUNBUFFERED=1 python -u start_driver.py --brief "${BRIEF_FILE}" > "logs/driver_$(date +%Y%m%d_%H%M%S).log" 2>&1 &
    DRIVER_PID=$!
    
    echo -e "${GREEN}✅ Driver Server 已启动 (PID: ${DRIVER_PID})${NC}"
    echo -e "${BLUE}📝 日志文件: logs/driver_$(date +%Y%m%d_%H%M%S).log${NC}"
    
    # 等待服务就绪
    wait_for_driver
    return $?
}

# 主流程
main() {
    # 检查是否已运行
    if check_driver; then
        echo -e "${GREEN}✅ Driver Server 正在运行${NC}"
        echo -e "${BLUE}📍 URL: ${DRIVER_URL}${NC}"
    else
        echo -e "${YELLOW}❌ Driver Server 未运行${NC}"
        
        # 启动 driver server
        if ! start_driver; then
            echo -e "${RED}❌ 启动 Driver Server 失败${NC}"
            exit 1
        fi
    fi
    
    echo ""
    echo "========================================"
    echo "🚀 运行命令"
    echo "========================================"
    echo -e "${BLUE}命令: $@${NC}"
    echo ""
    
    # 执行用户命令
    "$@"
    exit_code=$?
    
    echo ""
    echo "========================================"
    echo -e "${BLUE}✅ 命令执行完成 (退出码: ${exit_code})${NC}"
    echo "========================================"
    
    exit $exit_code
}

# 检查是否提供了命令
if [ $# -eq 0 ]; then
    echo -e "${RED}错误: 未提供命令${NC}"
    echo ""
    echo "用法:"
    echo "  $0 <command> [args...]"
    echo ""
    echo "示例:"
    echo "  $0 python -m rpa_llm.chatlog_automation --talker '群名' --start 2026-01-01 --end 2026-01-07"
    echo "  $0 python -m rpa_llm.cli --brief ./brief.yaml"
    exit 1
fi

main "$@"

