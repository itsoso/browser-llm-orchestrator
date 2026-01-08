# Browser LLM Orchestrator

> **多 LLM 编排决策系统** — 通过浏览器自动化并行调用多个大语言模型，交叉验证后由仲裁者模型综合出可信结论。

---

## 🎯 项目简介

**Browser LLM Orchestrator** 是一个创新的多模型编排框架，核心理念是：**不依赖单一 AI 模型的判断，而是让多个顶级 LLM 各抒己见，再由仲裁者模型做出最终裁决**。

### 为什么需要多 LLM 决策？

1. **减少幻觉风险**：单一模型可能产生"自信的错误"，多模型交叉验证能显著降低幻觉概率
2. **避免模型偏见**：不同模型有不同的训练数据和推理风格，综合多方观点更全面
3. **提升可信度**：当多个模型得出相同结论时，该结论的可信度更高
4. **充分利用资源**：同时调用多个已订阅账号的 LLM，最大化利用免费/付费额度

### 支持的 LLM 平台

| 平台 | 状态 | 说明 |
|:---|:---:|:---|
| **ChatGPT** | ✅ | 支持模型版本切换（5.2 Pro / 5.2 Thinking / 5.2 Instant） |
| **Gemini** | ✅ | Google AI 平台 |
| **Perplexity** | ✅ | 搜索增强型 AI |
| **Grok** | ✅ | X/Twitter AI |
| **千问 (Qianwen)** | ✅ | 阿里通义千问 |

---

## 🧠 设计理念

### 1. 多模型并行查询

```
┌─────────────────────────────────────────────────────┐
│                    User Prompt                       │
└─────────────────────────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
     ┌─────────┐    ┌─────────┐    ┌─────────┐
     │ ChatGPT │    │  Gemini │    │  Grok   │  ...
     └─────────┘    └─────────┘    └─────────┘
          │               │               │
          └───────────────┼───────────────┘
                          ▼
              ┌───────────────────────┐
              │   Arbitrator Model    │
              │  (综合 + 仲裁 + 裁决)   │
              └───────────────────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │   Final Decision      │
              │  (Markdown → Obsidian) │
              └───────────────────────┘
```

### 2. 仲裁机制

系统不做简单的"平均主义"，而是由指定的仲裁者模型（默认 Gemini）执行严格的综合分析：

- **共识识别**：找出多模型一致同意的结论
- **分歧裁决**：对不同意见进行评判，给出理由
- **置信度评估**：为每个结论标注 Low/Med/High 置信度
- **验证路径**：对不确定内容标注【需核验】并给出验证方法

### 3. 浏览器自动化

使用 Playwright 控制真实浏览器，具有以下优势：

- **绕过 API 限制**：直接使用网页版，无需申请 API key
- **利用已登录会话**：使用本地 Chrome Profile，保持登录状态
- **Stealth 模式**：降低反爬虫检测（Cloudflare 等）的触发率
- **可视化调试**：支持有头模式，便于排查问题

### 4. Obsidian 原生集成

所有输出直接写入 Obsidian Vault：

- 标准 Markdown 格式，支持 frontmatter 元数据
- 自动生成运行索引和目录结构
- 支持表格、列表、引用等富文本格式

---

## 📋 使用场景

- **研究调研**：多角度收集信息，交叉验证事实
- **决策支持**：重要决策前获取多模型建议
- **知识整理**：自动化生成研究笔记到 Obsidian
- **批量分析**：周报/月报/趋势分析的自动化生成

---

## 🚀 快速开始

```bash
source .venv/bin/activate
```

## Install
```bash
pip install -r requirements.txt
```

## 预热账号（首次使用或登录失效时）

如果遇到 `ensure_ready: still cannot locate textbox after manual checkpoint` 错误，说明需要手动登录并保存浏览器状态。

### 使用预热脚本

