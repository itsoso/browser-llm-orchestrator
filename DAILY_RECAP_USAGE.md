# 每日群聊复盘功能使用指南

## 📊 功能概述

每日群聊复盘功能可以帮你自动化处理多个微信群聊的每日总结，批量生成分析报告，并支持公开分享到小程序等外部应用。

### 核心特性

1. **批量处理**：一次选择多个群聊，自动生成所有群聊的每日总结
2. **灵活配置**：支持选择不同的 LLM（ChatGPT/Gemini）和模型版本
3. **进度跟踪**：实时查看每个群聊的处理状态和进度
4. **历史管理**：保存所有批次的历史记录，方便回溯查看
5. **公开分享**：支持将复盘结果标记为公开，提供 API 接口供小程序等外部应用访问
6. **智能优化**：自动使用精简模板处理长文本，避免输入截断问题

---

## 🚀 快速开始

### 1. 启动服务

确保以下服务正在运行：

```bash
# 1. 启动 Driver Server
python start_driver.py --brief ./brief.yaml

# 2. 启动 Chatlog 服务（如果还没运行）
# 参考你的 chatlog 服务启动方式

# 3. 启动 Web 管理界面
python web_admin.py
```

然后访问：http://127.0.0.1:5050

### 2. 使用 Web 界面

1. 点击顶部的 **📊 每日复盘** 标签页
2. 选择日期（默认为今天）
3. 选择 LLM 和模型版本：
   - **ChatGPT 5.2 Instant**：快速生成（约 30 秒）
   - **ChatGPT 5.2 Thinking**：深度思考（约 3-5 分钟）
   - **ChatGPT 5.2 Pro**：最佳质量（约 5-10 分钟）
4. 如果需要公开分享，勾选"公开分享"
5. 点击"加载群聊列表"
6. 勾选你想要复盘的群聊
7. 点击"创建批次并开始处理"

### 3. 查看结果

处理完成后，可以通过以下方式查看结果：

- **Web 界面**：在"复盘历史"列表中点击批次查看详情
- **Obsidian**：复盘结果会自动保存到你的 Obsidian Vault 中
- **文件系统**：结果保存在 `data/daily_recaps/` 目录

---

## 🛠️ 命令行使用

如果你更喜欢命令行，也可以直接使用：

### 列出可用群聊

```bash
python -m rpa_llm.daily_recap --list-talkers
```

输出示例：
```
找到 5 个群聊/联系人:

  - xx群 (156 条消息)
  - 技术交流群 (89 条消息)
  - 产品讨论 (34 条消息)
  ...
```

### 创建批次

```bash
python -m rpa_llm.daily_recap \
  --create-batch "xx群" "技术交流群" \
  --date 2026-01-07 \
  --llm chatgpt \
  --model 5.2instant
```

输出示例：
```
✓ 批次已创建: 20260107_143522
  - 日期: 2026-01-07
  - 任务数: 2
  - LLM: chatgpt (5.2instant)
```

### 处理批次

```bash
python -m rpa_llm.daily_recap --process-batch 20260107_143522
```

输出示例：
```
✓ 批次处理完成: 20260107_143522
  - 状态: completed
  - xx群: completed
  - 技术交流群: completed
```

### 列出所有批次

```bash
python -m rpa_llm.daily_recap --list-batches
```

---

## 📁 数据存储

### 批次数据

批次信息存储在：
```
data/daily_recaps/batch_{batch_id}.json
```

每个批次文件包含：
- 批次ID、日期、状态
- 所有任务的详细信息
- 处理进度和时间戳
- LLM 配置信息

### 复盘结果

复盘结果存储在你的 Obsidian Vault 中（与 chatlog_automation 相同的位置）：

```
{vault_path}/10_Sources/WeChat/{talker}/{year}/{month}/周{week}/
  └── {talker} {date}.md
```

---

## 🌐 公开分享 API

### 为小程序准备

