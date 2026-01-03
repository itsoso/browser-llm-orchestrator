# CLI 使用示例

本文档展示如何使用 `rpa_llm.cli` 的新功能。

## 示例 1: 使用 ChatGPT 5.2 Pro 进行分析

```bash
python -m rpa_llm.cli \
  --brief ./brief.yaml \
  --model-version 5.2pro
```

**说明：**
- 使用 ChatGPT 5.2 Pro 模型（研究级智能模型）
- 适合需要深度分析的场景
- 响应时间较长，但质量更高

## 示例 2: 使用自定义 Prompt 文件

假设你有一个 Obsidian 笔记文件 `/Users/liqiuhua/work/personal/obsidian/personal/Research/prompt.md`：

```markdown
# 分析任务

请分析以下主题：

## 主题
SDD（Spec-Driven Development）的最佳实践

## 背景
很多 Agent 支持了 AGENTS.md，基本解决了中期记忆的问题。
主流的工具有：Speckit、OpenSpec、Kiro、BMAD。

## 要求
1. 对比各个工具的优缺点
2. 给出选择建议
3. 提供实施步骤
```

使用该文件作为 prompt：

```bash
python -m rpa_llm.cli \
  --brief ./brief.yaml \
  --prompt-file "/Users/liqiuhua/work/personal/obsidian/personal/Research/prompt.md"
```

**说明：**
- 直接从 Obsidian 文件读取 prompt
- 不需要修改 `brief.yaml`
- 适合快速测试不同的 prompt

## 示例 3: 组合使用多个参数

```bash
python -m rpa_llm.cli \
  --brief ./brief.yaml \
  --prompt-file "./my_custom_prompt.md" \
  --model-version 5.2pro \
  --run-id "sdd_analysis_20260103" \
  --log-file "./logs/custom_run.log"
```

**说明：**
- 使用自定义 prompt 文件
- 使用 ChatGPT 5.2 Pro 模型
- 指定运行 ID（便于后续查找）
- 指定日志文件路径

## 示例 4: 批量处理不同模型版本

### 使用 5.2 Instant（快速）

```bash
python -m rpa_llm.cli \
  --brief ./brief.yaml \
  --model-version 5.2instant \
  --run-id "run_instant"
```

### 使用 5.2 Pro（深度）

```bash
python -m rpa_llm.cli \
  --brief ./brief.yaml \
  --model-version 5.2pro \
  --run-id "run_pro"
```

### 对比结果

运行完成后，可以在 Obsidian vault 中对比两个运行的结果：
- `10_ResearchRuns/run_instant/` - Instant 版本的结果
- `10_ResearchRuns/run_pro/` - Pro 版本的结果

## 示例 5: 从 Obsidian 笔记快速分析

如果你在 Obsidian 中有一个笔记，想要快速分析：

1. **复制笔记路径**（在 Obsidian 中右键 → Copy Path）

2. **运行分析**：
```bash
python -m rpa_llm.cli \
  --brief ./brief.yaml \
  --prompt-file "/Users/liqiuhua/work/personal/obsidian/personal/Research/My Note.md" \
  --model-version 5.2pro
```

3. **查看结果**：
   - 结果会保存到 `brief.yaml` 中配置的 `vault_path`
   - 日志文件在 `logs/cli_YYYYMMDD_HHMMSS.log`

## 注意事项

1. **模型版本选择**：
   - `5.2instant` - 快速响应，适合简单任务
   - `5.2pro` - 深度分析，适合复杂任务
   - `thinking` - 深度思考模式，响应时间最长

2. **Prompt 文件格式**：
   - 支持 Markdown 格式
   - 必须是 UTF-8 编码
   - 文件路径可以是绝对路径或相对路径

3. **优先级**：
   - CLI 参数优先级最高
   - `brief.yaml` 中的配置作为默认值
   - 如果 CLI 指定了参数，会覆盖 YAML 配置

4. **错误处理**：
   - 如果 prompt 文件不存在，程序会报错并退出
   - 如果模型版本不支持，会在运行时报错
   - 建议先测试小规模任务，确认配置正确

## 与 brief.yaml 的配合

即使使用了 CLI 参数，`brief.yaml` 仍然很重要：

- **必需配置**：
  - `sites`: 要使用的站点列表
  - `output.vault_path`: Obsidian vault 路径
  - `output.driver_url`: Driver server URL

- **可选配置**：
  - `output.site_model_versions`: 默认模型版本（可被 CLI 覆盖）
  - `streams[].prompt_template`: 默认 prompt 模板（可被 `--prompt-file` 覆盖）

## 故障排查

### 问题：模型版本不生效

**检查：**
1. 确认 `--model-version` 参数拼写正确
2. 确认站点是 `chatgpt`（其他站点不支持此参数）
3. 查看日志文件确认模型版本是否被正确传递

### 问题：Prompt 文件读取失败

**检查：**
1. 确认文件路径正确（使用绝对路径更可靠）
2. 确认文件存在且可读
3. 确认文件是 UTF-8 编码
4. 查看日志文件中的错误信息

### 问题：结果未保存

**检查：**
1. 确认 `brief.yaml` 中配置了 `output.vault_path`
2. 确认 vault 路径存在且可写
3. 查看日志文件确认是否有错误

