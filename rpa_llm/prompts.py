# -*- coding: utf-8 -*-
"""
Author: xiaofan with Codex Cusor
Created: 2025-12-30 15:34:36 +0800
Modified: 2025-12-31 19:09:49 +0800
"""
# rpa_llm/prompts.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
import textwrap

from .models import Brief, ModelResult


# ---------------------------
# Config
# ---------------------------

@dataclass(frozen=True)
class SynthesisPromptConfig:
    """
    Controls how synthesis/arbitration prompt is built.

    Notes:
    - keep_materials_folded: wrap model materials in <details> blocks (Obsidian-friendly).
    - max_material_chars: truncate each side materials to avoid overly long prompts.
    """
    left_site: str = "gemini"
    right_site: str = "chatgpt"
    left_label: str = "Gemini"
    right_label: str = "ChatGPT"

    keep_materials_folded: bool = True
    max_material_chars: int = 14000  # per side
    max_streams: int = 6            # safety cap

    # Rendering knobs (make prompt more readable for arbitrator model)
    material_code_fence_lang: str = "text"   # use "text" to avoid nested markdown surprises
    prefer_blockquote_context: bool = True   # render context as blockquote


# ---------------------------
# Helpers
# ---------------------------

def _safe(s: str) -> str:
    return (s or "").strip()


def _dedent(s: str) -> str:
    """
    Remove common leading indentation from multiline strings.
    Critical: prevents Markdown rendering as code blocks due to Python indentation.
    """
    return textwrap.dedent(s).strip("\n")


def _truncate(text: str, max_chars: int) -> str:
    """
    Truncate long material while keeping head+tail for context.
    """
    text = _safe(text)
    if max_chars <= 0 or len(text) <= max_chars:
        return text

    head = text[: int(max_chars * 0.6)]
    tail = text[-int(max_chars * 0.35):]
    omitted = len(text) - len(head) - len(tail)

    return _dedent(
        f"""
        {head}

        [... omitted ~{omitted} chars for brevity ...]

        {tail}
        """
    )


def _blockquote(md: str) -> str:
    """
    Render text as blockquote, preserving empty lines.
    """
    md = _safe(md)
    if not md:
        return "（无）"
    lines = md.splitlines()
    out = []
    for line in lines:
        if line.strip() == "":
            out.append(">")
        else:
            out.append(f"> {line}")
    return "\n".join(out)


def _build_questions_md(brief: Brief) -> str:
    qs = brief.questions or []
    if not qs:
        return "- （无）"
    return "\n".join([f"- {q.strip()}" for q in qs if _safe(q)])


def _folded_section(title: str, content: str) -> str:
    """
    Obsidian supports <details>. This keeps the final report clean.
    """
    content = _safe(content) or "（无）"
    return _dedent(
        f"""
        <details>
        <summary><strong>{title}</strong></summary>

        {content}

        </details>
        """
    )


def _collect_site_material(
    results: List[ModelResult],
    site_id: str,
    max_streams: int,
    fence_lang: str = "text",
) -> str:
    """
    Collect OK outputs for a site, grouped by stream. Returns a Markdown string.
    """
    ok_results = [
        r for r in results
        if r.ok and r.site_id == site_id and _safe(r.answer_text)
    ]
    if not ok_results:
        return f"（{site_id} 输出缺失或失败）"

    stream_ids = sorted({r.stream_id for r in ok_results})[:max_streams]
    parts: List[str] = []

    for sid in stream_ids:
        seg = [r for r in ok_results if r.stream_id == sid]
        combined = "\n\n".join([_safe(x.answer_text) for x in seg if _safe(x.answer_text)])
        if not combined:
            continue

        # Each stream as its own foldable block to keep prompt navigable
        block = _dedent(
            f"""
            ### Stream: `{sid}`

            ```{fence_lang}
            {combined}
            ```
            """
        )
        parts.append(block)

    return "\n\n".join(parts).strip() if parts else f"（{site_id} 输出为空）"


# ---------------------------
# Main builder
# ---------------------------

