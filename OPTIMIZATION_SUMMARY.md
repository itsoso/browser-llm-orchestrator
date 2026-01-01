# 日志分析与优化建议总结

## 日志分析发现的主要问题

### 1. **ChatGPT `user_count()` 频繁超时** ⚠️ 已优化
**问题描述：**
- 日志显示大量 `ask: user_count() timeout, retrying...`
- 发生在确认用户消息出现时，虽然最终能检测到，但等待时间过长（最多45秒）

**根本原因：**
- `_user_count()` 方法没有超时保护，直接调用 `page.locator(sel).count()`
- 在 `ask()` 中使用 `asyncio.wait_for(..., timeout=2.0)` 包装，但页面可能还在加载

**优化方案：**
- ✅ 在 `_user_count()` 内部添加 5 秒超时保护
- ✅ 移除 `ask()` 中的额外超时包装（因为内部已有超时）
- ✅ 改进错误处理，区分超时和其他异常

**代码变更：**
```python
async def _user_count(self) -> int:
    """获取用户消息数量，带超时保护。"""
    for sel in self.USER_MSG:
        try:
            return await asyncio.wait_for(
                self.page.locator(sel).count(),
                timeout=5.0  # 5秒超时
            )
        except asyncio.TimeoutError:
            continue  # 尝试下一个选择器
        except Exception:
            continue
    return 0
```

---

### 2. **Gemini 发送检测误报** ⚠️ 已优化
**问题描述：**
- 多次出现 `send: warning - textbox still has content after button click (len=XXX), may not have sent`
- 但实际上消息已经发送（后续有响应）

**根本原因：**
- 仅依赖输入框内容清空来判断是否发送
- Gemini 可能不会立即清空输入框，导致误报

**优化方案：**
- ✅ 降低输入框清空检测的阈值（从 50% 降到 30%）
- ✅ 添加部分清空检测（30%-70% 之间也认为已发送）
- ✅ 添加响应检测作为辅助判断（等待1秒后检查是否有新响应）
- ✅ 如果无法读取输入框，假设已发送（可能是页面正在刷新）

**代码变更：**
```python
# 方法1: 检查输入框内容（降低阈值）
if textbox_len_after < textbox_len_before_send * 0.3:  # 从 0.5 降到 0.3
    sent_confirmed = True
elif textbox_len_after < textbox_len_before_send * 0.7:
    # 部分清空也认为已发送
    sent_confirmed = True

# 方法2: 检查响应是否开始（更可靠）
await asyncio.sleep(1.0)
assistant_text = await self._last_assistant_text()
if assistant_text and len(assistant_text.strip()) > 10:
    sent_confirmed = True
```

---

### 3. **ChatGPT 清理失败错误日志不完整** ⚠️ 已优化
**问题描述：**
- 日志显示 `send: JS clear failed:` （错误信息为空）
- 无法诊断问题原因

**优化方案：**
- ✅ 记录详细的错误信息，包括异常类型和消息
- ✅ 即使异常消息为空，也记录异常类型

**代码变更：**
```python
except Exception as e:
    error_msg = f"{type(e).__name__}: {str(e)}" if str(e) else f"{type(e).__name__} (no message)"
    self._log(f"send: JS clear failed: {error_msg}")
```

---

### 4. **TargetClosedError 未处理** ⚠️ 已优化
**问题描述：**
- 日志中出现 `TargetClosedError: Target page, context or browser has been closed`
- 发生在 `type()` 输入过程中，导致任务失败

**优化方案：**
- ✅ 导入 `PlaywrightError` 类型
- ✅ 在 `type()` 调用时捕获 `PlaywrightError`
- ✅ 检测 `TargetClosedError` 并转换为更友好的错误消息

**代码变更：**
```python
from playwright.async_api import Frame, Locator, Error as PlaywrightError

try:
    await tb.type(prompt, delay=0, timeout=timeout_ms)
except PlaywrightError as pe:
    if "TargetClosed" in str(pe) or "Target page" in str(pe):
        raise RuntimeError(f"Browser/page closed during input: {pe}") from pe
    raise
```

---

## 其他观察与建议

### 5. **ensure_ready 耗时过长** 💡 建议优化
**问题描述：**
- ChatGPT 第一次尝试耗时 33 秒才找到输入框
- 多次 `ensure_ready: still locating textbox...` 重试

**建议：**
- 考虑优化 `_find_textbox_any_frame()` 的搜索策略
- 增加更快的选择器优先级
- 考虑缓存输入框位置（如果页面结构稳定）

### 6. **预热阶段的手动检查点** 💡 建议优化
**问题描述：**
- Gemini 和 ChatGPT 在预热阶段都需要手动检查点
- 说明页面加载或登录状态可能不稳定

**建议：**
- 检查 `base_url` 是否正确
- 考虑增加更长的等待时间
- 检查是否需要登录或处理 Cloudflare

### 7. **并发性能** 💡 建议优化
**问题描述：**
- ChatGPT 和 Gemini 同时运行时，ChatGPT 的 `user_count()` 超时更频繁
- 可能是资源竞争导致

**建议：**
- 考虑增加重试间隔
- 检查浏览器资源使用情况
- 考虑错峰执行（如果不需要严格并发）

---

## 优化效果预期

1. **减少超时等待**：`user_count()` 超时从频繁发生减少到偶尔发生
2. **减少误报**：Gemini 发送检测误报减少，更准确判断发送状态
3. **更好的错误诊断**：错误日志更详细，便于问题定位
4. **更稳定的执行**：TargetClosedError 得到正确处理，避免意外中断

---

## 后续优化方向

1. **性能优化**：
   - 优化 `ensure_ready()` 的搜索策略
   - 考虑使用更快的选择器
   - 缓存输入框位置

2. **稳定性优化**：
   - 增加重试机制
   - 改进错误恢复
   - 优化并发控制

3. **监控优化**：
   - 添加性能指标收集
   - 记录关键操作的耗时
   - 分析失败模式

