# ChatGPT 模型版本选择逻辑修复说明

## 问题描述

当 `chatlog_automation` 传入 `model_version="5.2instant"` 时，系统应该选择 ChatGPT 5.2 Instant 模型，但之前的逻辑会误判为 `pro`，导致选择了错误的模型。

## 根本原因

在 `_desired_variant()` 方法中，检查顺序有问题：

```python
# 错误的逻辑（修复前）
if "5.2" in v or "pro" in v:
    return "pro"  # 5.2instant 会在这里被匹配，返回 "pro"
if "instant" in v:
    return "instant"  # 永远不会到达这里
```

当 `model_version="5.2instant"` 时：
- `"5.2" in "5.2instant"` → `True`
- 立即返回 `"pro"`，导致选择了 Pro 模型而不是 Instant 模型

## 修复方案

### 1. 修复 `_desired_variant()` 方法

**关键改进**：先检查完整的组合匹配，再检查部分匹配

```python
# 修复后的逻辑
# 1. 优先检查完整的组合（优先级最高）
if "5.2instant" in v or "5-2-instant" in v or "5.2-instant" in v:
    return "custom"  # 需要打开模型选择器选择 5.2 Instant
if "5.2pro" in v or "5-2-pro" in v or "5.2-pro" in v:
    return "pro"  # 需要打开模型选择器选择 5.2 Pro

# 2. 再检查部分匹配（通用匹配）
if "thinking" in v:
    return "thinking"
if "instant" in v:
    return "instant"
if "pro" in v:
    return "pro"
```

### 2. 修复 `ensure_variant()` 方法

**关键改进**：对于 `5.2instant`，打开模型选择器并选择 Instant 版本

```python
# 修复后的逻辑
if v == "custom":  # 5.2instant 返回 "custom"
    # 打开模型选择器
    # 使用精确的正则表达式匹配 Instant
    pattern = re.compile(r"5[.\-]?2.*instant|instant.*5[.\-]?2|5[.\-]?2.*即时|即时.*5[.\-]?2", re.I)
    await self._select_model_menu_item(pattern, model_version="5.2instant")
```

### 3. 修复 `_select_model_menu_item()` 方法

**关键改进**：优先匹配完整的组合，避免误匹配

```python
# 修复后的逻辑
if "5.2instant" in model_version_lower:
    # 使用精确匹配，避免匹配到 "5.2 Pro"
    enhanced_pattern = re.compile(r"5[.\-]?2.*instant|instant.*5[.\-]?2|5[.\-]?2.*即时|即时.*5[.\-]?2", re.I)
elif "5.2pro" in model_version_lower:
    # 使用精确匹配，避免匹配到 "5.2 Instant"
    enhanced_pattern = re.compile(r"5[.\-]?2.*pro|pro.*5[.\-]?2|5[.\-]?2.*专业|专业.*5[.\-]?2", re.I)
```

## 测试结果

### 关键测试通过

```
✓ 5.2instant 不应该被误判为 pro
  输入: 5.2instant -> 输出: custom (期望: custom)

✓ 5.2-instant 不应该被误判为 pro
  输入: 5.2-instant -> 输出: custom (期望: custom)

✓ 5-2-instant 不应该被误判为 pro
  输入: 5-2-instant -> 输出: custom (期望: custom)
```

### 完整测试结果

| 输入 | 输出 | 说明 |
|------|------|------|
| `5.2instant` | `custom` | ✓ 需要打开模型选择器选择 Instant |
| `5.2-instant` | `custom` | ✓ 需要打开模型选择器选择 Instant |
| `5-2-instant` | `custom` | ✓ 需要打开模型选择器选择 Instant |
| `5.2pro` | `pro` | ✓ 需要打开模型选择器选择 Pro |
| `5.2-pro` | `pro` | ✓ 需要打开模型选择器选择 Pro |
| `instant` | `instant` | ✓ 只需要设置 thinking toggle |
| `thinking` | `thinking` | ✓ 只需要设置 thinking toggle |
| `pro` | `pro` | ✓ 需要打开模型选择器选择 Pro |

