# ChatGPT 模型版本自动切换功能使用说明

## 功能概述

现在系统支持自动切换 ChatGPT 模型版本，无需手动在浏览器中选择。支持通过多种方式指定模型版本：

- 环境变量
- `brief.yaml` 配置文件
- API 调用参数
- `chatlog_automation` 命令行参数

## 支持的模型版本格式

### 1. Pro 系列
- `5.2pro` / `5.2-pro` / `gpt-5.2-pro` → 选择 ChatGPT 5.2 Pro
- `pro` → 选择 ChatGPT Pro（通用）
- `GPT-5` → 选择 GPT-5 相关模型

### 2. 其他模型
- `GPT-4o` / `4o` → 选择 GPT-4o
- `thinking` → 启用 Thinking 模式
- `instant` → 使用 Instant 模式

## 使用方法

### 方法 1: 环境变量

```bash
# 设置环境变量
export CHATGPT_VARIANT=5.2pro

# 运行任务
python -m rpa_llm.cli --brief ./brief.yaml
```

### 方法 2: brief.yaml 配置文件

在 `brief.yaml` 中添加 `site_model_versions` 配置：

```yaml
output:
  vault_path: "/path/to/obsidian"
  root_dir: "10_ResearchRuns"
  task_timeout_s: 1200
  
  # 为每个站点指定模型版本
  site_model_versions:
    chatgpt: "5.2pro"
    gemini: null  # gemini 不需要模型版本参数
```

### 方法 3: API 调用

通过 `driver_server` API 调用时，在 payload 中添加 `model_version`：

```python
import requests

payload = {
    "site_id": "chatgpt",
    "prompt": "你的问题",
    "timeout_s": 1200,
    "model_version": "5.2pro"  # 指定模型版本
}

response = requests.post("http://127.0.0.1:27125/run_task", json=payload)
```

### 方法 4: chatlog_automation 命令行

```bash
python -m rpa_llm.chatlog_automation \
  --talker "川群-2025" \
  --start 2026-01-02 \
  --end 2026-01-02 \
  --model-version "5.2pro"
```

或者在 `chatlog_automation.yaml` 中配置：

```yaml
llm:
  arbitrator_site: "chatgpt"
  model_version: "5.2pro"
  task_timeout_s: 1200
```

## 工作原理

1. **模型选择器定位**：系统会自动查找 ChatGPT 页面上的模型选择按钮
2. **下拉菜单打开**：点击模型选择按钮，打开下拉菜单
3. **精确匹配**：根据提供的模型版本字符串，在下拉菜单中查找匹配的选项
4. **自动选择**：找到匹配项后自动点击选择

## 匹配逻辑

系统使用以下策略匹配模型：

1. **关键词匹配**：
   - `5.2` → 匹配包含 "5.2" 或 "5-2" 的选项
   - `pro` → 匹配包含 "Pro"、"专业"、"Professional" 的选项
   - `4o` → 匹配包含 "4o" 或 "4-o" 的选项

2. **组合匹配**：
   - `5.2pro` → 同时匹配 "5.2" 和 "Pro"
   - `GPT-5` → 匹配 "GPT" 和 "5"

3. **回退机制**：
   - 如果精确匹配失败，会尝试匹配 "Pro" 通用选项
   - 如果仍然失败，会记录日志但不阻塞任务执行

## 日志输出

启用模型版本切换后，日志会显示：

```
[2026-01-02T10:00:00+08:00] [driver] run_task recv | site=chatgpt | prompt_len=324 | timeout_s=1200 | model_version=5.2pro
[2026-01-02T10:00:01+08:00] [chatgpt] mode: desired=pro, model_version=5.2pro
[2026-01-02T10:00:02+08:00] [chatgpt] mode: selecting model 'ChatGPT 5.2 Pro' (matched by enhanced pattern)
[2026-01-02T10:00:03+08:00] [chatgpt] mode: successfully selected model (version=5.2pro)
```

## 注意事项

1. **超时时间**：使用 ChatGPT 5.2 Pro 时，建议设置 `task_timeout_s: 1200`（20分钟）或更长
2. **首次设置**：每个 adapter 实例只会设置一次模型版本，后续请求会复用
3. **失败处理**：如果模型选择失败，系统会记录日志但不会阻塞任务，会使用当前已选择的模型
4. **浏览器状态**：确保浏览器已登录 ChatGPT，并且有权限访问 Pro 模型

## 故障排查

### 问题：模型选择失败

**可能原因**：
- 页面未完全加载
- 模型选择器按钮未找到
- 下拉菜单中的选项文本不匹配

**解决方法**：
1. 检查日志中的错误信息
2. 手动在浏览器中确认模型选择器的位置
3. 尝试使用更通用的模型名称（如 `pro` 而不是 `5.2pro`）

### 问题：模型版本未生效

**可能原因**：
- 环境变量未正确设置
- `brief.yaml` 配置格式错误
- API 调用中未传递 `model_version` 参数

**解决方法**：
1. 检查日志中是否显示 `model_version` 参数
2. 确认 `brief.yaml` 中的 `site_model_versions` 配置格式正确
3. 验证 API payload 中是否包含 `model_version` 字段

## 示例配置

### 完整 brief.yaml 示例

```yaml
topic: "研究主题"
context: "背景信息"

sites:
  - chatgpt
  - gemini

output:
  vault_path: "/path/to/obsidian"
  root_dir: "10_ResearchRuns"
  task_timeout_s: 1200
  
  # 为每个站点指定模型版本
  site_model_versions:
    chatgpt: "5.2pro"
    gemini: null
  
  driver_url: "http://127.0.0.1:27125"
  
  tags:
    - "Multi-LLM"
    - "Synthesis"
```

### chatlog_automation.yaml 示例

```yaml
chatlog:
  url: "http://127.0.0.1:5030"
  api_key: null

llm:
  arbitrator_site: "chatgpt"
  model_version: "5.2pro"  # 指定 ChatGPT 5.2 Pro
  task_timeout_s: 1200

obsidian:
  base_path: "~/work/personal/obsidian/personal/10_Sources/WeChat"
  template: "./templates/chatlog_for_wechat.md"

driver:
  url: "http://127.0.0.1:27125"
```

## 相关文件

- `rpa_llm/adapters/chatgpt.py` - ChatGPT adapter 实现
- `rpa_llm/driver_server.py` - Driver server API
- `rpa_llm/driver_client.py` - Driver client 实现
- `rpa_llm/orchestrator.py` - 任务编排器
- `rpa_llm/chatlog_automation.py` - Chatlog 自动化脚本

