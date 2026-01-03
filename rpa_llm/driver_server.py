# -*- coding: utf-8 -*-
"""
Author: xiaofan with Codex Cusor
Created: 2025-12-30 01:40:10 +0800
Modified: 2025-12-31 19:09:41 +0800
"""
# rpa_llm/driver_server.py
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

from .adapters import create_adapter
from .utils import beijing_now_iso


def utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def local_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


@dataclass
class SiteRuntime:
    site_id: str
    adapter: object
    lock: asyncio.Lock
    ready: bool = False


class DriverServer:
    """
    一个极简 HTTP server：
    - GET  /health
    - GET  /status
    - POST /run_task  {"site_id": "...", "prompt": "...", "timeout_s": 1200, "model_version": "5.2pro"}
    每个 site_id 常驻一个 adapter（Playwright persistent context），并用 lock 保证站点内串行。
    """

    def __init__(
        self,
        host: str,
        port: int,
        sites: list[str],
        profiles_root: Path,
        artifacts_root: Path,
        headless: bool = False,
        prewarm: bool = True,
        stealth: bool = True,
    ):
        self.host = host
        self.port = port
        self.sites = sites
        self.profiles_root = profiles_root
        self.artifacts_root = artifacts_root
        self.headless = headless
        self.prewarm = prewarm
        self.stealth = stealth

        self._server: Optional[asyncio.AbstractServer] = None
        self._stop = asyncio.Event()

        self._sites: Dict[str, SiteRuntime] = {}

    async def start(self) -> None:
        self.profiles_root.mkdir(parents=True, exist_ok=True)
        self.artifacts_root.mkdir(parents=True, exist_ok=True)

        # 预创建 runtime（adapter 延迟/预热都可）
        for site_id in self.sites:
            self._sites[site_id] = SiteRuntime(site_id=site_id, adapter=None, lock=asyncio.Lock(), ready=False)

        if self.prewarm:
            print(f"[{beijing_now_iso()}] [driver] prewarm start | sites={self.sites} | local={local_iso()} | utc={utc_iso()}", flush=True)
            prewarm_failed = []
            for site_id in self.sites:
                try:
                    await self._ensure_site(site_id)
                except Exception as e:
                    prewarm_failed.append(site_id)
                    # 错误信息已在 _ensure_site 中打印，这里只记录失败
                    print(f"[{beijing_now_iso()}] [driver] prewarm failed: {site_id} (will retry on first request)", flush=True)
            
            if prewarm_failed:
                print(f"[{beijing_now_iso()}] [driver] prewarm partial | success={len(self.sites)-len(prewarm_failed)}/{len(self.sites)} | failed={prewarm_failed} | local={local_iso()} | utc={utc_iso()}", flush=True)
            else:
                print(f"[{beijing_now_iso()}] [driver] prewarm done | local={local_iso()} | utc={utc_iso()}", flush=True)

        self._server = await asyncio.start_server(self._handle_conn, self.host, self.port)
        addrs = ", ".join(str(sock.getsockname()) for sock in self._server.sockets or [])
        print(f"[driver] listening on {addrs}", flush=True)

    async def stop(self) -> None:
        print(f"[driver] stopping ...", flush=True)
        self._stop.set()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        # 关闭所有 adapter（释放浏览器）
        for site_id, rt in self._sites.items():
            if rt.adapter is not None:
                try:
                    # 直接调用 async context exit
                    await rt.adapter.__aexit__(None, None, None)
                    print(f"[driver] closed adapter: {site_id}", flush=True)
                except Exception as e:
                    print(f"[driver] close adapter failed: {site_id} err={e}", flush=True)

    async def _ensure_site(self, site_id: str) -> None:
        rt = self._sites.get(site_id)
        if rt is None:
            raise ValueError(f"unknown site_id: {site_id}")
        if rt.adapter is not None and rt.ready:
            return

        profile_dir = self.profiles_root / site_id
        art_dir = self.artifacts_root / site_id
        profile_dir.mkdir(parents=True, exist_ok=True)
        art_dir.mkdir(parents=True, exist_ok=True)

        adapter = create_adapter(site_id, profile_dir=profile_dir, artifacts_dir=art_dir, headless=self.headless, stealth=self.stealth)
        # 手动进入 async context（常驻）
        try:
            await adapter.__aenter__()
            rt.adapter = adapter
            rt.ready = True
            print(f"[{beijing_now_iso()}] [driver] site ready: {site_id}", flush=True)
        except Exception as e:
            # 如果初始化失败（通常是 ensure_ready 失败），提供友好提示
            error_msg = str(e)
            if "ensure_ready" in error_msg.lower() or "textbox" in error_msg.lower() or "cannot locate" in error_msg.lower():
                print(f"\n{'='*70}", flush=True)
                print(f"[{beijing_now_iso()}] [driver] ⚠️  {site_id} 初始化失败：需要登录或验证", flush=True)
                print(f"[{beijing_now_iso()}] [driver] 错误: {error_msg}", flush=True)
                print(f"[{beijing_now_iso()}] [driver] ", flush=True)
                print(f"[{beijing_now_iso()}] [driver] 💡 解决方案：运行预热脚本手动登录", flush=True)
                print(f"[{beijing_now_iso()}] [driver]    python warmup.py {site_id}", flush=True)
                print(f"[{beijing_now_iso()}] [driver] ", flush=True)
                print(f"[{beijing_now_iso()}] [driver] 或者：", flush=True)
                print(f"[{beijing_now_iso()}] [driver]    1. 检查 {profile_dir} 目录是否存在", flush=True)
                print(f"[{beijing_now_iso()}] [driver]    2. 检查是否需要安装 playwright-stealth: pip install playwright-stealth", flush=True)
                print(f"[{beijing_now_iso()}] [driver]    3. 查看截图: {art_dir}/", flush=True)
                print(f"{'='*70}\n", flush=True)
            # 不设置 ready=True，让后续请求可以重试
            rt.adapter = adapter  # 保留 adapter 引用，但标记为未就绪
            rt.ready = False
            raise  # 重新抛出异常，让调用者知道失败

    async def _handle_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # 添加请求追踪
        import uuid
        request_id = str(uuid.uuid4())[:8]
        recv_time = time.time()
        
        try:
            req_line = await reader.readline()
            if not req_line:
                writer.close()
                await writer.wait_closed()
                return
            req_line = req_line.decode("utf-8", errors="ignore").strip()
            parts = req_line.split()
            if len(parts) < 2:
                await self._write_json(writer, 400, {"ok": False, "error": "bad request line"})
                return

            method, path = parts[0].upper(), parts[1]

            headers = {}
            while True:
                line = await reader.readline()
                if not line or line in (b"\r\n", b"\n"):
                    break
                s = line.decode("utf-8", errors="ignore").strip()
                if ":" in s:
                    k, v = s.split(":", 1)
                    headers[k.strip().lower()] = v.strip()

            body = b""
            if method in ("POST", "PUT"):
                clen = int(headers.get("content-length", "0") or "0")
                if clen > 0:
                    body = await reader.readexactly(clen)

            if method == "GET" and path == "/health":
                await self._write_json(writer, 200, {"ok": True, "local": local_iso(), "utc": utc_iso()})
                return

            if method == "GET" and path == "/status":
                status = {sid: {"ready": rt.ready} for sid, rt in self._sites.items()}
                await self._write_json(writer, 200, {"ok": True, "sites": status, "local": local_iso(), "utc": utc_iso()})
                return

            if method == "POST" and path == "/run_task":
                try:
                    payload = json.loads(body.decode("utf-8")) if body else {}
                except Exception:
                    await self._write_json(writer, 400, {"ok": False, "error": "invalid json"})
                    return

                site_id = payload.get("site_id")
                prompt = payload.get("prompt")
                timeout_s = int(payload.get("timeout_s", 1200))
                model_version = payload.get("model_version")  # 可选的模型版本参数
                new_chat = payload.get("new_chat", False)  # 是否新开窗口
                auto_mode = payload.get("auto_mode", False)  # 是否自动模式（批量处理时不等待用户输入）
                
                # 关键修复：如果是批量处理模式（new_chat=True 或 auto_mode=True），设置环境变量
                # 这样 manual_checkpoint 会自动抛出异常而不是等待用户输入
                if auto_mode or new_chat:
                    os.environ["RPA_AUTO_MODE"] = "1"
                if not site_id or not prompt:
                    await self._write_json(writer, 400, {"ok": False, "error": "missing site_id or prompt"})
                    return
                if site_id not in self._sites:
                    await self._write_json(writer, 400, {"ok": False, "error": f"unknown site_id: {site_id}"})
                    return
                try:
                    plen = len(prompt) if isinstance(prompt, str) else -1
                    model_info = f" | model_version={model_version}" if model_version else ""
                    new_chat_info = f" | new_chat={new_chat}" if new_chat else ""
                    print(
                        f"[{beijing_now_iso()}] [driver] run_task recv | request_id={request_id} | site={site_id} | prompt_len={plen} | timeout_s={timeout_s}{model_info}{new_chat_info}",
                        flush=True,
                    )
                except Exception:
                    pass

                # 确保 site 常驻 adapter 已启动，并站点内串行
                rt = self._sites[site_id]
                lock_wait_start = time.time()
                async with rt.lock:
                    lock_wait_time = time.time() - lock_wait_start
                    if lock_wait_time > 0.1:
                        print(
                            f"[{beijing_now_iso()}] [driver] request {request_id} waited {lock_wait_time:.2f}s for lock",
                            flush=True,
                        )
                    process_start_time = time.time()
                    print(
                        f"[{beijing_now_iso()}] [driver] request {request_id} start processing | site={site_id}",
                        flush=True,
                    )
                    try:
                        await self._ensure_site(site_id)
                    except Exception as e:
                        # 如果初始化失败，返回友好错误
                        await self._write_json(
                            writer,
                            503,
                            {
                                "ok": False,
                                "site_id": site_id,
                                "error": f"Site initialization failed: {str(e)}. Please run: python warmup.py {site_id}",
                                "hint": "The site may need manual login/verification. Use warmup.py to prepare the profile.",
                            },
                        )
                        return
                    
                    started_utc = utc_iso()
                    started_local = local_iso()
                    t0 = time.perf_counter()

                    try:
                        # adapter.ask(prompt, timeout_s=..., model_version=..., new_chat=...) 兼容 ChatGPT；其他 adapter 也支持这些参数则传入
                        adapter = rt.adapter
                        try:
                            # 尝试传递 model_version 和 new_chat 参数（ChatGPT adapter 支持）
                            kwargs = {"timeout_s": timeout_s}
                            if model_version:
                                kwargs["model_version"] = model_version
                            if new_chat:
                                kwargs["new_chat"] = new_chat
                            
                            try:
                                answer, url = await adapter.ask(prompt, **kwargs)
                            except TypeError:
                                # 如果 adapter 不支持某些参数，逐步回退
                                kwargs.pop("new_chat", None)
                                try:
                                    answer, url = await adapter.ask(prompt, **kwargs)
                                except TypeError:
                                    kwargs.pop("model_version", None)
                                    answer, url = await adapter.ask(prompt, **kwargs)
                        except TypeError:
                            answer, url = await adapter.ask(prompt)  # fallback
                        ok = True
                        err = None
                    except Exception as e:
                        # 处理 TargetClosedError：重启 adapter 并重试一次
                        if "TargetClosed" in str(e) or "Target page" in str(e) or "Target context" in str(e):
                            try:
                                if rt.adapter is not None:
                                    await rt.adapter.__aexit__(None, None, None)
                            except Exception:
                                pass
                            rt.adapter = None
                            rt.ready = False
                            try:
                                await self._ensure_site(site_id)
                                adapter = rt.adapter
                                try:
                                    # 尝试传递 model_version 和 new_chat 参数（ChatGPT adapter 支持）
                                    kwargs = {"timeout_s": timeout_s}
                                    if model_version:
                                        kwargs["model_version"] = model_version
                                    if new_chat:
                                        kwargs["new_chat"] = new_chat
                                    
                                    try:
                                        answer, url = await adapter.ask(prompt, **kwargs)
                                    except TypeError:
                                        # 如果 adapter 不支持某些参数，逐步回退
                                        kwargs.pop("new_chat", None)
                                        try:
                                            answer, url = await adapter.ask(prompt, **kwargs)
                                        except TypeError:
                                            kwargs.pop("model_version", None)
                                            answer, url = await adapter.ask(prompt, **kwargs)
                                except TypeError:
                                    answer, url = await adapter.ask(prompt)
                                ok = True
                                err = None
                            except Exception as retry_err:
                                answer, url = "", ""
                                ok = False
                                err = str(retry_err)
                        else:
                            answer, url = "", ""
                            ok = False
                            err = str(e)

                    t1 = time.perf_counter()
                    ended_utc = utc_iso()
                    ended_local = local_iso()
                    dur_s = max(0.0, t1 - t0)
                    process_dur_s = max(0.0, t1 - process_start_time)
                    
                    # 记录请求完成日志
                    print(
                        f"[{beijing_now_iso()}] [driver] request {request_id} completed | "
                        f"site={site_id} | ok={ok} | duration={dur_s:.2f}s | process_duration={process_dur_s:.2f}s",
                        flush=True,
                    )

                    await self._write_json(
                        writer,
                        200,
                        {
                            "ok": ok,
                            "site_id": site_id,
                            "answer": answer,
                            "url": url,
                            "error": err,
                            "started_utc": started_utc,
                            "ended_utc": ended_utc,
                            "started_local": started_local,
                            "ended_local": ended_local,
                            "duration_s": dur_s,
                        },
                    )
                    return

            await self._write_json(writer, 404, {"ok": False, "error": "not found"})
        except Exception as e:
            try:
                await self._write_json(writer, 500, {"ok": False, "error": f"server error: {e}"})
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _write_json(self, writer: asyncio.StreamWriter, status: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        status_line = f"HTTP/1.1 {status} OK\r\n"
        headers = (
            "Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(data)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        )
        writer.write(status_line.encode("utf-8"))
        writer.write(headers.encode("utf-8"))
        writer.write(data)
        await writer.drain()


async def main_async() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=27124)
    ap.add_argument("--sites", default="chatgpt,gemini,perplexity,grok,qianwen")
    ap.add_argument("--profiles-root", default="profiles")
    ap.add_argument("--artifacts-root", default="runs/driver/artifacts")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--no-prewarm", action="store_true")
    args = ap.parse_args()

    sites = [s.strip() for s in args.sites.split(",") if s.strip()]
    server = DriverServer(
        host=args.host,
        port=args.port,
        sites=sites,
        profiles_root=Path(args.profiles_root).resolve(),
        artifacts_root=Path(args.artifacts_root).resolve(),
        headless=args.headless,
        prewarm=not args.no_prewarm,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(server.stop()))
        except NotImplementedError:
            pass

    await server.start()
    await server._stop.wait()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