def build_dual_model_arbitration_prompt(
    brief: Brief,
    results: List[ModelResult],
    cfg: Optional[SynthesisPromptConfig] = None,
) -> str:
    """
    Build a Markdown-first arbitration prompt comparing two model outputs.
    The arbitrator should output a clean report suitable for Obsidian.
    """
    cfg = cfg or SynthesisPromptConfig()

    left_raw = _collect_site_material(
        results, cfg.left_site, cfg.max_streams, fence_lang=cfg.material_code_fence_lang
    )
    right_raw = _collect_site_material(
        results, cfg.right_site, cfg.max_streams, fence_lang=cfg.material_code_fence_lang
    )

    left_text = _truncate(left_raw, cfg.max_material_chars)
    right_text = _truncate(right_raw, cfg.max_material_chars)

    if cfg.keep_materials_folded:
        left_block = _folded_section(f"Material A: {cfg.left_label} ({cfg.left_site})", left_text)
        right_block = _folded_section(f"Material B: {cfg.right_label} ({cfg.right_site})", right_text)
    else:
        left_block = _dedent(f"## Material A: {cfg.left_label} ({cfg.left_site})\n\n{left_text}")
        right_block = _dedent(f"## Material B: {cfg.right_label} ({cfg.right_site})\n\n{right_text}")

    topic = _safe(brief.topic) or "（未命名课题）"
    questions_md = _build_questions_md(brief)

    context = _safe(brief.context)
    if cfg.prefer_blockquote_context:
        context_md = _blockquote(context) if context else "（无）"
    else:
        context_md = context if context else "（无）"

    style_rules = _dedent(
        """
        写作与排版规则（必须严格遵守，确保输出美观、专业的 Markdown 格式）：
        
        1. **输出格式**：
           - 只输出纯 Markdown 格式，不要输出任何解释性文字、代码块标记或 YAML frontmatter
           - 不要输出 "我将遵守规则"、"以下是报告" 等前言
           - 直接以 `# 最终报告：{topic}` 开始
           - 确保所有 Markdown 语法正确，能够被 Obsidian 正确渲染
        
        2. **标题层级与空行**（关键：美观的视觉层次）：
           - 一级标题：`# 标题`（仅用于主标题，前后各空两行）
           - 二级标题：`## 标题`（用于主要章节，前后各空一行）
           - 三级标题：`### 标题`（用于子章节，前后各空一行）
           - 四级标题：`#### 标题`（用于更细分的章节，前后各空一行）
           - 不要跳级（例如不要从 `#` 直接跳到 `###`）
           - 标题后必须紧跟内容，不要空行（除非是章节分隔）
        
        3. **表格格式**（确保表格美观易读）：
           - 使用标准 Markdown 表格格式：`| 列1 | 列2 | 列3 |`
           - 表头后必须有分隔行：`| --- | --- | --- |`
           - 表格前后各空一行
           - 表格内容对齐：
             * 左对齐：`|:---|` 或 `|---|`
             * 居中：`|:---:|`
             * 右对齐：`|---:|`
           - 表格内容不要过长，必要时换行使用 `<br>` 或分多行
           - 每个表格下方可用 1-2 行文字补充解释，但避免长段落
           - 表格列宽尽量均匀，内容简洁明了
        
        4. **列表格式**（确保列表清晰美观）：
           - 无序列表：`- 项目`（使用 `-` 而不是 `*`）
           - 有序列表：`1. 项目`（用于有顺序的内容）
           - 任务清单：`- [ ] 待办` 或 `- [x] 已完成`
           - 列表项前后各空一行（列表整体前后）
           - 列表项之间不空行（除非是嵌套列表）
           - 嵌套列表使用 2 个空格缩进：`  - 子项目`
           - 列表项内容保持简洁，每项不超过 2 行
        
        5. **引用格式**（突出重要观点）：
           - 使用 blockquote：`> 引用内容`
           - 多行引用：每行都以 `>` 开头
           - 标注来源：`> **来源 (A)**: 来自 Gemini 的观点...` 或 `> **来源 (B)**: 来自 ChatGPT 的观点...`
           - 引用前后各空一行
           - 引用内容保持简洁，避免过长段落
        
        6. **分隔线**（用于章节分隔）：
           - 使用 `---` 分割大章节（前后各空一行）
           - 不要过度使用分隔线（只在主要章节之间使用）
           - 分隔线前后必须各有一个空行
        
        7. **强调格式**（增强可读性）：
           - 加粗：`**文字**`（用于重要概念、关键词）
           - 斜体：`*文字*`（用于强调、术语）
           - 代码：`` `代码` ``（用于技术术语、代码、参数名）
           - 不要使用 HTML 标签（如 `<b>`, `<i>` 等）
           - 重要信息使用加粗，如：`**关键结论**：...`
        
        8. **段落与空行**（确保版面美观）：
           - 段落之间必须有一个空行
           - 段落内容保持简洁，每段不超过 5 行
           - 长段落拆分为多个短段落
           - 列表、表格、引用等块级元素前后各空一行
           - 避免连续多个空行（最多连续 2 个空行）
        
        9. **内容要求**：
           - 不确定的内容必须标注【需核验】，并给出"最小验证动作"（一句话即可）
           - 禁止把 Appendix 的材料重新复述一遍；Appendix 只用于引用与原文存档
           - 保持行文简洁，避免冗长段落
           - 使用短句和要点化表达
           - 关键数据、时间、数字使用加粗：`**3-7天**`、`**80%**`
        
        10. **特殊标记与格式**：
            - 使用 `【】` 标注需要特别注意的内容：`【需核验】`、`【关键】`
            - 使用 `()` 标注补充说明
            - 使用 `[]` 标注可选内容
            - 重要结论使用加粗：`**结论**：...`
            - 数据、百分比、时间范围使用加粗突出
        
        11. **整体美观要求**：
            - 确保所有 Markdown 语法正确，能够被正确渲染
            - 保持一致的格式风格
            - 使用适当的空行分隔，但不要过度
            - 表格、列表、引用等元素对齐整齐
            - 标题层级清晰，视觉层次分明
            - 关键信息使用加粗突出
            - 避免过长的行（建议每行不超过 100 字符）
        """
    )

    # IMPORTANT: dedent the whole prompt to avoid Markdown rendering as code block
    prompt = _dedent(
        f"""
        你是“仲裁与融合分析师”。你将收到两份分析材料（A={cfg.left_label}，B={cfg.right_label}）。
        任务：做断言级对齐与融合，产出可用于决策的最终报告（可直接落盘 Obsidian）。

        硬规则：
        1) 只基于 A/B 材料总结与推理；不确定写【需核验】。
        2) 不做平均主义：必须判断“更可信的一侧/更可信的组合”，并给出理由（逻辑完备性、可验证性、内部一致性）。
        3) 最终建议必须可执行：包含【动作 + 指标 + 验证方法】。
        4) 明确区分【共识】与【分歧】；分歧给出裁决与验证路径（最小闭环）。

        {style_rules}

        ---

        # 最终报告：{topic}

        ## 0. 背景与问题
        **背景**：
        {context_md}

        **问题清单**：
        {questions_md}

        ---

        ## 1. 执行摘要

        要求：输出 3-8 条要点，每条一句话、结论导向，尽量包含关键数字/口径/时间范围。

        格式示例（必须严格遵守）：
        - **要点1**：具体结论（包含**数据/时间/范围**）
        - **要点2**：具体结论（包含**数据/时间/范围**）
        - **要点3**：具体结论（包含**数据/时间/范围**）

        注意：每条要点前使用加粗标记要点编号，关键数据使用加粗突出。

        ---

        ## 2. 共识结论

        要求：用标准 Markdown 表格输出至少 5 条共识结论。

        表格格式（必须严格遵守，确保美观对齐）：
        | 结论 | 重要性说明 | 证据 (A/B) | 不确定性 | 置信度 |
        |:---|:---|:---|:---|:---:|
        | **共识1** | 重要性说明 | (A) 或 (B) 或 (A+B) | 不确定性说明 | **Low/Med/High** |
        | **共识2** | 重要性说明 | (A) 或 (B) 或 (A+B) | 不确定性说明 | **Low/Med/High** |

        注意：Claim 列使用加粗，Confidence 列居中并对齐，内容简洁明了。

        ---

        ## 3. 分歧点与裁决路径

        要求：用标准 Markdown 表格输出至少 3 条分歧点。

        表格格式（必须严格遵守，确保美观对齐）：
        | Divergence | A says | B says | Why diverge | How to verify | Expected resolution |
        |:---|:---|:---|:---|:---|:---|
        | **分歧1** | A 的观点 | B 的观点 | 分歧原因 | 验证方法 | 预期结果 |
        | **分歧2** | A 的观点 | B 的观点 | 分歧原因 | 验证方法 | 预期结果 |

        注意：Divergence 列使用加粗，内容保持简洁，每列内容不超过 2 行。

        ---

        ## 4. 最终结论

        ### 4.1 结论

        **结论**：一句话结论（明确站队或明确"在何条件下站队"）。

        ### 4.2 动作清单

        使用任务清单格式（前后各空一行）：
        
        - [ ] **动作1**：具体描述（可选：负责人/时间/依赖）
        - [ ] **动作2**：具体描述（可选：负责人/时间/依赖）
        - [ ] **动作3**：具体描述（可选：负责人/时间/依赖）

        ### 4.3 衡量指标

        使用无序列表格式（前后各空一行）：
        
        - **指标1**：口径/阈值/频率
        - **指标2**：口径/阈值/频率
        - **指标3**：口径/阈值/频率

        ### 4.4 验证方法：最小闭环

        使用任务清单格式（前后各空一行）：
        
        - [ ] **步骤1**：如何证伪/证实；用最少数据闭环
        - [ ] **步骤2**：如何证伪/证实；用最少数据闭环
        - [ ] **步骤3**：如何证伪/证实；用最少数据闭环

        ---

        ## 5. 置信度 & 下次review

        使用无序列表格式（前后各空一行）：
        
        - **Confidence**: **Low / Med / High**（必须给理由：数据充分性/口径一致性/多来源交叉）
        - **What would change my mind**: 列出 3 条可能改变结论的条件
        - **Review trigger / date**: 触发条件或日期

        ---

        ## 引用与原文（只放引用与原文，不要在这里再写新结论）
        {left_block}

        {right_block}

       将以上内容使用obsidian markdown格式输出，将内容放到```text```，确保全文输出。

        """
    )

    return prompt.strip()