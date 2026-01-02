# Chatlog 集成设计方案

## 需求概述

通过 MCP 或 HTTP 方式调用 chatlog 服务，获取聊天记录，然后发送到 GPT/Gemini 进行分析，最后保存到 Obsidian。

## 架构设计

### 方案对比

| 方案 | 优点 | 缺点 | 推荐度 |
|------|------|------|--------|
| **方案 A：扩展 brief.yaml** | 简单，复用现有流程 | 需要修改核心代码 | ⭐⭐⭐⭐ |
| **方案 B：独立 CLI 命令** | 解耦，不影响现有功能 | 需要维护新代码 | ⭐⭐⭐⭐⭐ |
| **方案 C：MCP 服务器** | 标准化，可扩展 | 需要实现 MCP 协议 | ⭐⭐⭐ |
| **方案 D：HTTP API 服务** | 灵活，可独立部署 | 增加系统复杂度 | ⭐⭐⭐ |

**推荐方案：方案 B（独立 CLI 命令）+ 方案 A（扩展 brief.yaml）**

## 详细设计

### 方案 B：独立 CLI 命令（推荐）

#### 1. 创建 Chatlog 客户端模块

**文件**: `rpa_llm/chatlog_client.py`

```python
"""
Chatlog HTTP 客户端
支持从 chatlog 服务获取聊天记录
"""
import httpx
from typing import List, Dict, Optional
from datetime import datetime

class ChatlogClient:
    def __init__(self, base_url: str, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=30.0)
    
    async def get_conversations(
        self, 
        limit: int = 10,
        since: Optional[datetime] = None,
        tags: Optional[List[str]] = None
    ) -> List[Dict]:
        """
        获取聊天记录列表
        
        Args:
            limit: 返回数量限制
            since: 起始时间
            tags: 标签过滤
        
        Returns:
            聊天记录列表
        """
        params = {"limit": limit}
        if since:
            params["since"] = since.isoformat()
        if tags:
            params["tags"] = ",".join(tags)
        
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        
        response = await self.client.get(
            f"{self.base_url}/api/conversations",
            params=params,
            headers=headers
        )
        response.raise_for_status()
        return response.json()
    
    async def get_conversation(self, conversation_id: str) -> Dict:
        """
        获取单个聊天记录的详细信息
        
        Args:
            conversation_id: 聊天记录 ID
        
        Returns:
            聊天记录详情（包含消息列表）
        """
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        
        response = await self.client.get(
            f"{self.base_url}/api/conversations/{conversation_id}",
            headers=headers
        )
        response.raise_for_status()
        return response.json()
    
    async def format_conversation_for_prompt(self, conversation: Dict) -> str:
        """
        将聊天记录格式化为 prompt 格式
        
        Args:
            conversation: 聊天记录字典
        
        Returns:
            格式化后的文本
        """
        messages = conversation.get("messages", [])
        title = conversation.get("title", "未命名对话")
        created_at = conversation.get("created_at", "")
        
        formatted = f"# 聊天记录：{title}\n"
        if created_at:
            formatted += f"时间：{created_at}\n"
        formatted += "\n## 对话内容\n\n"
        
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            timestamp = msg.get("timestamp", "")
            
            formatted += f"**{role}**"
            if timestamp:
                formatted += f" ({timestamp})"
            formatted += f":\n{content}\n\n"
        
        return formatted
    
    async def close(self):
        await self.client.aclose()
```

#### 2. 创建 Chatlog CLI 命令

**文件**: `rpa_llm/chatlog_cli.py`

