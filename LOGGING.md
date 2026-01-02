# 日志文件功能说明

## 功能概述

现在所有日志都会自动保存到文件中，方便后续分析和性能优化。

## 使用方法

### 1. 启动 driver_server

```bash
python start_driver.py --brief ./brief.yaml --check-warmup
```

日志会自动保存到 `logs/driver_YYYYMMDD_HHMMSS.log`

### 2. 运行主程序

```bash
PYTHONUNBUFFERED=1 python -u -m rpa_llm.cli --brief ./brief.yaml
```

日志会自动保存到 `logs/cli_YYYYMMDD_HHMMSS.log`

### 3. 指定日志文件路径

如果需要指定日志文件路径，可以使用 `--log-file` 参数：

```bash
# driver_server
python start_driver.py --brief ./brief.yaml --log-file ./my_driver.log

# 主程序
PYTHONUNBUFFERED=1 python -u -m rpa_llm.cli --brief ./brief.yaml --log-file ./my_cli.log
```

## 日志文件位置

- **默认位置**: `logs/` 目录
- **driver_server 日志**: `logs/driver_YYYYMMDD_HHMMSS.log`
- **主程序日志**: `logs/cli_YYYYMMDD_HHMMSS.log`

## 日志内容

日志文件包含：
- 所有控制台输出（包括时间戳）
- 性能指标（耗时、阶段等）
- 错误和警告信息
- 所有调试信息

## 使用 Agent 分析日志

1. 运行程序后，日志文件路径会显示在控制台
2. 复制日志文件路径（绝对路径）
3. 将路径粘贴到 Agent 的 Console
4. Agent 会自动读取并分析日志，提供性能优化建议

## 示例

```bash
# 运行程序
$ PYTHONUNBUFFERED=1 python -u -m rpa_llm.cli --brief ./brief.yaml
[cli] 日志文件: logs/cli_20260102_143000.log
[cli] 日志文件路径: /Users/liqiuhua/work/personal/browser-llm-orchestrator/logs/cli_20260102_143000.log
...

# 复制路径到 Agent Console
/Users/liqiuhua/work/personal/browser-llm-orchestrator/logs/cli_20260102_143000.log
```

Agent 会自动读取日志文件并分析性能问题。