```bash
# 预热单个站点
python warmup.py chatgpt    # 预热 ChatGPT
python warmup.py gemini      # 预热 Gemini
python warmup.py perplexity  # 预热 Perplexity

# 或预热所有站点
python warmup.py all
```

**操作步骤：**
1. 脚本会打开浏览器窗口
2. 手动完成登录和验证（如 Cloudflare 验证）
3. 确保能看到聊天输入框
4. 回到终端按回车保存状态

**注意事项：**
- 预热脚本使用与 RPA 相同的配置，确保状态兼容
- 如果看到 "stealth mode not available" 警告，运行 `pip install playwright-stealth`
- 状态保存在 `profiles/<site_id>/` 目录下

## 配置

编辑 `brief.yaml` 文件，配置 driver_server 参数：

```yaml
driver_server:
  sites: "chatgpt,gemini,perplexity,grok,qianwen"
  port: 27125
  profiles_root: "profiles"
  artifacts_root: "runs/driver/artifacts"
  host: "127.0.0.1"
  headless: false
  prewarm: true
```

## 启动 Driver Server

### 方式1：自动检查并启动（推荐）

使用辅助脚本自动检查 Driver Server 状态：

```bash
# Python 脚本（跨平台）
python ensure_driver.py --brief ./brief.yaml --background --wait

# Bash 包装脚本（macOS/Linux）
./run_with_driver.sh python -m rpa_llm.cli --brief ./brief.yaml
```

### 方式2：手动启动

```bash
# 启动前检查预热状态（可选）
PYTHONUNBUFFERED=1 python -u start_driver.py --brief ./brief.yaml --check-warmup

# 正常启动
PYTHONUNBUFFERED=1 python -u start_driver.py --brief ./brief.yaml
```

### 方式2：使用命令行参数（覆盖配置）
```bash
PYTHONUNBUFFERED=1 python -u -m rpa_llm.driver_server --sites chatgpt,gemini,perplexity,grok,qianwen --port 27125 --profiles-root profiles
```

### 启动失败时的处理

如果启动时看到类似以下错误：
```
⚠️  chatgpt 初始化失败：需要登录或验证
错误: ensure_ready: still cannot locate textbox after manual checkpoint
```

**解决方案：**
1. 运行预热脚本手动登录：
   ```bash
   python warmup.py chatgpt
   ```
2. 重新启动 driver server
3. 如果问题持续，检查：
   - 是否安装了 `playwright-stealth`: `pip install playwright-stealth`
   - 查看截图: `runs/driver/artifacts/<site_id>/`

## 使用

### 基本用法

```bash
export RPA_DRIVER_URL="http://127.0.0.1:27125"
PYTHONUNBUFFERED=1 python -u -m rpa_llm.cli --brief ./brief.yaml
```

或者直接在 brief.yaml 中配置 `driver_url`，无需设置环境变量。 ·

### 高级功能

#### 1. 指定 ChatGPT 模型版本

使用 `--model-version` 参数可以覆盖 `brief.yaml` 中的模型版本配置：

```bash
# 使用 ChatGPT 5.2 Pro（深度推理，20-30分钟）
python -m rpa_llm.cli --brief ./brief.yaml --model-version 5.2pro

# 使用 ChatGPT 5.2 Thinking（平衡模式，10-15分钟）
python -m rpa_llm.cli --brief ./brief.yaml --model-version 5.2thinking

# 使用 ChatGPT 5.2 Instant（快速响应，2-5分钟）
python -m rpa_llm.cli --brief ./brief.yaml --model-version 5.2instant
```

**支持的模型版本：**
- `5.2pro` - **ChatGPT 5.2 Pro**（深度推理模式，20-30分钟，最深入的分析）✨ 推荐
- `5.2thinking` - **ChatGPT 5.2 Thinking**（平衡模式，10-15分钟，一般分析任务）
- `5.2instant` - **ChatGPT 5.2 Instant**（快速响应模式，2-5分钟，简单总结任务）

