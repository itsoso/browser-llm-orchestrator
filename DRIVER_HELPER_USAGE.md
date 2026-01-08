# Driver Server 辅助工具使用指南

本项目提供两个便捷工具，用于自动检查和启动 Driver Server，避免手动管理的繁琐。

---

## 🛠️ 工具概览

### 1. `ensure_driver.py` - Python 跨平台脚本

功能：
- ✅ 自动检查 Driver Server 运行状态
- 🚀 如未运行则自动启动（前台/后台）
- ⏳ 等待服务就绪
- 📊 显示详细健康信息

适用场景：
- 需要跨平台支持（Windows/macOS/Linux）
- 需要精确控制启动参数
- 需要集成到其他 Python 脚本

### 2. `run_with_driver.sh` - Bash 包装脚本

功能：
- ✅ 自动检查并启动 Driver Server
- 🚀 在后台运行 Driver Server
- 📝 自动创建日志文件
- 🔄 透明执行用户命令

适用场景：
- macOS/Linux 环境
- 需要一行命令搞定所有事
- 包装复杂的命令行调用

---

## 📖 详细用法

### `ensure_driver.py` 使用方法

#### 1. 基本检查

```bash
# 只检查状态，不启动
python ensure_driver.py --brief ./brief.yaml
```

**输出示例**：
```
============================================================
🔍 Driver Server 健康检查
============================================================
✅ Driver Server 正在运行
📍 URL: http://127.0.0.1:27125
📍 站点: chatgpt, gemini
```

#### 2. 后台启动并等待

```bash
# 如未运行则后台启动，并等待就绪
python ensure_driver.py --brief ./brief.yaml --background --wait
```

**输出示例**：
```
============================================================
🔍 Driver Server 健康检查
============================================================
❌ Driver Server 未运行
错误: [Errno 61] Connection refused

🚀 在后台启动 Driver Server...
📝 日志文件: logs/driver_20260107_193045.log
✅ Driver Server 已启动 (PID: 12345)

⏳ 等待 Driver Server 就绪 (最多 60 秒)...
.....
✅ Driver Server 已就绪！
📍 站点: chatgpt, gemini
```

#### 3. 前台启动（交互式）

```bash
# 前台运行，需要确认
python ensure_driver.py --brief ./brief.yaml
```

**输出示例**：
```
============================================================
🔍 Driver Server 健康检查
============================================================
❌ Driver Server 未运行
错误: [Errno 61] Connection refused

是否启动 Driver Server? [Y/n]: y

🚀 启动 Driver Server (前台模式)...
提示: Ctrl+C 停止服务
------------------------------------------------------------
[Driver Server 日志输出...]
```

#### 4. 自定义参数

```bash
# 指定 URL 和超时时间
python ensure_driver.py \
  --brief ./brief.yaml \
  --url http://127.0.0.1:27125 \
  --timeout 120 \
  --background \
  --wait
```

**参数说明**：
- `--brief`: Brief 配置文件路径（必需）
- `--url`: Driver Server URL（默认：http://127.0.0.1:27125）
- `--wait`: 等待服务就绪
- `--background`: 后台启动
- `--timeout`: 等待超时时间，单位秒（默认：60）

---

### `run_with_driver.sh` 使用方法

#### 1. 运行 CLI 命令

```bash
# 自动确保 Driver Server 运行，然后执行 CLI
./run_with_driver.sh python -m rpa_llm.cli --brief ./brief.yaml
```

**输出示例**：
```
========================================
🔍 Driver Server 自动检查
========================================
❌ Driver Server 未运行
🚀 启动 Driver Server...
✅ Driver Server 已启动 (PID: 12345)
📝 日志文件: logs/driver_20260107_193045.log
⏳ 等待 Driver Server 就绪...
.....
✅ Driver Server 已就绪！

========================================
🚀 运行命令
========================================
命令: python -m rpa_llm.cli --brief ./brief.yaml

[CLI 输出...]

========================================
✅ 命令执行完成 (退出码: 0)
========================================
```

#### 2. 运行 Chatlog 自动化

```bash
# 分析群聊记录
./run_with_driver.sh python -m rpa_llm.chatlog_automation \
  --talker "xx群-2025" \
  --start 2026-01-01 \
  --end 2026-01-07 \
  --config ./chatlog_automation.yaml
```

#### 3. 运行其他 Python 脚本

```bash
# 运行自定义脚本
./run_with_driver.sh python my_script.py --arg1 value1
```

#### 4. 环境变量配置

```bash
# 自定义 Driver Server URL
RPA_DRIVER_URL=http://127.0.0.1:8080 ./run_with_driver.sh python -m rpa_llm.cli

# 自定义 Brief 文件
BRIEF_FILE=./custom_brief.yaml ./run_with_driver.sh python -m rpa_llm.cli
```

