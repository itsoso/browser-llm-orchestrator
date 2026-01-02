# Chatlog 自动化工作流使用指南

## 功能说明

`chatlog_automation.py` 实现了完整的自动化工作流：

1. **获取聊天记录**：从 chatlog 服务获取指定时间范围的聊天记录
2. **保存 Raw 文件**：按照 Obsidian 目录结构保存原始聊天记录
3. **生成 Prompt**：从 template 文件（可选）生成分析 prompt
4. **LLM 分析**：调用大模型分析聊天记录
5. **保存 Summary 文件**：按照 Obsidian 目录结构保存分析结果

## 目录结构

自动化脚本会按照以下目录结构保存文件：

```
10_Sources/WeChat/
├── 00-raws/
│   └── {talker}/
│       └── {year}/
│           └── {month}/
│               └── 第{week}周/
│                   └── {talker} {date_range}-raw.md
└── 10-Summaries/
    └── {talker}/
        └── {year}/
            └── {month}/
                └── 第{week}周/
                    └── {talker} 第{week}周-{date_range}-Sum-{version}.md
```

## 使用示例

### 基本用法

```bash
python -m rpa_llm.chatlog_automation \
  --chatlog-url http://127.0.0.1:5030 \
  --talker "川群-2025" \
  --start 2025-12-28 \
  --end 2025-12-28
```

### 完整参数示例

```bash
python -m rpa_llm.chatlog_automation \
  --chatlog-url http://127.0.0.1:5030 \
  --talker "川群-2025" \
  --start 2025-12-28 \
  --end 2025-12-28 \
  --base-path ~/work/personal/obsidian/personal/10_Sources/WeChat \
  --template ./templates/chatlog_analysis.md \
  --arbitrator-site gemini \
  --model-version 5.2pro \
  --task-timeout-s 600
```

### 使用自定义 Template

创建 template 文件 `templates/chatlog_analysis.md`：

```markdown
你是资深研究员/分析师。请分析以下聊天记录，输出"结论清晰、证据可追溯、便于 Obsidian 阅读"的研究笔记。

聊天记录：
{conversation_content}

对话对象：{talker}
日期范围：{date_range}
周数：第{week}周

请按以下结构输出：
## 1. 关键结论
## 2. 输出洞察
## 3. 行动建议（如有）
## 4. 相关话题（如有）
```

然后使用：

```bash
python -m rpa_llm.chatlog_automation \
  --chatlog-url http://127.0.0.1:5030 \
  --talker "川群-2025" \
  --start 2025-12-28 \
  --end 2025-12-28 \
  --template ./templates/chatlog_analysis.md
```

## 参数说明

- `--chatlog-url` (必填): chatlog 服务地址
- `--talker` (必填): 聊天对象标识
- `--start` (必填): 开始日期，格式为 YYYY-MM-DD
- `--end` (必填): 结束日期，格式为 YYYY-MM-DD
- `--base-path` (可选): Obsidian 基础路径，默认 `~/work/personal/obsidian/personal/10_Sources/WeChat`
- `--template` (可选): Prompt 模板文件路径
- `--driver-url` (可选): driver_server URL，默认从环境变量或 brief.yaml 读取
- `--arbitrator-site` (可选): LLM 分析站点，默认 `gemini`
- `--model-version` (可选): 模型版本，默认 `5.2pro`
- `--task-timeout-s` (可选): 任务超时时间（秒），默认 600
- `--log-file` (可选): 日志文件路径，默认自动生成到 `logs/` 目录

## 工作流程

1. **获取聊天记录**
   - 从 chatlog 服务获取指定时间范围的聊天记录
   - 支持时间范围查询

2. **保存 Raw 文件**
   - 自动创建目录结构：`00-raws/{talker}/{year}/{month}/第{week}周/`
   - 文件名格式：`{talker} {date_range}-raw.md`
   - 包含 frontmatter 和格式化后的聊天记录

3. **生成 Prompt**
   - 如果提供了 template 文件，使用 template 生成 prompt
   - 否则使用默认模板
   - 支持占位符：`{conversation_content}`, `{talker}`, `{date_range}`, `{week}`

4. **LLM 分析**
   - 调用指定的 LLM 站点（默认 gemini）进行分析
   - 支持超时设置

5. **保存 Summary 文件**
   - 自动创建目录结构：`10-Summaries/{talker}/{year}/{month}/第{week}周/`
   - 文件名格式：`{talker} 第{week}周-{date_range}-Sum-{version}.md`
   - 包含 frontmatter 和 LLM 分析结果

## 注意事项

1. **确保 driver_server 正在运行**：需要 driver_server 来处理 LLM 请求
2. **目录结构**：脚本会自动创建所需的目录结构
3. **周数计算**：使用 ISO 8601 标准（周一开始，第一周包含 1 月 4 日）
4. **日期范围**：如果 start 和 end 相同，文件名中只显示一个日期

## 与 chatlog_cli 的区别

- **chatlog_cli**: 用于研究分析，保存到 `10_ResearchRuns/` 目录，支持多站点分析和 synthesis
- **chatlog_automation**: 用于日常整理，保存到 `10_Sources/WeChat/` 目录，单站点分析，按照原始目录结构组织

## 故障排查

### 1. 无法连接到 chatlog 服务

```bash
# 测试连接
curl "http://127.0.0.1:5030/api/v1/chatlog?time=2025-12-28&talker=川群-2025&format=json"
```

### 2. 目录创建失败

检查基础路径是否正确，确保有写入权限。

### 3. LLM 分析失败

检查 driver_server 是否正在运行，driver_url 是否正确。