**注意：**
- 如果 `brief.yaml` 中配置了 `output.site_model_versions`，CLI 参数会覆盖 ChatGPT 站点的配置
- 其他站点（如 Gemini）的模型版本仍使用 `brief.yaml` 中的配置

#### 2. 使用自定义 Prompt 文件

使用 `--prompt-file` 参数可以从文件读取 prompt，而不是使用 `brief.yaml` 中的 `prompt_template`：

```bash
# 使用 Obsidian 文件作为 prompt
python -m rpa_llm.cli --brief ./brief.yaml --prompt-file "/path/to/obsidian/note.md"

# 使用本地 Markdown 文件
python -m rpa_llm.cli --brief ./brief.yaml --prompt-file "./my_prompt.md"
```

**使用场景：**
- 从 Obsidian 笔记中直接复制 prompt 进行分析
- 使用复杂的多段落 prompt，不适合放在 YAML 中
- 复用已有的 prompt 文件

**注意事项：**
- 文件路径支持绝对路径和相对路径
- 文件必须是 UTF-8 编码
- 如果文件不存在，程序会报错并退出
- 使用自定义 prompt 文件时，`brief.yaml` 中的 `prompt_template` 会被忽略

#### 3. 组合使用

可以同时使用多个参数：

```bash
# 使用自定义 prompt 文件 + 指定模型版本
python -m rpa_llm.cli \
  --brief ./brief.yaml \
  --prompt-file "/path/to/prompt.md" \
  --model-version 5.2pro \
  --run-id "custom_run_001"
```

### 完整参数列表

```bash
python -m rpa_llm.cli --help
```

**主要参数：**
- `--brief` (必需): Brief YAML 文件路径
- `--run-id`: 运行 ID（默认：UTC 时间戳）
- `--headless`: 无头模式（不推荐用于 LLM 站点）
- `--log-file`: 日志文件路径（默认：自动生成到 `logs/` 目录）
- `--model-version`: 指定 ChatGPT 模型版本（覆盖 brief.yaml 配置）
- `--prompt-file`: 自定义 prompt 文件路径（覆盖 brief.yaml 中的 prompt_template）

---

## 💬 Chatlog 集成：微信群聊自动化分析

### 概述

