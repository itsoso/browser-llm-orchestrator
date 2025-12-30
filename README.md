# Browser LLM Orchestrator (Playwright)

source .venv/bin/activate

## Install
```bash
pip install -r requirements.txt
```

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
PYTHONUNBUFFERED=1 python -u start_driver.py --brief ./brief.yaml
```

### 方式2：使用命令行参数（覆盖配置）
```bash
PYTHONUNBUFFERED=1 python -u -m rpa_llm.driver_server --sites chatgpt,gemini,perplexity,grok,qianwen --port 27125 --profiles-root profiles
```

## 使用

```bash
export RPA_DRIVER_URL="http://127.0.0.1:27125"
PYTHONUNBUFFERED=1 python -u -m rpa_llm.cli --brief ./brief.yaml
```

或者直接在 brief.yaml 中配置 `driver_url`，无需设置环境变量。