如果你创建批次时勾选了"公开分享"，可以通过以下 API 访问结果：

#### 获取公开批次结果

```bash
GET /api/recap/public/{batch_id}
```

**响应示例：**

```json
{
  "ok": true,
  "batch_id": "20260107_143522",
  "date": "2026-01-07",
  "status": "completed",
  "created_at": "2026-01-07T14:35:22+08:00",
  "completed_at": "2026-01-07T15:20:15+08:00",
  "summaries": [
    {
      "talker": "xx群",
      "date": "2026-01-07",
      "message_count": 156,
      "content": "# xx群 2026-01-07 复盘\n\n## 关键结论\n..."
    },
    {
      "talker": "技术交流群",
      "date": "2026-01-07",
      "message_count": 89,
      "content": "# 技术交流群 2026-01-07 复盘\n\n## 关键结论\n..."
    }
  ]
}
```

### 在小程序中使用

```javascript
// 微信小程序示例
wx.request({
  url: 'http://your-server:5050/api/recap/public/20260107_143522',
  method: 'GET',
  success(res) {
    if (res.data.ok) {
      const summaries = res.data.summaries;
      // 显示复盘内容
      summaries.forEach(summary => {
        console.log(`${summary.talker}: ${summary.content}`);
      });
    }
  }
});
```

**注意事项：**
- 只有标记为 `public=True` 的批次才能通过此接口访问
- 生产环境请配置适当的访问控制和认证
- 建议使用 HTTPS 加密传输

---

## ⚙️ 高级配置

### 自定义模板

如果默认的精简模板不满足需求，可以指定自定义模板：

```bash
python -m rpa_llm.daily_recap \
  --process-batch 20260107_143522 \
  --template ./templates/my_custom_template.md
```

### 超时设置

对于内容特别多的群聊，可以增加超时时间：

```bash
# API 调用
POST /api/recap/process/{batch_id}
{
  "timeout": 2400  // 40 分钟
}
```

### 批量处理策略

**推荐做法：**

1. **先测试单个群聊**：
   - 选择一个典型的群聊
   - 使用 Instant 模式快速测试
   - 确认结果符合预期

2. **逐步扩大规模**：
   - 每次处理 3-5 个群聊
   - 观察处理时间和质量
   - 根据需要调整模型版本

3. **定时批处理**：
   - 可以设置 cron job 每天自动运行
   - 建议选择非高峰时段（如凌晨）
   - 使用 Instant 模式保证速度

---

## 🔧 故障排查

### 批次一直处于 "processing" 状态

**可能原因：**
1. Driver Server 崩溃或重启
2. 某个群聊的消息量过大导致超时
3. ChatGPT 输入被截断

**解决方法：**
1. 检查 Driver Server 状态：`curl http://127.0.0.1:27125/health`
2. 查看日志：`logs/chatlog_automation_*.log`
3. 重新启动 Driver Server 并重试

### 某些任务失败

**查看失败原因：**
1. 在 Web 界面点击批次查看详情
2. 失败的任务会显示错误信息
3. 检查对应的日志文件

**常见错误：**
- `timeout`：消息量太大，尝试缩短日期范围或使用更快的模型
- `truncation`：输入超过限制，已自动使用精简模板，如仍失败可手动缩短日期
- `502 Bad Gateway`：Driver Server 未运行，启动它

### 复盘结果质量不佳

**优化建议：**
1. 使用更高级的模型（Pro > Thinking > Instant）
2. 确保日期范围合理（建议每天单独处理）
3. 检查原始聊天记录是否完整
4. 考虑自定义模板以适应特定群聊风格

---

## 📊 使用场景

### 个人知识管理

每天晚上自动复盘所有群聊，生成结构化笔记：

```bash
# 在 crontab 中添加
0 23 * * * cd /path/to/project && python -m rpa_llm.daily_recap \
  --create-batch "工作群" "学习群" "生活群" \
  --date $(date +%Y-%m-%d) \
  --llm chatgpt \
  --model 5.2instant
```

