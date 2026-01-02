# 日志分析报告 - driver_20260103_005748.log

## 执行时间线

```
00:57:48 - driver_server 启动
00:57:56 - prewarm 完成，开始监听
00:59:39 - 收到第一个请求 (prompt_len=8863, new_chat=True)
00:59:39 - 开始处理第一个请求
00:59:52 - 模型选择完成（耗时 13 秒）
00:59:56 - 收到第二个请求 (prompt_len=15777, new_chat=True) ⚠️ 第一个请求还在处理中
01:00:01 - 第一个请求开始 new_chat（等待锁释放）
01:00:16 - new_chat 完成，开始发送 prompt
01:00:27 - prompt 输入完成，开始发送
01:00:38 - 发送确认失败（高频轮询失败）
01:00:42 - 发送阶段完成（耗时 26.22 秒）
01:00:49 - 等待回复超时（wait_for_function 2秒超时）
01:01:21 - 触发 MANUAL CHECKPOINT（39秒后）
```

## 发现的隐患

### 1. ⚠️ 并发请求处理问题

**问题描述**：
- 00:59:39 收到第一个请求，开始处理
- 00:59:56 收到第二个请求（第一个请求还在处理中）
- 第二个请求的 `prompt_len=15777`，但实际应该是第一个请求的 prompt（8863）

**可能原因**：
- 日志记录时机问题：第二个请求的日志可能在解析 payload 时记录，但此时第一个请求的 prompt 还在内存中
- 或者第二个请求确实有更长的 prompt，但时间戳显示它在第一个请求处理期间到达

**影响**：
- 如果第二个请求的 prompt_len 记录错误，可能导致调试困难
- 并发请求会排队等待锁，但日志没有明确显示等待状态

**建议修复**：
- 在日志中明确标记请求的接收时间和开始处理时间
- 添加请求 ID 或序列号，便于追踪
- 在等待锁时记录日志

### 2. ⚠️ 发送确认失败率高

**问题描述**：
```
01:00:27 - 开始发送（Control+Enter）
01:00:38 - 高频轮询失败（11秒后）
01:00:38 - 第一次 Control+Enter 未确认
01:00:42 - 快路径失败，跳过按钮尝试
```

**分析**：
- 高频轮询设置了 0.8 秒超时（160次 × 0.005秒）
- 但实际等待了 11 秒才失败，说明可能在某个地方阻塞了
- `_fast_send_confirm` 的超时时间只有 50ms，可能太短

**可能原因**：
- `page.evaluate` 调用可能在某些情况下阻塞
- 发送确认的检查逻辑可能不够健壮
- 新聊天后，页面状态可能不稳定，导致确认失败

**建议修复**：
- 增加 `_fast_send_confirm` 的超时时间（从 50ms 增加到 200-500ms）
- 在发送确认失败时，增加重试逻辑
- 添加更详细的日志，记录每次检查的结果

### 3. ⚠️ new_chat 执行时间过长

**问题描述**：
```
01:00:01 - new_chat: best effort
01:00:16 - ensure_ready: start（15秒后）
```

**分析**：
- `new_chat()` 方法执行后，等待了 15 秒才继续
- 这可能是因为新聊天需要页面加载时间
- 但代码中 `new_chat()` 后只等待了 1.5 秒，可能不够

**建议修复**：
- 在 `new_chat()` 后增加更智能的等待逻辑
- 使用 `wait_for_load_state` 等待页面加载完成
- 添加超时保护，避免无限等待

### 4. ⚠️ 等待回复超时

**问题描述**：
```
01:00:42 - 开始等待回复
01:00:49 - wait_for_function 超时（2秒）
01:01:21 - 触发 MANUAL CHECKPOINT（39秒后）
```

**分析**：
- `wait_for_function` 的超时时间只有 2 秒，可能太短
- 对于 ChatGPT Pro 的思考模式，2 秒可能不够
- 最终触发 manual checkpoint，说明确实没有收到回复

**可能原因**：
- ChatGPT Pro 在思考模式下，可能需要更长时间才开始输出
- 页面可能卡在某个状态（如 Cloudflare 验证）
- 网络问题导致请求未发送成功

**建议修复**：
- 增加 `wait_for_function` 的超时时间（从 2 秒增加到 5-10 秒）
- 在等待回复时，定期检查页面状态（如 Cloudflare、登录提示等）
- 添加更详细的错误诊断信息

### 5. ⚠️ HTTP 连接超时风险

**问题描述**：
- `driver_client.py` 中的 `urllib.request.urlopen` 使用 `timeout=timeout_s + 30`
- 如果任务超时时间很长（如 1200 秒），HTTP 连接也会等待很长时间
- 如果连接在等待期间断开，可能导致请求丢失

**建议修复**：
- 使用 HTTP keep-alive 或心跳机制
- 在长时间任务中，定期发送心跳包
- 添加连接重试逻辑

## 代码重构建议

### 1. 请求追踪和日志改进

**当前问题**：
- 日志中没有请求 ID，难以追踪并发请求
- 没有明确标记请求的接收时间和开始处理时间

**重构建议**：
```python
# 在 driver_server.py 中
import uuid

async def _handle_conn(self, reader, writer):
    request_id = str(uuid.uuid4())[:8]
    recv_time = time.time()
    
    # 记录请求接收
    print(f"[{beijing_now_iso()}] [driver] request {request_id} recv | ...")
    
    async with rt.lock:
        wait_time = time.time() - recv_time
        if wait_time > 0.1:
            print(f"[{beijing_now_iso()}] [driver] request {request_id} waited {wait_time:.2f}s for lock")
        
        start_time = time.time()
        print(f"[{beijing_now_iso()}] [driver] request {request_id} start processing | ...")
```

