# Chatlog 集成实现指南

## 快速开始

### 1. 基本使用（独立 CLI）

```bash
# 分析指定的聊天记录
python -m rpa_llm.chatlog_cli \
  --chatlog-url http://localhost:8080 \
  --conversation-ids conv1 conv2 conv3 \
  --sites chatgpt gemini \
  --vault-path ~/work/personal/obsidian/personal

# 分析最近的 10 条记录
python -m rpa_llm.chatlog_cli \
  --chatlog-url http://localhost:8080 \
  --limit 10 \
  --sites chatgpt gemini
```

### 2. 通过 brief.yaml 集成（扩展方案）

在 `brief.yaml` 中添加 chatlog 配置：

```yaml
topic: "从 chatlog 获取的对话分析"
context: "自动从 chatlog 服务获取"

# Chatlog 配置
chatlog:
  enabled: true
  url: "http://localhost:8080"
  api_key: null  # 可选
  conversation_ids: []  # 可选，指定对话 ID
  limit: 10  # 如果未指定 ID，获取最近 N 条
  prompt_template: |
    你是资深研究员/分析师。请分析以下聊天记录...
    {conversation_content}

sites:
  - chatgpt
  - gemini

output:
  vault_path: "/Users/liqiuhua/work/personal/obsidian/personal"
  driver_url: "http://127.0.0.1:27125"
```

然后运行：
```bash
python -m rpa_llm.cli --brief brief.yaml
```

## 实现步骤

### 阶段 1：基础实现（已完成）

✅ 创建了 `ChatlogClient` 类
✅ 创建了 `chatlog_cli.py` CLI 命令
✅ 支持从 chatlog 获取聊天记录
✅ 支持格式化聊天记录为 prompt
✅ 支持发送到 LLM 分析
✅ 支持保存到 Obsidian

### 阶段 2：扩展 brief.yaml 支持（待实现）

需要修改以下文件：

1. **`rpa_llm/models.py`** - 添加 ChatlogConfig 数据类
2. **`rpa_llm/orchestrator.py`** - 修改 `load_brief` 和 `build_tasks` 支持 chatlog
3. **`brief.yaml`** - 添加 chatlog 配置节

### 阶段 3：MCP 服务器（可选）

如果需要通过 MCP 协议集成，可以创建 `rpa_llm/mcp_server.py`。

## Chatlog API 接口假设

当前实现假设 chatlog 服务提供以下 API：

### GET /api/conversations
获取聊天记录列表

**请求参数**:
- `limit` (int): 返回数量限制
- `since` (string, ISO 8601): 起始时间
- `before` (string, ISO 8601): 结束时间
- `tags` (string, comma-separated): 标签过滤

**响应格式**:
```json
[
  {
    "id": "conv1",
    "title": "对话标题",
    "created_at": "2026-01-02T10:00:00Z",
    "tags": ["tag1", "tag2"]
  }
]
```

或

```json
{
  "conversations": [...]
}
```

### GET /api/conversations/{id}
获取单个聊天记录详情

**响应格式**:
```json
{
  "id": "conv1",
  "title": "对话标题",
  "created_at": "2026-01-02T10:00:00Z",
  "messages": [
    {
      "role": "user",
      "content": "用户消息",
      "timestamp": "2026-01-02T10:00:00Z"
    },
    {
      "role": "assistant",
      "content": "助手回复",
      "timestamp": "2026-01-02T10:00:05Z"
    }
  ],
  "metadata": {}
}
```

## 如果 API 不同

如果 chatlog 的实际 API 与假设不同，需要修改 `ChatlogClient` 类：

1. 修改 `get_conversations` 方法的请求参数和响应解析
2. 修改 `get_conversation` 方法的响应解析
3. 修改 `format_conversation_for_prompt` 方法以适应实际的数据结构

## 测试

### 1. 测试 Chatlog 连接

```python
import asyncio
from rpa_llm.chatlog_client import ChatlogClient

async def test():
    client = ChatlogClient("http://localhost:8080")
    try:
        conversations = await client.get_conversations(limit=5)
        print(f"获取到 {len(conversations)} 条记录")
        for conv in conversations:
            print(f"- {conv.get('title', '未命名')}")
    finally:
        await client.close()

asyncio.run(test())
```

### 2. 测试完整流程

```bash
# 确保 driver_server 正在运行
python start_driver.py --brief ./brief.yaml

# 在另一个终端运行
python -m rpa_llm.chatlog_cli \
  --chatlog-url http://localhost:8080 \
  --limit 1 \
  --sites chatgpt
```

## 下一步

1. **确认 chatlog API 规范**：需要了解实际的 API 接口
2. **测试集成**：测试完整流程
3. **扩展 brief.yaml 支持**：实现阶段 2
4. **优化和文档**：根据实际使用情况优化

