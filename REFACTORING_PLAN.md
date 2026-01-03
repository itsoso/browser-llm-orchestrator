# ChatGPT Adapter 重构计划

## 目标
将 `chatgpt.py` (2648 行) 拆分为多个独立模块，减少文件行数，提高可维护性和可扩展性。

## 已完成的模块

### 1. `chatgpt_model.py` - 模型版本选择模块
**功能**: 处理模型版本的选择和切换逻辑
- `ChatGPTModelSelector` 类
- `_desired_variant()` - 确定所需的变体类型
- `_set_thinking_toggle()` - 设置 thinking toggle
- `_select_model_menu_item()` - 从下拉菜单选择模型
- `ensure_variant()` - 设置模型版本

**行数**: ~300 行

### 2. `chatgpt_state.py` - 状态检测模块
**功能**: 检测 ChatGPT 页面的各种状态
- `ChatGPTStateDetector` 类
- `assistant_count()` - 获取 assistant 消息数量
- `user_count()` - 获取用户消息数量
- `last_assistant_text()` - 获取最后一条 assistant 消息
- `get_assistant_text_by_index()` - 根据索引获取消息
- `is_generating()` - 检查是否正在生成
- `is_thinking()` - 检查是否在思考模式

**行数**: ~200 行

### 3. `chatgpt_textbox.py` - 输入框操作模块
**功能**: 处理输入框的查找、定位和准备
- `ChatGPTTextboxFinder` 类
- `find_textbox_any_frame()` - 多 frame 查找输入框
- `try_find_in_frame()` - 在指定 frame 中查找
- `ready_check_textbox()` - 检查输入框是否就绪
- `fast_ready_check()` - 快速路径检查
- `dismiss_overlays()` - 关闭浮层/菜单
- `is_cloudflare()` - 检测 Cloudflare 验证
- `ensure_ready()` - 确保页面就绪

**行数**: ~250 行

## 待完成的模块

### 4. `chatgpt_send.py` - 发送模块（待创建）
**功能**: 处理 prompt 的发送逻辑
- `ChatGPTSender` 类
- `_send_prompt()` - 发送 prompt（900+ 行，需要拆分）
- `_trigger_send_fast()` - 快路径发送
- `_fast_send_confirm()` - 快速确认发送成功
- `_arm_input_events()` - 触发输入事件

**预计行数**: ~1000 行（需要进一步拆分）

### 5. `chatgpt_wait.py` - 等待和稳定化模块（可选）
**功能**: 处理等待 assistant 回复和输出稳定化的逻辑
- `ChatGPTWaiter` 类
- `wait_for_assistant_message()` - 等待 assistant 消息出现
- `wait_for_content()` - 等待消息内容出现
- `wait_for_stabilization()` - 等待输出稳定

**预计行数**: ~300 行

## 重构后的主文件结构

重构后的 `chatgpt.py` 将：
1. 导入所有模块
2. 在 `__init__` 中初始化各个模块
3. 通过委托方法调用各个模块的功能
4. 保留核心的 `ask()` 方法作为主要入口

**预计行数**: ~500 行（相比原来的 2648 行，减少约 80%）

## 实施步骤

1. ✅ 创建 `chatgpt_model.py`
2. ✅ 创建 `chatgpt_state.py`
3. ✅ 创建 `chatgpt_textbox.py`
4. ⏳ 创建 `chatgpt_send.py`（需要拆分 `_send_prompt` 方法）
5. ⏳ 可选：创建 `chatgpt_wait.py`
6. ⏳ 重构主文件 `chatgpt.py`，整合所有模块
7. ⏳ 测试确保功能正常
8. ⏳ 更新导入和依赖

## 注意事项

1. **向后兼容**: 确保重构后的代码与现有代码兼容
2. **测试**: 重构后需要全面测试所有功能
3. **性能**: 确保模块化不会影响性能
4. **文档**: 更新相关文档说明新的模块结构

## 下一步

由于 `_send_prompt` 方法非常长（900+ 行），建议：
1. 先完成主文件的重构，保留 `_send_prompt` 在原文件中
2. 后续再逐步将 `_send_prompt` 拆分到 `chatgpt_send.py`
3. 或者将 `_send_prompt` 进一步拆分为多个小方法

