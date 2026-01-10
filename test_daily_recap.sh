#!/bin/bash
# 每日复盘功能测试脚本

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}每日复盘功能测试${NC}"
echo -e "${BLUE}========================================${NC}\n"

# 检查 Python 环境
echo -e "${BLUE}1. 检查 Python 环境...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python3 未安装${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Python3 已安装${NC}\n"

# 检查依赖
echo -e "${BLUE}2. 检查依赖...${NC}"
python3 -c "import httpx, yaml" 2>/dev/null
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ 依赖已安装${NC}\n"
else
    echo -e "${RED}❌ 缺少依赖，正在安装...${NC}"
    pip install -r requirements.txt
fi

# 检查 Driver Server
echo -e "${BLUE}3. 检查 Driver Server...${NC}"
if curl -s http://127.0.0.1:27125/health &> /dev/null; then
    echo -e "${GREEN}✓ Driver Server 正在运行${NC}\n"
else
    echo -e "${RED}⚠️  Driver Server 未运行，某些测试可能失败${NC}"
    echo -e "${BLUE}   提示: python start_driver.py --brief ./brief.yaml${NC}\n"
fi

# 测试 1: 列出群聊
echo -e "${BLUE}4. 测试: 列出可用群聊${NC}"
python3 -m rpa_llm.daily_recap --list-talkers || {
    echo -e "${RED}❌ 列出群聊失败${NC}"
    echo -e "${RED}   请确保 Chatlog 服务正在运行${NC}"
}
echo ""

# 测试 2: 创建批次（使用模拟数据）
echo -e "${BLUE}5. 测试: 创建批次（不实际处理）${NC}"
BATCH_ID=$(python3 -m rpa_llm.daily_recap \
    --create-batch "测试群聊" \
    --date 2026-01-07 \
    --llm chatgpt \
    --model 5.2instant | grep "批次已创建" | awk -F': ' '{print $2}')

if [ -n "$BATCH_ID" ]; then
    echo -e "${GREEN}✓ 批次创建成功: $BATCH_ID${NC}\n"
    
    # 测试 3: 列出批次
    echo -e "${BLUE}6. 测试: 列出所有批次${NC}"
    python3 -m rpa_llm.daily_recap --list-batches
    echo -e "${GREEN}✓ 列出批次成功${NC}\n"
    
    # 检查批次文件
    echo -e "${BLUE}7. 测试: 验证批次文件${NC}"
    if [ -f "data/daily_recaps/batch_${BATCH_ID}.json" ]; then
        echo -e "${GREEN}✓ 批次文件已创建${NC}"
        echo -e "${BLUE}   文件路径: data/daily_recaps/batch_${BATCH_ID}.json${NC}\n"
    else
        echo -e "${RED}❌ 批次文件未找到${NC}\n"
    fi
else
    echo -e "${RED}❌ 批次创建失败${NC}\n"
fi

# 测试 Web API（如果 web_admin 正在运行）
echo -e "${BLUE}8. 测试: Web API${NC}"
if curl -s http://127.0.0.1:5050/ &> /dev/null; then
    echo -e "${GREEN}✓ Web 管理界面正在运行${NC}"
    
    echo -e "${BLUE}   测试 API: /api/recap/talkers${NC}"
    RESPONSE=$(curl -s http://127.0.0.1:5050/api/recap/talkers)
    if echo "$RESPONSE" | grep -q '"ok":true'; then
        echo -e "${GREEN}   ✓ API 响应正常${NC}"
    else
        echo -e "${RED}   ❌ API 响应异常${NC}"
    fi
    
    echo -e "${BLUE}   测试 API: /api/recap/batches${NC}"
    RESPONSE=$(curl -s http://127.0.0.1:5050/api/recap/batches)
    if echo "$RESPONSE" | grep -q '"ok":true'; then
        echo -e "${GREEN}   ✓ API 响应正常${NC}"
    else
        echo -e "${RED}   ❌ API 响应异常${NC}"
    fi
else
    echo -e "${RED}⚠️  Web 管理界面未运行${NC}"
    echo -e "${BLUE}   提示: python web_admin.py${NC}"
fi
echo ""

# 总结
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}✅ 测试完成${NC}"
echo -e "${BLUE}========================================${NC}\n"

echo -e "${BLUE}下一步:${NC}"
echo -e "1. 访问 Web 界面: ${GREEN}http://127.0.0.1:5050${NC}"
echo -e "2. 点击 ${GREEN}📊 每日复盘${NC} 标签页"
echo -e "3. 选择群聊并创建批次"
echo -e "4. 查看使用文档: ${GREEN}DAILY_RECAP_USAGE.md${NC}\n"
