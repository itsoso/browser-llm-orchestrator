# 长文本输入处理指南

## 🚨 ChatGPT 输入框字符限制

### 问题描述

ChatGPT 网页版的输入框有**约 10,000 字符**的限制。超过此限制的文本会被截断，导致：
- ❌ 分析不完整
- ❌ 丢失重要信息
- ❌ 输出质量下降

### 如何判断是否受限

运行时会看到如下警告：

```
⚠️  警告: Prompt 长度 (12541) 超过 ChatGPT 输入框限制 (~10000)
⚠️  建议: 使用文件上传或分段输入。当前将尝试输入，但可能被截断。
⚠️  警告: 输入可能被截断！预期 12541 字符，实际 9876 字符
```

---

## ✅ 解决方案

### 方案 1：优化 Prompt 模板（推荐）

减少模板中的冗余内容，只保留核心指令。

**优化前**（示例）：
```markdown
你是一个基于第一性原理的分析师...（大量说明文字）

## 硬性规则
1. ...
2. ...
3. ...

## 任务
Step1...
Step2...
Step3...

## 群聊记录内容
{{conversation_content}}

---

（更多说明和示例）
```

**优化后**（示例）：
```markdown
你是群聊分析专家。基于以下聊天记录进行分析：

{{conversation_content}}

---

输出要求：
1. 关键结论（带证据引用）
2. 重要洞察
3. 行动建议
```

**优化目标**：
- 模板部分控制在 1000-2000 字符
- 为聊天内容留出 8000+ 字符空间

### 方案 2：缩短分析周期

减少单次分析的聊天记录数量：

```bash
# 原来：分析一周（可能超过 10K 字符）
python -m rpa_llm.chatlog_automation \
  --talker "xx群" \
  --start 2026-01-01 \
  --end 2026-01-07

# 优化：分析1-2天（控制在 10K 以内）
python -m rpa_llm.chatlog_automation \
  --talker "xx群" \
  --start 2026-01-07 \
  --end 2026-01-07
```

### 方案 3：去除 Raw 文件的 Frontmatter

Raw 文件的 YAML frontmatter 会占用字符数，但对分析无用。

**修改 `rpa_llm/chatlog_automation.py`**：

在生成 prompt 时，去除 frontmatter：

```python
# 读取 raw 文件内容
raw_content = raw_path.read_text(encoding="utf-8")

# 去除 frontmatter（以 --- 开始和结束的部分）
import re
raw_content = re.sub(r'^---\s*\n.*?\n---\s*\n', '', raw_content, flags=re.DOTALL)
```

这样可以节省约 200-300 字符。

### 方案 4：使用文件上传（未实现）

**理论方案**（需要额外开发）：

1. 将聊天记录保存为临时文件
2. 使用 Playwright 模拟点击"附件上传"按钮
3. 上传文件后再输入分析指令

**优点**：
- ✅ 无字符数限制
- ✅ 支持更大的数据量

**缺点**：
- ❌ 需要额外开发
- ❌ ChatGPT 可能不支持所有文件格式
- ❌ 增加复杂度

### 方案 5：切换到 API 模式（未实现）

使用 OpenAI API 而不是浏览器自动化：

**优点**：
- ✅ 无输入框字符限制
- ✅ 更稳定、更快速
- ✅ 支持更长的 context

**缺点**：
- ❌ 需要 API Key 和付费
- ❌ 可能无法使用 ChatGPT Pro 模式
- ❌ 需要重构代码

---

## 📊 字符数统计

### 典型场景的字符数

| 场景 | 模板 | 聊天记录 | 总计 | 是否超限 |
|------|------|---------|------|---------|
| 1天，20条消息 | 1500 | 3000 | 4500 | ✅ 正常 |
| 2天，50条消息 | 1500 | 6000 | 7500 | ✅ 正常 |
| 3天，100条消息 | 1500 | 9000 | 10500 | ⚠️ 接近限制 |
| 7天，300条消息 | 1500 | 15000 | 16500 | ❌ 超限 |

**结论**：
- ✅ **1-2天** 的聊天记录通常不会超限
- ⚠️ **3-4天** 需要优化模板
- ❌ **5天以上** 必须分段处理或优化

### 如何查看字符数

运行前查看 prompt 长度：

```bash
# 运行后查看日志
cat logs/chatlog_automation_*.log | grep "Prompt 生成完成"
```

输出示例：
```
[2026-01-07T20:55:16+08:00] [automation] ✓ Prompt 生成完成（长度: 12541 字符）
```

---

## 🔧 实施步骤

### 立即可执行的优化

#### 1. 优化模板

编辑 `templates/chatlog_for_wechat.md`：

**精简前**（1421 字符）：
```markdown
你是一个"基于第一性原理 + 可验证改进"的复盘分析师...

## 硬性规则（必须遵守）
1) ...
2) ...
...（大量规则）

## 任务（Workflow）
Step1 ...
Step2 ...
...（详细步骤）
```

