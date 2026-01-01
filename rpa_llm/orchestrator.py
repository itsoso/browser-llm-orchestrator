# -*- coding: utf-8 -*-
"""
Author: xiaofan with Codex Cusor
Created: 2025-12-29 20:27:11 +0800
Modified: 2025-12-31 19:09:41 +0800
"""
# rpa_llm/orchestrator.py
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from .adapters import create_adapter
from .driver_client import run_task as driver_run_task
from .models import Brief, ModelResult, StreamSpec, Task
from .prompts import SynthesisPromptConfig, build_dual_model_arbitration_prompt
from .utils import beijing_now_iso, ensure_dir, slugify, utc_now_iso
from .vault import (
    build_run_index_note,
    make_model_output_filename,
    make_run_paths,
    model_output_note_body,
    write_markdown,
)


def _now_local_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _fmt_secs(s: float | None) -> str:
    if s is None:
        return "n/a"
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
    """
    渲染 prompt 模板，替换占位符。
    
    确保 context 和 topic 被正确格式化（去除多余空白，保留内容）。
    """
    # 清理 context：去除首尾空白，但保留内部格式
    context_clean = context.strip() if context else ""
    
    # 清理 topic
    topic_clean = topic.strip() if topic else ""
    
    # 生成问题清单
    qb = "\n".join([f"- {q.strip()}" for q in questions if q.strip()]) if questions else "-（无）"
    
    # 替换模板中的占位符
    prompt = stream.prompt_template.format(
        topic=topic_clean,
        context=context_clean,
        questions_bullets=qb,
    )
    
    # 清理最终输出：去除首尾空白，但保留内部格式
    return prompt.strip()


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
    driver_url: str | None = None,
    task_timeout_s: int = 480,
) -> List[ModelResult]:
    results: List[ModelResult] = []
    driver_url = (driver_url or "").strip() or None

    async def _run_via_driver() -> List[ModelResult]:
        total = len(tasks)
        durations: List[float] = []

        print(
            f"[{beijing_now_iso()}] [{site_id}] worker(driver) start | tasks={total} | driver={driver_url} "
            f"| local={_now_local_iso()} | utc={_now_utc_iso()}",
            flush=True,
        )

        for idx, t in enumerate(tasks, start=1):
            started_utc = utc_now_iso()
            started_local = _now_local_iso()
            t0 = time.perf_counter()

            avg = (sum(durations) / len(durations)) if durations else None
            eta = avg * (total - idx + 1) if avg is not None else None

            print(
                f"[{beijing_now_iso()}] [{site_id}] task {idx}/{total} start(driver) | stream={t.stream_id} "
                f"| local={started_local} | utc={started_utc}"
                + (f" | avg={_fmt_secs(avg)} | eta~{_fmt_secs(eta)}" if avg is not None else ""),
                flush=True,
            )

            # 优化：错峰启动机制，避免多个浏览器窗口同时抢 CPU 导致资源死锁
            # 第一个任务立即启动，后续任务错峰 3 秒（让前一个页面的 heavy JS 加载完）
            if idx > 1:
                await asyncio.sleep(3.0)
                print(f"[{beijing_now_iso()}] [{site_id}] staggered start delay (3s) for task {idx}", flush=True)

            try:
                payload = await asyncio.to_thread(driver_run_task, driver_url, site_id, t.prompt, task_timeout_s)
                ok = bool(payload.get("ok"))
                answer = payload.get("answer") or ""
                url = payload.get("url") or ""
                err = payload.get("error")
                if not ok and err:
                    print(f"[{beijing_now_iso()}] [{site_id}] driver error: {err}", flush=True)
            except Exception as e:
                ok = False
                answer, url, err = "", "", str(e)
                print(f"[{beijing_now_iso()}] [{site_id}] driver exception: {err}", flush=True)

            t1 = time.perf_counter()
            ended_utc = utc_now_iso()
            ended_local = _now_local_iso()
            duration_s = max(0.0, t1 - t0)
            durations.append(duration_s)

            avg2 = sum(durations) / len(durations)
            eta2 = avg2 * (total - idx)

            print(
                f"[{beijing_now_iso()}] [{site_id}] task {idx}/{total} done(driver) | stream={t.stream_id} | ok={ok} "
                f"| dur={_fmt_secs(duration_s)} | avg={_fmt_secs(avg2)} | eta~{_fmt_secs(eta2)} "
                f"| local_end={ended_local} | utc_end={ended_utc}",
                flush=True,
            )

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
                    created_utc=ended_utc,
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
                    created_utc=ended_utc,
                    ok=False,
                    error=err,
                )

            results.append(res)

            fname = make_model_output_filename(t.topic, t.stream_id, t.site_id)
            out_path = vault_paths["model"] / fname
            fm = {
                "type": ["model_output"],
                "created": started_local,
                "author": res.site_id,
                "run_id": res.run_id,
                "topic": res.topic,
                "stream": f"{res.stream_id} | {res.stream_name}",
                "url": res.source_url,
                "ok": str(ok),
                "started": started_local,
                "ended": ended_local,
                "duration_s": f"{duration_s:.3f}",
                "driver_url": driver_url or "",
                "tags": tags[:12],
            }
            body = model_output_note_body(res.prompt, res.answer_text if res.ok else f"ERROR: {res.error}")
            write_markdown(out_path, fm, body)

        print(
            f"[{beijing_now_iso()}] [{site_id}] worker(driver) end | done={len(results)}/{total} | local={_now_local_iso()} | utc={_now_utc_iso()}",
            flush=True,
        )
        return results

    async def _run_local_adapter() -> List[ModelResult]:
        total = len(tasks)
        durations: List[float] = []

        profile_dir = profiles_root / site_id
        site_artifacts = artifacts_root / site_id
        ensure_dir(profile_dir)
        ensure_dir(site_artifacts)

        adapter = create_adapter(site_id, profile_dir=profile_dir, artifacts_dir=site_artifacts, headless=headless)

        print(
            f"[{beijing_now_iso()}] [{site_id}] worker(local) start | tasks={total} | local={_now_local_iso()} | utc={_now_utc_iso()}",
            flush=True,
        )

        async with adapter:
            for idx, t in enumerate(tasks, start=1):
                started_utc = utc_now_iso()
                started_local = _now_local_iso()
                t0 = time.perf_counter()

                avg = (sum(durations) / len(durations)) if durations else None
                eta = avg * (total - idx + 1) if avg is not None else None

                print(
                    f"[{beijing_now_iso()}] [{site_id}] task {idx}/{total} start | stream={t.stream_id} "
                    f"| local={started_local} | utc={started_utc}"
                    + (f" | avg={_fmt_secs(avg)} | eta~{_fmt_secs(eta)}" if avg is not None else ""),
                    flush=True,
                )

                try:
                    try:
                        answer, url = await adapter.ask(t.prompt, timeout_s=task_timeout_s)
                    except TypeError:
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

                avg2 = sum(durations) / len(durations)
                eta2 = avg2 * (total - idx)

                print(
                    f"[{beijing_now_iso()}] [{site_id}] task {idx}/{total} done | stream={t.stream_id} | ok={ok} "
                    f"| dur={_fmt_secs(duration_s)} | avg={_fmt_secs(avg2)} | eta~{_fmt_secs(eta2)} "
                    f"| local_end={ended_local} | utc_end={ended_utc}",
                    flush=True,
                )

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
                        created_utc=ended_utc,
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
                        created_utc=ended_utc,
                        ok=False,
                        error=err,
                    )

                results.append(res)

                fname = make_model_output_filename(t.topic, t.stream_id, t.site_id)
                out_path = vault_paths["model"] / fname
                fm = {
                    "type": ["model_output"],
                    "created": started_local,
                    "author": res.site_id,
                    "run_id": res.run_id,
                    "topic": res.topic,
                    "stream": f"{res.stream_id} | {res.stream_name}",
                    "url": res.source_url,
                    "ok": str(ok),
                    "started": started_local,
                    "ended": ended_local,
                    "duration_s": f"{duration_s:.3f}",
                    "tags": tags[:12],
                }
                body = model_output_note_body(res.prompt, res.answer_text if res.ok else f"ERROR: {res.error}")
                write_markdown(out_path, fm, body)

        print(
            f"[{beijing_now_iso()}] [{site_id}] worker(local) end | done={len(results)}/{total} | local={_now_local_iso()} | utc={_now_utc_iso()}",
            flush=True,
        )
        return results

    async def _run() -> List[ModelResult]:
        if driver_url:
            return await _run_via_driver()
        return await _run_local_adapter()

    if sem is None:
        return await _run()

    async with sem:
        return await _run()


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
    arbitrator_site = str(brief.output.get("arbitrator_site", "gemini")).strip().lower()
    timeout_s = int(brief.output.get("synthesis_timeout_s", 420))

    # driver_url：优先 brief，其次 env（保留兜底）
    driver_url = (str(brief.output.get("driver_url", "")).strip() or None) or (
        (os.environ.get("RPA_DRIVER_URL") or "").strip() or None
    )

    left_site = str(brief.output.get("synthesis_left_site", "gemini")).strip().lower()
    right_site = str(brief.output.get("synthesis_right_site", "chatgpt")).strip().lower()

    cfg = SynthesisPromptConfig(
        left_site=left_site,
        right_site=right_site,
        left_label=left_site.capitalize(),
        right_label=right_site.capitalize(),
    )
    prompt = build_dual_model_arbitration_prompt(brief, results, cfg)

    print(
        f"[{beijing_now_iso()}] [synthesis] start | arbitrator={arbitrator_site} | local={_now_local_iso()} | utc={_now_utc_iso()}",
        flush=True,
    )
    t0 = time.perf_counter()
    started_utc = utc_now_iso()
    started_local = _now_local_iso()

    if driver_url:
        payload = await asyncio.to_thread(driver_run_task, driver_url, arbitrator_site, prompt, timeout_s)
        ok = bool(payload.get("ok"))
        answer = payload.get("answer") or ""
        url = payload.get("url") or ""
        err = payload.get("error")
        if not ok:
            raise RuntimeError(f"driver synthesis failed (site={arbitrator_site}): {err}")
    else:
        profile_dir = profiles_root / arbitrator_site
        site_artifacts = artifacts_root / f"{arbitrator_site}__synthesis"
        ensure_dir(profile_dir)
        ensure_dir(site_artifacts)

        adapter = create_adapter(arbitrator_site, profile_dir=profile_dir, artifacts_dir=site_artifacts, headless=headless)
        async with adapter:
            try:
                answer, url = await adapter.ask(prompt, timeout_s=timeout_s)
            except TypeError:
                answer, url = await adapter.ask(prompt)

    t1 = time.perf_counter()
    ended_utc = utc_now_iso()
    ended_local = _now_local_iso()
    dur = max(0.0, t1 - t0)
    print(
        f"[{beijing_now_iso()}] [synthesis] done | dur={_fmt_secs(dur)} | local_end={ended_local} | utc_end={ended_utc}",
        flush=True,
    )

    topic_slug = slugify(brief.topic, max_len=40)  # 主题缩略，最多40字符
    synth_path = vault_paths["synth"] / f"synthesis__{arbitrator_site}__{topic_slug}.md"
    fm_s = {
        "type": ["synthesis"],
        "created": started_local,
        "author": arbitrator_site,
        "run_id": run_id,
        "topic": brief.topic,
        "url": url,
        "started": started_local,
        "ended": ended_local,
        "duration_s": f"{dur:.3f}",
        "tags": tags[:12],
    }
    write_markdown(synth_path, fm_s, answer)

    final_path = vault_paths["final"] / f"final__{arbitrator_site}__{topic_slug}.md"
    fm_f = {
        "type": ["final_decision"],
        "created": started_local,
        "author": arbitrator_site,
        "run_id": run_id,
        "topic": brief.topic,
        "url": url,
        "started": started_local,
        "ended": ended_local,
        "duration_s": f"{dur:.3f}",
        "tags": tags[:12],
    }
    write_markdown(final_path, fm_f, answer)


