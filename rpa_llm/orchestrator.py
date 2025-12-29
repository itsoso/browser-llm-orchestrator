# rpa_llm/orchestrator.py
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from .adapters import create_adapter
from .models import Brief, ModelResult, StreamSpec, Task
from .utils import ensure_dir, utc_now_iso
from .vault import (
    build_run_index_note,
    make_model_output_filename,
    make_run_paths,
    model_output_note_body,
    write_markdown,
)


def _now_local_iso() -> str:
    # 本地时区（macOS 会自动取系统时区）
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _fmt_secs(s: float) -> str:
    if s < 60:
        return f"{s:.1f}s"
    if s < 3600:
        return f"{s/60:.1f}m"
    return f"{s/3600:.2f}h"


def load_brief(path: Path) -> Brief:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    streams = [StreamSpec(**s) for s in data["streams"]]
    return Brief(
        topic=data["topic"],
        context=data.get("context", ""),
        questions=list(data.get("questions", [])),
        streams=streams,
        sites=list(data.get("sites", [])),
        output=dict(data.get("output", {})),
    )


def render_prompt(stream: StreamSpec, topic: str, context: str, questions: List[str]) -> str:
    qb = "\n".join([f"- {q}" for q in questions]) if questions else "-（无）"
    return stream.prompt_template.format(
        topic=topic,
        context=context,
        questions_bullets=qb,
    ).strip()


def build_tasks(run_id: str, brief: Brief) -> List[Task]:
    tasks: List[Task] = []
    for site in brief.sites:
        for stream in brief.streams:
            prompt = render_prompt(stream, brief.topic, brief.context, brief.questions)
            tasks.append(
                Task(
                    run_id=run_id,
                    site_id=site,
                    stream_id=stream.id,
                    stream_name=stream.name,
                    prompt=prompt,
                    topic=brief.topic,
                )
            )
    return tasks


async def run_site_worker(
    site_id: str,
    tasks: List[Task],
    vault_paths: Dict[str, Path],
    profiles_root: Path,
    artifacts_root: Path,
    tags: List[str],
    headless: bool = False,
    sem: asyncio.Semaphore | None = None,
) -> List[ModelResult]:
    """
    站点内串行（tasks 顺序跑），站点间可并行（由 run_all 调度）。
    增加时间打点：每 task 的开始/结束/耗时 + rolling avg + ETA。
    """
    results: List[ModelResult] = []
    profile_dir = profiles_root / site_id
    site_artifacts = artifacts_root / site_id
    ensure_dir(profile_dir)
    ensure_dir(site_artifacts)

    adapter = create_adapter(site_id, profile_dir=profile_dir, artifacts_dir=site_artifacts, headless=headless)

    async def _run() -> List[ModelResult]:
        total = len(tasks)
        durations: List[float] = []

        print(
            f"[{site_id}] worker start | tasks={total} | local={_now_local_iso()} | utc={_now_utc_iso()}",
            flush=True,
        )

        async with adapter:
            for idx, t in enumerate(tasks, start=1):
                started_utc = utc_now_iso()
                started_local = _now_local_iso()
                t0 = time.perf_counter()

                # 站点内 ETA：用已完成任务均值估计
                avg = (sum(durations) / len(durations)) if durations else None
                eta = None
                if avg is not None:
                    remaining = total - idx + 1
                    eta = avg * remaining

                print(
                    f"[{site_id}] task {idx}/{total} start | stream={t.stream_id} "
                    f"| local={started_local} | utc={started_utc}"
                    + (f" | avg={_fmt_secs(avg)} | eta~{_fmt_secs(eta)}" if avg is not None else ""),
                    flush=True,
                )

                try:
                    answer, url = await adapter.ask(t.prompt)
                    ok = True
                    err = None
                except Exception as e:
                    answer, url = "", (adapter.page.url if adapter else "")
                    ok = False
                    err = str(e)

                t1 = time.perf_counter()
                ended_utc = utc_now_iso()
                ended_local = _now_local_iso()
                duration_s = max(0.0, t1 - t0)
                durations.append(duration_s)

                # 更新 ETA（完成后再给一次更准确的滚动均值）
                avg2 = sum(durations) / len(durations)
                remaining2 = total - idx
                eta2 = avg2 * remaining2

                print(
                    f"[{site_id}] task {idx}/{total} done | stream={t.stream_id} | ok={ok} "
                    f"| dur={_fmt_secs(duration_s)} | avg={_fmt_secs(avg2)} | eta~{_fmt_secs(eta2)} "
                    f"| local_end={ended_local} | utc_end={ended_utc}",
                    flush=True,
                )

                created = ended_utc
                if ok:
                    res = ModelResult(
                        run_id=t.run_id,
                        site_id=t.site_id,
                        stream_id=t.stream_id,
                        stream_name=t.stream_name,
                        topic=t.topic,
                        prompt=t.prompt,
                        answer_text=answer,
                        source_url=url,
                        created_utc=created,
                        ok=True,
                    )
                else:
                    res = ModelResult(
                        run_id=t.run_id,
                        site_id=t.site_id,
                        stream_id=t.stream_id,
                        stream_name=t.stream_name,
                        topic=t.topic,
                        prompt=t.prompt,
                        answer_text="",
                        source_url=url,
                        created_utc=created,
                        ok=False,
                        error=err,
                    )

                results.append(res)

                # 写入 Obsidian：每个结果一份（把时间信息写入 frontmatter）
                fname = make_model_output_filename(t.topic, t.stream_id, t.site_id)
                out_path = vault_paths["model"] / fname
                fm = {
                    "type": ["model_output"],
                    "created": res.created_utc,
                    "author": res.site_id,
                    "run_id": res.run_id,
                    "topic": res.topic,
                    "stream": f"{res.stream_id} | {res.stream_name}",
                    "url": res.source_url,
                    "ok": str(ok),
                    "started_utc": started_utc,
                    "ended_utc": ended_utc,
                    "started_local": started_local,
                    "ended_local": ended_local,
                    "duration_s": f"{duration_s:.3f}",
                    "tags": tags[:12],
                }
                body = model_output_note_body(res.prompt, res.answer_text if res.ok else f"ERROR: {res.error}")
                write_markdown(out_path, fm, body)

        print(
            f"[{site_id}] worker end | done={len(results)}/{total} | local={_now_local_iso()} | utc={_now_utc_iso()}",
            flush=True,
        )
        return results

    if sem is None:
        return await _run()

    async with sem:
        return await _run()