**精简后**（约 500 字符）：
```markdown
你是群聊分析专家。分析以下聊天记录，输出结构化分析：

{{conversation_content}}

---

输出格式（Markdown）：

## 关键结论
- 结论1 [引用: 张三 2026-01-05]
- 结论2 [引用: 李四 2026-01-06]

## 重要洞察
- 洞察1
- 洞察2

## 行动建议
- [ ] 建议1
- [ ] 建议2
```

#### 2. 去除 Frontmatter

修改 `chatlog_automation.py` 中的 `load_template_and_generate_prompt` 函数：

```python
# 在读取 raw_content 后添加
import re
# 去除 frontmatter（以 --- 开始和结束的部分）
raw_content = re.sub(r'^---\s*\n.*?\n---\s*\n', '', raw_content, flags=re.DOTALL)
# 去除标题（# 与 xx 的聊天记录）
raw_content = re.sub(r'^# 与.*?的聊天记录\s*\n', '', raw_content, flags=re.MULTILINE)
# 去除时间范围行
raw_content = re.sub(r'^时间范围：.*?\n', '', raw_content, flags=re.MULTILINE)
# 去除 "## 对话内容" 标题
raw_content = re.sub(r'^## 对话内容\s*\n', '', raw_content, flags=re.MULTILINE)
```

这样可以节省约 300-400 字符。

#### 3. 分段处理

对于长周期数据，分段处理：

```bash
# 脚本示例：每2天分析一次
for start in {01..06..2}; do
  python -m rpa_llm.chatlog_automation \
    --talker "xx群-2025" \
    --start "2026-01-$start" \
    --end "2026-01-$(($start + 1))" \
    --model-version 5.2thinking
done
```

---

## 📈 效果对比

### 优化前

```
Prompt 长度: 12541 字符
- 模板: 1421 字符
- Frontmatter: 150 字符
- 标题: 80 字符
- 聊天内容: 10890 字符

结果: ❌ 超过 10000 字符限制，被截断
```

### 优化后

```
Prompt 长度: 9500 字符
- 模板: 500 字符（精简 65%）
- Frontmatter: 0 字符（已移除）
- 标题: 0 字符（已移除）
- 聊天内容: 9000 字符

结果: ✅ 在限制内，完整输入
```

**节省**: ~3000 字符（24%）

---

## 🎯 最佳实践

### 日常使用建议

1. **短周期分析**：
   - ✅ 每天分析前一天的内容
   - ✅ 或每2-3天分析一次
   
2. **精简模板**：
   - ✅ 只保留核心指令
   - ✅ 移除示例和冗余说明
   
3. **监控字符数**：
   - ✅ 运行前检查日志中的 prompt 长度
   - ✅ 超过 9000 字符时考虑优化

### 周度/月度分析

对于需要长周期分析的场景：

1. **分段 + 汇总**：
   ```
   Day1-2 → Summary1
   Day3-4 → Summary2
   Day5-7 → Summary3
   ↓
   Summaries → Final Report
   ```

2. **使用 Thinking 或 Instant 模式**：
   - 更快的响应
   - 可能对输入长度更宽容

---

## 🔍 故障排查

### 问题：输入被截断

**症状**：
- 日志显示：`⚠️  警告: 输入可能被截断！`
- ChatGPT 说没有收到完整内容

**解决**：
1. 检查 prompt 总长度：`grep "Prompt 生成完成" logs/*.log`
2. 如果超过 10000，执行上述优化方案
3. 重新运行

### 问题：优化后仍然超限

**症状**：
- 即使优化了模板，仍然超过 10K

**解决**：
1. 缩短日期范围（从7天改为2-3天）
2. 或使用方案 4（文件上传）
3. 或考虑 API 模式

### 问题：如何验证是否截断

**方法 1**：查看日志

```bash
grep -A2 "警告.*截断" logs/driver_*.log
```

**方法 2**：查看 ChatGPT 界面

查看发送的消息，如果显示 `<prompt>` 标签或内容不完整，说明被截断了。

---

## 📚 相关资源

- [CHATGPT_MODEL_VERSIONS.md](./CHATGPT_MODEL_VERSIONS.md) - 模型版本选择
- [chatlog_automation.yaml](./chatlog_automation.yaml) - 配置文件
- [templates/chatlog_for_wechat.md](./templates/chatlog_for_wechat.md) - Prompt 模板

---

## 💡 未来改进

可能的长期解决方案：

1. **实现文件上传功能**
   - 自动化点击上传按钮
   - 支持大文件分析

2. **API 模式支持**
   - 使用 OpenAI API
   - 无字符限制
   - 更稳定

3. **智能分段**
   - 自动检测内容长度
   - 按话题或时间自动分段
   - 生成汇总报告

4. **内容压缩**
   - 提取关键信息
   - 移除冗余对话
   - 智能摘要

---

## 🎉 总结

**当前推荐方案**：
1. ✅ 精简 prompt 模板（500 字符以内）
2. ✅ 去除 raw 文件的 frontmatter 和标题
3. ✅ 分析周期控制在 2-3 天
4. ✅ 监控日志中的字符数警告

**这样可以确保绝大多数场景都在 10K 限制内！** 🎊

