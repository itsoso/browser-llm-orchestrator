# 日志分析：重复请求问题

## 问题描述

从日志分析，发现了一个问题：
- 01:17:27 - 第一个 ask 完成（done, len=17382）
- 01:17:27 - 立即又触发了一个新的 ask: start（prompt_len=15777）

两个请求的时间戳完全相同，但 prompt_len 不同：
- 第一个请求：prompt_len=8863
- 第二个请求：prompt_len=15777

## 可能原因

1. **用户运行了两次脚本**：最可能的原因
   - 用户可能手动运行了两次 `chatlog_automation.py`
   - 或者有某种自动化脚本调用了两次

2. **并发执行**：不太可能
   - 代码中没有并发逻辑
   - 但如果有多个进程同时运行，可能导致重复

3. **重试机制**：不太可能
   - 代码中没有自动重试逻辑
   - 只有在错误时才会重试

4. **driver_server 重试**：不太可能
   - driver_server 有重试逻辑，但只在 TargetClosedError 时触发
   - 第一个请求成功完成，不应该触发重试

## 解决方案

### 方案 1：添加请求去重机制（推荐）

在 `run_automation` 函数开始时，检查是否已经有相同的请求正在执行：

```python
# 使用文件锁或内存锁，防止重复执行
import fcntl
import os

async def run_automation(...):
    # 创建锁文件
    lock_file = Path(f"/tmp/chatlog_automation_{talker}_{start}_{end}.lock")
    
    try:
        # 尝试获取锁
        with open(lock_file, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # 执行任务
            ...
    except BlockingIOError:
        # 锁已被占用，说明有相同的请求正在执行
        print(f"[automation] ⚠️  警告: 检测到相同的请求正在执行，跳过本次执行")
        return
    finally:
        # 释放锁
        if lock_file.exists():
            lock_file.unlink()
```

### 方案 2：添加请求 ID 和日志记录

在每次请求开始时，生成唯一的请求 ID，并记录到日志中：

```python
import uuid

async def run_automation(...):
    request_id = str(uuid.uuid4())[:8]
    print(f"[automation] 请求 ID: {request_id}")
    print(f"[automation] 开始自动化流程 (request_id={request_id})")
    
    # 在调用 driver_run_task 时，也记录 request_id
    print(f"[automation] 发送到 {arbitrator_site} 进行分析 (request_id={request_id})...")
```

### 方案 3：添加执行状态检查

在执行前检查是否已经有相同的任务正在执行或已完成：

```python
async def run_automation(...):
    # 检查是否已经有相同的 summary 文件
    summary_path = get_summary_path(base_path, talker, start, end, model_version)
    if summary_path.exists():
        print(f"[automation] ⚠️  警告: 检测到已存在的 summary 文件: {summary_path}")
        print(f"[automation] 如果确实需要重新分析，请先删除该文件")
        return
```

## 建议

1. **立即实施**：添加请求 ID 和日志记录，便于追踪重复请求的来源
2. **短期实施**：添加文件锁机制，防止重复执行
3. **长期优化**：添加执行状态检查，避免重复分析相同的数据