def _build_synthesis_prompt(brief: Brief, results: List[ModelResult]) -> str:
    ok_results = [r for r in results if r.ok and r.answer_text.strip()]
    blocks = []
    for r in ok_results:
        blocks.append(
            f"### Source: {r.site_id} / {r.stream_id} ({r.stream_name})\n"
            f"URL: {r.source_url}\n\n"
            "```text\n"
            f"{r.answer_text.strip()}\n"
            "```\n"
        )

    materials = "\n".join(blocks) if blocks else "（无可用材料）"

    return f"""
你是一个“第一性原理 + 可验证改进”的仲裁与融合分析师。
任务：对同一课题的多模型输出做“断言级融合”，产出可用于决策的最终结论与验证计划。

硬规则：
1) 只基于下方材料做总结与推理；不确定写【需核验】。
2) 输出必须可执行：每条建议都要包含【动作 + 指标 + 验证方法】。
3) 明确区分【共识结论】与【分歧点】。
4) 给出【证据质量评级】（High/Med/Low）与【置信度】（High/Med/Low）。

课题：{brief.topic}

背景：
{brief.context}

研究问题：
{chr(10).join([f"- {q}" for q in brief.questions])}

请输出两部分（Markdown）：
A) ## Claim Matrix
- 用表格列出核心断言（C1..）
- 列：Claim | Supported By | Contradicted By | Evidence Quality | Confidence | How to Verify

B) ## Final Decision
- Executive Summary（<=10行）
- Recommendation（动作/指标/验证）
- Risks & Unknowns（含【需核验】清单）
- Next Steps（可验证步骤清单）

下面是多模型材料（原文）：
{materials}
""".strip()


