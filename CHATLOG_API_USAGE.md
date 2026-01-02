# Chatlog API 使用指南（实际 API）

## API 接口说明

实际的 chatlog API 接口为：

```
GET /api/v1/chatlog?time=YYYY-MM-DD~YYYY-MM-DD&talker=xxx&format=json
```

### 参数说明

- `time`: 时间范围，格式为 `YYYY-MM-DD` 或 `YYYY-MM-DD~YYYY-MM-DD`
  - 示例：`2026-01-02` 或 `2026-01-02~2026-01-02`
- `talker`: 聊天对象标识（**必填**），支持：
  - wxid（微信 ID）
  - 群聊 ID
  - 备注名
  - 昵称
- `sender`: 发送者过滤（可选）
- `keyword`: 关键词过滤（可选）
- `limit`: 返回记录数量（可选，0 表示不限制）
- `offset`: 分页偏移量（可选）
- `format`: 输出格式，支持 `json`、`csv` 或 `text`（默认 `text`）

### 响应格式

当 `format=json` 时，返回 JSON 数组，每个消息包含：

```json
[
  {
    "seq": 1234567890,
    "time": "2026-01-02T10:00:00Z",
    "talker": "wxid_xxx",
    "talkerName": "聊天对象名称",
    "isChatRoom": false,
    "sender": "wxid_yyy",
    "senderName": "发送者名称",
    "isSelf": false,
    "type": 1,
    "subType": 1,
    "content": "消息内容",
    "contents": {}
  }
]
```

## 使用示例

### 基本用法

```bash
# 分析指定日期与特定联系人的聊天记录
python -m rpa_llm.chatlog_cli \
  --chatlog-url http://127.0.0.1:5030 \
  --talker "川群-2025" \
  --time-range "2026-01-02~2026-01-02" \
  --sites chatgpt gemini \
  --vault-path ~/work/personal/obsidian/personal
```

### 完整参数示例

```bash
python -m rpa_llm.chatlog_cli \
  --chatlog-url http://127.0.0.1:5030 \
  --talker "川群-2025" \
  --time-range "2026-01-02~2026-01-02" \
  --sender "某个发送者" \
  --keyword "关键词" \
  --limit 200 \
  --sites chatgpt gemini \
  --prompt-template ./custom_prompt.txt \
  --vault-path ~/work/personal/obsidian/personal \
  --driver-url http://127.0.0.1:27125 \
  --task-timeout-s 600 \
  --tags Chatlog Analysis Multi-LLM
```

### 使用日期范围

```bash
# 使用 --start 和 --end 参数
python -m rpa_llm.chatlog_cli \
  --chatlog-url http://127.0.0.1:5030 \
  --talker "川群-2025" \
  --start 2026-01-01 \
  --end 2026-01-31 \
  --sites chatgpt gemini
```

### 仅分析特定发送者的消息

```bash
python -m rpa_llm.chatlog_cli \
  --chatlog-url http://127.0.0.1:5030 \
  --talker "川群-2025" \
  --time-range "2026-01-02~2026-01-02" \
  --sender "某个发送者" \
  --sites chatgpt
```

### 关键词搜索

```bash
python -m rpa_llm.chatlog_cli \
  --chatlog-url http://127.0.0.1:5030 \
  --talker "川群-2025" \
  --time-range "2026-01-02~2026-01-02" \
  --keyword "项目进度" \
  --sites chatgpt gemini
```

## 注意事项

1. **talker 参数是必填的**：必须指定聊天对象标识
2. **时间范围**：如果不指定 `time-range`、`start` 或 `end`，默认使用今天
3. **URL 编码**：时间范围中的 `~` 会被自动 URL 编码为 `%7E`
4. **消息数量**：默认限制为 100 条，可以通过 `--limit` 调整
5. **driver_server**：需要确保 driver_server 正在运行

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

## 故障排查

### 1. 无法连接到 chatlog 服务

```bash
# 测试连接
curl "http://127.0.0.1:5030/api/v1/chatlog?time=2026-01-02&talker=川群-2025&format=json"
```

### 2. 获取不到消息

检查：
- chatlog 服务是否正常运行
- `talker` 参数是否正确（支持 wxid、群聊 ID、备注名、昵称）
- 时间范围是否正确
- 是否有符合条件的消息

### 3. LLM 分析失败

检查：
- driver_server 是否正在运行
- driver_url 是否正确
- 网络连接是否正常