本项目集成了 [Chatlog](https://github.com/nicethings/chatlog) 服务，实现了**微信群聊记录自动化分析**的完整工作流：

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Chatlog 自动化流程                               │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
    ┌───────────────────────────────┼───────────────────────────────┐
    │                               │                               │
    ▼                               ▼                               ▼
┌─────────┐                   ┌─────────┐                   ┌─────────────┐
│ Chatlog │  ──── HTTP ────>  │  本项目  │  ────────────>   │  Obsidian   │
│ 服务    │    获取群聊记录     │ 自动化   │   保存分析结果    │   Vault     │
└─────────┘                   └─────────┘                   └─────────────┘
                                    │
                     ┌──────────────┴──────────────┐
                     │                             │
                     ▼                             ▼
              ┌─────────────┐              ┌─────────────┐
              │  Raw 文件    │              │ Summary 文件 │
              │ (原始记录)   │              │ (LLM 分析)   │
              └─────────────┘              └─────────────┘
```

### 设计理念

1. **本地化数据闭环**：聊天记录 → LLM 分析 → Obsidian 存储，全程本地运行
2. **构建个人知识库**：分析结果直接落地 Obsidian，便于后续检索和 embedding
3. **自动化周期处理**：支持按周批量分析，适合周报/复盘场景
4. **模板驱动**：支持自定义分析模板，适应不同群聊类型

### 工作流程

#### 1. 获取聊天记录

通过 Chatlog HTTP API 获取指定时间范围内的群聊记录：

```bash
# Chatlog API 示例
curl "http://127.0.0.1:5030/api/v1/chatlog?time=2026-01-01~2026-01-07&talker=xx群-2025&format=json"
```

#### 2. 保存 Raw 文件

原始聊天记录保存到 Obsidian，便于溯源：

```
10_Sources/WeChat/
└── 00-raws/
    └── xx群-2025/
        └── 2026/
            └── 01/
                └── 第1周/
                    └── xx群-2025 2026-01-01~2026-01-07-raw.md
```

#### 3. LLM 分析

使用本项目的多模型能力分析聊天内容：
- 支持自定义分析模板（见 `templates/chatlog_for_wechat.md`）
- 支持指定模型版本（如 ChatGPT 5.2 Pro）
- 输出结构化的复盘报告

#### 4. 保存 Summary 文件

分析结果保存到 Obsidian，形成知识沉淀：

```
10_Sources/WeChat/
└── 10-Summaries/
    └── xx群-2025/
        └── 2026/
            └── 01/
                └── 第1周/
                    └── xx群-2025 第1周-2026-01-01~2026-01-07-Sum-5.2pro.md
```

### 快速使用

#### 配置文件

创建 `chatlog_automation.yaml`：

```yaml
# Chatlog 服务配置
chatlog:
  url: "http://127.0.0.1:5030"

# LLM 分析配置
llm:
  arbitrator_site: "chatgpt"  # 可选: chatgpt, gemini
  model_version: "5.2pro"     # 模型版本
  task_timeout_s: 1200        # 超时时间（秒）
  new_chat: true              # 每次打开新窗口

# Obsidian 路径配置
obsidian:
  base_path: "~/work/personal/obsidian/personal/10_Sources/WeChat"
  template: "./templates/chatlog_for_wechat.md"  # 分析模板
```

#### 单次运行

```bash
# 方式1：自动确保 Driver Server 运行（推荐）
./run_with_driver.sh python -m rpa_llm.chatlog_automation \
  --talker "xx群-2025" \
  --start 2026-01-01 \
  --end 2026-01-07 \
  --config ./chatlog_automation.yaml

# 方式2：手动启动（需要先启动 Driver Server）
python -m rpa_llm.chatlog_automation \
  --talker "xx群-2025" \
  --start 2026-01-01 \
  --end 2026-01-07 \
  --config ./chatlog_automation.yaml
```

#### 批量周分析

```bash
# 批量处理多周数据
python rpa_llm/batch_weekly_analysis.py \
  --talker "xx群-2025" \
  --year 2025 \
  --start-week 48 \
  --end-week 53 \
  --config ./chatlog_automation.yaml
```

### 输出示例

分析结果包含结构化的复盘内容：

```markdown
---
type: weekly_review
create: 2026-01-08T10:30:00+08:00
group: xx群-2025
week: 1
period_start: 2026-01-01
period_end: 2026-01-07
source: "wechat chatlog"
raw_note: '[[10_Sources/WeChat/00-raws/xx群-2025/2026/01/第1周/xx群-2025 2026-01-01~2026-01-07-raw]]'
tags: [投资, 复盘, AI, ...]
topics: [市场分析, 技术趋势, ...]
key_people: [王川, ...]
---

# xx群-2025 第1周 周期看板（2026.01.01～2026.01.07）

## Step2 Digest：高信号摘要
...

## Step3 观点卡片库
...

## Step7 洞察判断
...
```

### 与 Obsidian 知识库集成

#### 1. Wikilink 自动关联

Summary 文件自动包含对 Raw 文件的 wikilink 引用：

```markdown
raw_note: '[[10_Sources/WeChat/00-raws/xx群-2025/2026/01/第1周/xx群-2025 2026-01-01~2026-01-07-raw]]'
```

点击即可跳转到原始聊天记录。

#### 2. 目录结构

按年/月/周组织，便于时间维度检索：

```
10_Sources/WeChat/
├── 00-raws/           # 原始记录
│   └── {群名}/
│       └── {年}/
│           └── {月}/
│               └── 第{周}周/
└── 10-Summaries/      # 分析结果
    └── {群名}/
        └── {年}/
            └── {月}/
                └── 第{周}周/
```

#### 3. Frontmatter 元数据

支持 Obsidian Dataview 查询：

```dataview
TABLE group, week, period_start, period_end
FROM "10_Sources/WeChat/10-Summaries"
WHERE type = "weekly_review"
SORT period_start DESC
```

#### 4. 本地 Embedding

分析结果以 Markdown 格式存储，可直接用于：
- Obsidian 本地搜索
- 第三方 embedding 工具（如 Ollama + RAG）
- 构建个人知识图谱

### 命令行参数

```bash
python -m rpa_llm.chatlog_automation --help
```

**主要参数：**

| 参数 | 说明 | 示例 |
|:---|:---|:---|
| `--talker` | 群聊标识（必填） | `xx群-2025` |
| `--start` | 开始日期 | `2026-01-01` |
| `--end` | 结束日期 | `2026-01-07` |
| `--config` | 配置文件路径 | `./chatlog_automation.yaml` |
| `--template` | 分析模板路径 | `./templates/chatlog_for_wechat.md` |
| `--model-version` | 模型版本 | `5.2pro` |
| `--new-chat` | 每次打开新窗口 | - |
| `--auto-mode` | 自动模式（批量处理推荐） | - |

### 前置依赖

1. **Chatlog 服务**：需要先部署并运行 Chatlog 服务
2. **Driver Server**：需要启动本项目的 driver_server
3. **Obsidian Vault**：需要有可写入的 Obsidian vault 目录

```bash
# 1. 启动 Chatlog 服务（参考 Chatlog 项目文档）
# 2. 启动 Driver Server
python start_driver.py --brief ./brief.yaml
# 3. 运行自动化分析
python -m rpa_llm.chatlog_automation --talker "xx群" --start 2026-01-01 --end 2026-01-07
```

---

## 🌐 Web 管理界面（推荐）

**一键启动可视化管理界面**：

```bash
python web_admin.py
```

访问 **http://127.0.0.1:5050** 即可通过浏览器管理整个系统！

**功能特性**：
- 📊 **实时状态监控** - Driver Server 状态、站点连接情况
- 🔥 **可视化预热** - 一键预热所有 LLM 站点
- 💬 **表单化执行** - 通过表单配置和执行 Chatlog 分析
- 📝 **日志查看** - 在线查看所有运行日志
- ⚙️ **配置管理** - 查看和管理系统配置

详细使用指南：[WEB_ADMIN_USAGE.md](./WEB_ADMIN_USAGE.md) ⭐⭐⭐

---

## 📚 相关文档

- **[WEB_ADMIN_USAGE.md](./WEB_ADMIN_USAGE.md) - Web 管理界面使用指南** ⭐⭐⭐ (新手推荐)
- **[CHATGPT_MODEL_VERSIONS.md](./CHATGPT_MODEL_VERSIONS.md) - ChatGPT 5.2 模型版本详解** ⭐⭐
- **[LONG_TEXT_HANDLING.md](./LONG_TEXT_HANDLING.md) - 长文本输入处理指南** ⭐ (遇到截断问题必读)
- [CLI_USAGE_EXAMPLES.md](./CLI_USAGE_EXAMPLES.md) - CLI 详细用法示例
- [CHATLOG_USAGE_EXAMPLE.md](./CHATLOG_USAGE_EXAMPLE.md) - Chatlog 集成详细示例
- [DRIVER_HELPER_USAGE.md](./DRIVER_HELPER_USAGE.md) - Driver Server 辅助工具使用指南
- [LOGGING.md](./LOGGING.md) - 日志系统说明
- [REFACTORING_SUMMARY.md](./REFACTORING_SUMMARY.md) - 重构总结