async def run_synthesis_and_final(
    brief: Brief,
    run_id: str,
    results: List[ModelResult],
    vault_paths: Dict[str, Path],
    profiles_root: Path,
    artifacts_root: Path,
    tags: List[str],
    headless: bool = False,
) -> None:
    site_id = "chatgpt"
    profile_dir = profiles_root / site_id
    site_artifacts = artifacts_root / f"{site_id}__synthesis"
    ensure_dir(profile_dir)
    ensure_dir(site_artifacts)

    prompt = _build_synthesis_prompt(brief, results)

    print(f"[synthesis] start | local={_now_local_iso()} | utc={_now_utc_iso()}", flush=True)
    t0 = time.perf_counter()
    started_utc = utc_now_iso()
    started_local = _now_local_iso()

    adapter = create_adapter(site_id, profile_dir=profile_dir, artifacts_dir=site_artifacts, headless=headless)
    async with adapter:
        answer, url = await adapter.ask(prompt, timeout_s=420)

    t1 = time.perf_counter()
    ended_utc = utc_now_iso()
    ended_local = _now_local_iso()
    dur = max(0.0, t1 - t0)
    print(
        f"[synthesis] done | dur={_fmt_secs(dur)} | local_end={ended_local} | utc_end={ended_utc}",
        flush=True,
    )

    synth_path = vault_paths["synth"] / "synthesis__chatgpt.md"
    fm_s = {
        "type": ["synthesis"],
        "created": ended_utc,
        "author": "chatgpt",
        "run_id": run_id,
        "topic": brief.topic,
        "url": url,
        "started_utc": started_utc,
        "ended_utc": ended_utc,
        "started_local": started_local,
        "ended_local": ended_local,
        "duration_s": f"{dur:.3f}",
        "tags": tags[:12],
    }
    write_markdown(synth_path, fm_s, answer)

    final_path = vault_paths["final"] / "final__chatgpt.md"
    fm_f = {
        "type": ["final_decision"],
        "created": ended_utc,
        "author": "chatgpt",
        "run_id": run_id,
        "topic": brief.topic,
        "url": url,
        "started_utc": started_utc,
        "ended_utc": ended_utc,
        "started_local": started_local,
        "ended_local": ended_local,
        "duration_s": f"{dur:.3f}",
        "tags": tags[:12],
    }
    write_markdown(final_path, fm_f, answer)


async def run_all(brief_path: Path, run_id: str, headless: bool = False) -> Tuple[Path, List[ModelResult]]:
    brief = load_brief(brief_path)
    vault_path = Path(brief.output["vault_path"]).expanduser().resolve()
    root_dir = brief.output.get("root_dir", "10_ResearchRuns")
    tags = list(brief.output.get("tags", []))

    vault_paths = make_run_paths(vault_path, root_dir, run_id)
    for p in vault_paths.values():
        ensure_dir(p)

    run_index_path = vault_paths["run_root"] / "README.md"
    fm = {
        "type": ["research_run"],
        "created": utc_now_iso(),
        "author": "browser-orchestrator",
        "run_id": run_id,
        "topic": brief.topic,
        "tags": tags[:12],
    }
    write_markdown(run_index_path, fm, build_run_index_note(run_id, brief.topic, tags))

    local_runs = Path("runs").resolve()
    artifacts_root = local_runs / run_id / "artifacts"
    profiles_root = Path("profiles").resolve()
    ensure_dir(artifacts_root)
    ensure_dir(profiles_root)

    tasks = build_tasks(run_id, brief)
    site_map: Dict[str, List[Task]] = {}
    for t in tasks:
        site_map.setdefault(t.site_id, []).append(t)

    total_tasks = len(tasks)
    print(
        f"[run_all] start | sites={len(site_map)} tasks={total_tasks} "
        f"| local={_now_local_iso()} | utc={_now_utc_iso()}",
        flush=True,
    )
    global_t0 = time.perf_counter()

    # 并行执行站点（站点内仍串行），并发上限建议 2~3
    max_parallel_sites = int(brief.output.get("max_parallel_sites", 2))
    sem = asyncio.Semaphore(max_parallel_sites)

    coros = []
    for site_id, ts in site_map.items():
        coros.append(
            run_site_worker(
                site_id=site_id,
                tasks=ts,
                vault_paths=vault_paths,
                profiles_root=profiles_root,
                artifacts_root=artifacts_root,
                tags=tags,
                headless=headless,
                sem=sem,
            )
        )

    all_results: List[ModelResult] = []
    all_results_nested = await asyncio.gather(*coros, return_exceptions=True)

    for item in all_results_nested:
        if isinstance(item, Exception):
            print(f"[run_all] site worker failed: {item}", flush=True)
            continue
        all_results.extend(item)

    # 仲裁融合与最终结论（用 chatgpt UI）
    try:
        await run_synthesis_and_final(
            brief=brief,
            run_id=run_id,
            results=all_results,
            vault_paths=vault_paths,
            profiles_root=profiles_root,
            artifacts_root=artifacts_root,
            tags=tags,
            headless=headless,
        )
    except Exception as e:
        print(f"[synthesis] failed: {e}", flush=True)

    global_t1 = time.perf_counter()
    print(
        f"[run_all] end | results={len(all_results)}/{total_tasks} "
        f"| dur={_fmt_secs(max(0.0, global_t1 - global_t0))} "
        f"| local={_now_local_iso()} | utc={_now_utc_iso()}",
        flush=True,
    )

    return run_index_path, all_results