**支持的环境变量**：
- `RPA_DRIVER_URL`: Driver Server URL（默认：http://127.0.0.1:27125）
- `BRIEF_FILE`: Brief 配置文件路径（默认：./brief.yaml）

---

## 🔄 对比与选择

| 特性 | `ensure_driver.py` | `run_with_driver.sh` |
|------|-------------------|---------------------|
| **跨平台** | ✅ Windows/macOS/Linux | ⚠️ 仅 macOS/Linux |
| **交互式启动** | ✅ 支持 | ❌ 不支持 |
| **后台启动** | ✅ 支持 | ✅ 自动后台 |
| **命令包装** | ❌ 不支持 | ✅ 支持 |
| **日志管理** | ✅ 自定义路径 | ✅ 自动生成 |
| **依赖** | Python + httpx/urllib | Bash + curl |

**推荐使用**：
- **Windows 用户**：使用 `ensure_driver.py`
- **macOS/Linux 用户**：使用 `run_with_driver.sh`（更简洁）
- **CI/CD 环境**：使用 `ensure_driver.py --background --wait`

---

## 🧪 典型使用场景

### 场景1：开发调试

```bash
# 启动 Driver Server 并在前台运行，方便查看日志
python ensure_driver.py --brief ./brief.yaml
```

### 场景2：自动化脚本

```bash
# 在自动化脚本中使用，确保服务运行
python ensure_driver.py --brief ./brief.yaml --background --wait

# 然后运行你的自动化任务
python -m rpa_llm.chatlog_automation --talker "xx群" --start 2026-01-01 --end 2026-01-07
```

### 场景3：一键执行（推荐）

```bash
# 一行命令搞定所有事情
./run_with_driver.sh python -m rpa_llm.chatlog_automation \
  --talker "xx群" \
  --start 2026-01-01 \
  --end 2026-01-07
```

### 场景4：CI/CD 集成

```yaml
# GitHub Actions 示例
steps:
  - name: 启动 Driver Server
    run: |
      python ensure_driver.py \
        --brief ./brief.yaml \
        --background \
        --wait \
        --timeout 120
  
  - name: 运行测试
    run: |
      python -m pytest tests/
```

---

## ⚠️ 注意事项

1. **虚拟环境**：确保在正确的虚拟环境中运行
   ```bash
   source .venv/bin/activate  # macOS/Linux
   .venv\Scripts\activate     # Windows
   ```

2. **日志文件**：后台运行时，日志会保存到 `logs/driver_*.log`
   ```bash
   # 查看最新日志
   tail -f logs/driver_*.log
   ```

3. **端口占用**：确保 27125 端口未被占用
   ```bash
   # 检查端口
   lsof -i :27125  # macOS/Linux
   ```

4. **权限问题**：`run_with_driver.sh` 需要可执行权限
   ```bash
   chmod +x run_with_driver.sh
   ```

---

## 🐛 故障排查

### 问题1：启动超时

**症状**：
```
⏳ 等待 Driver Server 就绪 (最多 60 秒)...
...............................
❌ 超时: Driver Server 未能在 60 秒内就绪
```

**解决方案**：
1. 检查日志文件：`tail -f logs/driver_*.log`
2. 确认浏览器驱动已安装：`playwright install chromium`
3. 增加超时时间：`--timeout 120`

### 问题2：端口被占用

**症状**：
```
OSError: [Errno 48] Address already in use
```

**解决方案**：
```bash
# 查找占用进程
lsof -i :27125

# 杀死进程
kill -9 <PID>
```

### 问题3：权限不足

**症状**：
```
bash: ./run_with_driver.sh: Permission denied
```

**解决方案**：
```bash
chmod +x run_with_driver.sh
```

---

## 📚 相关文档

- [README.md](./README.md) - 项目总览
- [CLI_USAGE_EXAMPLES.md](./CLI_USAGE_EXAMPLES.md) - CLI 详细用法
- [CHATLOG_USAGE_EXAMPLE.md](./CHATLOG_USAGE_EXAMPLE.md) - Chatlog 集成示例

---

## 🎯 下一步

1. **首次使用**：先运行预热命令
   ```bash
   python warmup.py chatgpt
   python warmup.py gemini
   ```

2. **启动服务**：使用辅助工具
   ```bash
   python ensure_driver.py --brief ./brief.yaml --background --wait
   ```

3. **运行任务**：执行你的自动化任务
   ```bash
   python -m rpa_llm.cli --brief ./brief.yaml
   ```

祝使用愉快！🎉

