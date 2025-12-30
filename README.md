# Browser LLM Orchestrator (Playwright)

source .venv/bin/activate

## Install
```bash
pip install -r requirements.txt


##打开单独环境
source .venv/bin/activate


## Server打开，确保浏览器性能
PYTHONUNBUFFERED=1 python -u -m rpa_llm.driver_server --sites chatgpt,gemini,perplexity,grok,qianwen --port 27125 --profiles-root profiles


## 使用
export RPA_DRIVER_URL="http://127.0.0.1:27125";PYTHONUNBUFFERED=1 python -u -m rpa_llm.cli --brief ./brief.yaml