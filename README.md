# Browser LLM Orchestrator (Playwright)

source .venv/bin/activate

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

### 方式1：从 brief.yaml 读取配置（推荐）
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

或者直接在 brief.yaml 中配置 `driver_url`，无需设置环境变量。

### 高级功能

#### 1. 指定 ChatGPT 模型版本

使用 `--model-version` 参数可以覆盖 `brief.yaml` 中的模型版本配置：

```bash
# 使用 ChatGPT 5.2 Pro
python -m rpa_llm.cli --brief ./brief.yaml --model-version 5.2pro

# 使用 ChatGPT 5.2 Instant
python -m rpa_llm.cli --brief ./brief.yaml --model-version 5.2instant

# 使用 Thinking 模式
python -m rpa_llm.cli --brief ./brief.yaml --model-version thinking
```

**支持的模型版本：**
- `5.2pro` - ChatGPT 5.2 Pro（研究级智能模型）
- `5.2instant` - ChatGPT 5.2 Instant（快速响应）
- `thinking` - Thinking 模式（深度思考）

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