### 团队协作

每周一复盘上周所有项目群聊，分享给团队：

1. 创建批次时勾选"公开分享"
2. 将批次 ID 分享给团队成员
3. 团队成员通过 API 或小程序查看

### 内容创作

从群聊中提取有价值的讨论和灵感：

1. 使用 Pro 模式获得最高质量总结
2. 在 Obsidian 中进一步编辑整理
3. 发布到博客或公众号

---

## 🔗 与其他功能集成

### 与 Chatlog Automation 的区别

| 特性 | Chatlog Automation | Daily Recap |
|------|-------------------|-------------|
| **处理方式** | 单个群聊 | 批量群聊 |
| **使用场景** | 精细化单次分析 | 每日批量复盘 |
| **配置方式** | 命令行参数丰富 | Web 界面友好 |
| **结果管理** | 独立文件 | 批次管理 |
| **公开分享** | 不支持 | 支持 API |

### 与 Health-LLM-Driven 集成

在你的小程序项目中，可以这样集成：

```javascript
// pages/recap/recap.js
Page({
  data: {
    batches: [],
    currentSummary: null
  },
  
  onLoad() {
    this.loadBatches();
  },
  
  async loadBatches() {
    // 从你的后端获取公开批次列表
    const res = await wx.request({
      url: 'https://your-api.com/recap/public/batches',
      method: 'GET'
    });
    
    this.setData({ batches: res.data.batches });
  },
  
  async viewSummary(batchId) {
    const res = await wx.request({
      url: `https://your-api.com/api/recap/public/${batchId}`,
      method: 'GET'
    });
    
    this.setData({ currentSummary: res.data });
    wx.navigateTo({ url: '/pages/summary/summary' });
  }
});
```

---

## 📝 最佳实践

### 1. 合理安排处理时间

- **Instant 模式**：适合每日自动化，速度快
- **Thinking 模式**：适合重要讨论，质量好
- **Pro 模式**：适合周报月报，最佳质量

### 2. 控制批次大小

- 单个批次建议不超过 10 个群聊
- 每个群聊建议单日处理（避免消息量过大）
- 合理利用多个批次分批处理

### 3. 定期清理数据

```bash
# 删除 30 天前的批次数据
find data/daily_recaps -name "batch_*.json" -mtime +30 -delete
```

### 4. 备份重要结果

复盘结果保存在 Obsidian Vault 中，建议：
- 定期备份 Vault
- 使用 Git 进行版本控制
- 将重要批次导出为 PDF

---

## 🎯 后续规划

- [ ] 支持更多 LLM（Claude、Qwen 等）
- [ ] 智能推荐最佳模型版本
- [ ] 批次对比和趋势分析
- [ ] 导出为 PDF/Word
- [ ] 邮件通知功能
- [ ] 微信机器人自动发送

---

## ❓ 常见问题

**Q: 可以处理历史数据吗？**
A: 可以！在创建批次时选择任意历史日期即可。

**Q: 支持其他聊天工具吗（钉钉、Slack 等）？**
A: 目前只支持微信。如果你的 Chatlog 服务支持其他来源，理论上也可以处理。

**Q: 处理失败的任务可以重试吗？**
A: 目前需要创建新批次重试。后续版本会支持单任务重试。

**Q: 公开分享安全吗？**
A: 建议在生产环境中添加认证机制，不要直接暴露到公网。

**Q: 如何批量处理一周的数据？**
A: 可以用循环创建多个批次：
```bash
for date in 2026-01-01 2026-01-02 2026-01-03; do
  python -m rpa_llm.daily_recap --create-batch "xx群" --date $date
done
```

---

## 📧 反馈与支持

如有问题或建议，欢迎：
- 提交 Issue
- 发起 Pull Request
- 联系项目维护者

祝使用愉快！🎉