### 2. 发送确认逻辑优化

**当前问题**：
- `_fast_send_confirm` 的超时时间太短（50ms）
- 发送确认失败后，没有足够的重试逻辑

**重构建议**：
```python
async def _trigger_send_fast(self, user0: int) -> None:
    # 第一次尝试
    await self.page.keyboard.press("Control+Enter")
    
    # 增加确认超时时间
    if await self._fast_send_confirm(user0, timeout_ms=500):  # 从 50ms 增加到 500ms
        return
    
    # 如果失败，等待一下再重试
    await asyncio.sleep(0.5)
    await self.page.keyboard.press("Control+Enter")
    
    # 再次确认
    if await self._fast_send_confirm(user0, timeout_ms=1000):  # 增加到 1 秒
        return
    
    # 如果还是失败，抛出异常
    raise RuntimeError("send not confirmed after 2 attempts")
```

### 3. new_chat 等待逻辑优化

**当前问题**：
- `new_chat()` 后只等待 1.5 秒，可能不够
- 没有等待页面加载完成

**重构建议**：
```python
async def new_chat(self) -> None:
    self._log("new_chat: best effort")
    await self.try_click(self.NEW_CHAT, timeout_ms=2000)
    
    # 等待页面加载完成
    try:
        await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
        await self.page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass  # 超时不影响继续
    
    # 等待输入框出现
    await asyncio.sleep(1.0)  # 从 1.5 秒减少到 1.0 秒，因为上面已经等待了页面加载
    
    # 关闭可能的弹窗/遮罩
    await self._dismiss_overlays()
    await asyncio.sleep(0.5)
```

### 4. 等待回复逻辑优化

**当前问题**：
- `wait_for_function` 的超时时间只有 2 秒，可能太短
- 没有定期检查页面状态

**重构建议**：
```python
# 在 ask() 方法中
assistant_wait_timeout = min(remaining * 0.2, 30)  # 从 15 秒增加到 30 秒

# wait_for_function 的超时时间
wait_timeout_ms = int(min(assistant_wait_timeout, 10) * 1000)  # 从 2 秒增加到 10 秒

# 在等待期间，定期检查页面状态
while time.time() - t1 < assistant_wait_timeout:
    # 检查是否有 Cloudflare 或其他阻塞
    if await self._is_cloudflare():
        await self.manual_checkpoint("检测到 Cloudflare 验证", ...)
    
    # 继续等待回复
    ...
```

### 5. 错误处理和诊断改进

**当前问题**：
- 错误信息不够详细
- 没有提供足够的诊断信息

**重构建议**：
```python
# 在发送确认失败时
if not confirmed:
    # 检查页面状态
    page_state = await self._check_page_state()
    self._log(f"send: confirmation failed, page_state={page_state}")
    
    # 保存截图用于调试
    await self.save_artifacts("send_confirmation_failed")
    
    # 提供更详细的错误信息
    raise RuntimeError(
        f"send not confirmed after {attempts} attempts. "
        f"Page state: {page_state}. "
        f"Check artifacts for screenshots."
    )
```

### 6. 并发请求队列管理

**当前问题**：
- 没有请求队列管理
- 没有请求优先级
- 没有请求超时保护

**重构建议**：
```python
# 在 driver_server.py 中
from collections import deque
import asyncio

class RequestQueue:
    def __init__(self, max_size: int = 100):
        self.queue = deque()
        self.max_size = max_size
    
    async def put(self, request):
        if len(self.queue) >= self.max_size:
            raise RuntimeError("Request queue is full")
        self.queue.append(request)
    
    async def get(self):
        if not self.queue:
            return None
        return self.queue.popleft()

# 在 DriverServer 中
self._request_queues: Dict[str, RequestQueue] = {}

# 在 _handle_conn 中
if len(self._request_queues[site_id].queue) > 10:
    await self._write_json(writer, 503, {
        "ok": False,
        "error": "Request queue is full, please retry later"
    })
    return
```

## 性能优化建议

### 1. 减少不必要的等待

- `new_chat()` 后的等待时间可以优化
- 发送确认的超时时间可以调整
- 等待回复的超时时间可以增加

### 2. 增加并发处理能力

- 考虑为每个 site 创建多个 adapter 实例（如果支持）
- 使用连接池管理 HTTP 连接
- 实现请求队列和优先级

### 3. 改进错误恢复

- 发送确认失败时，增加重试逻辑
- 等待回复超时时，检查页面状态
- 提供更详细的错误诊断信息

## 总结

主要隐患：
1. ⚠️ 并发请求处理缺乏明确的追踪和日志
2. ⚠️ 发送确认失败率高，需要优化确认逻辑
3. ⚠️ new_chat 执行时间过长，需要优化等待逻辑
4. ⚠️ 等待回复超时，需要增加超时时间和状态检查
5. ⚠️ HTTP 连接超时风险，需要改进连接管理

建议优先修复：
1. **P0**: 优化发送确认逻辑（增加超时时间，添加重试）
2. **P0**: 优化 new_chat 等待逻辑（等待页面加载完成）
3. **P1**: 增加请求追踪和日志（添加请求 ID）
4. **P1**: 优化等待回复逻辑（增加超时时间，添加状态检查）
5. **P2**: 改进错误处理和诊断（提供更详细的错误信息）