async def run_all(brief_path: Path, run_id: str, headless: bool = False) -> Tuple[Path, List[ModelResult]]:
    brief = load_brief(brief_path)

    vault_path = Path(brief.output["vault_path"]).expanduser().resolve()
    root_dir = brief.output.get("root_dir", "10_ResearchRuns")
    tags = list(brief.output.get("tags", []))

    driver_url = (str(brief.output.get("driver_url", "")).strip() or None) or (
        (os.environ.get("RPA_DRIVER_URL") or "").strip() or None
    )
    task_timeout_s = int(brief.output.get("task_timeout_s", 480))

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
        f"[{beijing_now_iso()}] [run_all] start | sites={len(site_map)} tasks={total_tasks} "
        f"| local={_now_local_iso()} | utc={_now_utc_iso()}",
        flush=True,
    )
    global_t0 = time.perf_counter()

    max_parallel_sites = int(brief.output.get("max_parallel_sites", 2))
    sem = asyncio.Semaphore(max_parallel_sites)
    # 优化：默认错峰启动 5 秒，避免多个浏览器窗口同时抢 CPU 导致资源死锁
    # 虽然看起来慢了 5 秒，但能避免 60 秒的资源争抢卡顿
    stagger_start_s = float(brief.output.get("stagger_start_s", 5) or 5)

    all_results: List[ModelResult] = []
    # 记录每个站点的耗时
    site_durations: Dict[str, float] = {}
    
    # 包装函数：同时处理延迟启动和耗时记录
    async def _run_with_delay_and_timing(site_id: str, delay_s: float, coro):
        if delay_s > 0:
            await asyncio.sleep(delay_s)
        site_t0 = time.perf_counter()
        try:
            result = await coro
            site_t1 = time.perf_counter()
            site_durations[site_id] = max(0.0, site_t1 - site_t0)
            return result
        except Exception as e:
            site_t1 = time.perf_counter()
            site_durations[site_id] = max(0.0, site_t1 - site_t0)
            raise e
    
    # 构建 coros，同时处理延迟启动和耗时记录
    coros = []
    for idx, (site_id, ts) in enumerate(site_map.items()):
        delay_s = max(0.0, stagger_start_s * idx)
        coros.append(
            _run_with_delay_and_timing(
                site_id=site_id,
                delay_s=delay_s,
                coro=run_site_worker(
                    site_id=site_id,
                    tasks=ts,
                    vault_paths=vault_paths,
                    profiles_root=profiles_root,
                    artifacts_root=artifacts_root,
                    tags=tags,
                    headless=headless,
                    sem=sem,
                    driver_url=driver_url,
                    task_timeout_s=task_timeout_s,
                ),
            )
        )
    
    all_results_nested = await asyncio.gather(*coros, return_exceptions=True)

    for item in all_results_nested:
        if isinstance(item, Exception):
            print(f"[{beijing_now_iso()}] [run_all] site worker failed: {item}", flush=True)
            continue
        all_results.extend(item)

    # 记录 synthesis 的耗时
    synthesis_duration = 0.0
    try:
        synthesis_t0 = time.perf_counter()
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
        synthesis_t1 = time.perf_counter()
        synthesis_duration = max(0.0, synthesis_t1 - synthesis_t0)
    except Exception as e:
        synthesis_t1 = time.perf_counter()
        synthesis_duration = max(0.0, synthesis_t1 - synthesis_t0)
        print(f"[{beijing_now_iso()}] [synthesis] failed: {e}", flush=True)

    global_t1 = time.perf_counter()
    total_duration = max(0.0, global_t1 - global_t0)
    
    # 打印详细的耗时统计
    print(
        f"[{beijing_now_iso()}] [run_all] end | results={len(all_results)}/{total_tasks} "
        f"| dur={_fmt_secs(total_duration)} "
        f"| local={_now_local_iso()} | utc={_now_utc_iso()}",
        flush=True,
    )
    
    # 打印各 LLM 的处理耗时
    print(f"[{beijing_now_iso()}] [run_all] timing breakdown:", flush=True)
    for site_id in sorted(site_map.keys()):
        if site_id in site_durations:
            print(f"[{beijing_now_iso()}] [run_all]   {site_id}: {_fmt_secs(site_durations[site_id])}", flush=True)
    if synthesis_duration > 0:
        print(f"[{beijing_now_iso()}] [run_all]   synthesis: {_fmt_secs(synthesis_duration)}", flush=True)
    print(f"[{beijing_now_iso()}] [run_all]   total: {_fmt_secs(total_duration)}", flush=True)

    return run_index_path, all_results