## 工作流程

### 当 `model_version="5.2instant"` 时：

1. **`ask()` 方法**：接收 `model_version="5.2instant"` 参数
2. **`ensure_variant()` 方法**：
   - 调用 `_desired_variant()` → 返回 `"custom"`
   - 打开模型选择器（点击模型选择按钮）
   - 调用 `_select_model_menu_item()` 选择模型
3. **`_select_model_menu_item()` 方法**：
   - 检测到 `"5.2instant"` 在 `model_version` 中
   - 使用精确的正则表达式：`r"5[.\-]?2.*instant|instant.*5[.\-]?2|5[.\-]?2.*即时|即时.*5[.\-]?2"`
   - 在下拉菜单中查找匹配的选项（如 "ChatGPT 5.2 Instant"）
   - 点击匹配的选项
4. **结果**：成功选择 ChatGPT 5.2 Instant 模型

### 当 `model_version="5.2pro"` 时：

1. **`ask()` 方法**：接收 `model_version="5.2pro"` 参数
2. **`ensure_variant()` 方法**：
   - 调用 `_desired_variant()` → 返回 `"pro"`
   - 打开模型选择器
   - 调用 `_select_model_menu_item()` 选择模型
3. **`_select_model_menu_item()` 方法**：
   - 检测到 `"5.2pro"` 在 `model_version` 中
   - 使用精确的正则表达式：`r"5[.\-]?2.*pro|pro.*5[.\-]?2|5[.\-]?2.*专业|专业.*5[.\-]?2"`
   - 在下拉菜单中查找匹配的选项（如 "ChatGPT 5.2 Pro"）
   - 点击匹配的选项
4. **结果**：成功选择 ChatGPT 5.2 Pro 模型

## 使用示例

### 通过 chatlog_automation 使用

```bash
# 使用 5.2 Instant 模型
python -m rpa_llm.chatlog_automation \
  --talker "川群-2025" \
  --start 2026-01-02 \
  --end 2026-01-02 \
  --model-version "5.2instant"

# 使用 5.2 Pro 模型
python -m rpa_llm.chatlog_automation \
  --talker "川群-2025" \
  --start 2026-01-02 \
  --end 2026-01-02 \
  --model-version "5.2pro"
```

### 通过环境变量使用

```bash
# 使用 5.2 Instant 模型
export CHATGPT_VARIANT=5.2instant
python -m rpa_llm.cli --brief ./brief.yaml

# 使用 5.2 Pro 模型
export CHATGPT_VARIANT=5.2pro
python -m rpa_llm.cli --brief ./brief.yaml
```

### 通过 brief.yaml 使用

```yaml
output:
  site_model_versions:
    chatgpt: "5.2instant"  # 或 "5.2pro"
```

## 验证方法

运行测试脚本验证逻辑：

```bash
python3 tests/test_chatgpt_model_version_logic.py
```

关键验证点：
- ✓ `5.2instant` 返回 `custom`，不会被误判为 `pro`
- ✓ `5.2pro` 返回 `pro`
- ✓ `instant` 返回 `instant`（只设置 toggle）
- ✓ `thinking` 返回 `thinking`（只设置 toggle）

## 相关文件

- `rpa_llm/adapters/chatgpt.py` - ChatGPT adapter 实现
- `tests/test_chatgpt_model_version_logic.py` - 逻辑测试脚本
- `tests/test_chatgpt_model_version.py` - 完整单元测试（需要 pytest）
- `tests/test_chatgpt_model_version_simple.py` - 简化测试脚本

## 注意事项

1. **优先级顺序很重要**：必须先检查完整的组合（如 `5.2instant`），再检查部分匹配（如 `5.2` 或 `instant`）
2. **正则表达式精确性**：使用精确的正则表达式避免误匹配（如 `5.2.*instant` 而不是 `5.2`）
3. **模型选择器**：`5.2instant` 和 `5.2pro` 都需要打开模型选择器，而单独的 `instant` 只需要设置 thinking toggle