```python
"""
Chatlog 集成 CLI
从 chatlog 获取聊天记录，发送到 LLM 分析，保存到 Obsidian
"""
import argparse
import asyncio
from pathlib import Path
from typing import List, Dict

from .chatlog_client import ChatlogClient
from .orchestrator import run_site_worker, run_synthesis_and_final
from .models import Brief, Task, ModelResult
from .vault import make_run_paths, ensure_dir
from .utils import utc_now_iso, beijing_now_iso

async def analyze_chatlog_conversations(
    chatlog_url: str,
    chatlog_api_key: Optional[str],
    conversation_ids: Optional[List[str]],
    limit: int = 10,
    sites: List[str] = ["chatgpt", "gemini"],
    prompt_template: str = None,
    vault_path: Path = None,
    driver_url: str = None,
):
    """
    从 chatlog 获取聊天记录并分析
    
    Args:
        chatlog_url: chatlog 服务地址
        chatlog_api_key: API 密钥（可选）
        conversation_ids: 指定的对话 ID 列表（可选）
        limit: 如果未指定 ID，获取最近 N 条
        sites: 使用的 LLM 站点
        prompt_template: 分析 prompt 模板
        vault_path: Obsidian vault 路径
        driver_url: driver_server URL
    """
    # 初始化 chatlog 客户端
    client = ChatlogClient(chatlog_url, chatlog_api_key)
    
    try:
        # 获取聊天记录
        if conversation_ids:
            conversations = []
            for conv_id in conversation_ids:
                conv = await client.get_conversation(conv_id)
                conversations.append(conv)
        else:
            conversations = await client.get_conversations(limit=limit)
        
        print(f"[{beijing_now_iso()}] [chatlog] 获取到 {len(conversations)} 条聊天记录")
        
        # 为每条聊天记录创建分析任务
        run_id = utc_now_iso().replace(":", "").replace("+", "_")
        
        # 设置默认 prompt 模板
        if not prompt_template:
            prompt_template = """你是资深研究员/分析师。请分析以下聊天记录，输出"结论清晰、证据可追溯、便于 Obsidian 阅读"的研究笔记。

聊天记录：
{conversation_content}

请按以下结构输出：
## 1. 关键结论
## 2. 输出洞察
## 3. 行动建议（如有）
"""
        
        # 设置默认 vault 路径
        if not vault_path:
            vault_path = Path("~/work/personal/obsidian/personal").expanduser()
        
        vault_paths = make_run_paths(vault_path, "10_ResearchRuns", run_id)
        for p in vault_paths.values():
            ensure_dir(p)
        
        # 处理每条聊天记录
        all_results = []
        for idx, conv in enumerate(conversations, 1):
            print(f"[{beijing_now_iso()}] [chatlog] 处理第 {idx}/{len(conversations)} 条记录: {conv.get('title', '未命名')}")
            
            # 格式化聊天记录
            formatted_conv = await client.format_conversation_for_prompt(conv)
            
            # 构建 prompt
            prompt = prompt_template.format(conversation_content=formatted_conv)
            
            # 创建任务
            tasks = []
            for site in sites:
                tasks.append(Task(
                    run_id=run_id,
                    site_id=site,
                    stream_id="chatlog_analysis",
                    stream_name="Chatlog Analysis",
                    topic=conv.get("title", "未命名对话"),
                    prompt=prompt,
                ))
            
            # 执行分析
            # ... 调用 run_site_worker ...
            
    finally:
        await client.close()
```

#### 3. 扩展 brief.yaml 支持 chatlog

**文件**: `brief.yaml` (扩展)

```yaml
# 原有配置...
topic: "从 chatlog 获取的对话分析"
context: "自动从 chatlog 服务获取"

# 新增：chatlog 配置
chatlog:
  enabled: true
  url: "http://localhost:8080"  # chatlog 服务地址
  api_key: null  # 可选，API 密钥
  conversation_ids: []  # 可选，指定对话 ID 列表
  limit: 10  # 如果未指定 ID，获取最近 N 条
  prompt_template: |
    你是资深研究员/分析师。请分析以下聊天记录...
    {conversation_content}

streams:
  - id: chatlog_analysis
    name: "Chatlog Analysis"
    prompt_template: |
      {conversation_content}
      
      请按以下结构输出：
      ## 1. 关键结论
      ## 2. 输出洞察
```

### 方案 C：MCP 服务器（可选）

#### 1. 创建 MCP 服务器

**文件**: `rpa_llm/mcp_server.py`

