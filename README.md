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

```bash
export RPA_DRIVER_URL="http://127.0.0.1:27125"
PYTHONUNBUFFERED=1 python -u -m rpa_llm.cli --brief ./brief.yaml
```

或者直接在 brief.yaml 中配置 `driver_url`，无需设置环境变量。