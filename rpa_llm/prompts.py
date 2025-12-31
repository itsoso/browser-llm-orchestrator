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
        写作与排版规则（必须严格遵守）：
        
        1. **输出格式**：
           - 只输出纯 Markdown 格式，不要输出任何解释性文字、代码块标记或 YAML frontmatter
           - 不要输出 "我将遵守规则"、"以下是报告" 等前言
           - 直接以 `# 最终仲裁报告：{topic}` 开始
        
        2. **标题层级**：
           - 一级标题：`# 标题`（仅用于主标题）
           - 二级标题：`## 标题`（用于主要章节）
           - 三级标题：`### 标题`（用于子章节）
           - 不要跳级（例如不要从 `#` 直接跳到 `###`）
           - 标题前后各空一行
        
        3. **表格格式**：
           - 使用标准 Markdown 表格格式：`| 列1 | 列2 | 列3 |`
           - 表头后必须有分隔行：`| --- | --- | --- |`
           - 表格前后各空一行
           - 表格内容对齐：左对齐或居中对齐
           - 每个表格下方可用 1-3 行文字补充解释，但避免长段落
        
        4. **列表格式**：
           - 无序列表：`- 项目`（使用 `-` 而不是 `*`）
           - 任务清单：`- [ ] 待办` 或 `- [x] 已完成`
           - 列表项前后各空一行（列表整体前后）
           - 列表项之间不空行（除非是嵌套列表）
        
        5. **引用格式**：
           - 使用 blockquote：`> 引用内容`
           - 标注来源：`> (A) 来自 Gemini 的观点...` 或 `> (B) 来自 ChatGPT 的观点...`
           - 引用前后各空一行
        
        6. **分隔线**：
           - 使用 `---` 分割大章节（前后各空一行）
           - 不要过度使用分隔线
        
        7. **强调格式**：
           - 加粗：`**文字**`
           - 斜体：`*文字*`
           - 代码：`` `代码` ``
           - 不要使用 HTML 标签
        
        8. **内容要求**：
           - 不确定的内容必须标注【需核验】，并给出"最小验证动作"（一句话即可）
           - 禁止把 Appendix 的材料重新复述一遍；Appendix 只用于引用与原文存档
           - 保持行文简洁，避免冗长段落
           - 使用短句和要点化表达
        
        9. **特殊标记**：
           - 使用 `【】` 标注需要特别注意的内容
           - 使用 `()` 标注补充说明
           - 使用 `[]` 标注可选内容
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

        # 最终仲裁报告：{topic}

        ## 0. 背景与问题
        **背景**：
        {context_md}

        **问题清单**：
        {questions_md}

        ---

        ## 1. Executive Summary

        要求：输出 3-8 条要点，每条一句话、结论导向，尽量包含关键数字/口径/时间范围。

        格式示例：
        - 要点1：具体结论（包含数据/时间/范围）
        - 要点2：具体结论（包含数据/时间/范围）
        - 要点3：具体结论（包含数据/时间/范围）

        ---

        ## 2. Consensus（共识结论）

        要求：用标准 Markdown 表格输出至少 5 条共识结论。

        表格格式（必须严格遵守）：
        | Claim | Why it matters | Evidence (A/B) | Uncertainty | Confidence |
        | --- | --- | --- | --- | --- |
        | 共识1 | 重要性说明 | (A) 或 (B) 或 (A+B) | 不确定性说明 | Low/Med/High |
        | 共识2 | 重要性说明 | (A) 或 (B) 或 (A+B) | 不确定性说明 | Low/Med/High |

        ---

        ## 3. Divergences（分歧点与裁决路径）

        要求：用标准 Markdown 表格输出至少 3 条分歧点。

        表格格式（必须严格遵守）：
        | Divergence | A says | B says | Why diverge | How to verify | Expected resolution |
        | --- | --- | --- | --- | --- | --- |
        | 分歧1 | A 的观点 | B 的观点 | 分歧原因 | 验证方法 | 预期结果 |
        | 分歧2 | A 的观点 | B 的观点 | 分歧原因 | 验证方法 | 预期结果 |

        ---

        ## 4. Final Decision（最终结论：可执行）

        ### 4.1 Decision

        一句话结论（明确站队或明确"在何条件下站队"）。

        ### 4.2 Actions（动作清单）

        使用任务清单格式：
        - [ ] 动作1（可选：负责人/时间/依赖）
        - [ ] 动作2（可选：负责人/时间/依赖）
        - [ ] 动作3（可选：负责人/时间/依赖）

        ### 4.3 Metrics（衡量指标）

        使用无序列表格式：
        - 指标1：口径/阈值/频率
        - 指标2：口径/阈值/频率
        - 指标3：口径/阈值/频率

        ### 4.4 Verification Plan（验证方法：最小闭环）

        使用任务清单格式：
        - [ ] 步骤1（如何证伪/证实；用最少数据闭环）
        - [ ] 步骤2（如何证伪/证实；用最少数据闭环）
        - [ ] 步骤3（如何证伪/证实；用最少数据闭环）

        ---

        ## 5. Confidence & Next Review

        使用无序列表格式：
        - **Confidence**: Low / Med / High（必须给理由：数据充分性/口径一致性/多来源交叉）
        - **What would change my mind**: 列出 3 条可能改变结论的条件
        - **Review trigger / date**: 触发条件或日期

        ---

        ## Appendix: Source Materials（只放引用与原文，不要在这里再写新结论）
        {left_block}

        {right_block}
        """
    )

    return prompt.strip()