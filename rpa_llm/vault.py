# -*- coding: utf-8 -*-
"""
Author: xiaofan with Codex Cusor
Created: 2025-12-29 20:27:11 +0800
Modified: 2025-12-29 20:27:11 +0800
"""
# rpa_llm/vault.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .utils import ensure_dir, slugify, utc_now_iso


def _yaml_list(values: List[str]) -> str:
    lines = []
    for v in values:
        v2 = v.replace('"', '\\"')
        lines.append(f'  - "{v2}"')
    return "\n".join(lines)


def write_markdown(path: Path, frontmatter: Dict[str, Any], body: str) -> None:
    ensure_dir(path.parent)
    fm_lines = ["---"]
    for k, v in frontmatter.items():
        if isinstance(v, list):
            fm_lines.append(f"{k}:")
            fm_lines.append(_yaml_list([str(x) for x in v]))
        else:
            if v is None:
                fm_lines.append(f"{k}:")
            else:
                v2 = str(v).replace('"', '\\"')
                fm_lines.append(f'{k}: "{v2}"')
    fm_lines.append("---")
    content = "\n".join(fm_lines) + "\n\n" + body.strip() + "\n"
    path.write_text(content, encoding="utf-8")


def make_run_paths(vault_path: Path, root_dir: str, run_id: str) -> Dict[str, Path]:
    run_root = vault_path / root_dir / run_id
    return {
        "run_root": run_root,
        "raw": run_root / "00_raw",
        "model": run_root / "03_model_runs",
        "synth": run_root / "04_synthesis",
        "final": run_root / "05_final",
    }


def build_run_index_note(run_id: str, topic: str, tags: List[str]) -> str:
    created = utc_now_iso()
    return (
        "# Research Run {run_id}\n\n"
        "- **Topic**: {topic}\n"
        "- **Created (UTC)**: {created}\n\n"
        "## Artifacts\n"
        "- `00_raw/` 原始输入\n"
        "- `03_model_runs/` 各站点原始输出\n"
        "- `04_synthesis/` 融合与断言矩阵\n"
        "- `05_final/` 最终结论\n\n"
        "## Next\n"
        "- 检查各模型输出质量\n"
        "- 进行自动仲裁与总结\n"
    ).format(run_id=run_id, topic=topic, created=created)


def model_output_note_body(prompt: str, answer: str) -> str:
    return (
        "## Prompt\n\n"
        "```text\n"
        f"{prompt.strip()}\n"
        "```\n\n"
        "## Answer (Raw)\n\n"
        f"{answer.strip()}\n"
    )


def make_model_output_filename(topic: str, stream_id: str, site_id: str) -> str:
    return f"{slugify(topic)}__{stream_id}__{site_id}.md"