```python
"""
MCP 服务器，提供 chatlog 集成功能
"""
from mcp.server import Server
from mcp.types import Tool, TextContent

app = Server("browser-llm-orchestrator")

@app.list_tools()
async def list_tools():
    return [
        Tool(
            name="analyze_chatlog",
            description="从 chatlog 获取聊天记录并发送到 LLM 分析",
            inputSchema={
                "type": "object",
                "properties": {
                    "conversation_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "对话 ID 列表"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "获取最近 N 条（如果未指定 ID）"
                    }
                }
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "analyze_chatlog":
        # 调用 chatlog 客户端和分析逻辑
        ...
        return [TextContent(type="text", text="分析完成")]
```

## 实现步骤

### 阶段 1：基础实现（推荐先做）

1. **创建 Chatlog 客户端模块**
   - 实现 `ChatlogClient` 类
   - 支持获取聊天记录列表和详情
   - 支持格式化聊天记录为 prompt

2. **创建独立 CLI 命令**
   - 实现 `chatlog_cli.py`
   - 支持命令行参数配置
   - 集成到现有的 orchestrator 流程

3. **测试验证**
   - 测试 chatlog 连接
   - 测试数据获取和格式化
   - 测试 LLM 分析和 Obsidian 保存

### 阶段 2：扩展集成

1. **扩展 brief.yaml**
   - 添加 chatlog 配置节
   - 修改 `load_brief` 函数支持 chatlog
   - 修改 `build_tasks` 支持从 chatlog 获取数据

2. **优化用户体验**
   - 添加进度显示
   - 添加错误处理
   - 添加日志记录

### 阶段 3：高级功能（可选）

1. **MCP 服务器**
   - 实现 MCP 协议
   - 提供标准化接口
   - 支持其他工具集成

2. **HTTP API 服务**
   - 创建独立的 HTTP 服务
   - 提供 RESTful API
   - 支持 Webhook 集成

## 使用示例

### 方式 1：独立 CLI 命令

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

### 方式 2：通过 brief.yaml

```yaml
chatlog:
  enabled: true
  url: "http://localhost:8080"
  conversation_ids: ["conv1", "conv2"]
  
sites:
  - chatgpt
  - gemini
```

然后运行：
```bash
python -m rpa_llm.cli --brief brief.yaml
```

### 方式 3：通过 MCP（如果实现）

```python
# 在其他工具中调用
from mcp import Client

client = Client("browser-llm-orchestrator")
result = await client.call_tool("analyze_chatlog", {
    "conversation_ids": ["conv1", "conv2"]
})
```

## 技术细节

### Chatlog API 假设

假设 chatlog 服务提供以下 API：

```
GET /api/conversations
  - 参数: limit, since, tags
  - 返回: [{id, title, created_at, ...}]

GET /api/conversations/{id}
  - 返回: {id, title, created_at, messages: [{role, content, timestamp}]}
```

如果实际 API 不同，需要调整 `ChatlogClient` 实现。

### 数据流

```
chatlog HTTP 服务
    ↓
ChatlogClient.get_conversations()
    ↓
ChatlogClient.format_conversation_for_prompt()
    ↓
构建 Task (prompt = 格式化后的聊天记录)
    ↓
run_site_worker() → GPT/Gemini 分析
    ↓
保存到 Obsidian
```

## 文件结构

```
rpa_llm/
  ├── chatlog_client.py      # Chatlog HTTP 客户端
  ├── chatlog_cli.py          # Chatlog CLI 命令
  ├── mcp_server.py           # MCP 服务器（可选）
  ├── orchestrator.py          # 扩展支持 chatlog
  └── ...
```

## 下一步行动

1. **确认 chatlog API 接口**：需要了解 chatlog 服务的实际 API 规范
2. **实现 ChatlogClient**：根据实际 API 实现客户端
3. **实现 CLI 命令**：创建独立的命令行工具
4. **测试集成**：测试完整流程
5. **文档和示例**：编写使用文档

## 问题与考虑

1. **chatlog API 规范**：需要确认实际的 API 接口和认证方式
2. **数据格式**：需要确认聊天记录的数据结构
3. **性能优化**：如果聊天记录很大，可能需要分块处理
4. **错误处理**：网络错误、API 错误、LLM 错误等
5. **安全性**：API 密钥管理、数据隐私等

