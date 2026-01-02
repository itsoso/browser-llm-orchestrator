# Chatlog 集成使用示例

## 场景说明

从 chatlog HTTP 服务获取聊天记录，发送到 GPT/Gemini 进行分析，最后保存到 Obsidian。

## 使用方式

### 方式 1：独立 CLI 命令（推荐）

#### 基本用法

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

#### 完整参数示例

```bash
python -m rpa_llm.chatlog_cli \
  --chatlog-url http://localhost:8080 \
  --chatlog-api-key "your-api-key" \
  --conversation-ids conv1 conv2 \
  --limit 10 \
  --sites chatgpt gemini \
  --prompt-template ./custom_prompt.txt \
  --vault-path ~/work/personal/obsidian/personal \
  --driver-url http://127.0.0.1:27125 \
  --task-timeout-s 600 \
  --tags Chatlog Analysis Multi-LLM
```

### 方式 2：通过 Python 代码调用

```python
import asyncio
from pathlib import Path
from rpa_llm.chatlog_cli import analyze_chatlog_conversations

async def main():
    await analyze_chatlog_conversations(
        chatlog_url="http://localhost:8080",
        chatlog_api_key=None,
        conversation_ids=["conv1", "conv2"],
        limit=10,
        sites=["chatgpt", "gemini"],
        prompt_template=None,  # 使用默认模板
        vault_path=Path("~/work/personal/obsidian/personal").expanduser(),
        driver_url="http://127.0.0.1:27125",
        task_timeout_s=480,
        tags=["Chatlog", "Multi-LLM", "Analysis"],
    )

if __name__ == "__main__":
    asyncio.run(main())
```

### 方式 3：扩展 brief.yaml（待实现）

在 `brief.yaml` 中添加 chatlog 配置：

```yaml
topic: "从 chatlog 获取的对话分析"
context: "自动从 chatlog 服务获取"

# Chatlog 配置
chatlog:
  enabled: true
  url: "http://localhost:8080"
  api_key: null
  conversation_ids: []
  limit: 10
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

## 工作流程

```
1. 启动 driver_server
   python start_driver.py --brief ./brief.yaml

2. 运行 chatlog_cli
   python -m rpa_llm.chatlog_cli --chatlog-url http://localhost:8080 --limit 10

3. 流程：
   chatlog HTTP 服务
     ↓
   ChatlogClient.get_conversations()
     ↓
   ChatlogClient.format_conversation_for_prompt()
     ↓
   构建 Task (prompt = 格式化后的聊天记录)
     ↓
   driver_server → GPT/Gemini 分析
     ↓
   保存到 Obsidian
```

## 输出结构

分析结果会保存到 Obsidian vault：

```
10_ResearchRuns/
  {run_id}/
    README.md                    # 运行索引
    03_model_runs/
      {topic}__chatlog_analysis__chatgpt.md
      {topic}__chatlog_analysis__gemini.md
    05_final/
      final__{arbitrator}__{topic}.md  # 综合结果（如果启用 synthesis）
```

## 自定义 Prompt 模板

创建 `custom_prompt.txt`:

```
你是资深研究员/分析师。请分析以下聊天记录，输出"结论清晰、证据可追溯、便于 Obsidian 阅读"的研究笔记。

聊天记录：
{conversation_content}

请按以下结构输出：
## 1. 关键结论
## 2. 输出洞察
## 3. 行动建议（如有）
## 4. 相关话题（如有）
## 5. 时间线分析（如有）
```

然后使用：
```bash
python -m rpa_llm.chatlog_cli \
  --chatlog-url http://localhost:8080 \
  --prompt-template ./custom_prompt.txt \
  --limit 10
```

## 注意事项

1. **确保 driver_server 正在运行**：chatlog_cli 需要 driver_server 来处理 LLM 请求
2. **chatlog API 兼容性**：如果 chatlog 的 API 与假设不同，需要修改 `ChatlogClient` 类
3. **网络连接**：确保可以访问 chatlog 服务
4. **API 密钥**：如果 chatlog 需要认证，使用 `--chatlog-api-key` 参数

## 故障排查

### 1. 无法连接到 chatlog 服务

```bash
# 测试连接
curl http://localhost:8080/api/conversations?limit=1
```

### 2. 获取不到聊天记录

检查：
- chatlog 服务是否正常运行
- API 地址是否正确
- 是否需要 API 密钥
- conversation_ids 是否存在

### 3. LLM 分析失败

检查：
- driver_server 是否正在运行
- driver_url 是否正确
- 网络连接是否正常

