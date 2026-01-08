#!/bin/bash
# -*- coding: utf-8 -*-
#
# 测试优化后的 Chatlog 分析（解决截断问题）
#
# 用法：
#   ./test_chatlog_optimized.sh

set -e

# 颜色输出
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo "========================================"
echo "🧪 测试优化后的 Chatlog 分析"
echo "========================================"
echo ""

# 激活虚拟环境
if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
fi

# 测试参数（2天数据，避免超过 10K 字符限制）
TALKER="川群-2025"
START_DATE="2026-01-06"
END_DATE="2026-01-07"
MODEL="5.2thinking"

echo -e "${BLUE}📋 测试配置:${NC}"
echo "  群聊: $TALKER"
echo "  日期: $START_DATE ~ $END_DATE"
echo "  模型: $MODEL"
echo ""

echo -e "${YELLOW}⏳ 开始分析...${NC}"
echo ""

# 运行分析
python -m rpa_llm.chatlog_automation \
  --talker "$TALKER" \
  --start "$START_DATE" \
  --end "$END_DATE" \
  --model-version "$MODEL" \
  --config ./chatlog_automation.yaml

EXIT_CODE=$?

echo ""
echo "========================================"

if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}✅ 分析完成！${NC}"
else
    echo -e "${RED}❌ 分析失败（退出码: $EXIT_CODE）${NC}"
fi

echo "========================================"
echo ""

# 查找最新的日志文件
LATEST_LOG=$(ls -t logs/chatlog_automation_*.log 2>/dev/null | head -1)

if [ -n "$LATEST_LOG" ]; then
    echo -e "${BLUE}📝 日志文件: $LATEST_LOG${NC}"
    echo ""
    
    # 检查字符数优化
    echo -e "${BLUE}🔍 检查字符数优化:${NC}"
    grep "Raw 内容优化" "$LATEST_LOG" || echo "  未找到优化日志"
    echo ""
    
    # 检查 Prompt 长度
    echo -e "${BLUE}📊 Prompt 长度:${NC}"
    grep "Prompt 生成完成" "$LATEST_LOG" || echo "  未找到 Prompt 生成日志"
    echo ""
    
    # 检查是否有截断警告
    echo -e "${BLUE}⚠️  截断警告检查:${NC}"
    if grep -q "警告.*截断" "$LATEST_LOG"; then
        echo -e "${RED}❌ 发现截断警告:${NC}"
        grep "警告.*截断" "$LATEST_LOG"
        echo ""
        echo -e "${YELLOW}💡 建议:${NC}"
        echo "  1. 进一步精简 prompt 模板（templates/chatlog_for_wechat.md）"
        echo "  2. 或缩短日期范围（只分析 1 天）"
        echo "  3. 查看详细指南: LONG_TEXT_HANDLING.md"
    else
        echo -e "${GREEN}✅ 未发现截断警告，输入正常！${NC}"
    fi
    echo ""
    
    # 查找 Driver Server 日志中的输入验证
    LATEST_DRIVER_LOG=$(ls -t logs/driver_*.log 2>/dev/null | head -1)
    if [ -n "$LATEST_DRIVER_LOG" ]; then
        echo -e "${BLUE}🔍 Driver Server 输入验证:${NC}"
        grep "输入验证" "$LATEST_DRIVER_LOG" | tail -5 || echo "  未找到验证日志"
        echo ""
    fi
    
    # 显示输出文件
    echo -e "${BLUE}📁 输出文件:${NC}"
    grep "Summary 文件已保存" "$LATEST_LOG" | sed 's/.*Summary 文件已保存: /  /' || echo "  未找到输出文件"
    echo ""
fi

echo "========================================"
echo -e "${BLUE}🎯 下一步:${NC}"
echo "  1. 查看生成的 Summary 文件（在 Obsidian 中）"
echo "  2. 检查分析是否完整"
echo "  3. 如果满意，可以用于日常工作流"
echo "========================================"

